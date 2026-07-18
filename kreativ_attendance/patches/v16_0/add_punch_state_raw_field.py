"""Add punch_state_raw custom field to Employee Checkin.

Stores the original ZKTeco/EasyTime punch_state code (0-5) so that
Break In/Out and Overtime In/Out punches remain visible after sync,
instead of being silently flattened into IN/OUT. quality.py reads this
field to block payroll while unreviewed Break punches exist.
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def execute():
    if frappe.db.has_column("Employee Checkin", "punch_state_raw"):
        return
    create_custom_field(
        "Employee Checkin",
        {
            "fieldname": "punch_state_raw",
            "label": "Punch State (Device)",
            "fieldtype": "Data",
            "insert_after": "log_type",
            "read_only": 1,
            "no_copy": 1,
            "in_standard_filter": 1,
            "description": (
                "Raw ZKTeco punch_state code: 0=Check In, 1=Check Out, "
                "2=Break Out, 3=Break In, 4=Overtime In, 5=Overtime Out. "
                "Codes 2-5 mean the employee pressed the wrong key and are "
                "flagged by the payroll quality gate."
            ),
        },
    )
    frappe.db.commit()
