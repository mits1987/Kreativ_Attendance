"""Quality gate for a payroll month.

This module is the single answer to the question: "is this month safe to pay?"

It scans a (year, month) period for every condition that historically caused
over/under-payment in the old Excel + Python workflow:

    1. anomalies      — unpaired punches / missing checkouts (KG Employee Attendance Shift
                         rows with status Anomaly or Missing Check-Out).
                         previous_month_carryover is informational and does
                         NOT block.
    2. long_sessions  — paired shifts longer than the configured threshold
                         (default 13h, same as the yellow rows in the old
                         Excel report). A >13h "shift" is almost always a
                         missed middle punch that merged two days.
    3. break_punches  — raw device punches whose original ZKTeco punch_state
                         was a Break/undefined state (stored in the
                         punch_state_raw custom field by zkteco_sync). The old
                         script hard-stopped on these; we surface them here.
    4. missing_standard_hours — employees with shifts this month but no
                         Employee.working_hours row (silently falling back
                         to the 8h default changes their overtime).

`assert_month_clean()` throws with a readable, per-employee list — call it
before any HRMS sync / payroll step. `get_month_issues()` returns the same
data structurally for dashboards and the monthly close job.

Configuration (site_config.json, all optional):
    kreativ_long_session_hours       float, default 13
    kreativ_block_on_long_sessions   0/1,   default 1
    kreativ_block_on_break_punches   0/1,   default 1
"""
from datetime import date

import frappe
from frappe import _

from kreativ_attendance.attendance.service import _build_shift_hours_fallback_map

# Blocking anomaly reasons. previous_month_carryover is informational only —
# the old script printed it as a note and continued.
BLOCKING_STATUSES = ("Anomaly", "Missing Check-Out")
INFORMATIONAL_REASONS = ("previous_month_carryover",)

DEFAULT_LONG_SESSION_HOURS = 13.0

DOCTYPE = "KG Employee Attendance Shift"


def _period(year: int, month: int):
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def long_session_seconds() -> int:
    """Read threshold from HR Settings (kreativ_long_session_hours) or site_config fallback."""
    try:
        hours = frappe.db.get_single_value("HR Settings", "kreativ_long_session_hours")
    except Exception:
        hours = None
    if hours is None:
        hours = frappe.conf.get("kreativ_long_session_hours")
    hours = hours or DEFAULT_LONG_SESSION_HOURS
    return int(float(hours) * 3600)


def get_month_issues(year: int, month: int, employee: str = None) -> dict:
    """Scan the month and return every payroll-relevant issue.

    Returns:
        {
          "period": "YYYY-MM",
          "anomalies":            [ {employee, employee_name, shift_date, status, anomaly_reason, name} ],
          "long_sessions":        [ {employee, employee_name, shift_date, worked_hours, worked_seconds, name} ],
          "break_punches":        [ {employee, time, punch_state_raw, name} ],
          "missing_standard_hours": [employee, ...],
          "blocking": bool   # True if anything above should stop payroll
        }
    """
    year, month = int(year), int(month)
    start, end = _period(year, month)

    base_filters = [
        ["shift_date", ">=", start],
        ["shift_date", "<", end],
    ]
    if employee:
        base_filters.append(["employee", "=", employee])

    # --- 1. Anomalies (unpaired / missing checkout), excluding informational ---
    anomalies = frappe.get_all(
        "KG Employee Attendance Shift",
        filters=base_filters + [["status", "in", list(BLOCKING_STATUSES)]],
        fields=["name", "employee", "employee_name", "shift_date",
                "status", "anomaly_reason"],
        order_by="employee, shift_date",
    )
    anomalies = [
        a for a in anomalies
        if (a.get("anomaly_reason") or "") not in INFORMATIONAL_REASONS
    ]

    # --- 2. Long sessions (the old script's yellow >13h rows) ---
    threshold = long_session_seconds()
    long_sessions = frappe.get_all(
        "KG Employee Attendance Shift",
        filters=base_filters + [
            ["status", "in", ["Paired", "Manual"]],
            ["worked_seconds", ">", threshold],
        ],
        fields=["name", "employee", "employee_name", "shift_date",
                "worked_hours", "worked_seconds"],
        order_by="employee, shift_date",
    )

    # --- 3. Break punches in the raw checkin feed ---
    break_punches = []
    if frappe.db.has_column("Employee Checkin", "punch_state_raw"):
        ck_filters = [
            ["time", ">=", str(start)],
            ["time", "<", str(end)],
            ["punch_state_raw", "in", ["2", "3", "BREAK"]],
        ]
        if employee:
            ck_filters.append(["employee", "=", employee])
        break_punches = frappe.get_all(
            "Employee Checkin",
            filters=ck_filters,
            fields=["name", "employee", "time", "log_type", "punch_state_raw"],
            order_by="employee, time",
        )

    # --- 4. Employees silently on the 8h default ---
    emps_with_shifts = set(
        r[0] for r in frappe.db.sql(
            """SELECT DISTINCT employee FROM `tabKG Employee Attendance Shift`
               WHERE shift_date >= %s AND shift_date < %s
               {emp}""".format(emp="AND employee = %s" if employee else ""),
            (start, end, employee) if employee else (start, end),
        )
    )
    have_hours = set(frappe.get_all("Employee", filters={"working_hours": [">", 0]}, pluck="name"))

    # Also check Employee.default_shift -> Shift Type as valid standard hours source
    shift_fallback = set(_build_shift_hours_fallback_map().keys())
    have_effective_hours = have_hours | shift_fallback
    missing_standard_hours = sorted(emps_with_shifts - have_effective_hours)

    block_long = bool(frappe.conf.get("kreativ_block_on_long_sessions", 1))
    block_break = bool(frappe.conf.get("kreativ_block_on_break_punches", 1))

    blocking = bool(
        anomalies
        or (block_long and long_sessions)
        or (block_break and break_punches)
    )

    return {
        "period": f"{year}-{month:02d}",
        "anomalies": anomalies,
        "long_sessions": long_sessions,
        "break_punches": break_punches,
        "missing_standard_hours": missing_standard_hours,
        "long_session_threshold_hours": threshold / 3600.0,
        "blocking": blocking,
    }


