"""OpenWA Health Check - runs every 5 minutes via scheduler.

Circuit Breaker (per-site):
    Tracks consecutive health check failures PER SITE using scoped cache keys
    (openwa:streak:{site}). After 3 consecutive failures, trips the breaker
    and enters exponential backoff (5min, 10min, 20min...) to avoid hammering
    a down OpenWA instance. Reset on first success.

Auto-Recovery:
    1. Session 404 (lost/deleted) -> Auto-creates a new session on OpenWA,
       updates Frappe settings, starts the session, and logs a prominent
       error: "QR SCAN NEEDED" for admin to re-link WhatsApp.
    2. Session "created"/"disconnected" -> Auto-starts via POST /start
       (works when multi-device credentials still exist on disk).
    3. Session stale (lastActive > 60 min) -> Stop/start cycle via API.
    4. Session "ready"/"connected" -> Healthy, reset breaker.

Per-Site Isolation (2026-07-13):
    Each site (kreativ216, kreativ316) now has its own OpenWA session
    and circuit breaker state.  One site's session going down no longer
    affects the other.

Gateway Safety:
    Auto-restart of the gateway process is DISABLED in health checks to
    prevent one site from killing the other's connection.  Gateway
    restarts must be done manually via 'sudo supervisorctl restart openwa'.
    The gateway is now managed by supervisor for automatic crash recovery.
"""
import frappe
import random
import requests
from frappe.utils import get_datetime, now
from datetime import datetime, timezone
import time

# Circuit breaker — shared with gravures_custom via consolidated module
from gravures_custom.overrides.whatsapp_queue import (
    CIRCUIT_BREAKER_THRESHOLD,
    _breaker_key,
    _get_failure_streak,
    increment_circuit_breaker as _increment_failure_streak,
    reset_circuit_breaker as _reset_failure_streak,
)

MAX_BACKOFF_MINUTES = 60        # cap backoff at 1 hour
MAX_BREAKER_DURATION_MINUTES = 60  # hard ceiling: auto-reset breaker after this


def _is_breaker_tripped() -> bool:
	"""Check if circuit breaker is active (>= threshold consecutive failures)."""
	return _get_failure_streak() >= CIRCUIT_BREAKER_THRESHOLD



def _calculate_backoff_minutes() -> int:
	"""Calculate exponential backoff in minutes based on failure streak.

	Streak 3 -> 5 min, 4 -> 10 min, 5 -> 20 min, 6 -> 40 min, 7+ -> 60 min (capped)
	"""
	streak = _get_failure_streak()
	if streak < CIRCUIT_BREAKER_THRESHOLD:
		return 5  # default interval
	backoff_minutes = 5 * (2 ** (streak - CIRCUIT_BREAKER_THRESHOLD))
	return min(backoff_minutes, MAX_BACKOFF_MINUTES)


