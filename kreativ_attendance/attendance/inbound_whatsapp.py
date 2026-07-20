"""Inbound WhatsApp Webhook Handler — Redirect Bridge.

The canonical implementation is now in kreativ_notification.notification.inbound.
This module re-exports receive_whatsapp_message() so the existing OpenWA webhook URL
(https://testing.kreativgravures.com/api/method/kreativ_attendance.attendance.inbound_whatsapp.receive_whatsapp_message)
continues to work without reconfiguring the gateway.
"""
from kreativ_notification.notification.inbound import receive_whatsapp_message  # noqa: F401