def format_issues(issues: dict) -> str:
    """Human-readable multi-line summary of get_month_issues() output.
    Used in frappe.throw messages, WhatsApp admin alerts and Error Logs."""
    lines = []
    if issues["anomalies"]:
        lines.append(f"Unresolved anomalies ({len(issues['anomalies'])}):")
        for a in issues["anomalies"][:30]:
            lines.append(
                f"  - {a.get('employee_name') or a['employee']} on {a['shift_date']}: "
                f"{a['status']}"
                + (f" ({a['anomaly_reason']})" if a.get("anomaly_reason") else "")
            )
        if len(issues["anomalies"]) > 30:
            lines.append(f"  ... and {len(issues['anomalies']) - 30} more")

    if issues["long_sessions"]:
        t = issues.get("long_session_threshold_hours", DEFAULT_LONG_SESSION_HOURS)
        lines.append(
            f"Suspicious long sessions > {t:g}h "
            f"({len(issues['long_sessions'])}) — possible missed middle punch:"
        )
        for s in issues["long_sessions"][:30]:
            lines.append(
                f"  - {s.get('employee_name') or s['employee']} on {s['shift_date']}: "
                f"{s['worked_hours']} worked"
            )
        if len(issues["long_sessions"]) > 30:
            lines.append(f"  ... and {len(issues['long_sessions']) - 30} more")

    if issues["break_punches"]:
        lines.append(
            f"Break-state punches ({len(issues['break_punches'])}) — employee "
            f"pressed Break instead of Check on the device:"
        )
        for b in issues["break_punches"][:30]:
            lines.append(f"  - {b['employee']} at {b['time']} (raw state {b['punch_state_raw']})")

    if issues["missing_standard_hours"]:
        lines.append(
            "Employees using the 8h default (no Employee.working_hours row): "
            + ", ".join(issues["missing_standard_hours"])
        )
    return "\n".join(lines) if lines else "No issues."


def assert_month_clean(year: int, month: int, employee: str = None,
                       force: bool = False) -> dict:
    """Throw if the month has payroll-blocking issues. Returns the issues dict.

    Call this at the top of any HRMS-sync / payroll-generation path.
    `force=True` (an explicit HR override) logs the override and continues.
    """
    issues = get_month_issues(year, month, employee=employee)
    if not issues["blocking"]:
        return issues

    if force:
        frappe.log_error(
            title=f"Payroll gate OVERRIDDEN for {issues['period']} by {frappe.session.user}",
            message=format_issues(issues),
        )
        return issues

    frappe.throw(
        _("Cannot proceed — {0} has unresolved attendance issues:<br><pre>{1}</pre>"
          "<br>Fix the punches (or pass force=1 to override with audit log).")
        .format(issues["period"], frappe.utils.escape_html(format_issues(issues))),
        title=_("Payroll blocked by attendance quality gate"),
    )


@frappe.whitelist()
def month_issues(year: int = None, month: int = None, employee: str = None) -> dict:
    """Whitelisted wrapper for the dashboard: list everything blocking payroll."""
    frappe.only_for(("System Manager", "HR Manager", "HR User"))
    if not year or not month:
        frappe.throw(_("Both year and month are required"))
    return get_month_issues(int(year), int(month), employee=employee or None)