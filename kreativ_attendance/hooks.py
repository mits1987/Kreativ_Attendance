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
		],
		# 02:30 on the 1st of every month — close the previous month
		"30 2 1 * *": [
			"kreativ_attendance.attendance.monthly.monthly_close"
		],
	},
	"daily": [
		"kreativ_attendance.install.validate_scheduled_jobs"
	]
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
	"Employee Shift": [
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
	]
}

# Fixtures
fixtures = [
	{"dt": "Client Script", "filters": [["module", "=", "Kreativ Attendance"]]},
	{"dt": "Workspace", "filters": [["module", "=", "Kreativ Attendance"]]},
]

# Install / Migrate hooks — auto-sync Scheduled Job Types from hooks.py
after_install = "kreativ_attendance.install.after_install"
after_migrate = "kreativ_attendance.install.after_migrate"

# Patches
patches = [
	"kreativ_attendance.patches.v16_0.add_openwa_settings_fields",
	"kreativ_attendance.patches.v16_0.add_punch_state_raw_field"
]

# Update website context
# kreativ_attendance doesn't need its own login_marker - gravures_custom provides the environment banner
# update_website_context = [
#     "kreativ_attendance.login_marker.update_website_context"
# ]