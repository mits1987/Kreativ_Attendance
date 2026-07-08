"""ZKTeco biometric sync — fetches punches from EasyTime Pro and creates
Employee Checkin records in ERPNext.

Moved here from zkteco_checkins_sync so the logic lives in kreativ_attendance.
The ZKTeco Config doctype stays in zkteco_checkins_sync (already in DB).
"""
import frappe
from frappe import _
from frappe.utils import today, now_datetime, get_datetime
from datetime import timedelta
import requests
import json


def get_jwt_token():
    """Get a fresh JWT access token from EasyTime Pro using stored credentials.
    Returns (token, token_type) tuple where token_type is 'JWT' or 'Token'."""
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
                return access, "JWT"
    except Exception:
        pass

    # Fallback to basic token endpoint
    resp = requests.post(
        f"http://{server_ip}:{server_port}/api-token-auth/",
        json={"username": username, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json().get("token", "")
    return token, "Token"


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
    """Create Employee Checkin record from a ZKTeco transaction."""
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
        if punch_state == "1":
            log_type = "OUT"

        # Check if checkin already exists (by employee + time + device)
        existing_checkin = frappe.db.exists(
            "Employee Checkin",
            {"employee": employee, "time": punch_datetime, "device_id": device_id},
        )
        if existing_checkin:
            return True

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
                return True

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
        frappe.db.commit()

        return True

    except Exception as e:
        frappe.log_error(f"Error creating Employee Checkin: {str(e)}", "ZKTeco Checkin Creation")
        return False


def sync_zkteco_transactions():
    """Main sync — fetches punches from last 7 days and creates Employee Checkins."""
    cfg = frappe.get_single("ZKTeco Config")
    if not cfg.enable_sync:
        frappe.log_error("ZKTeco sync is disabled", "ZKTeco Sync")
        return

    if not cfg.token:
        frappe.log_error("ZKTeco token not configured", "ZKTeco Sync")
        return

    try:
        last_sync = frappe.db.get_single_value("ZKTeco Config", "last_sync") or (
            now_datetime() - timedelta(hours=1)
        )
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
            processed_count = 0
            error_count = 0

            for transaction in transactions:
                try:
                    if create_employee_checkin(transaction):
                        processed_count += 1
                    else:
                        error_count += 1
                except Exception as e:
                    error_count += 1
                    frappe.log_error(
                        f"Error creating checkin for transaction {transaction}: {str(e)}",
                        "ZKTeco Sync Error",
                    )

            total_synced = frappe.db.get_single_value("ZKTeco Config", "total_synced_records") or 0
            frappe.db.set_single_value("ZKTeco Config", "last_sync", current_time)
            frappe.db.set_single_value(
                "ZKTeco Config", "total_synced_records", total_synced + processed_count
            )
            frappe.db.commit()

            frappe.logger().info(
                f"ZKTeco Sync completed: {processed_count} processed, {error_count} errors"
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
