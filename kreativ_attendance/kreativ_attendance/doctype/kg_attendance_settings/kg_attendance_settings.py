# Copyright (c) 2026, kreativ-gravures
# License: MIT
import frappe
from frappe.model.document import Document


class KGAttendanceSettings(Document):
    def validate(self):
        if self.default_standard_hours and float(self.default_standard_hours) <= 0:
            frappe.throw("Default Standard Hours must be greater than zero.")
        if self.close_day is not None and not (1 <= int(self.close_day) <= 28):
            frappe.throw("Close Day must be between 1 and 28.")
        if self.close_hour is not None and not (0 <= int(self.close_hour) <= 23):
            frappe.throw("Close Hour must be between 0 and 23.")
        if self.auto_submit_payroll and self.shadow_mode:
            frappe.msgprint(
                "Shadow Mode is ON, so Payroll Entry will not be created or submitted.",
                indicator="orange", alert=True,
            )
