# ---------------------------------------------------------------------------
# MERGE NOTE: reconstructed during the WhatsApp-stack consolidation.
# Diff against your current hooks.py before replacing — keep any extra
# sections (workspaces, extra fixtures) your version has. Changes marked
# with  # CHANGED / # REMOVED comments.
# ---------------------------------------------------------------------------

app_name = "kreativ_attendance"
app_title = "Kreativ Attendance"
app_publisher = "Mitesh"
app_description = "Attendance and HR management for Kreativ Gravures"
app_email = "info@kreativ.com"
app_license = "MIT"

override_doctype_class = {
    "Salary Slip": "kreativ_attendance.attendance.salary_slip_override.KGSalarySlip"
}

# Override zkteco_checkins_sync's test_connection which uses a stale stored token.
# Our version authenticates fresh each time (JWT or basic token fallback).
override_whitelisted_methods = {
    "zkteco_checkins_sync.zkteco_checkin_sync.doctype.zkteco_config.zkteco_config.test_connection":
        "kreativ_attendance.attendance.zkteco_sync.test_connection",
}

# DocType list_js
doctype_list_js = {
    "KG Employee Attendance Shift": "public/js/kg_employee_attendance_shift_list.js"
}

# DocType form_js
doctype_form_js = {
    "KG Employee Attendance Shift": "public/js/kg_employee_attendance_shift_form.js"
}

# ---------------------------------------------------------------------------
# Document Events — ATTENDANCE CONCERNS ONLY.
#
# CHANGED: this app no longer wires any kreativ_notification.* handlers.
# The old wiring pointed at "kreativ_notification.notification.hooks.*",
# a module path that didn't exist in that package — those doc events were
# raising ImportError on every checkin save. WhatsApp notification for
# Employee Checkin (after_insert) and Salary Slip (on_submit) is now wired
# inside kreativ_notification/hooks.py itself. Frappe merges doc_events
# across installed apps, so both recalc (here) and notify (there) fire.
# ---------------------------------------------------------------------------
doc_events = {
    "Employee Checkin": {
        # on_change fires on create AND edit -> covers both for recalc.
        "on_change": "kreativ_attendance.attendance.hooks.on_checkin_updated",
        "on_trash": "kreativ_attendance.attendance.hooks.on_checkin_trashed",
    },
    "Salary Slip": {
        # Payroll lock only. WhatsApp delivery: kreativ_notification.
        "on_submit": "kreativ_attendance.attendance.hooks.on_salary_slip_submit",
    },
}

# ---------------------------------------------------------------------------
# Scheduled Tasks — ATTENDANCE CONCERNS ONLY.
#
# REMOVED: check_openwa_session, check_inbound_webhook_health and
# retry_missed_notifications — all owned by kreativ_notification/hooks.py
# now. Having them here meant they'd silently stop if kreativ_attendance
# were ever uninstalled, and ran double if both apps listed them.
# ---------------------------------------------------------------------------
scheduler_events = {
    "cron": {
        "*/5 * * * *": [
            "kreativ_attendance.attendance.zkteco_sync.scheduled_sync",
        ],
    },
    "hourly": [
        "kreativ_attendance.attendance.monthly.scheduled_monthly_close",
    ],
    "daily": [
        "kreativ_attendance.install.validate_scheduled_jobs",
    ],
}

# ---------------------------------------------------------------------------
# Custom Fields to create on target doctypes
#
# NOTE: whatsapp_retry_count is retired (dispatcher owns retries) but the
# field is intentionally KEPT so historical data survives and no patch is
# needed. whatsapp_sent semantics simplified: 0/None=pending, 1=dispatched,
# 3=invalid number (2 is no longer written).
# ---------------------------------------------------------------------------
custom_fields = {
    "Employee Checkin": [
        {
            "fieldname": "whatsapp_sent",
            "label": "WhatsApp Sent",
            "fieldtype": "Int",
            "insert_after": "log_type",
            "read_only": 1,
            "no_copy": 1,
            "default": 0,
            "description": "0=pending, 1=handed to dispatcher (see WhatsApp Send Log), 3=invalid number (stop)",
        },
        {
            "fieldname": "whatsapp_retry_count",
            "label": "WhatsApp Retry Count",
            "fieldtype": "Int",
            "insert_after": "whatsapp_sent",
            "read_only": 1,
            "no_copy": 1,
            "default": 0,
            "description": "DEPRECATED — transport retries now tracked in WhatsApp Send Log",
        },
        {
            "fieldname": "punch_state_raw",
            "label": "Punch State Raw",
            "fieldtype": "Data",
            "insert_after": "whatsapp_retry_count",
            "read_only": 1,
            "no_copy": 1,
            "description": "Raw ZKTeco punch_state code (0=IN, 1=OUT, 2=Break Out, 3=Break In, 4=OT In, 5=OT Out)",
        },
    ],
    "KG Employee Attendance Shift": [
        {
            "fieldname": "locked",
            "label": "Locked",
            "fieldtype": "Check",
            "insert_after": "status",
            "read_only": 1,
            "default": 0,
            "description": "Set when the employee-month is payroll-locked (no further edits/pairing)",
        },
        {
            "fieldname": "lock_period",
            "label": "Lock Period",
            "fieldtype": "Data",
            "insert_after": "locked",
            "read_only": 1,
            "no_copy": 1,
            "description": "Period identifier (YYYY-MM) this shift row belongs to for the lock",
        },
    ],
}

# Fixtures
fixtures = [
    {"dt": "Client Script", "filters": [["module", "=", "Kreativ Attendance"]]},
    {"dt": "Custom Field", "filters": [["dt", "=", "HR Settings"], ["fieldname", "=", "kreativ_long_session_hours"]]},
    {"dt": "Report", "filters": [["module", "=", "Kreativ Attendance"]]},
]

# Install / Migrate hooks — auto-sync Scheduled Job Types from hooks.py
after_install = "kreativ_attendance.install.after_install"
after_migrate = "kreativ_attendance.install.after_migrate"

# Patches
patches = [
    "kreativ_attendance.patches.v16_0.add_openwa_settings_fields",
    "kreativ_attendance.patches.v16_0.add_punch_state_raw_field",
    "kreativ_attendance.patches.v16_0.add_openwa_webhook_fields",
    "kreativ_attendance.patches.v16_0.create_attendance_settings",
    "kreativ_attendance.patches.v16_0.retire_employee_standard_hours",
    "kreativ_attendance.patches.v16_0.setup_payroll_structure",
]