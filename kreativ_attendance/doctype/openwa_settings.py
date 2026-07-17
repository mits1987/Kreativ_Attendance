# Copyright (c) 2026, kreativ-gravures
# License: MIT

import frappe
from frappe.model.document import Document
from gravures_custom.overrides.whatsapp_queue import OpenWAClient


class OpenWASettings(Document):
    pass


@frappe.whitelist()
def send_test_message():
    """Send a test message so the setup can be verified from the settings form."""
    frappe.only_for(("System Manager", "HR Manager"))
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not (settings.base_url and settings.chat_id):
        frappe.throw("Set Base URL and Recipient Chat ID first, then Save.")
    client = OpenWAClient()
    result = client.send_text(settings.chat_id, "Test message from ERPNext (consolidated OpenWAClient).")
    if not result.get("success"):
        frappe.throw(result.get("error", "Could not send test message"))
    return "sent"


@frappe.whitelist()
def get_session_status():
    """Fetch current session status from OpenWA gateway (via OpenWAClient)."""
    frappe.only_for(("System Manager", "HR Manager"))
    client = OpenWAClient()
    return client.get_session_status()


@frappe.whitelist()
def get_session_qr():
    """Get QR code image for the session (via OpenWAClient)."""
    frappe.only_for(("System Manager", "HR Manager"))
    client = OpenWAClient()
    return client.get_session_qr()


@frappe.whitelist()
def start_session():
    """Start/Restart the WhatsApp session (via OpenWAClient)."""
    frappe.only_for(("System Manager", "HR Manager"))
    client = OpenWAClient()
    return client.start_session()


@frappe.whitelist()
def stop_session():
    """Stop the WhatsApp session (via OpenWAClient)."""
    frappe.only_for(("System Manager", "HR Manager"))
    client = OpenWAClient()
    return client.stop_session()


@frappe.whitelist()
def create_new_session():
    """Create a brand new WhatsApp session on OpenWA and update settings (via OpenWAClient)."""
    frappe.only_for(("System Manager", "HR Manager"))
    client = OpenWAClient()
    return client.create_session()
