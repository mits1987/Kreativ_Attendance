# Copyright (c) 2026, Mitesh and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class EmployeeShift(Document):
	def validate(self):
		"""Prevent edits on locked shifts (payroll-finalized)."""
		if self.locked and not self.is_new():
			# Allow the system to update lock fields during salary slip processing,
			# but block manual edits by users.
			changed_fields = set(self.fields.keys()) - {"locked", "lock_period", "modified", "modified_by"}
			if changed_fields and not frappe.flags.in_lock_update:
				frappe.throw(
					f"This shift for {self.employee_name or self.employee} on {self.shift_date} "
					f"is locked (payroll finalized). Unlock the period first to make edits."
				)
