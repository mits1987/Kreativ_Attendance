"""Derive WD / WO / PH from the standard ERPNext Holiday List.

This replaces the previous situation where WD and WO were typed into the salary
sheet by hand each month.

    WO = count of Holiday rows in the period flagged `weekly_off`
    PH = count of Holiday rows in the period NOT flagged `weekly_off`
    WD = days_in_month - WO - PH

Employees can sit on different Holiday Lists (Employee.holiday_list), which is
how June produced WO=4 for most people, WO=3 for two, and WO=0 for one.

Resolution order for an employee's list, matching HRMS:
    Employee.holiday_list -> Company.default_holiday_list
"""
import calendar
from datetime import date

import frappe


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(int(year), int(month))[1]


def period_bounds(year: int, month: int):
    """Return (first_day, first_day_of_next_month)."""
    year, month = int(year), int(month)
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def resolve_holiday_list(employee: str) -> str:
    """Employee's Holiday List, falling back to the company default."""
    emp = frappe.db.get_value(
        "Employee", employee, ["holiday_list", "company"], as_dict=True
    )
    if not emp:
        return None
    if emp.get("holiday_list"):
        return emp["holiday_list"]
    if emp.get("company"):
        return frappe.db.get_value("Company", emp["company"], "default_holiday_list")
    return None


def calendar_for_list(holiday_list: str, year: int, month: int) -> dict:
    """Return {'days_in_month', 'wo', 'ph', 'wd', 'holiday_list'} for one list."""
    dim = days_in_month(year, month)
    result = {
        "days_in_month": dim,
        "wo": 0.0,
        "ph": 0.0,
        "wd": float(dim),
        "holiday_list": holiday_list,
    }
    if not holiday_list:
        return result

    start, end = period_bounds(year, month)
    rows = frappe.get_all(
        "Holiday",
        filters=[
            ["parent", "=", holiday_list],
            ["parenttype", "=", "Holiday List"],
            ["holiday_date", ">=", start],
            ["holiday_date", "<", end],
        ],
        fields=["holiday_date", "weekly_off"],
    )
    wo = sum(1 for r in rows if r.get("weekly_off"))
    ph = len(rows) - wo
    result["wo"] = float(wo)
    result["ph"] = float(ph)
    result["wd"] = float(dim - wo - ph)
    return result


def calendar_for_employee(employee: str, year: int, month: int) -> dict:
    """Return the WD/WO/PH breakdown for one employee-month.

    Results are cached per (holiday_list, year, month) for the duration of the
    request, because a monthly close resolves this once per employee and most
    employees share a list.
    """
    hl = resolve_holiday_list(employee)
    cache_key = f"kg_cal::{hl}::{year}::{month}"
    cached = frappe.local.cache.get(cache_key) if hasattr(frappe.local, "cache") else None
    if cached:
        return dict(cached)

    result = calendar_for_list(hl, year, month)
    try:
        frappe.local.cache[cache_key] = dict(result)
    except Exception:
        pass
    return result


def employees_without_holiday_list(employees) -> list:
    """Subset of `employees` with neither an Employee nor a Company holiday list.

    These fall back to WD = days_in_month with WO = 0, which almost always
    understates their pay days, so the quality gate reports them.
    """
    missing = []
    for emp in employees:
        if not resolve_holiday_list(emp):
            missing.append(emp)
    return sorted(missing)
