# Copyright (c) 2026, kreativ-gravures
# License: MIT

import frappe
from frappe.model.document import Document


class OpenWASettings(Document):
    pass


@frappe.whitelist()
def send_test_message():
    """Send a test message so the setup can be verified from the settings form."""
    frappe.only_for(("System Manager", "HR Manager"))
    from kreativ_attendance.attendance.whatsapp import send_text
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not (settings.base_url and settings.chat_id):
        frappe.throw("Set Base URL and Recipient Chat ID first, then Save.")
    send_text(settings, "Test message from ERPNext (Kreativ Attendance).",
              raise_on_error=True)
    return "sent"
