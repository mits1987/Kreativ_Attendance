"""Glue between the pure pairing logic and Frappe DB + doctypes.

Public entrypoints:
    recalculate_period(year, month, employee=None) -> dict
    recalculate_employee_for_period(emp_id, year, month) -> dict
    recalculate_for_checkin(checkin_name) -> dict   (checkin saved/edited)
    recalculate_around(employee, time) -> dict      (checkin deleted; doc gone)
"""
from datetime import date, datetime, timedelta
import time
import frappe
from frappe.utils import get_datetime

from kreativ_attendance.attendance.pairing import (
    pair_checkins,
    format_hhmm,
    seconds_from_hours,
)


DEFAULT_STANDARD_SECONDS = 8 * 3600


def build_standard_hours_map() -> dict:
    """Return {employee_doc_name: standard_seconds} from Employee Standard Hours.

    Key is the Employee document name (HR-EMP-XXXXX), not the ZKTeco code,
    because the translation to codes happens at lookup time in recalculate_period.

    Falls back to Employee.default_shift -> Shift Type (start_time/end_time) if
    Employee Standard Hours record doesn't exist.
    """
    # First, get explicit Employee Standard Hours records
    try:
        esh_rows = frappe.db.get_all(
            "Employee Standard Hours",
            fields=["employee", "standard_hours"],
        )
    except frappe.DoesNotExistError:
        esh_rows = []
    standard_map = {r["employee"]: seconds_from_hours(r["standard_hours"]) for r in esh_rows}

    # Build fallback map from Employee.default_shift -> Shift Type duration
    shift_map = _build_shift_hours_fallback_map()

    # For employees without explicit standard hours, use shift fallback
    all_employees = set(standard_map.keys()) | set(shift_map.keys())
    for emp in all_employees:
        if emp not in standard_map and emp in shift_map:
            standard_map[emp] = shift_map[emp]

    return standard_map


def _build_shift_hours_fallback_map() -> dict:
    """Return {employee: standard_seconds} from Employee.default_shift -> Shift Type."""
    # Get employees with default_shift set
    emp_shifts = frappe.db.get_all(
        "Employee",
        filters={"default_shift": ["!=", ""]},
        fields=["name", "default_shift"],
    )
    if not emp_shifts:
        return {}

    shift_names = list({e["default_shift"] for e in emp_shifts})
    # Get shift type start/end times
    shift_types = frappe.get_all(
        "Shift Type",
        filters={"name": ["in", shift_names]},
        fields=["name", "start_time", "end_time"],
    )
    shift_hours = {}
    for st in shift_types:
        start = st.get("start_time") or 0
        end = st.get("end_time") or 0
        # Time fields return timedelta; convert to seconds
        if hasattr(start, "total_seconds"):
            start = int(start.total_seconds())
        if hasattr(end, "total_seconds"):
            end = int(end.total_seconds())
        if end > start:
            shift_hours[st["name"]] = int(end - start)
        else:
            shift_hours[st["name"]] = DEFAULT_STANDARD_SECONDS

    # Map employee -> shift standard seconds
    emp_map = {}
    for e in emp_shifts:
        shift_name = e["default_shift"]
        if shift_name in shift_hours:
            emp_map[e["name"]] = shift_hours[shift_name]
    return emp_map


def default_standard_seconds(standard_map: dict, employee: str) -> int:
    """Return per-employee standard, or 8h default if not set anywhere."""
    return standard_map.get(employee) or DEFAULT_STANDARD_SECONDS


def fetch_checkins_for_period(start: datetime, end: datetime, employee: str = None) -> dict:
    """Return {employee: [sorted list of checkin dicts]} for the inclusive [start, end) window."""
    filters = {"time": ["between", [start, end]]}
    if employee:
        filters["employee"] = employee

    records = frappe.db.get_all(
        "Employee Checkin",
        filters=filters,
        fields=["name", "employee", "time", "log_type"],
        order_by="employee, time",
    )

    grouped = {}
    for r in records:
        grouped.setdefault(r["employee"], []).append(r)
    return grouped


def _serialize_checkin_for_pairing(checkins):
    """Convert DB rows to format expected by pure pair_checkins()."""
    out = []
    for c in checkins:
        ct = c["time"]
        if isinstance(ct, str):
            ct = get_datetime(ct)
        out.append({
            "time": ct,
            "log_type": c["log_type"],
            "checkin_name": c["name"],
        })
    return out


