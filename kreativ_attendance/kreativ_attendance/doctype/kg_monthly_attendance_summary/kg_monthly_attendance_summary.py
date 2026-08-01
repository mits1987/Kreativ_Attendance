# Copyright (c) 2026, kreativ-gravures
# License: MIT
"""Controller for KG Monthly Attendance Summary.

This is the payroll handoff artifact: one row per employee per month carrying
the three numbers payroll needs (PD, WO, PH) plus the overtime figure.

HR may override wd / wo / ph / standard_hours / total_hours / pd by hand; the
derived fields (required_hours, pay_days, ot_hours, ot_amount) are always
recomputed from whatever is in the editable fields, so a manual correction can
never leave the row internally inconsistent.
"""
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class KGMonthlyAttendanceSummary(Document):
    def validate(self):
        self._set_period()
        self._guard_locked()
        self._recompute()
        self._stamp_review()

    def _set_period(self):
        if self.period_month:
            self.period = f"{int(self.period_year)}-{int(self.period_month):02d}"

    def _guard_locked(self):
        """A Locked row is payroll-final and may only change its notes."""
        if self.is_new() or self.status == "Locked":
            return
        previous = self.get_doc_before_save()
        if previous and previous.status == "Locked":
            frappe.throw(
                "This summary is Locked because payroll has been finalised for "
                f"{self.period}. Set the status back to Reviewed only with a note "
                "explaining why."
            )

    def _recompute(self):
        from kreativ_attendance.attendance.payroll_math import (
            compute_attendance,
            compute_overtime_amount,
            compute_pay_days,
        )

        wd = float(self.wd or 0)
        std = float(self.standard_hours or 0)
        total = float(self.total_hours or 0)

        derived = compute_attendance(total_hours=total, standard_hours=std, wd=wd)
        self.required_hours = derived["required_hours"]

        # PD is editable: only recompute it when HR has not overridden it.
        if self.flags.recompute_pd or self.pd in (None, ""):
            self.pd = derived["pd"]

        self.ot_hours = derived["ot_hours"]
        self.pay_days = compute_pay_days(self.pd, self.wo, self.ph)
        self.ot_amount = compute_overtime_amount(
            self.ot_hours, self.rate_of_wages, wd, std
        )

        if self.pay_days > float(self.days_in_month or 0) > 0:
            frappe.msgprint(
                f"Pay Days ({self.pay_days}) exceeds Days in Month "
                f"({self.days_in_month}). Check WD / WO / PH.",
                indicator="orange", alert=True,
            )

    def _stamp_review(self):
        if self.status in ("Reviewed", "Locked") and not self.reviewed_at:
            self.reviewed_at = now_datetime()
            self.reviewed_by = frappe.session.user
