"""OpenWA Health Check - runs every 5 minutes via scheduler.

Also performs module cache validation (Fix #1) to prevent the
"Module attendance not found" error that killed all notifications
on 2026-07-11.
"""
import frappe
import requests
from frappe.utils import get_datetime, now
from datetime import datetime, timezone
import time


def _validate_module_cache():
	"""Validate that the Frappe module cache is not stale.

	FIX #1: On 2026-07-11, a stale Redis cache caused "Module attendance
	not found" which killed ALL WhatsApp notifications for the entire day.
	This function detects and fixes that by:

	1. Checking if 'attendance' module resolves to 'kreativ_attendance'
	2. If not, clearing the app_modules cache and rebuilding the map
	3. Logging the fix so we can detect recurrence

	This runs every 5 minutes as part of the health check.
	"""
	try:
		# Check if the attendance module resolves correctly
		app = frappe.local.module_app.get("attendance")
		if app == "kreativ_attendance":
			return  # Cache is healthy

		# Cache is stale or missing — rebuild it aggressively
		frappe.cache().delete_value("app_modules")
		frappe.client_cache.delete_value("installed_app_modules")
		# Clear ALL controller caches (not just the current site)
		# This prevents "Module import failed" errors from cached lookups
		frappe.controllers.clear()
		# Also clear the doctype_python_modules cache
		import frappe.modules.utils
		frappe.modules.utils.doctype_python_modules.clear()
		frappe.setup_module_map()

		# Verify the fix
		app_after = frappe.local.module_app.get("attendance")
		if app_after == "kreativ_attendance":
			frappe.log_error(
				title="Module Cache Rebuilt",
				message=(
					"Stale module cache detected and fixed. "
					f"attendance module now maps to: {app_after}. "
					"Previous value: " + (app or "None") + ". "
					"This was the root cause of notification failures on 2026-07-11."
				),
			)
		else:
			frappe.log_error(
				title="Module Cache Rebuild Failed",
				message=(
					f"attendance module still not found after cache rebuild. "
					f"Current mapping: {app_after}. "
					f"Check kreativ_attendance/modules.txt exists and has 'attendance'."
				),
			)
	except Exception as e:
		frappe.log_error(
			title="Module Cache Validation Error",
			message=str(e),
		)


def _session_is_stale(settings, data: dict) -> bool:
	"""Check if the OpenWA session's lastActive is older than 60 minutes.

	Sets a cache flag so retry_missed_notifications can short-circuit.
	Returns True if stale, False otherwise.
	"""
	last_active = data.get("lastActive")
	if not last_active:
		return False

	last_dt = get_datetime(last_active)
	age_minutes = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60

	if age_minutes > 60:
		frappe.cache().set_value("openwa_session_stale", True, expires_in_sec=7200)
		return True

	# Session is healthy again — clear the stale flag
	frappe.cache().delete_value("openwa_session_stale")
	return False


def _restart_session(settings) -> dict:
	"""Stop and restart the OpenWA session via API to recover a stale WebSocket.

	OpenWA keeps the session credentials (multi-device keys), so a stop/start
	cycle re-establishes the WhatsApp Web WebSocket without needing a QR scan.
	Returns {"status": "recovered"} on success or {"status": "stale", ...} on failure.
	"""
	base_url = settings.base_url.rstrip("/")
	api_key = settings.get_password("api_key", raise_exception=False) or ""
	session_id = settings.session_id or "default"
	headers = {"X-API-Key": api_key}

	# 1. Stop the session
	try:
		r = requests.post(f"{base_url}/api/sessions/{session_id}/stop", headers=headers, timeout=10)
		if r.status_code not in (200, 204):
			frappe.log_error(
				title="OpenWA Session Stop Failed",
				message=f"Stop returned {r.status_code}: {r.text[:200]}",
			)
			return {"status": "stale", "reason": f"Stop returned {r.status_code}"}
	except Exception as e:
		frappe.log_error(title="OpenWA Session Stop Error", message=str(e))
		return {"status": "stale", "reason": f"Stop error: {e}"}

	time.sleep(2)

	# 2. Start the session
	try:
		r = requests.post(f"{base_url}/api/sessions/{session_id}/start", headers=headers, timeout=15)
	except Exception as e:
		frappe.log_error(title="OpenWA Session Start Error", message=str(e))
		return {"status": "stale", "reason": f"Start error: {e}"}

	time.sleep(2)

	# 3. Verify session recovered
	try:
		r = requests.get(f"{base_url}/api/sessions/{session_id}", headers=headers, timeout=10)
		if r.status_code == 200:
			data = r.json()
			new_status = data.get("status", "")
			last_active = data.get("lastActive", "")

			if new_status in ("ready", "connected") and last_active:
				last_dt = get_datetime(last_active)
				age_seconds = (datetime.now(timezone.utc) - last_dt).total_seconds()
				if age_seconds < 120:  # active within last 2 minutes = recovered
					frappe.log_error(
						title="OpenWA Session Recovered",
						message=f"Auto-recovered via stop/start. lastActive={last_active}",
					)
					frappe.cache().delete_value("openwa_session_stale")
					return {"status": "recovered", "lastActive": last_active}
	except Exception:
		pass

	return {"status": "stale", "reason": "Session not active after restart"}


