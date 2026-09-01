"""ZKTeco biometric sync — fetches punches from EasyTime Pro and creates
Employee Checkin records in ERPNext.

Moved here from zkteco_checkins_sync so the logic lives in kreativ_attendance.
The ZKTeco Config doctype stays in zkteco_checkins_sync (already in DB).
"""
import base64
import json
import time

import frappe
from frappe import _
from frappe.utils import today, now_datetime, get_datetime
from datetime import timedelta
import requests


JWT_CACHE_KEY = "zkteco_jwt_token"
ZKTECO_FAILURE_CACHE_KEY = "zkteco_failure_streak"
MAX_FAILURES = 15
CIRCUIT_RESET_SECONDS = 1800  # auto-reset after 30 minutes


def _check_circuit_breaker():
    """Check if ZKTeco sync circuit breaker is tripped.
    Auto-resets after CIRCUIT_RESET_SECONDS so transient outages don't block sync all day."""
    data = frappe.cache().get_value(ZKTECO_FAILURE_CACHE_KEY) or {}
    if isinstance(data, dict):
        streak = data.get("streak", 0)
        tripped_at = data.get("tripped_at")
    else:
        streak = data
        tripped_at = None
    if streak >= MAX_FAILURES and tripped_at:
        elapsed = time.time() - tripped_at
        if elapsed > CIRCUIT_RESET_SECONDS:
            frappe.logger().info(f"ZKTeco circuit breaker auto-reset after {int(elapsed)}s")
            _reset_failure_streak()
            return False
    return streak >= MAX_FAILURES


def _increment_failure_streak():
    """Increment failure streak in cache."""
    data = frappe.cache().get_value(ZKTECO_FAILURE_CACHE_KEY) or {}
    if isinstance(data, dict):
        streak = data.get("streak", 0)
    else:
        streak = data
    frappe.cache().set_value(ZKTECO_FAILURE_CACHE_KEY, {
        "streak": streak + 1,
        "tripped_at": time.time() if streak + 1 >= MAX_FAILURES else (data.get("tripped_at") if isinstance(data, dict) else None),
    }, expires_in_sec=86400)


def _reset_failure_streak():
    """Reset failure streak on successful sync."""
    frappe.cache().delete_value(ZKTECO_FAILURE_CACHE_KEY)


def _disable_sync():
    """Disable ZKTeco sync in config after circuit breaker trips."""
    frappe.db.set_single_value("ZKTeco Config", "enable_sync", 0)
    frappe.db.commit()
    frappe.log_error(
        f"ZKTeco sync disabled after {MAX_FAILURES} consecutive failures. "
        f"Check connectivity to server {frappe.get_single('ZKTeco Config').server_ip}:{frappe.get_single('ZKTeco Config').server_port} "
        f"and re-enable manually in ZKTeco Config.",
        "ZKTeco Circuit Breaker"
    )

# punch_state -> ERPNext log_type. Direction is preserved even for Break /
# Overtime states so pairing still works after HR confirms the intent.
PUNCH_STATE_TO_LOG_TYPE = {
    "0": "IN",
    "1": "OUT",
    "2": "OUT",   # Break Out — flagged, HR must confirm
    "3": "IN",    # Break In  — flagged, HR must confirm
    "4": "IN",    # Overtime In  — flagged
    "5": "OUT",   # Overtime Out — flagged
}
NON_STANDARD_STATES = {"2", "3", "4", "5"}


def _get_jwt_payload(token: str) -> dict:
    """Decode JWT payload (middle segment) without cryptographic verification.
    Returns dict or empty dict on failure."""
    try:
        segments = token.split(".")
        if len(segments) < 2:
            return {}
        payload = segments[1]
        # Fix base64url padding
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _cache_jwt_token(token: str, token_type: str):
    """Store JWT in Frappe cache with TTL matching the token's exp claim.
    Falls back to 23 hours if exp can't be decoded."""
    ttl = 23 * 3600  # default fallback
    payload = _get_jwt_payload(token)
    exp = payload.get("exp")
    if exp:
        ttl = max(300, int(exp) - int(time.time()) - 120)  # 2 min safety buffer
    frappe.cache().set_value(JWT_CACHE_KEY, {"token": token, "type": token_type}, expires_in_sec=ttl)


