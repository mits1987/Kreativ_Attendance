import frappe


def execute():
	"""Add webhook and auto-reply fields to OpenWA Settings.

	This patch runs when kreativ_attendance is installed/updated.
	Adds inbound webhook configuration and auto-reply bot fields
	to the existing OpenWA Settings DocType (managed by gravures_custom).
	"""

	# Since OpenWA Settings is provided by gravures_custom, we add
	# custom fields to it rather than recreating the DocType.
	_add_webhook_fields_to_existing()


def _add_webhook_fields_to_existing():
	"""Add webhook/auto-reply fields to existing OpenWA Settings via Custom Fields"""
	# Get existing custom fields for OpenWA Settings
	existing_fields = set(frappe.get_all("Custom Field",
		filters={"dt": "OpenWA Settings"},
		pluck="fieldname"
	))

	# Also check DocType fields (base fields from gravures_custom)
	doctype_fields = set()
	if frappe.db.exists("DocType", "OpenWA Settings"):
		meta = frappe.get_meta("OpenWA Settings")
		doctype_fields = set(f.fieldname for f in meta.fields)

	all_existing = existing_fields | doctype_fields

	# List of fields to add
	new_fields = [
		{
			"fieldname": "section_break_webhook",
			"label": "Inbound Webhook Settings",
			"fieldtype": "Section Break",
			"insert_after": "default_country_code"
		},
		{
			"fieldname": "webhook_enabled",
			"label": "Webhook Enabled",
			"fieldtype": "Check",
			"default": "0",
			"description": "Enable processing of incoming WhatsApp messages via OpenWA webhook",
			"insert_after": "section_break_webhook"
		},
		{
			"fieldname": "webhook_secret",
			"label": "Webhook Secret",
			"fieldtype": "Password",
			"description": "Secret key for verifying OpenWA webhook HMAC signatures. Set the same value in OpenWA webhook configuration.",
			"insert_after": "webhook_enabled"
		},
		{
			"fieldname": "column_break_webhook_2",
			"fieldtype": "Column Break",
			"insert_after": "webhook_secret"
		},
		{
			"fieldname": "auto_reply_enabled",
			"label": "Auto-Reply Enabled",
			"fieldtype": "Check",
			"default": "0",
			"description": "Automatically reply to invoice requests from authorised employees",
			"insert_after": "column_break_webhook_2"
		},
		{
			"description": "Employee roles allowed to use the auto-reply bot. Leave empty to allow all active employees.",
			"fieldname": "allowed_roles",
			"fieldtype": "Table MultiSelect",
			"label": "Allowed Roles",
			"options": "Role",
			"insert_after": "auto_reply_enabled"
		},
		{
			"default": "invoice,inv,बिल",
			"description": "Comma-separated keywords that trigger invoice PDF fetch (case-insensitive).",
			"fieldname": "invoice_keywords",
			"fieldtype": "Data",
			"label": "Invoice Keywords",
			"insert_after": "allowed_roles"
		},
	]

	fields_to_add = []
	for field_def in new_fields:
		if field_def["fieldname"] not in all_existing:
			fields_to_add.append(field_def)

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
		print(f"Added {len(fields_to_add)} webhook/auto-reply fields to OpenWA Settings")
	else:
		print("All webhook/auto-reply fields already exist on OpenWA Settings")