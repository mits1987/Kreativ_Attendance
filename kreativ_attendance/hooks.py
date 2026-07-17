app_name = "kreativ_attendance"
app_title = "Kreativ Attendance"
app_publisher = "Mitesh"
app_description = "Attendance and HR management for Kreativ Gravures"
app_email = "info@kreativ.com"
app_license = "MIT"

# DocType list_js
doctype_list_js = {
    "Employee Shift": "public/js/employee_shift_list.js"
}

# Document Events
doc_events = {
    "Employee Checkin": {
        "on_change": "kreativ_attendance.attendance.hooks.on_checkin_updated",
        "on_trash": "kreativ_attendance.attendance.hooks.on_checkin_trashed",
        "after_insert": "kreativ_attendance.attendance.hooks.on_checkin_created"
    },
    "Salary Slip": {
        "on_submit": [
            "kreativ_attendance.attendance.hooks.on_salary_slip_submit",
            "kreativ_attendance.attendance.hooks.on_salary_slip_whatsapp"
        ]
    }
}

# Scheduled Tasks
scheduler_events = {
    "cron": {
        "*/5 * * * *": [
            "kreativ_attendance.attendance.openwa_health.check_openwa_session",
            "kreativ_attendance.attendance.zkteco_sync.scheduled_sync",
        ],
        "*/10 * * * *": [
            "kreativ_attendance.attendance.whatsapp.retry_missed_notifications"
        ]
    }
}

# Custom Fields to create on target doctypes
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
            "description": "0=not sent, 1=sent, 2=failed (retry), 3=invalid number (stop)",
        },
        {
            "fieldname": "whatsapp_retry_count",
            "label": "WhatsApp Retry Count",
            "fieldtype": "Int",
            "insert_after": "whatsapp_sent",
            "read_only": 1,
            "no_copy": 1,
            "default": 0,
            "description": "Number of times WhatsApp send has been attempted",
        },
    ]
}

# Fixtures
fixtures = [
    {"dt": "Client Script", "filters": [["module", "=", "Kreativ Attendance"]]},
    {"dt": "Workspace", "filters": [["module", "=", "Kreativ Attendance"]]},
]

# Patches
patches = [
    "kreativ_attendance.patches.v16_0.add_openwa_settings_fields"
]

# Update website context
# kreativ_attendance doesn't need its own login_marker - gravures_custom provides the environment banner
# update_website_context = [
#     "kreativ_attendance.login_marker.update_website_context"
# ]