def _retry_unsent():
	"""Retry missed WhatsApp notifications now that the session is healthy.

	Called at the end of every successful health check so the backlog is
	cleared without waiting for the next retry scheduler run.

	The perm-failure reset (status 2 → 0) lives in retry_missed_notifications
	now, so this is just a thin trigger.
	"""
	try:
		from kreativ_attendance.attendance.whatsapp import retry_missed_notifications
		retry_missed_notifications()
	except Exception:
		pass  # retry will fire again in 5 min


def check_openwa_session():
	"""
	Scheduled job: runs every 5 minutes to verify OpenWA session is healthy.
	If session is disconnected, restarts the OpenWA service via supervisor.

	Also validates the module cache (Fix #1) to prevent "Module attendance
	not found" errors that kill all notifications.
	"""
	try:
		# Fix #1: Validate module cache before anything else.
		# If the cache is stale, notifications will fail with "Module not found".
		_validate_module_cache()

		settings = frappe.get_cached_doc("OpenWA Settings")
		if not settings.enabled:
			return {"status": "skipped", "reason": "OpenWA not enabled"}

		base_url = settings.base_url.rstrip("/") if settings.base_url else ""
		api_key = settings.get_password("api_key", raise_exception=False) or ""
		session_id = settings.session_id or "default"

		if not base_url or not api_key:
			return {"status": "error", "reason": "Missing base_url or api_key in settings"}

		# 1. Check HTTP endpoint
		try:
			r = requests.get(f"{base_url}/", timeout=10)
			if r.status_code != 200:
				return _restart_openwa(f"HTTP {r.status_code}")
		except Exception as e:
			return _restart_openwa(f"HTTP check failed: {e}")

		# 2. Check session status
		try:
			r = requests.get(
				f"{base_url}/api/sessions/{session_id}",
				headers={"X-API-Key": api_key},
				timeout=10
			)
			if r.status_code != 200:
				return _restart_openwa(f"Session API {r.status_code}")

			data = r.json()
			status = data.get("status", "")

			if status not in ["ready", "connected"]:
				frappe.log_error(
					f"OpenWA session unhealthy: {status}",
					"OpenWA Health Check"
				)
				return _restart_openwa(f"Session status: {status}")

			# Check for stale session (lastActive > 60 min ago)
			if _session_is_stale(settings, data):
				# Auto-recover: stop/start session to re-establish WebSocket
				result = _restart_session(settings)
				if result.get("status") == "recovered":
					# Retry unsent messages now that session is back
					_retry_unsent()
					return {
						"status": "recovered",
						"session": status,
						"lastActive": result.get("lastActive"),
						"checked": now(),
					}
				# Recovery failed — log and keep stale flag set
				last_active = data.get("lastActive", "unknown")
				frappe.log_error(
					title="OpenWA Session Stale",
					message=(
						f"Session {settings.session_id} "
						f"lastActive={last_active}. "
						f"Auto-recovery failed: "
						f"{result.get('reason', 'unknown')}. "
						f"Phone may be offline. Scan QR code at {settings.base_url}/ to reconnect."
					),
				)
				return {"status": "stale", "session": status, "lastActive": data.get("lastActive")}

		except Exception as e:
			return _restart_openwa(f"Session check failed: {e}")

		# 3. Retry any missed notifications now that session is confirmed healthy
		_retry_unsent()

		# 4. Optional: Check for recent message activity (heartbeat)
		# If no messages for > 1 hour, could indicate silent failure
		# This is optional - uncomment if needed
		# _check_message_activity(base_url, api_key, session_id)

		return {"status": "healthy", "session": status, "checked": now()}

	except Exception as e:
		frappe.log_error(f"OpenWA health check error: {e}", "OpenWA Health Check")
		return {"status": "error", "reason": str(e)}


def _restart_openwa(reason: str):
	"""Restart OpenWA via supervisor and log the action"""
	frappe.log_error(
		f"OpenWA restarted by health check: {reason}",
		"OpenWA Auto-Restart"
	)

	try:
		import subprocess
		result = subprocess.run(
			["supervisorctl", "restart", "openwa"],
			capture_output=True, text=True, timeout=30
		)
		if result.returncode == 0:
			frappe.log_error(
				f"OpenWA restart successful: {reason}",
				"OpenWA Auto-Restart"
			)
			return {"status": "restarted", "reason": reason}
		else:
			frappe.log_error(
				f"OpenWA restart failed: {result.stderr}",
				"OpenWA Auto-Restart Failed"
			)
			return {"status": "restart_failed", "reason": reason, "error": result.stderr}
	except Exception as e:
		frappe.log_error(f"OpenWA restart exception: {e}", "OpenWA Auto-Restart Failed")
		return {"status": "restart_failed", "reason": reason, "error": str(e)}


def _check_message_activity(base_url: str, api_key: str, session_id: str):
	"""Optional: Check if messages are flowing (heartbeat)"""
	try:
		r = requests.get(
			f"{base_url}/api/sessions/{session_id}/messages?limit=1",
			headers={"X-API-Key": api_key},
			timeout=10
		)
		if r.status_code == 200:
			data = r.json()
			messages = data.get("messages", [])
			if messages:
				last_msg = messages[0]
				ts = last_msg.get("timestamp", 0)
				if ts:
					from datetime import datetime
					last_time = datetime.fromtimestamp(ts)
					now_dt = datetime.now()
					age_minutes = (now_dt - last_time).total_seconds() / 60
					if age_minutes > 60:
						frappe.log_error(
							f"No WhatsApp messages for {age_minutes:.0f} minutes",
							"OpenWA Heartbeat Warning"
						)
	except Exception:
		pass  # Silent fail for optional check