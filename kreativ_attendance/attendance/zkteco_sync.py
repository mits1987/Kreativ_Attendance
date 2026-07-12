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

        log_type = "IN"
        if str(punch_state) == "1":
            log_type = "OUT"

        # Check if checkin already exists (by employee + time + device)
        existing_checkin = frappe.db.exists(
            "Employee Checkin",
            {"employee": employee, "time": punch_datetime, "device_id": device_id},
        )
        if existing_checkin:
            return "skip"

        # Also check by transaction ID proximity
        if transaction_id:
            existing_by_id = frappe.db.get_value(
                "Employee Checkin",
                {
                    "device_id": device_id,
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
            }
        )
        checkin.insert(ignore_permissions=True)

        return "new"

    except Exception as e:
        frappe.log_error(f"Error creating Employee Checkin: {str(e)}", "ZKTeco Checkin Creation")
        return False


def sync_zkteco_transactions():
    """Main sync — fetches punches from last 7 days and creates Employee Checkins."""
    cfg = frappe.get_single("ZKTeco Config")
    if not cfg.enable_sync:
        return

    try:
        current_time = now_datetime()

        # 7-day lookback from start of today to catch missed punches
        lookback_start = get_datetime(f"{today()} 00:00:00") - timedelta(days=7)

        token_result = get_jwt_token()
        if not token_result or not token_result[0]:
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
        else:
            frappe.logger().info("ZKTeco Sync: No transactions found in lookback window")

    except Exception as e:
        frappe.log_error(f"ZKTeco sync failed: {str(e)}", "ZKTeco Sync Fatal Error")


def scheduled_sync():
    """Scheduled sync that respects the frequency setting in ZKTeco Config."""
    try:
        cfg = frappe.get_single("ZKTeco Config")
        if not cfg.enable_sync:
            return

        sync_seconds = int(cfg.seconds or 300)
        if sync_seconds < 60:
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
