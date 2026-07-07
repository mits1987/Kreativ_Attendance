"""OpenWA Health Check - runs every 5 minutes via scheduler"""
import frappe
import requests
from frappe.utils import get_datetime, now


def check_openwa_session():
	"""
	Scheduled job: runs every 5 minutes to verify OpenWA session is healthy.
	If session is disconnected, restarts the OpenWA service via supervisor.
	"""
	try:
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
			
		except Exception as e:
			return _restart_openwa(f"Session check failed: {e}")
		
		# 3. Optional: Check for recent message activity (heartbeat)
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