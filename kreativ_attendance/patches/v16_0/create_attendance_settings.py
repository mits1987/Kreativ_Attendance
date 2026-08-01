"""Create KG Attendance Settings and migrate values out of site_config.json.

Existing site_config keys are read once and copied onto the Single so HR can
see and change them. The keys are left in site_config for now (settings.py still
honours them as an override); remove them once you have confirmed the desk page
shows the right values.
"""
import frappe

CONF_MAP = {
    "kreativ_long_session_hours": ("long_session_hours", float),
    "kreativ_block_on_long_sessions": ("block_on_long_sessions", int),
    "kreativ_block_on_break_punches": ("block_on_break_punches", int),
    "kreativ_monthly_auto_close": ("monthly_auto_close", int),
    "kreativ_auto_create_payroll": ("auto_create_payroll", int),
    "kreativ_auto_submit_payroll": ("auto_submit_payroll", int),
    "kreativ_close_notify_admin": ("notify_admin", int),
}


def execute():
    if not frappe.db.exists("DocType", "KG Attendance Settings"):
        return

    doc = frappe.get_single("KG Attendance Settings")

    # Shadow Mode ON by default: this system must not write payroll until the
    # operator has reconciled an already-paid month and turned it off.
    doc.shadow_mode = 1
    doc.auto_create_payroll = 0
    doc.auto_submit_payroll = 0
    doc.close_day = 2
    doc.close_hour = 10
    doc.ot_rate_base = "Rate of Wages"
    doc.default_standard_hours = 8

    for conf_key, (field, cast) in CONF_MAP.items():
        val = frappe.conf.get(conf_key)
        if val is not None:
            try:
                doc.set(field, cast(val))
            except Exception:
                pass

    # HR Settings custom field, if the older install used one
    try:
        hrs = frappe.db.get_single_value("HR Settings", "kreativ_long_session_hours")
        if hrs:
            doc.long_session_hours = float(hrs)
    except Exception:
        pass

    # Break punches are not in use; do not block payroll on them.
    doc.block_on_break_punches = 0

    doc.flags.ignore_permissions = True
    doc.save(ignore_permissions=True)
    frappe.db.commit()

    print("KG Attendance Settings created. SHADOW MODE IS ON - no payroll "
          "documents will be written until you turn it off.")