def get_jwt_token():
    """Get a JWT access token, cached across sync cycles.

    Returns (token, token_type) tuple where token_type is 'JWT' or 'Token'.
    Checks Frappe cache first; only fetches from EasyTime Pro when the
    cached token has expired."""
    cached = frappe.cache().get_value(JWT_CACHE_KEY)
    if cached:
        return cached["token"], cached["type"]

    cfg = frappe.get_single("ZKTeco Config")
    server_ip = cfg.server_ip
    server_port = cfg.server_port
    username = cfg.username
    password = cfg.get_password("password")

    if not all([server_ip, server_port, username, password]):
        frappe.throw(_("Please configure server IP, port, username, and password in ZKTeco Config."))

    # Try JWT endpoint first (EasyTime Pro / ZKBio)
    try:
        resp = requests.post(
            f"http://{server_ip}:{server_port}/api/jwt-api-token-auth/",
            json={"username": username, "password": password},
            timeout=15,
        )
        if resp.ok:
            data = resp.json()
            access = data.get("access", "")
            if access:
                _cache_jwt_token(access, "JWT")
                return access, "JWT"
    except Exception:
        pass

    # Fallback to basic token endpoint
    try:
        resp = requests.post(
            f"http://{server_ip}:{server_port}/api-token-auth/",
            json={"username": username, "password": password},
            timeout=15,
        )
        resp.raise_for_status()
        token = resp.json().get("token", "")
        _cache_jwt_token(token, "Token")
        return token, "Token"
    except Exception as e:
        frappe.throw(_("Could not authenticate with ZKTeco server: {0}").format(str(e)))