def delete_existing_shifts(year: int, month: int, employee: str = None,
                           exclude_employees: set = None) -> int:
    """Wipe Employee Shift rows for the period before recalc. Returns count deleted.

    Deletes ONLY shifts dated inside the target month. shift_date is always the
    check-in date and pairs are bucketed by check-in month, so the ±2 day fetch
    window (needed to *pair* cross-month checkins) must never widen the delete —
    otherwise a May recalc destroys June 1-2 shifts it will not recreate.

    Uses a single bulk SQL DELETE instead of per-doc frappe.delete_doc().
    Hook-free by design: Employee Shift has no custom-coded hooks in this
    app, and a bulk recalc is a system-internal operation, not user-triggered.

    Retries with exponential backoff on lock-wait timeouts (up to 5 attempts).

    No commit here: recalculate_period commits once after the rebuild, so a
    failed rebuild rolls the deletes back instead of losing the month.
    """
    period_start = datetime(year, month, 1)
    period_end = (
        datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    )

    # Build WHERE clause parts
    conditions = [
        "shift_date >= %(period_start)s",
        "shift_date < %(period_end)s",
    ]
    params = {
        "period_start": period_start.date(),
        "period_end": period_end.date(),
    }
    if employee:
        conditions.append("employee = %(employee)s")
        params["employee"] = employee
    if exclude_employees:
        conditions.append("employee NOT IN %(excluded)s")
        params["excluded"] = list(exclude_employees)

    where_clause = " AND ".join(conditions)
    count_sql = f"SELECT COUNT(*) FROM `tabEmployee Shift` WHERE {where_clause}"
    count = frappe.db.sql(count_sql, params)[0][0]
    if not count:
        return 0

    delete_sql = f"DELETE FROM `tabEmployee Shift` WHERE {where_clause}"

    # Retry on lock-wait timeouts with exponential backoff
    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            frappe.db.sql(delete_sql, params)
            return count
        except frappe.exceptions.QueryTimeoutError:
            if attempt == max_attempts - 1:
                raise
            wait = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s, 4s, 8s
            time.sleep(wait)
            frappe.db.rollback()
    return 0


def create_shift_record(employee, paired_shift) -> str:
    """Insert one Employee Shift. Returns name."""
    doc = frappe.get_doc({
        "doctype": "Employee Shift",
        "employee": employee,
        "shift_date": paired_shift["shift_date"],
        "check_in": paired_shift["check_in_time"],
        "check_out": paired_shift.get("check_out_time"),
        "worked_hours": format_hhmm(paired_shift["total_seconds"]),
        "overtime_hours": format_hhmm(paired_shift["overtime_seconds"]),
        "worked_seconds": paired_shift["total_seconds"],
        "overtime_seconds": paired_shift["overtime_seconds"],
        "standard_hours": paired_shift.get("standard_hours", 8.0),
        "check_in_record": paired_shift.get("check_in_name"),
        "check_out_record": paired_shift.get("check_out_name"),
        "status": "Paired" if paired_shift.get("check_out_time") else "Missing Check-Out",
        "anomaly_reason": "" if paired_shift.get("check_out_time") else "missing_checkout",
    })
    doc.insert(ignore_permissions=True)
    return doc.name


# ---------------------------------------------------------------------------
# Bulk insert — used by recalculate_period for performance
# ---------------------------------------------------------------------------

_SHIFT_FIELDS = [
    "name", "employee", "shift_date", "check_in", "check_out",
    "worked_hours", "overtime_hours", "worked_seconds", "overtime_seconds",
    "standard_hours", "check_in_record", "check_out_record",
    "status", "anomaly_reason",
]


def _bulk_insert_shifts(emp: str, standard: float,
                        paired_shifts: list, anomalies: list,
                        year: int, month: int):
    """Insert all Employee Shift records for one employee in a single bulk INSERT.

    Paired shifts and anomaly rows are inserted together.  Returns
    (paired_count, anomaly_count).
    """
    import uuid
    values = []

    for p in paired_shifts:
        p["standard_hours"] = standard / 3600.0
        values.append((
            str(uuid.uuid4()).replace("-", "")[:24],
            emp,
            p["shift_date"],
            p["check_in_time"],
            p.get("check_out_time"),
            format_hhmm(p["total_seconds"]),
            format_hhmm(p["overtime_seconds"]),
            p["total_seconds"],
            p["overtime_seconds"],
            p.get("standard_hours", 8.0),
            p.get("check_in_name"),
            p.get("check_out_name"),
            "Paired" if p.get("check_out_time") else "Missing Check-Out",
            "" if p.get("check_out_time") else "missing_checkout",
        ))

    for a in anomalies:
        a_time = a["time"]
        if not (a_time.year == year and a_time.month == month):
            continue
        values.append((
            str(uuid.uuid4()).replace("-", "")[:24],
            emp,
            a_time.date() if hasattr(a_time, "date") else a_time,
            a_time if a.get("log_type") == "IN" else None,
            a_time if a.get("log_type") == "OUT" else None,
            "",
            "",
            0,
            0,
            standard / 3600.0 if standard else 8,
            a.get("checkin_name") if a.get("log_type") == "IN" else None,
            a.get("checkin_name") if a.get("log_type") == "OUT" else None,
            "Anomaly",
            _map_anomaly_reason(a.get("reason", "")),
        ))

    if not values:
        return 0, 0

    frappe.db.bulk_insert("Employee Shift", _SHIFT_FIELDS, values)
    return len(paired_shifts), len(values) - len(paired_shifts)


