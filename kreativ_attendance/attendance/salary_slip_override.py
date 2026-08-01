"""Salary Slip subclass: take payment days from the Monthly Attendance Summary.

WHY THIS EXISTS
---------------
Stock HRMS derives `payment_days` by counting Attendance records — one per day,
present or absent. The approved KREATIV GRAVURES rule derives present days from
*hours*:

    PD = min(WD, total_hours / standard_hours)

Those two disagree whenever daily hours are uneven, which with 20-30 hour
flexible sessions is always. An employee who physically appeared on 15 days
totalling 96 hours has 15 Attendance records but a PD of 12 at an 8h standard.

Rather than fabricate absences on days the employee demonstrably worked, this
subclass overrides only the working-days calculation and leaves the whole of the
rest of HRMS payroll — components, formulas, statutory deductions, rounding,
Payroll Entry, journal postings — completely untouched.

Wire-up (hooks.py):
    override_doctype_class = {
        "Salary Slip": "kreativ_attendance.attendance.salary_slip_override.KGSalarySlip"
    }

Salary Structure earning formulas should then read:
    base * payment_days / total_working_days
which evaluates to  base * pay_days / days_in_month  — exactly column T..X of
the approved salary sheet.

SAFETY: if no Reviewed/Locked summary exists for the period, this class does
nothing at all and stock HRMS behaviour applies unchanged. A Draft summary is
deliberately ignored: draft figures have not been reviewed by HR and must never
reach a Salary Slip.
"""
import frappe

try:
    from hrms.payroll.doctype.salary_slip.salary_slip import SalarySlip
except ImportError:  # pragma: no cover - older layouts
    from erpnext.payroll.doctype.salary_slip.salary_slip import SalarySlip


class KGSalarySlip(SalarySlip):
    def get_working_days_details(self, *args, **kwargs):
        # Let HRMS do its normal work first, so every attribute it sets exists.
        super().get_working_days_details(*args, **kwargs)

        summary = self._kg_get_summary()
        if not summary:
            return

        self.total_working_days = float(summary.days_in_month or 0)
        self.payment_days = float(summary.pay_days or 0)
        self.absent_days = max(0.0, float(summary.wd or 0) - float(summary.pd or 0))
        self._kg_summary = summary

    def _kg_get_summary(self):
        """Reviewed or Locked summary matching this slip's period, else None."""
        if not self.employee or not self.start_date:
            return None
        start = frappe.utils.getdate(self.start_date)
        name = frappe.db.get_value(
            "KG Monthly Attendance Summary",
            {
                "employee": self.employee,
                "period_year": start.year,
                "period_month": start.month,
                "status": ["in", ["Reviewed", "Locked"]],
            },
            "name",
        )
        if not name:
            return None
        return frappe.get_doc("KG Monthly Attendance Summary", name)
