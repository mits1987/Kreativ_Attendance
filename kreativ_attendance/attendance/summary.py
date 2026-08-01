"""Build KG Monthly Attendance Summary rows from paired shifts.

This is the bridge between the daily layer (pairing, which exists to catch bad
punches) and payroll (which needs only PD, WO, PH and the overtime figure).

    total_hours    = sum of worked_seconds over Paired/Manual shifts in the month
    standard_hours = Employee.working_hours, else the settings default
    WD/WO/PH       = from the Holiday List (calendar_util)
    PD             = min(WD, total_hours / standard_hours), to nearest 0.5
    ot_hours       = max(0, total_hours - WD * standard_hours)
    ot_amount      = ot_hours * (rate_of_wages / (WD * standard_hours))

Rows in status Reviewed or Locked are never overwritten — a rebuild only
touches Draft rows, so a manual correction survives the next recalculation.
"""
import frappe
from frappe.utils import now_datetime

from kreativ_attendance.attendance import settings as kg_settings
from kreativ_attendance.attendance.calendar_util import (
    calendar_for_employee,
    period_bounds,
)
from kreativ_attendance.attendance.payroll_math import (
    compute_attendance,
    compute_overtime_amount,
    compute_pay_days,
)

SUMMARY_DOCTYPE = "KG Monthly Attendance Summary"
SHIFT_DOCTYPE = "KG Employee Attendance Shift"

WORKED_STATUSES = ("Paired", "Manual")
ANOMALY_STATUSES = ("Anomaly", "Missing Check-Out")

# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def get_standard_hours_map() -> dict:
    """Return {employee: (hours, source)} from Employee.working_hours."""
    default = kg_settings.default_standard_hours()
    rows = frappe.get_all(
        "Employee", filters={"working_hours": [">", 0]},
        fields=["name", "working_hours"],
    )
    out = {r["name"]: (float(r["working_hours"]), "Employee.working_hours") for r in rows}
    return out, default


def get_rate_of_wages(employee: str, on_date) -> float:
    """Return the employee's Rate of Wages — the divisor for the overtime rate.

    Rate of Wages = Basic + HRA + Conveyance + Medical + Other
                  = column R of the approved salary sheet.

    Resolution order:
      1. Salary Structure Assignment `base` on or before `on_date`.
         setup_payroll_structure.py defines base as exactly this sum, so this
         is the authoritative source once assignments exist.
      2. The Employee kg_* component fields, summed.
         Used before any assignment has been created, so a reconciliation run
         still produces a sensible overtime figure.

    With Overtime Rate Base = "Basic Only", returns kg_basic instead.
    """
    basic_only = kg_settings.ot_rate_base() == "Basic Only"

    emp = frappe.db.get_value(
        "Employee", employee,
        ["kg_basic", "kg_hra", "kg_conveyance", "kg_medical", "kg_other"],
        as_dict=True,
    ) or {}

    if basic_only:
        return float(emp.get("kg_basic") or 0)

    assignment = frappe.get_all(
        "Salary Structure Assignment",
        filters=[
            ["employee", "=", employee],
            ["docstatus", "=", 1],
            ["from_date", "<=", on_date],
        ],
        fields=["base"],
        order_by="from_date desc",
        limit=1,
    )
    if assignment and float(assignment[0].get("base") or 0) > 0:
        return float(assignment[0]["base"])

    return sum(float(emp.get(k) or 0) for k in
               ("kg_basic", "kg_hra", "kg_conveyance", "kg_medical", "kg_other"))


