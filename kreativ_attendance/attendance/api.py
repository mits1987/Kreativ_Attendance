"""Whitelisted (client-callable) API endpoints for the attendance module.

Exposed via /api/method/kreativ_attendance.attendance.api.<name>
"""
import frappe
from frappe import _

from kreativ_attendance.attendance.service import (
    recalculate_period,
    recalculate_employee_for_period,
)
# Alias: this module defines a whitelisted function with the same name below.
# Without the alias the endpoint would shadow the import and call itself
# (infinite recursion).
from kreativ_attendance.attendance.service import (
    recalculate_for_checkin as _service_recalculate_for_checkin,
)

ALLOWED_ROLES = ("System Manager", "HR Manager", "HR User")


def _format_hhmm(seconds: int) -> str:
    """Convert seconds to HH:MM string."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}:{m:02d}"


@frappe.whitelist()
def month_summary(year: int = None, month: int = None) -> dict:
    """Return monthly attendance summary for the dashboard.

    Returns:
        {
            "totals": {employees, present_days, total_hours, overtime, anomalies},
            "rows": [{employee, employee_name, department, present_days, total_hours, overtime, anomalies}],
            "missing_standard_hours": [employee, ...]
        }
    """
    frappe.only_for(ALLOWED_ROLES)
    if not year or not month:
        frappe.throw(_("Both year and month are required"))
    year = int(year)
    month = int(month)
    if not (1 <= month <= 12):
        frappe.throw(_("Month must be between 1 and 12"))

    from datetime import date
    period_start = date(year, month, 1)
    period_end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    # Get all shifts for the period
    shifts = frappe.db.get_all(
        "KG Employee Attendance Shift",
        filters=[
            ["shift_date", ">=", period_start],
            ["shift_date", "<", period_end],
        ],
        fields=["employee", "employee_name", "department", "shift_date",
                "worked_seconds", "overtime_seconds", "status", "anomaly_reason"],
    )

    # Build per-employee aggregation
    emp_data = {}
    for s in shifts:
        emp = s.employee
        if emp not in emp_data:
            emp_data[emp] = {
                "employee": emp,
                "employee_name": s.employee_name,
                "department": s.department,
                "present_days": 0,
                "worked_seconds": 0,
                "overtime_seconds": 0,
                "anomalies": 0,
            }
        d = emp_data[emp]
        if s.status == "Paired" or s.status == "Manual":
            d["present_days"] += 1
            d["worked_seconds"] += s.worked_seconds or 0
            d["overtime_seconds"] += s.overtime_seconds or 0
        if s.status in ("Anomaly", "Missing Check-Out"):
            d["anomalies"] += 1

    # Totals
    totals = {
        "employees": len(emp_data),
        "present_days": sum(d["present_days"] for d in emp_data.values()),
        "total_hours": _format_hhmm(sum(d["worked_seconds"] for d in emp_data.values())),
        "overtime": _format_hhmm(sum(d["overtime_seconds"] for d in emp_data.values())),
        "anomalies": sum(d["anomalies"] for d in emp_data.values()),
    }

    # Rows for table
    rows = []
    for d in sorted(emp_data.values(), key=lambda x: x["employee"]):
        rows.append({
            "employee": d["employee"],
            "employee_name": d["employee_name"],
            "department": d["department"],
            "present_days": d["present_days"],
            "total_hours": _format_hhmm(d["worked_seconds"]),
            "overtime": _format_hhmm(d["overtime_seconds"]),
            "anomalies": d["anomalies"],
        })

    # Missing standard hours
    emps_with_shifts = set(emp_data.keys())
    have_hours = set(frappe.get_all("Employee", filters={"working_hours": [">", 0]}, pluck="name"))
    missing_standard_hours = sorted(emps_with_shifts - have_hours)

    return {
        "totals": totals,
        "rows": rows,
        "missing_standard_hours": missing_standard_hours,
    }


@frappe.whitelist()
def recalculate_year_month(year: int = None, month: int = None) -> dict:
    """Recalculate KG Employee Attendance Shift records for the given month/year.

    Args:
        year: e.g. 2026
        month: e.g. 5 (May)

    Returns dict with counts of paired/anomalies/employees affected.
    """
    frappe.only_for(ALLOWED_ROLES)
    if not year or not month:
        frappe.throw(_("Both year and month are required"))
    year = int(year)
    month = int(month)
    if not (1 <= month <= 12):
        frappe.throw(_("Month must be between 1 and 12"))
    return recalculate_period(year=year, month=month, employee=None)


@frappe.whitelist()
def recalculate_employee(emp: str = None, year: int = None, month: int = None) -> dict:
    """Recalculate KG Employee Attendance Shift records for ONE employee in a given period."""
    frappe.only_for(ALLOWED_ROLES)
    if not emp or not year or not month:
        frappe.throw(_("Employee, year and month are all required"))
    return recalculate_employee_for_period(emp_id=emp, year=int(year), month=int(month))


@frappe.whitelist()
def recalculate_for_checkin(checkin_name: str = None) -> dict:
    """Manual trigger — same as what runs when an Employee Checkin is saved.

    Wipes & rebuilds the affected employee's shifts for current AND previous month.
    """
    frappe.only_for(ALLOWED_ROLES)
    if not checkin_name:
        frappe.throw(_("checkin_name is required"))
    return _service_recalculate_for_checkin(checkin_name)


@frappe.whitelist()
def unlock_period(emp: str = None, year: int = None, month: int = None, reason: str = "") -> dict:
    """Unlock a KG Employee Shift Lock period so corrections can be made.

    Requires: emp, year, month, and a non-empty reason for audit trail.
    Clears the `locked` flag and `lock_period` on all affected KG Employee Attendance Shift records.
    """
    frappe.only_for(ALLOWED_ROLES)
    if not emp:
        frappe.throw(_("Employee is required"))
    if not year or not month:
        frappe.throw(_("Both year and month are required"))
    if not reason or not reason.strip():
        frappe.throw(_("An unlock reason is required for audit trail"))

    year = int(year)
    month = int(month)

    from frappe.utils import now_datetime
    from kreativ_attendance.attendance.lock import release_shift_flags

    lock = frappe.db.get_value(
        "KG Employee Shift Lock",
        [
            ["employee", "=", emp],
            ["period_year", "=", year],
            ["period_month", "=", month],
            ["unlocked_at", "is", "not set"],
        ],
        "name",
    )
    if not lock:
        frappe.throw(_("No active lock found for {0} in {1}-{2}. Already unlocked?").format(emp, year, month))

    # Unlock the lock record
    lock_doc = frappe.get_doc("KG Employee Shift Lock", lock)
    lock_doc.is_unlocked = 1
    lock_doc.unlocked_at = now_datetime()
    lock_doc.unlocked_by = frappe.session.user
    lock_doc.unlock_reason = reason.strip()
    lock_doc.save(ignore_permissions=True)

    released = release_shift_flags(emp, year, month)

    frappe.db.commit()
    return {
        "status": "unlocked",
        "lock": lock,
        "shifts_released": released,
        "period": f"{year}-{month:02d}",
    }


@frappe.whitelist()
def preview_payroll(year: int = None, month: int = None) -> dict:
    """Dry-run payroll computation for a month — no records written.

    Returns per-employee salary breakdown using KG Monthly Attendance Summary
    data + Employee custom fields (kg_basic, etc.) + payroll_math.compute_salary().
    """
    frappe.only_for(ALLOWED_ROLES)
    if not year or not month:
        frappe.throw(_("Both year and month are required"))
    year, month = int(year), int(month)

    from calendar import monthrange
    from kreativ_attendance.attendance.payroll_math import compute_salary

    days_in_month = monthrange(year, month)[1]

    summaries = frappe.get_all(
        "KG Monthly Attendance Summary",
        filters={"period_year": year, "period_month": month},
        fields=["employee", "employee_name", "pd", "wo", "ph", "pay_days",
                "ot_hours", "ot_amount", "standard_hours", "total_hours",
                "wd", "rate_of_wages", "anomaly_count"],
        order_by="employee",
    )
    if not summaries:
        return {"rows": [], "totals": {}}

    emp_ids = [s.employee for s in summaries]
    emps = frappe.get_all(
        "Employee",
        filters={"name": ["in", emp_ids]},
        fields=["name", "employee_name", "salary_sheet_id",
                "kg_basic", "kg_hra", "kg_conveyance", "kg_medical", "kg_other",
                "pf_applicable", "esi_applicable"],
    )
    emp_map = {e.name: e for e in emps}

    rows = []
    for s in summaries:
        e = emp_map.get(s.employee, {})
        result = compute_salary(
            basic=e.get("kg_basic") or 0,
            hra=e.get("kg_hra") or 0,
            con=e.get("kg_conveyance") or 0,
            med=e.get("kg_medical") or 0,
            other=e.get("kg_other") or 0,
            pd=s.pd or 0,
            wo=s.wo or 0,
            ph=s.ph or 0,
            days_in_month=days_in_month,
            incentive=0,
            overtime=s.ot_amount or 0,
            pf_applicable=bool(e.get("pf_applicable")),
            esi_applicable=bool(e.get("esi_applicable")),
        )
        result["employee"] = s.employee
        result["employee_name"] = s.employee_name
        result["salary_sheet_id"] = e.get("salary_sheet_id") or ""
        result["pd"] = s.pd
        result["wo"] = s.wo
        result["ph"] = s.ph
        result["total_hours"] = s.total_hours
        result["ot_hours"] = s.ot_hours
        result["anomaly_count"] = s.anomaly_count or 0
        rows.append(result)

    totals = {
        "gross": sum(r["gross"] for r in rows),
        "total_deduction": sum(r["total_deduction"] for r in rows),
        "net": sum(r["net"] for r in rows),
        "ot_amount": sum(r["overtime"] for r in rows),
        "pf": sum(r["pf"] for r in rows),
        "esi": sum(r["esi"] for r in rows),
        "employees": len(rows),
    }

    return {"rows": rows, "totals": totals, "days_in_month": days_in_month}


@frappe.whitelist()
def sync_month_to_hrms(year: int = None, month: int = None, employee: str = None) -> dict:
    """Create HRMS Attendance records + Overtime Additional Salary for a month.

    Run this once shifts for the month are reviewed (all rows green). Then use
    a standard HRMS Payroll Entry -> Create Salary Slips: payment days come from
    the Attendance records, overtime pay from the Additional Salary rows.
    """
    frappe.only_for(ALLOWED_ROLES)
    if not year or not month:
        frappe.throw(_("Both year and month are required"))
    from kreativ_attendance.attendance.hrms import sync_month_to_hrms as _sync
    return _sync(int(year), int(month), employee=employee or None)