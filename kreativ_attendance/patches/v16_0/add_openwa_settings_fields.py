import frappe


def execute():
	"""Add kreativ_attendance specific fields to OpenWA Settings if not present.

	This patch runs when kreativ_attendance is installed. It either:
	1. Creates OpenWA Settings from scratch (if gravures_custom not installed)
	2. Adds only kreativ_attendance specific fields to existing OpenWA Settings

	If OpenWA Settings already exists (from gravures_custom), it only adds
	the attendance-specific fields without overwriting the base fields.
	"""

	# Check if OpenWA Settings exists
	if not frappe.db.exists("DocType", "OpenWA Settings"):
		# Create full OpenWA Settings with all fields (base + attendance)
		_create_full_openwa_settings()
		print("Created OpenWA Settings with all fields (base + attendance)")
		return

	# OpenWA Settings exists - add only attendance-specific fields via Custom Fields
	_add_attendance_fields_to_existing()


def _create_full_openwa_settings():
	"""Create OpenWA Settings DocType with ALL fields (base + attendance)"""
	doc = frappe.get_doc({
		"doctype": "DocType",
		"name": "OpenWA Settings",
		"module": "Kreativ Attendance",
		"issingle": 1,
		"field_order": [
			"enabled",
			"base_url",
			"api_key",
			"column_break_1",
			"session_id",
			"chat_id",
			"default_country_code",
			"notify_on",
			"section_break_session",
			"session_status",
			"session_qr",
			"session_phone",
			"session_pushname",
			"salary_section",
			"send_salary_slips",
			"salary_slip_print_format",
			"test_section",
			"test_mode",
			"test_chat_id"
		],
		"fields": [
			{
				"default": "0",
				"fieldname": "enabled",
				"fieldtype": "Check",
				"label": "Enabled"
			},
			{
				"description": "e.g. http://localhost:2785 (where the OpenWA Docker container runs)",
				"fieldname": "base_url",
				"fieldtype": "Data",
				"label": "OpenWA Base URL"
			},
			{
				"description": "X-API-Key created in the OpenWA dashboard",
				"fieldname": "api_key",
				"fieldtype": "Password",
				"label": "API Key"
			},
			{
				"fieldname": "column_break_1",
				"fieldtype": "Column Break"
			},
			{
				"default": "default",
				"description": "The OpenWA session (WhatsApp account) to send from",
				"fieldname": "session_id",
				"fieldtype": "Data",
				"label": "Session ID"
			},
			{
				"description": "Number: 9779812345678@c.us — or a group: 1203630XXXXXXX@g.us (get group IDs from the OpenWA dashboard)",
				"fieldname": "chat_id",
				"fieldtype": "Data",
				"label": "Recipient Chat ID"
			},
			{
				"default": "91",
				"description": "Digits only, e.g. 91. Prefixed to employee mobile numbers that don't already include a country code.",
				"fieldname": "default_country_code",
				"fieldtype": "Data",
				"label": "Default Country Code"
			},
			{
				"default": "IN and OUT",
				"description": "Which checkin events should trigger WhatsApp notifications",
				"fieldname": "notify_on",
				"fieldtype": "Select",
				"label": "Notify On",
				"options": "IN and OUT\nIN only\nOUT only"
			},
			{
				"fieldname": "section_break_session",
				"fieldtype": "Section Break",
				"label": "Session Status & QR Code"
			},
			{
				"default": "unknown",
				"description": "Current connection status of the WhatsApp session",
				"fieldname": "session_status",
				"fieldtype": "Select",
				"label": "Session Status",
				"options": "ready\nconnected\nqr_ready\ndisconnected\ncreated\nunknown\nerror",
				"read_only": 1
			},
			{
				"fieldname": "session_qr",
				"fieldtype": "HTML",
				"label": "QR Code (scan with WhatsApp on phone)",
				"options": "<div style='padding:20px;text-align:center;color:#888;'><p style='font-size:14px;'>📱 Configure settings on the left, then click <b>Refresh Status & QR</b> at the top.</p><p style='font-size:12px;'>The QR code will appear here automatically when the session is ready to pair.</p></div>",
				"read_only": 1
			},
			{
				"fieldname": "session_phone",
				"fieldtype": "Data",
				"label": "Linked Phone",
				"read_only": 1
			},
			{
				"fieldname": "session_pushname",
				"fieldtype": "Data",
				"label": "WhatsApp Name",
				"read_only": 1
			},
			{
				"fieldname": "salary_section",
				"fieldtype": "Section Break",
				"label": "Salary Slip Delivery"
			},
			{
				"default": "0",
				"description": "Sends to the employee's Mobile (cell_number on the Employee record). Verify numbers first — salary data is sensitive.",
				"fieldname": "send_salary_slips",
				"fieldtype": "Check",
				"label": "Send Salary Slip PDF to employee on submit"
			},
			{
				"description": "Leave blank for the default format",
				"fieldname": "salary_slip_print_format",
				"fieldtype": "Link",
				"label": "Salary Slip Print Format",
				"options": "Print Format"
			},
			{
				"fieldname": "test_section",
				"fieldtype": "Section Break",
				"label": "Testing"
			},
			{
				"default": "0",
				"description": "When enabled, all WhatsApp notifications go to the Test Chat ID below instead of employee phones. Use for testing without disturbing employees.",
				"fieldname": "test_mode",
				"fieldtype": "Check",
				"label": "Test Mode"
			},
			{
				"depends_on": "eval:doc.test_mode",
				"description": "Chat ID to receive test notifications (e.g., 919106526195@c.us for Mitesh). Only used when Test Mode is enabled.",
				"fieldname": "test_chat_id",
				"fieldtype": "Data",
				"label": "Test Chat ID"
			}
		],
		"permissions": [
			{"role": "System Manager", "create": 1, "read": 1, "write": 1},
			{"role": "HR Manager", "create": 1, "read": 1, "write": 1}
		]
	})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()


