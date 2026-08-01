"""Bridge from Kreativ Attendance into standard HRMS doctypes.

REPLACES the previous version, which created one submitted Attendance per shift
date plus an "Overtime" Additional Salary priced from Employee.overtime_rate_per_hour.
That model did not match the approved payroll rule and has been removed:

  * overtime is no longer per-shift. Per-shift OT (`max(0, worked - standard)`)
    discards short days while keeping long ones, which systematically OVERPAYS
    when daily hours are uneven. OT is now a monthly residual:
    `max(0, total_hours - WD * standard_hours)`.
  * Employee.overtime_rate_per_hour is gone. The rate is derived:
    `rate_of_wages / (WD * standard_hours)`, so it cannot drift out of step
    with a salary revision.

Two things are written here, both idempotent and both gated on Shadow Mode:

1. Attendance — one submitted record per employee per worked date. These exist
   for compliance and reporting. They do NOT drive pay: payment days come from
   the Monthly Attendance Summary via salary_slip_override.KGSalarySlip.

2. Additional Salary — one "Overtime" row per employee per month, amount taken
   straight from the reviewed summary.

The Production Bonus (the "Incentive" column of the salary sheet) is NOT created
here. It is a discretionary monthly figure decided by management and must be
entered by HR as its own Additional Salary row.
"""
import frappe
from frappe.utils import getdate

from kreativ_attendance.attendance import settings as kg_settings
from kreativ_attendance.attendance.calendar_util import period_bounds
from kreativ_attendance.attendance.summary import SUMMARY_DOCTYPE

OT_COMPONENT = "Overtime"
WORKED_STATUSES = ("Paired", "Manual")


class ShadowModeError(Exception):
    """Raised when a payroll write is attempted while Shadow Mode is on."""


def sync_month_to_hrms(year: int, month: int, employee: str = None) -> dict:
    """Write Attendance + Overtime for a period. Refuses in Shadow Mode."""
    year, month = int(year), int(month)
    period = f"{year}-{month:02d}"

    if kg_settings.is_shadow_mode():
        return {
            "period": period,
            "skipped": True,
            "message": (
                "Shadow Mode is ON — no Attendance, Additional Salary or Payroll "
                "Entry was created. Turn Shadow Mode off in KG Attendance Settings "
                "once this system is the payroll system of record."
            ),
        }

    summaries = _reviewed_summaries(year, month, employee)
    if not summaries:
        return {
            "period": period,
            "skipped": True,
            "message": (
                "No Reviewed summaries for this period. HR must review the KG "
                "Monthly Attendance Summary rows before payroll can be written."
            ),
        }

    att = _create_attendance(year, month, employee)
    ot = _create_overtime(summaries, year, month)

    return {
        "period": period,
        "skipped": False,
        "employees": len(summaries),
        "attendance_created": att["created"],
        "attendance_skipped_existing": att["skipped"],
        "attendance_errors": att["errors"],
        "overtime_created": ot["created"],
        "overtime_skipped_existing": ot["skipped"],
        "overtime_zero": ot["zero"],
        "overtime_errors": ot["errors"],
    }


def _reviewed_summaries(year, month, employee=None):
    filters = {
        "period_year": year,
        "period_month": month,
        "status": ["in", ["Reviewed", "Locked"]],
    }
    if employee:
        filters["employee"] = employee
    return frappe.get_all(
        SUMMARY_DOCTYPE, filters=filters,
        fields=["name", "employee", "ot_hours", "ot_amount", "pd", "pay_days"],
        order_by="employee",
    )


def _create_attendance(year, month, employee=None) -> dict:
    """One submitted Attendance per employee per worked date.

    Several IN/OUT pairs on the same date collapse into one record with summed
    hours, earliest IN and latest OUT. Status is Present: these records are a
    factual log of attendance, not the basis of payment.
    """
    start, end = period_bounds(year, month)
    filters = [
        ["shift_date", ">=", start],
        ["shift_date", "<", end],
        ["status", "in", list(WORKED_STATUSES)],
    ]
    if employee:
        filters.append(["employee", "=", employee])

    shifts = frappe.get_all(
        "KG Employee Attendance Shift", filters=filters,
        fields=["employee", "shift_date", "check_in", "check_out", "worked_seconds"],
        order_by="employee, check_in",
    )

    by_day = {}
    for s in shifts:
        d = by_day.setdefault((s.employee, s.shift_date), {
            "worked": 0, "in": s.check_in, "out": s.check_out,
        })
        d["worked"] += s.worked_seconds or 0
        if s.check_in and (not d["in"] or s.check_in < d["in"]):
            d["in"] = s.check_in
        if s.check_out and (not d["out"] or s.check_out > d["out"]):
            d["out"] = s.check_out

    created, skipped, errors = 0, 0, []
    for (emp, day), d in sorted(by_day.items()):
        if frappe.db.exists("Attendance", {
            "employee": emp, "attendance_date": day, "docstatus": ["<", 2],
        }):
            skipped += 1
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Attendance",
                "employee": emp,
                "attendance_date": day,
                "status": "Present",
                "working_hours": round(d["worked"] / 3600.0, 2),
                "in_time": d["in"],
                "out_time": d["out"],
            })
            doc.flags.ignore_validate = False
            doc.insert(ignore_permissions=True)
            doc.submit()
            created += 1
        except Exception as e:
            errors.append(f"{emp} {day}: {e}")
    return {"created": created, "skipped": skipped, "errors": errors}


def _create_overtime(summaries, year, month) -> dict:
    """One submitted Overtime Additional Salary per employee per month."""
    _, end = period_bounds(year, month)
    payroll_date = frappe.utils.add_days(end, -1)

    if not frappe.db.exists("Salary Component", OT_COMPONENT):
        return {
            "created": 0, "skipped": 0, "zero": [],
            "errors": [
                f"Salary Component '{OT_COMPONENT}' does not exist. Create it once "
                "as described in SALARY_STRUCTURE.md (type Earning, do not include "
                "in the PF base, do include in the ESI base). No overtime was posted."
            ],
        }

    created, skipped, zero, errors = 0, 0, [], []
    for s in summaries:
        amount = float(s.get("ot_amount") or 0)
        if amount <= 0:
            zero.append(s["employee"])
            continue
        if frappe.db.exists("Additional Salary", {
            "employee": s["employee"],
            "salary_component": OT_COMPONENT,
            "payroll_date": payroll_date,
            "docstatus": ["<", 2],
        }):
            skipped += 1
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Additional Salary",
                "employee": s["employee"],
                "salary_component": OT_COMPONENT,
                "amount": amount,
                "payroll_date": payroll_date,
                "overwrite_salary_structure_amount": 0,
                "company": frappe.db.get_value("Employee", s["employee"], "company"),
                "ref_doctype": SUMMARY_DOCTYPE,
                "ref_docname": s["name"],
            })
            doc.insert(ignore_permissions=True)
            doc.submit()
            created += 1
        except Exception as e:
            errors.append(f"{s['employee']}: {e}")
    return {"created": created, "skipped": skipped, "zero": zero, "errors": errors}
