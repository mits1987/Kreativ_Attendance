"""WhatsApp checkin notifications via a self-hosted OpenWA gateway
(https://github.com/rmyndharis/OpenWA).

Configured in the "OpenWA Settings" single doctype. Fired from the
Employee Checkin after_insert hook — new punches only, never edits.
Failures are logged (Error Log), never allowed to break the device sync.
"""
import frappe
import requests
from frappe.utils import format_datetime, get_datetime
from datetime import datetime, timedelta


def _get_shift_hours_for_out(employee: str, checkin_time: datetime) -> str:
    """Calculate shift hours for an OUT punch by finding the matching IN.
    
    Returns formatted string like '8:30' or empty string if not found.
    
    For overnight shifts, the IN may be on the previous day, so we look
    for the last IN before this OUT regardless of date.
    
    First tries to use Employee Shift's worked_hours (most accurate),
    then falls back to calculating from checkins.
    """
    try:
        # First try to get worked_hours from Employee Shift
        checkin_date = checkin_time.date()
        shift = frappe.db.get_value(
            "Employee Shift",
            {"employee": employee, "shift_date": checkin_date},
            "worked_hours",
        )
        if shift:
            return shift
        
        # Also check previous day for overnight shifts
        prev_date = checkin_date - timedelta(days=1)
        shift = frappe.db.get_value(
            "Employee Shift",
            {"employee": employee, "shift_date": prev_date},
            "worked_hours",
        )
        if shift:
            return shift
        
        # Fallback: calculate from checkins
        checkins = frappe.db.get_all(
            "Employee Checkin",
            filters={
                "employee": employee,
                "time": ["<=", checkin_time],
            },
            fields=["name", "time", "log_type"],
            order_by="time asc",
        )
        
        in_time = None
        for checkin in checkins:
            ct = checkin.time
            if isinstance(ct, str):
                ct = get_datetime(ct)
            
            if checkin.log_type == "IN":
                in_time = ct
            elif checkin.log_type == "OUT" and in_time:
                if abs((ct - checkin_time).total_seconds()) < 60:
                    total_seconds = int((ct - in_time).total_seconds())
                    hours = int(total_seconds // 3600)
                    minutes = int((total_seconds % 3600) // 60)
                    return f"{hours}:{minutes:02d}"
                in_time = None
        
        return ""
    except Exception:
        return ""
        return ""


def notify_checkin(checkin_name: str, test_mode: bool = False):
    """Background job: send one WhatsApp message for a new punch.
    
    Args:
        checkin_name: Employee Checkin document name
        test_mode: If True, only sends to admin chat_id (for testing)
    """
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not (settings.enabled and settings.base_url):
        return

    c = frappe.db.get_value(
        "Employee Checkin", checkin_name,
        ["employee", "employee_name", "log_type", "time", "whatsapp_sent"],
        as_dict=True,
    )
    if not c:
        return  # deleted before the job ran

    # Skip if already sent (deduplication)
    if c.whatsapp_sent:
        return

    if settings.notify_on == "IN only" and c.log_type != "IN":
        return
    if settings.notify_on == "OUT only" and c.log_type != "OUT":
        return

    icon = "🟢 IN" if c.log_type == "IN" else "🔴 OUT"
    text = "{0} — {1} at {2}".format(
        icon,
        c.employee_name or c.employee,
        format_datetime(c.time, "dd-MM-yyyy HH:mm"),
    )
    
    # Add shift hours for OUT punches
    if c.log_type == "OUT":
        shift_hours = _get_shift_hours_for_out(c.employee, c.time)
        if shift_hours:
            text = "{0} shift hours: {1}".format(text, shift_hours)

    # Test mode: only send to admin chat_id
    if test_mode or settings.test_mode:
        if settings.test_chat_id:
            _post(settings, "send-text", {"chatId": settings.test_chat_id, "text": text})
        elif settings.chat_id:
            _post(settings, "send-text", {"chatId": settings.chat_id, "text": text})
        return

    # Production mode: send to employee's own mobile first
    mobile = frappe.db.get_value("Employee", c.employee, "cell_number") or ""
    digits = "".join(filter(str.isdigit, mobile))
    if len(digits) >= 10:
        if len(digits) == 10:
            digits = "91" + digits
        # Use @c.us format to match saved WhatsApp contacts
        chat_id = digits + "@c.us"
        if _post(settings, "send-text", {"chatId": chat_id, "text": text}):
            # Mark as sent
            frappe.db.set_value("Employee Checkin", checkin_name, "whatsapp_sent", 1, update_modified=False)
            frappe.db.commit()
    else:
        # Fall back to admin chat_id
        if settings.chat_id:
            _post(settings, "send-text", {"chatId": settings.chat_id, "text": text})


def send_salary_slip(salary_slip: str):
    """Background job: render the Salary Slip PDF and WhatsApp it to the
    employee's own mobile number."""
    import base64
    import re

    settings = frappe.get_cached_doc("OpenWA Settings")
    if not (settings.enabled and settings.send_salary_slips and settings.base_url):
        return

    slip = frappe.get_doc("Salary Slip", salary_slip)
    mobile = frappe.db.get_value("Employee", slip.employee, "cell_number") or ""
    digits = re.sub(r"\D", "", mobile)
    if not digits:
        frappe.log_error(
            title="Salary slip WhatsApp skipped: no mobile number",
            message=f"{slip.employee} ({slip.employee_name}) has no cell_number on the Employee record.",
        )
        return
    cc = re.sub(r"\D", "", settings.default_country_code or "")
    if cc and not digits.startswith(cc) and len(digits) <= 10:
        digits = cc + digits

    pdf = frappe.get_print(
        "Salary Slip", slip.name,
        print_format=settings.salary_slip_print_format or None,
        as_pdf=True,
    )
    period = frappe.utils.format_date(slip.start_date, "MMMM yyyy")
    _post(settings, "send-document", {
        "chatId": f"{digits}@c.us",
        "base64": base64.b64encode(pdf).decode(),
        "mimetype": "application/pdf",
        "filename": f"Salary Slip {period} - {slip.employee_name}.pdf",
        "caption": f"Salary Slip — {period}",
    })


def _post(settings, endpoint: str, payload: dict, raise_on_error: bool = False):
    url = "{0}/api/sessions/{1}/messages/{2}".format(
        settings.base_url.rstrip("/"),
        settings.session_id or "default",
        endpoint,
    )
    api_key = settings.get_password("api_key", raise_exception=False) or ""
    try:
        r = requests.post(
            url,
            json=payload,
            headers={"X-API-Key": api_key},
            timeout=30,
        )
        r.raise_for_status()
        return True
    except Exception:
        frappe.log_error(
            title="OpenWA WhatsApp send failed",
            message=frappe.get_traceback(),
        )
        if raise_on_error:
            frappe.throw(
                "Could not send via OpenWA — check Base URL / API Key / session. "
                "Details are in the Error Log."
            )
        return False