def _add_attendance_fields_to_existing():
	"""Add kreativ_attendance specific fields to existing OpenWA Settings via Custom Fields"""
	# Get existing custom fields for OpenWA Settings
	existing_fields = set(frappe.get_all("Custom Field",
		filters={"dt": "OpenWA Settings"},
		pluck="fieldname"
	))

	# Also check DocType fields (base fields)
	doctype_fields = set()
	if frappe.db.exists("DocType", "OpenWA Settings"):
		meta = frappe.get_meta("OpenWA Settings")
		doctype_fields = set(f.fieldname for f in meta.fields)

	all_existing = existing_fields | doctype_fields

	fields_to_add = []

	# Attendance notification fields
	if "notify_on" not in all_existing:
		fields_to_add.append({
			"fieldname": "notify_on",
			"label": "Notify On",
			"fieldtype": "Select",
			"options": "IN and OUT\nIN only\nOUT only",
			"default": "IN and OUT",
			"description": "Which checkin events should trigger WhatsApp notifications",
			"insert_after": "default_country_code"
		})

	# Salary slip delivery fields
	if "salary_section" not in all_existing:
		fields_to_add.append({
			"fieldname": "salary_section",
			"label": "Salary Slip Delivery",
			"fieldtype": "Section Break",
			"insert_after": "notify_on"
		})

	if "send_salary_slips" not in all_existing:
		fields_to_add.append({
			"fieldname": "send_salary_slips",
			"label": "Send Salary Slip PDF to employee on submit",
			"fieldtype": "Check",
			"default": "0",
			"description": "Sends to the employee's Mobile (cell_number on the Employee record). Verify numbers first — salary data is sensitive.",
			"insert_after": "salary_section"
		})

	if "salary_slip_print_format" not in all_existing:
		fields_to_add.append({
			"fieldname": "salary_slip_print_format",
			"label": "Salary Slip Print Format",
			"fieldtype": "Link",
			"options": "Print Format",
			"description": "Leave blank for the default format",
			"insert_after": "send_salary_slips"
		})

	# Testing section
	if "test_section" not in all_existing:
		fields_to_add.append({
			"fieldname": "test_section",
			"label": "Testing",
			"fieldtype": "Section Break",
			"insert_after": "salary_slip_print_format"
		})

	if "test_mode" not in all_existing:
		fields_to_add.append({
			"fieldname": "test_mode",
			"label": "Test Mode",
			"fieldtype": "Check",
			"default": "0",
			"description": "When enabled, all WhatsApp notifications go to the Test Chat ID below instead of employee phones. Use for testing without disturbing employees.",
			"insert_after": "test_section"
		})

	if "test_chat_id" not in all_existing:
		fields_to_add.append({
			"fieldname": "test_chat_id",
			"label": "Test Chat ID",
			"fieldtype": "Data",
			"description": "Chat ID to receive test notifications (e.g., 919106526195@c.us for Mitesh). Only used when Test Mode is enabled.",
			"depends_on": "eval:doc.test_mode",
			"insert_after": "test_mode"
		})

	if fields_to_add:
		# Add Custom Fields to the OpenWA Settings DocType
		for field_def in fields_to_add:
			custom_field = frappe.get_doc({
				"doctype": "Custom Field",
				"dt": "OpenWA Settings",
				"fieldname": field_def["fieldname"],
				"label": field_def["label"],
				"fieldtype": field_def["fieldtype"],
				"options": field_def.get("options", ""),
				"default": field_def.get("default", ""),
				"description": field_def.get("description", ""),
				"depends_on": field_def.get("depends_on", ""),
				"insert_after": field_def.get("insert_after", ""),
				"module": "Kreativ Attendance"
			})
			custom_field.insert(ignore_permissions=True)

		frappe.db.commit()
		frappe.clear_cache(doctype="OpenWA Settings")
		print(f"Added {len(fields_to_add)} kreativ_attendance fields to OpenWA Settings")
	else:
		print("All kreativ_attendance fields already exist on OpenWA Settings")