def fetch_zkteco_transactions(cfg, start_time, end_time, token=None, token_type="Token"):
    """Fetch transactions from EasyTime Pro API with pagination."""
    server_ip = cfg.server_ip
    server_port = cfg.server_port
    if not token:
        token, token_type = get_jwt_token()

    base_url = f"http://{server_ip}:{server_port}/iclock/api/transactions/"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"{token_type} {token}",
    }

    params = {
        "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        all_transactions = []
        page = 1
        page_size = 100

        while True:
            params["page"] = page
            params["page_size"] = page_size
            resp = requests.get(base_url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()

            data = resp.json()

            if isinstance(data, dict) and "data" in data:
                records = data["data"]
                all_transactions.extend(records)
                if not data.get("next") or len(records) < page_size:
                    break
                page += 1
            elif isinstance(data, dict) and "results" in data:
                records = data["results"]
                all_transactions.extend(records)
                if len(records) < page_size:
                    break
                page += 1
            elif isinstance(data, list):
                return data
            else:
                break

        return all_transactions

    except Exception as e:
        frappe.log_error(f"Failed to fetch ZKTeco transactions: {str(e)}", "ZKTeco API Error")
        return []


def find_employee_by_code(emp_code):
    """Find employee by employee, user_id, or attendance_device_id."""
    employee = frappe.db.get_value("Employee", {"employee": emp_code}, "name")
    if employee:
        return employee

    employee = frappe.db.get_value("Employee", {"user_id": emp_code}, "name")
    if employee:
        return employee

    if frappe.db.has_column("Employee", "attendance_device_id"):
        employee = frappe.db.get_value("Employee", {"attendance_device_id": emp_code}, "name")
        if employee:
            return employee

    return None


def create_employee_checkin(transaction):
    """Create Employee Checkin record from a ZKTeco transaction.
    Returns 'new' if created, 'skip' if already exists, False on error."""
    try:
        emp_code = transaction.get("emp_code")
        punch_time = transaction.get("punch_time")
        punch_state = transaction.get("punch_state")
        device_id = transaction.get("terminal_alias") or transaction.get("terminal_sn")
        transaction_id = transaction.get("id")

        if not emp_code or not punch_time:
            frappe.log_error(
                f"Missing required fields in transaction: {transaction}",
                "ZKTeco Transaction Error",
            )
            return False

        employee = find_employee_by_code(emp_code)
        if not employee:
            frappe.log_error(
                f"Employee not found for code: {emp_code}", "ZKTeco Employee Mapping"
            )
            return False

        if isinstance(punch_time, str):
            punch_datetime = get_datetime(punch_time)
        else:
            punch_datetime = punch_time

        # --- Punch state mapping (see module docstring) -------------------
        state = str(punch_state) if punch_state is not None else ""
        log_type = PUNCH_STATE_TO_LOG_TYPE.get(state)
        if log_type is None:
            # Unknown state: keep the record (never lose a punch), best-guess
            # OUT only for "1", otherwise IN — and flag loudly.
            log_type = "OUT" if state == "1" else "IN"
            frappe.log_error(
                f"Unknown punch_state '{state}' for {employee} at {punch_datetime}. "
                f"Stored as {log_type}; verify on the device/EasyTime Pro.",
                "ZKTeco Unknown Punch State",
            )

        # Check if checkin already exists (by employee + time + device)
        # Search for device_id both with and without transaction_id suffix
        existing_checkin = frappe.db.exists(
            "Employee Checkin",
            {"employee": employee, "time": punch_datetime, "device_id": device_id},
        )
        if not existing_checkin:
            # Also try with the transaction_id suffix format
            if transaction_id:
                existing_checkin = frappe.db.exists(
                    "Employee Checkin",
                    {"employee": employee, "time": punch_datetime, "device_id": f"{device_id} (ZKTeco-{transaction_id})"},
                )
        if existing_checkin:
            return "skip"

        # Also check by transaction ID proximity (±5 seconds)
        if transaction_id:
            existing_by_id = frappe.db.get_value(
                "Employee Checkin",
                {
                    "device_id": ["like", f"{device_id}%"],
                    "employee": employee,
                    "time": [
                        "between",
                        [punch_datetime - timedelta(seconds=5), punch_datetime + timedelta(seconds=5)],
                    ],
                },
                "name",
            )
            if existing_by_id:
                return "skip"

        checkin = frappe.get_doc(
            {
                "doctype": "Employee Checkin",
                "employee": employee,
                "time": punch_datetime,
                "log_type": log_type,
                "device_id": (
                    f"{device_id} (ZKTeco-{transaction_id})" if transaction_id else device_id or "ZKTeco Device"
                ),
                "skip_auto_attendance": 0,
                # Preserve raw device state before insert — single write
                "punch_state_raw": state if frappe.db.has_column("Employee Checkin", "punch_state_raw") else None,
            }
        )
        checkin.insert(ignore_permissions=True)

        # Non-standard states (Break/Overtime) are payroll-relevant: log so
        # they show up in Error Log even before the quality gate runs.
        if state in NON_STANDARD_STATES:
            labels = {"2": "Break Out", "3": "Break In",
                      "4": "Overtime In", "5": "Overtime Out"}
            frappe.log_error(
                f"{labels.get(state, state)} punch from {employee} at "
                f"{punch_datetime} (stored as {log_type}, checkin {checkin.name}). "
                f"Employee likely pressed the wrong key — confirm before payroll. "
                f"The quality gate will block month close until reviewed.",
                "ZKTeco Break/OT Punch",
            )

        return "new"

    except Exception as e:
        frappe.log_error(f"Error creating Employee Checkin: {str(e)}", "ZKTeco Checkin Creation")
        return False


def sync_zkteco_transactions():
    """Main sync — fetches punches from last_sync (with 6h overlap) and creates Employee Checkins."""
    # Circuit breaker check
    if _check_circuit_breaker():
        frappe.log_error(
            "ZKTeco sync skipped: circuit breaker open (too many consecutive failures)",
            "ZKTeco Circuit Breaker"
        )
        return

    cfg = frappe.get_single("ZKTeco Config")
    if not cfg.enable_sync:
        return

    try:
        current_time = now_datetime()

        # Use last_sync with 6-hour overlap instead of flat 7-day lookback
        last_sync = cfg.last_sync
        if last_sync:
            lookback_start = get_datetime(last_sync) - timedelta(hours=6)
        else:
            # First run: 7 days back
            lookback_start = get_datetime(f"{today()} 00:00:00") - timedelta(days=7)

        token_result = get_jwt_token()
        if not token_result or not token_result[0]:
            _increment_failure_streak()
            frappe.log_error("Could not obtain JWT token for ZKTeco sync", "ZKTeco Sync")
            return
        token, token_type = token_result

        transactions = fetch_zkteco_transactions(cfg, lookback_start, current_time, token, token_type)

        if transactions:
            new_count = 0
            skip_count = 0
            error_count = 0

            for transaction in transactions:
                try:
                    result = create_employee_checkin(transaction)
                    if result == "new":
                        new_count += 1
                    elif result == "skip":
                        skip_count += 1
                    else:
                        error_count += 1
                except Exception as e:
                    error_count += 1
                    frappe.log_error(
                        f"Error creating checkin for transaction {transaction}: {str(e)}",
                        "ZKTeco Sync Error",
                    )

            # Update last sync time and commit everything together
            frappe.db.set_single_value("ZKTeco Config", "last_sync", current_time)
            frappe.db.commit()

            frappe.logger().info(
                f"ZKTeco Sync completed: {new_count} new, {skip_count} skipped, {error_count} errors"
            )
            _reset_failure_streak()
        else:
            frappe.logger().info("ZKTeco Sync: No transactions found in lookback window")
            _reset_failure_streak()

    except Exception as e:
        _increment_failure_streak()
        data = frappe.cache().get_value(ZKTECO_FAILURE_CACHE_KEY) or {}
        streak = data.get("streak", 0) if isinstance(data, dict) else data
        frappe.log_error(
            f"ZKTeco sync failed (streak: {streak}): {str(e)}", "ZKTeco Sync Fatal Error"
        )


def scheduled_sync():
    """Scheduled sync that respects the frequency setting in ZKTeco Config."""
    try:
        cfg = frappe.get_single("ZKTeco Config")
        if not cfg.enable_sync:
            return

        sync_seconds = int(cfg.seconds or 300)
        # Throttle unconditionally using cache-based timestamp
        last_run = frappe.cache().get_value("zkteco_last_sync_run")
        current_time = now_datetime()

        if last_run:
            time_diff = (current_time - get_datetime(last_run)).total_seconds()
            if time_diff < sync_seconds:
                return

        frappe.cache().set_value("zkteco_last_sync_run", current_time)

        sync_zkteco_transactions()

    except Exception as e:
        frappe.log_error(
            f"Scheduled ZKTeco sync failed: {str(e)}", "ZKTeco Scheduled Sync Error"
        )


# ---------------------------------------------------------------------------
# Test Connection override — replaces zkteco_checkins_sync's stale-token version
# ---------------------------------------------------------------------------

def _get_fresh_token():
    """Obtain a fresh auth token from the ZKTeco server (JWT or basic)."""
    cfg = frappe.get_single("ZKTeco Config")
    server_ip = cfg.server_ip
    server_port = cfg.server_port
    username = cfg.username
    password = cfg.get_password("password")

    if not all([server_ip, server_port, username, password]):
        return None, None, _("Missing server credentials in ZKTeco Config.")

    # Try JWT endpoint first
    try:
        resp = requests.post(
            f"http://{server_ip}:{server_port}/api/jwt-api-token-auth/",
            json={"username": username, "password": password},
            timeout=15,
        )
        if resp.ok:
            data = resp.json()
            access = data.get("access", "")
            if access:
                frappe.db.set_single_value("ZKTeco Config", "token", access)
                frappe.db.commit()
                return access, "JWT", None
    except Exception:
        pass

    # Fallback to basic token endpoint
    try:
        resp = requests.post(
            f"http://{server_ip}:{server_port}/api-token-auth/",
            json={"username": username, "password": password},
            timeout=15,
        )
        resp.raise_for_status()
        token = resp.json().get("token", "")
        if token:
            frappe.db.set_single_value("ZKTeco Config", "token", token)
            frappe.db.commit()
            return token, "Token", None
    except Exception as e:
        return None, None, str(e)

    return None, None, _("Could not authenticate with ZKTeco server.")


@frappe.whitelist()
def test_connection():
    """Fresh-auth test connection — replaces zkteco_checkins_sync's stale-token version."""
    cfg = frappe.get_single("ZKTeco Config")
    server_ip = cfg.server_ip
    server_port = cfg.server_port

    token, token_type, auth_error = _get_fresh_token()
    if not token:
        return {"ok": False, "error": auth_error}

    base_url = f"http://{server_ip}:{server_port}/iclock/api/transactions/"
    day = today()
    start_time = f"{day} 00:00:00"
    end_time = f"{day} 23:59:59"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"{token_type} {token}",
    }
    params = {
        "start_time": start_time,
        "end_time": end_time,
    }

    try:
        resp = requests.get(base_url, headers=headers, params=params, timeout=15)

        if resp.ok:
            try:
                data = resp.json()

                formatted_transactions = []
                transaction_count = 0

                if isinstance(data, dict) and "data" in data:
                    transactions = data["data"]
                    transaction_count = data.get("count", len(transactions))
                elif isinstance(data, dict) and "results" in data:
                    transactions = data["results"]
                    transaction_count = len(transactions)
                elif isinstance(data, list):
                    transactions = data
                    transaction_count = len(transactions)
                else:
                    transactions = []

                for transaction in transactions[:5]:
                    try:
                        emp_code = transaction.get("emp_code")
                        punch_time = transaction.get("punch_time")
                        punch_state = transaction.get("punch_state")
                        device_id = transaction.get("terminal_alias") or transaction.get("terminal_sn")
                        first_name = transaction.get("first_name", "")
                        last_name = transaction.get("last_name", "") or ""
                        zkteco_name = f"{first_name} {last_name}".strip()

                        employee_name = zkteco_name
                        erpnext_employee = None
                        if emp_code:
                            employee = frappe.db.get_value(
                                "Employee", {"employee": emp_code}, ["name", "employee_name"]
                            )
                            if not employee:
                                employee = frappe.db.get_value(
                                    "Employee", {"user_id": emp_code}, ["name", "employee_name"]
                                )
                            if employee:
                                erpnext_employee = employee[0] if isinstance(employee, tuple) else employee
                                employee_name = (
                                    f"{employee[1]} (ERPNext)" if isinstance(employee, tuple) else f"{employee} (ERPNext)"
                                )

                        log_type = "OUT" if punch_state == "1" else "IN"

                        formatted_transactions.append({
                            "id": transaction.get("id"),
                            "employee_code": emp_code,
                            "employee_name": employee_name,
                            "erpnext_employee": erpnext_employee,
                            "punch_time": punch_time,
                            "log_type": log_type,
                            "device_id": device_id,
                            "zkteco_name": zkteco_name,
                        })
                    except Exception:
                        continue

                return {
                    "ok": True,
                    "status_code": resp.status_code,
                    "url": resp.url,
                    "total_transactions": transaction_count,
                    "transactions_preview": formatted_transactions,
                    "raw_sample": transactions[:2] if transactions else [],
                    "message": f"Found {transaction_count} transactions for {day}",
                }

            except json.JSONDecodeError as e:
                return {
                    "ok": False,
                    "status_code": resp.status_code,
                    "error": f"Invalid JSON response: {str(e)}",
                    "raw_response": resp.text[:500],
                }
        else:
            return {
                "ok": False,
                "status_code": resp.status_code,
                "url": resp.url,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }

    except requests.RequestException as e:
        return {
            "ok": False,
            "error": f"Connection error: {str(e)}",
        }