def aggregate_hours(year: int, month: int, employee: str = None) -> dict:
    """Return {employee: {'total_hours', 'shift_count', 'anomaly_count'}}."""
    start, end = period_bounds(year, month)
    filters = [["shift_date", ">=", start], ["shift_date", "<", end]]
    if employee:
        filters.append(["employee", "=", employee])

    rows = frappe.get_all(
        SHIFT_DOCTYPE, filters=filters,
        fields=["employee", "status", "worked_seconds"],
    )
    out = {}
    for r in rows:
        d = out.setdefault(r["employee"], {
            "total_hours": 0.0, "shift_count": 0, "anomaly_count": 0,
        })
        if r["status"] in WORKED_STATUSES:
            d["total_hours"] += float(r.get("worked_seconds") or 0) / 3600.0
            d["shift_count"] += 1
        elif r["status"] in ANOMALY_STATUSES:
            d["anomaly_count"] += 1
    for d in out.values():
        d["total_hours"] = round(d["total_hours"], 2)
    return out


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_month(year: int, month: int, employee: str = None) -> dict:
    """Create or refresh Draft summary rows for the period.

    Reviewed and Locked rows are left untouched and counted as `preserved`.
    """
    year, month = int(year), int(month)
    period = f"{year}-{month:02d}"
    _, end = period_bounds(year, month)
    on_date = frappe.utils.add_days(end, -1)

    std_map, std_default = get_standard_hours_map()
    hours_map = aggregate_hours(year, month, employee=employee)

    created = updated = preserved = 0
    using_default = []

    for emp, agg in sorted(hours_map.items()):
        existing = frappe.db.get_value(
            SUMMARY_DOCTYPE,
            {"employee": emp, "period_year": year, "period_month": month},
            ["name", "status"], as_dict=True,
        )
        if existing and existing["status"] in ("Reviewed", "Locked"):
            preserved += 1
            continue

        std, source = std_map.get(emp, (std_default, "Settings default"))
        if source == "Settings default":
            using_default.append(emp)

        cal = calendar_for_employee(emp, year, month)
        derived = compute_attendance(
            total_hours=agg["total_hours"], standard_hours=std, wd=cal["wd"]
        )
        row = get_rate_of_wages(emp, on_date)
        values = {
            "employee": emp,
            "period_year": year,
            "period_month": month,
            "period": period,
            "status": "Draft",
            "days_in_month": cal["days_in_month"],
            "wd": cal["wd"],
            "wo": cal["wo"],
            "ph": cal["ph"],
            "holiday_list": cal["holiday_list"],
            "standard_hours": std,
            "hours_source": source,
            "required_hours": derived["required_hours"],
            "total_hours": agg["total_hours"],
            "shift_count": agg["shift_count"],
            "anomaly_count": agg["anomaly_count"],
            "pd": derived["pd"],
            "pay_days": compute_pay_days(derived["pd"], cal["wo"], cal["ph"]),
            "ot_hours": derived["ot_hours"],
            "rate_of_wages": row,
            "ot_amount": compute_overtime_amount(
                derived["ot_hours"], row, cal["wd"], std
            ),
            "computed_at": now_datetime(),
            "computed_by": frappe.session.user,
        }

        if existing:
            doc = frappe.get_doc(SUMMARY_DOCTYPE, existing["name"])
            doc.update(values)
            doc.flags.recompute_pd = True
            doc.save(ignore_permissions=True)
            updated += 1
        else:
            doc = frappe.get_doc(dict(doctype=SUMMARY_DOCTYPE, **values))
            doc.flags.recompute_pd = True
            doc.insert(ignore_permissions=True)
            created += 1

    return {
        "period": period,
        "created": created,
        "updated": updated,
        "preserved": preserved,
        "employees": len(hours_map),
        "using_default_standard_hours": sorted(using_default),
    }


def get_summary(employee: str, year: int, month: int):
    """Return the summary doc for one employee-month, or None."""
    name = frappe.db.get_value(
        SUMMARY_DOCTYPE,
        {"employee": employee, "period_year": int(year), "period_month": int(month)},
        "name",
    )
    return frappe.get_doc(SUMMARY_DOCTYPE, name) if name else None


def lock_month(year: int, month: int, employee: str = None) -> int:
    """Mark summaries Locked once payroll is final. Returns rows locked."""
    filters = {"period_year": int(year), "period_month": int(month)}
    if employee:
        filters["employee"] = employee
    names = frappe.get_all(SUMMARY_DOCTYPE, filters=filters, pluck="name")
    for n in names:
        frappe.db.set_value(SUMMARY_DOCTYPE, n, "status", "Locked", update_modified=False)
    return len(names)
