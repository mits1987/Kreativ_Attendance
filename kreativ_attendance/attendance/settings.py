"""Single accessor for every Kreativ Attendance tunable.

Replaces the previous mix of `frappe.conf.get(...)` keys in site_config.json and
a custom field on HR Settings. Values now live on the KG Attendance Settings
Single, visible and editable by HR.

site_config.json keys are still honoured as an override so an emergency change
can be made without the desk (and so the migration patch has something to read),
but the settings doctype is authoritative.
"""
import frappe

_CONF_OVERRIDES = {
    "shadow_mode": "kreativ_shadow_mode",
    "default_standard_hours": "kreativ_default_standard_hours",
    "long_session_hours": "kreativ_long_session_hours",
    "block_on_long_sessions": "kreativ_block_on_long_sessions",
    "block_on_break_punches": "kreativ_block_on_break_punches",
    "monthly_auto_close": "kreativ_monthly_auto_close",
    "auto_create_payroll": "kreativ_auto_create_payroll",
    "auto_submit_payroll": "kreativ_auto_submit_payroll",
    "notify_admin": "kreativ_close_notify_admin",
}

_DEFAULTS = {
    "shadow_mode": 1,
    "default_standard_hours": 8.0,
    "ot_rate_base": "Rate of Wages",
    "long_session_hours": 13.0,
    "block_on_long_sessions": 1,
    "block_on_break_punches": 0,
    "monthly_auto_close": 1,
    "close_day": 2,
    "close_hour": 10,
    "auto_create_payroll": 0,
    "auto_submit_payroll": 0,
    "notify_admin": 1,
    "admin_whatsapp": "",
}


def get(key, default=None):
    """Return one setting. site_config override wins, then the Single, then the default."""
    conf_key = _CONF_OVERRIDES.get(key)
    if conf_key is not None:
        val = frappe.conf.get(conf_key)
        if val is not None:
            return val
    try:
        val = frappe.db.get_single_value("KG Attendance Settings", key)
    except Exception:
        val = None
    if val in (None, ""):
        return _DEFAULTS.get(key, default)
    return val


def is_shadow_mode() -> bool:
    """True while the old payroll system is still authoritative.

    In shadow mode the monthly close recalculates, runs the quality gate, builds
    the Monthly Attendance Summary and sends alerts, but writes NOTHING to
    Attendance, Additional Salary or Payroll Entry.
    """
    return bool(int(get("shadow_mode", 1) or 0))


def default_standard_hours() -> float:
    return float(get("default_standard_hours", 8.0) or 8.0)


def long_session_seconds() -> int:
    return int(float(get("long_session_hours", 13.0) or 13.0) * 3600)


def ot_rate_base() -> str:
    return get("ot_rate_base", "Rate of Wages") or "Rate of Wages"


def set_state(period: str, status: str):
    """Record the outcome of the last monthly close."""
    from frappe.utils import now_datetime
    try:
        doc = frappe.get_single("KG Attendance Settings")
        doc.last_closed_period = period
        doc.last_close_status = status
        doc.last_close_at = now_datetime()
        doc.flags.ignore_permissions = True
        doc.flags.ignore_validate = True
        doc.save(ignore_permissions=True)
    except Exception:
        frappe.log_error(title="KG Attendance: could not record close state",
                         message=frappe.get_traceback())


def already_closed(period: str) -> bool:
    return (get("last_closed_period") or "") == period and \
           (get("last_close_status") or "") == "ok"