def has_active_lock(employee: str, year: int, month: int) -> bool:
    """Return True if there is a non-unlocked Employee Shift Lock for this (emp, year, month).

    An active lock has unlocked_at == None. Used by recalculate_period / recalculate_for_checkin
    to refuse re-pairing if the period is payroll-finalized.
    """
    try:
        name = frappe.db.get_value(
            "Employee Shift Lock",
            [
                ["employee", "=", employee],
                ["period_year", "=", int(year)],
                ["period_month", "=", int(month)],
                ["unlocked_at", "is", "not set"],
            ],
            "name",
        )
    except frappe.DoesNotExistError:
        return False
    return bool(name)


def recalculate_period(year: int, month: int, employee: str = None) -> dict:
    """
    Wipe & rebuild Employee Shift records for the given month.
    Includes cross-month carryover (extends window by ±2 days).

    Returns {employees_processed, paired, anomalies, deleted}.
    """
    period_start = datetime(year, month, 1)
    if month == 12:
        period_end = datetime(year + 1, 1, 1)
    else:
        period_end = datetime(year, month + 1, 1)
    # Extend window by 2 days on each side to catch cross-month pairs
    fetch_start = period_start - timedelta(days=2)
    fetch_end = period_end + timedelta(days=2)

    standard_map = build_standard_hours_map()
    grouped = fetch_checkins_for_period(fetch_start, fetch_end, employee=employee)

    # PRE-FLIGHT: never delete/rewrite a payroll-locked employee-month.
    # Single-employee recalc: throw (the caller explicitly asked for this employee).
    # Bulk recalc: skip locked employees so one payroll lock doesn't block everyone.
    try:
        locked_emps = set(frappe.db.get_all(
            "Employee Shift Lock",
            filters=[
                ["period_year", "=", int(year)],
                ["period_month", "=", int(month)],
                ["unlocked_at", "is", "not set"],
            ],
            pluck="employee",
        ))
    except frappe.DoesNotExistError:
        locked_emps = set()
    if employee and employee in locked_emps:
        frappe.throw(
            f"Cannot recalculate: an Employee Shift Lock for "
            f"{employee} / {year}-{month:02d} is active. "
            "Click 'Unlock Period' on the lock record with a reason to allow edits."
        )

    deleted = delete_existing_shifts(
        year, month, employee=employee, exclude_employees=locked_emps
    )

    total_paired = 0
    total_anomalies = 0
    emps_processed = 0

    for emp, checkins in grouped.items():
        if emp in locked_emps:
            continue
        emps_processed += 1
        standard = default_standard_seconds(standard_map, emp)
        sorted_ck = sorted(checkins, key=lambda c: c["time"])
        prepared = _serialize_checkin_for_pairing(sorted_ck)
        paired, anomalies = pair_checkins(
            prepared,
            standard_seconds=standard,
            period_year=year,
            period_month=month,
        )

        pc, ac = _bulk_insert_shifts(emp, standard, paired, anomalies, year, month)
        total_paired += pc
        total_anomalies += ac

    frappe.db.commit()
    return {
        "employees": emps_processed,
        "paired": total_paired,
        "anomalies": total_anomalies,
        "deleted": deleted,
        "skipped_locked": sorted(locked_emps) if not employee else [],
        "period": f"{year}-{month:02d}",
    }


def _map_anomaly_reason(reason: str) -> str:
    """Map internal pairing reason → doctype Anomaly Reason select values."""
    r = reason.lower()
    if "carryover" in r:
        return "previous_month_carryover"
    if "unpaired" in r or "missing" in r:
        return "missing_checkout"
    return ""


def recalculate_employee_for_period(emp_id: str, year: int, month: int) -> dict:
    return recalculate_period(year, month, employee=emp_id)


def recalculate_for_checkin(checkin_name: str) -> dict:
    """Called after Employee Checkin save / edit."""
    c = frappe.get_doc("Employee Checkin", checkin_name)
    return recalculate_around(c.employee, c.time)


def recalculate_around(employee: str, time) -> dict:
    """Rebuild the employee's shifts for the month containing `time`, plus any
    neighboring month whose pairing window overlaps that punch.

    Each month's pairing fetches checkins from a ±2 day window, so a punch can
    only affect the previous month if it falls on day 1-2, and the next month
    if it falls on the last 2 days. Anything else would be a full rebuild that
    changes nothing. Payroll-locked neighbor months are skipped quietly.

    Takes (employee, time) rather than a checkin name so it also works after
    the checkin has been DELETED (removing a bogus punch must rebuild too).
    """
    t = get_datetime(time)
    year, month = t.year, t.month
    result = {"current": recalculate_employee_for_period(employee, year, month)}

    def _neighbor(y, m):
        if has_active_lock(employee, y, m):
            return {"skipped": "locked", "period": f"{y}-{m:02d}"}
        return recalculate_employee_for_period(employee, y, m)

    if t.day <= 2:
        py, pm = (year, month - 1) if month > 1 else (year - 1, 12)
        result["previous"] = _neighbor(py, pm)

    next_start = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    if (next_start - t.date()).days <= 2:
        result["next"] = _neighbor(next_start.year, next_start.month)

    return result