def _can_attempt_probe() -> bool:
	"""Check if we can attempt an operation given the circuit breaker state.

	If breaker is not tripped: always allow.
	If breaker is tripped:
	  1. Hard ceiling: if breaker has been tripped for >MAX_BREAKER_DURATION_MINUTES,
	     force-reset and allow (prevents indefinite lockdown).
	  2. Otherwise: allow only if backoff period (with jitter) has elapsed since
	     the last probe attempt.

	This replaces the old hard-block with a rate-limited probe so that:
	  - Most jobs skip fast (no wasted retries)
	  - One probe per backoff period actually tries OpenWA
	  - Recovery happens as soon as OpenWA is reachable again
	  - Breaker auto-resets after hard ceiling (prevents deadlock)
	"""
	if not _is_breaker_tripped():
		return True

	# --- Hard ceiling: auto-reset if breaker tripped too long ---
	breaker_tripped_at = frappe.cache().get_value(_breaker_key("tripped"))
	if breaker_tripped_at:
		try:
			elapsed_minutes = (get_datetime() - get_datetime(breaker_tripped_at)).total_seconds() / 60
			if elapsed_minutes > MAX_BREAKER_DURATION_MINUTES:
				_reset_failure_streak()
				frappe.log_error(
					title="OpenWA Circuit Breaker Auto-Reset",
					message=(
						f"Breaker was tripped for {elapsed_minutes:.0f} minutes "
						f"(>{MAX_BREAKER_DURATION_MINUTES} min ceiling). "
						f"Force-resetting to allow recovery probe."
					),
				)
				frappe.cache().set_value(_breaker_key("probe"), str(get_datetime()))
				return True
		except Exception:
			pass

	# --- Rate-limited probe: one attempt per jittered backoff period ---
	last_probe = frappe.cache().get_value(_breaker_key("probe"))
	if not last_probe:
		frappe.cache().set_value(_breaker_key("probe"), str(get_datetime()))
		return True

	backoff = _calculate_backoff_minutes()
	# Add jitter (60%-140% of backoff) to prevent thundering herd when
	# multiple sites/workers retry simultaneously
	jittered_backoff = backoff * random.uniform(0.6, 1.4)
	try:
		elapsed = (get_datetime() - get_datetime(last_probe)).total_seconds() / 60
	except Exception:
		elapsed = jittered_backoff + 1  # can't parse → allow probe

	if elapsed >= jittered_backoff:
		frappe.cache().set_value(_breaker_key("probe"), str(get_datetime()))
		return True

	return False


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
		frappe.cache().set_value(_breaker_key("stale"), True, expires_in_sec=7200)
		return True

	# Session is healthy again — clear the stale flag
	frappe.cache().delete_value(_breaker_key("stale"))
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
					frappe.cache().delete_value(_breaker_key("stale"))
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

	Circuit Breaker: Tracks consecutive failures. After 3 failures, trips
	and enters exponential backoff (5min, 10min, 20min...) to avoid
	hammering a down OpenWA instance. Reset on first success.
	"""
	# --- Circuit Breaker: Probe periodically instead of hard-blocking ---
	# When the breaker is tripped, we still allow ONE health check per
	# backoff period.  This prevents the deadlock where the breaker
	# blocks all recovery attempts indefinitely.
	if _is_breaker_tripped() and not _can_attempt_probe():
		backoff = _calculate_backoff_minutes()
		frappe.logger().info(
			f"OpenWA Circuit Breaker Open — Health check skipped. "
			f"{_get_failure_streak()} consecutive failures, "
			f"backing off for {backoff} minutes."
		)
		return {
			"status": "circuit_open",
			"failure_streak": _get_failure_streak(),
			"backoff_minutes": backoff,
			"checked": now(),
		}
	# If breaker is tripped but probe is due, fall through to actual check

	try:
		settings = frappe.get_cached_doc("OpenWA Settings")
		if not settings.enabled:
			# OpenWA disabled — reset streak and return
			_reset_failure_streak()
			return {"status": "skipped", "reason": "OpenWA not enabled"}

		base_url = settings.base_url.rstrip("/") if settings.base_url else ""
		api_key = settings.get_password("api_key", raise_exception=False) or ""
		session_id = settings.session_id or "default"

		if not base_url or not api_key:
			_increment_failure_streak()
			return {"status": "error", "reason": "Missing base_url or api_key in settings"}

		# 1. Check HTTP endpoint
		try:
			r = requests.get(f"{base_url}/", timeout=10)
			if r.status_code != 200:
				_increment_failure_streak()
				frappe.log_error(
					title="OpenWA Health Check Failed",
					message=f"HTTP {r.status_code} from {base_url}. Gateway may be down — restart manually if persistent.",
				)
				return {"status": "error", "reason": f"HTTP {r.status_code}"}
		except Exception as e:
			_increment_failure_streak()
			frappe.log_error(
				title="OpenWA Health Check Failed",
				message=f"HTTP check failed: {e}. Gateway may be down — restart manually if persistent.",
			)
			return {"status": "error", "reason": f"HTTP check failed: {e}"}

		# 2. Check session status
		try:
			r = requests.get(
				f"{base_url}/api/sessions/{session_id}",
				headers={"X-API-Key": api_key},
				timeout=10
			)

			# --- Session not found on server (404) — auto-create a new one ---
			if r.status_code == 404:
				site_name = frappe.local.site or "default"
				frappe.log_error(
					title="OpenWA Session Lost — Auto-Creating",
					message=(
						f"Session {session_id} was not found on OpenWA server (404). "
						f"Attempting to create a new session for site {site_name}. "
						"QR scan will be needed to re-link WhatsApp."
					),
				)
				try:
					create_r = requests.post(
						f"{base_url}/api/sessions",
						headers={"Content-Type": "application/json", "X-API-Key": api_key},
						json={"name": site_name},
						timeout=10
					)
					if create_r.status_code == 201:
						new_session = create_r.json()
						new_id = new_session.get("id", "")
						if new_id:
							# Save new session ID to Frappe settings
							settings.db_set("session_id", new_id, commit=True)
							# Start the session to get QR_READY
							requests.post(
								f"{base_url}/api/sessions/{new_id}/start",
								headers={"X-API-Key": api_key},
								timeout=10
							)
							frappe.log_error(
								title="OpenWA New Session Created — QR SCAN NEEDED",
								message=(
									f"New session created: {new_session.get('name', '')} (id: {new_id}). "
									f"Status: {new_session.get('status', '')}. "
									f"Updated OpenWA Settings with new session_id. "
									f"YOU MUST SCAN THE QR CODE at {base_url}/ to link WhatsApp. "
									f"Until scanned, the session will stay in disconnected state."
								),
							)
							_increment_failure_streak()
							return {"status": "qr_needed", "new_session_id": new_id, "checked": now()}
					else:
						frappe.log_error(
							title="OpenWA Session Creation Failed",
							message=f"POST /api/sessions returned {create_r.status_code}: {create_r.text[:300]}",
						)
				except Exception as create_e:
					frappe.log_error(
						title="OpenWA Session Creation Error",
						message=f"Exception creating session: {create_e}",
					)
				_increment_failure_streak()
				return {"status": "error", "reason": "Session lost and re-creation failed"}

			if r.status_code != 200:
				_increment_failure_streak()
				frappe.log_error(
					title="OpenWA Health Check Failed",
					message=f"Session API returned {r.status_code}. Restart manually if persistent.",
				)
				return {"status": "error", "reason": f"Session API {r.status_code}"}

			data = r.json()
			status = data.get("status", "")

			# --- Auto-start if session is created/disconnected (credentials may still exist) ---
			if status in ["created", "disconnected"]:
				try:
					start_r = requests.post(
						f"{base_url}/api/sessions/{session_id}/start",
						headers={"X-API-Key": api_key},
						timeout=15
					)
					if start_r.status_code in (200, 201):
						start_data = start_r.json()
						new_status = start_data.get("status", "")
						_increment_failure_streak()
						frappe.logger().info(
							f"OpenWA session restarted: {status} -> {new_status}. "
							f"If {new_status} != 'connected', QR scan may be needed."
						)
						return {"status": "started", "from": status, "to": new_status, "checked": now()}
					else:
						_increment_failure_streak()
						frappe.log_error(
							title="OpenWA Session Start Failed",
							message=f"Start returned {start_r.status_code}: {start_r.text[:200]}",
						)
						return {"status": "error", "reason": f"Start returned {start_r.status_code}"}
				except Exception as start_e:
					_increment_failure_streak()
					frappe.log_error(title="OpenWA Session Start Error", message=str(start_e))
					return {"status": "error", "reason": f"Start error: {start_e}"}

			if status not in ["ready", "connected"]:
				_increment_failure_streak()
				frappe.log_error(
					title="OpenWA Session Unhealthy",
					message=f"Session status: {status}. No auto-recovery available for this state.",
				)
				return {"status": "error", "reason": f"Session status: {status}"}

			# Check for stale session (lastActive > 60 min ago)
			if _session_is_stale(settings, data):
				# Auto-recover: stop/start session to re-establish WebSocket
				result = _restart_session(settings)
				if result.get("status") == "recovered":
					# Retry unsent messages now that session is back
					_retry_unsent()
					_reset_failure_streak()
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

			# Session is healthy — reset failure streak
			_reset_failure_streak()

		except Exception as e:
			_increment_failure_streak()
			frappe.log_error(
				title="OpenWA Health Check Failed",
				message=f"Session check failed: {e}. Restart manually if persistent.",
			)
			return {"status": "error", "reason": f"Session check failed: {e}"}
	# 3. Retry any missed notifications now that session is confirmed healthy
		_retry_unsent()

		# 4. Optional: Check for recent message activity (heartbeat)
		# If no messages for > 1 hour, could indicate silent failure
		# This is optional - uncomment if needed
		# _check_message_activity(base_url, api_key, session_id)

		return {"status": "healthy", "session": status, "checked": now()}

	except Exception as e:
		_increment_failure_streak()
		frappe.log_error(f"OpenWA health check error: {e}", "OpenWA Health Check")
		return {"status": "error", "reason": str(e)}


def _restart_openwa(reason: str):
	"""Restart OpenWA via supervisor (MANUAL USE ONLY).

	⚠️  DANGER: In a shared-gateway setup (kreativ316 + kreativ216 both
	using the same OpenWA instance), restarting the gateway kills ALL
	active sessions.  This function is kept for manual/admin use only.
	Health checks now log errors and let a human decide to restart.

	Use: `bench --site kreativ316 execute kreativ_attendance.attendance.openwa_health._restart_openwa --args '["manual restart"]'`
	"""
	_increment_failure_streak()
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