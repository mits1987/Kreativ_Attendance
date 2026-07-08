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

# Fixtures
fixtures = [
    {"dt": "Client Script", "filters": [["module", "=", "Kreativ Attendance"]]},
    {"dt": "Workspace", "filters": [["module", "=", "Kreativ Attendance"]]},
]

# Update website context
update_website_context = [
    "kreativ_attendance.login_marker.update_website_context"
]
