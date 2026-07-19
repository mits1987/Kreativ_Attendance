# Copyright (c) 2026, kreativ-gravures
# License: MIT

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, get_datetime


class KGEmployeeShiftLock(Document):
    """Controller for KG Employee Shift Lock doctype.

    Inherits lock implementation from kreativ_attendance.attendance.lock
    """

    def validate(self):
        # UI unlock workflow: HR ticks "Unlocked" + enters a reason on the
        # form. Stamp the audit fields here so has_active_lock() (which keys
        # off unlocked_at) agrees with the checkbox.
        if self.is_unlocked and not self.unlocked_at:
            if not (self.unlock_reason or "").strip():
                frappe.throw("An Unlock Reason is required for the audit trail.")
            self.unlocked_at = now_datetime()
            self.unlocked_by = frappe.session.user
            self._release_on_update = True

    def on_update(self):
        if getattr(self, "_release_on_update", False):
            from kreativ_attendance.attendance.lock import release_shift_flags
            released = release_shift_flags(
                self.employee, self.period_year, self.period_month
            )
            frappe.msgprint(
                f"Period unlocked — {released} KG Employee Attendance Shift record(s) released for editing."
            )