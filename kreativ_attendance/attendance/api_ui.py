"""Whitelisted endpoints backing the Month Console UI.

Kept separate from api.py so the UI layer can evolve without touching the
payroll endpoints.

Design goal: ONE round trip per screen. The old dashboard made four separate
calls (summary, issues, settings, shift counts) and still could not tell the
operator what to do next. `month_status` returns everything the console needs,
including an explicit `next_action` the UI renders as the primary button.
"""
import frappe
from frappe import _

from kreativ_attendance.attendance import settings as kg_settings
from kreativ_attendance.attendance.calendar_util import days_in_month

ALLOWED_ROLES = ("System Manager", "HR Manager", "HR User")

SUMMARY_DOCTYPE = "KG Monthly Attendance Summary"
SHIFT_DOCTYPE = "KG Employee Attendance Shift"


# ---------------------------------------------------------------------------
# One call, whole screen
# ---------------------------------------------------------------------------

@frappe.whitelist()
def month_status(year: int = None, month: int = None) -> dict:
    """Everything the Month Console needs, in a single round trip."""
    frappe.only_for(ALLOWED_ROLES)
    if not year or not month:
        frappe.throw(_("Both year and month are required"))
    year, month = int(year), int(month)

    from kreativ_attendance.attendance.quality import get_month_issues

    issues = get_month_issues(year, month)
    shadow = kg_settings.is_shadow_mode()

    counts = _summary_counts(year, month)
    shift_count = frappe.db.count(SHIFT_DOCTYPE, _period_filter(year, month))

    # --- What should the operator do next? -------------------------------
    # A single explicit instruction beats four cards the reader has to
    # interpret. This is the string the console renders as its primary button.
    if not shift_count:
        step, action, label = 1, "recalculate", _("Recalculate this month")
        hint = _("No shifts have been built yet for this month.")
    elif issues.get("blocking"):
        step, action, label = 2, "fix_issues", _("Fix {0} blocking issue(s)").format(
            len(issues.get("anomalies", [])) + len(issues.get("long_sessions", []))
        )
        hint = _("Payroll is blocked until every punch is corrected. "
                 "Each issue below has a Fix button.")
    elif not counts["total"]:
        step, action, label = 3, "build_summary", _("Build monthly summary")
        hint = _("Punches are clean. Build the Pay Days figures.")
    elif counts["draft"]:
        step, action, label = 4, "review", _("Review {0} draft row(s)").format(counts["draft"])
        hint = _("Check the Pay Days against your salary sheet, then mark them Reviewed.")
    elif shadow:
        step, action, label = 5, "shadow_blocked", _("Shadow Mode is ON")
        hint = _("Everything is reviewed. Payroll will not be written while "
                 "Shadow Mode is on — this is correct until cutover.")
    else:
        step, action, label = 5, "payroll", _("Write payroll to HRMS")
        hint = _("Reviewed and ready. Remember to enter the Production Bonus first.")

    return {
        "period": f"{year}-{month:02d}",
        "year": year,
        "month": month,
        "days_in_month": days_in_month(year, month),
        "shadow_mode": 1 if shadow else 0,
        "long_session_hours": float(kg_settings.get("long_session_hours", 13) or 13),
        "shift_count": shift_count,
        "summary": counts,
        "issues": issues,
        "next": {"step": step, "action": action, "label": label, "hint": hint},
        "last_close": {
            "period": kg_settings.get("last_closed_period") or "",
            "status": kg_settings.get("last_close_status") or "",
            "at": str(kg_settings.get("last_close_at") or ""),
        },
    }


def _period_filter(year, month):
    from kreativ_attendance.attendance.calendar_util import period_bounds
    start, end = period_bounds(year, month)
    return [["shift_date", ">=", start], ["shift_date", "<", end]]


def _summary_counts(year, month) -> dict:
    rows = frappe.get_all(
        SUMMARY_DOCTYPE,
        filters={"period_year": year, "period_month": month},
        fields=["status", "anomaly_count"],
    )
    return {
        "total": len(rows),
        "draft": sum(1 for r in rows if r.status == "Draft"),
        "reviewed": sum(1 for r in rows if r.status == "Reviewed"),
        "locked": sum(1 for r in rows if r.status == "Locked"),
        "with_anomalies": sum(1 for r in rows if (r.anomaly_count or 0) > 0),
    }


# ---------------------------------------------------------------------------
# Issue list, shaped for one-click fixing
# ---------------------------------------------------------------------------

@frappe.whitelist()
def month_issue_rows(year: int = None, month: int = None) -> list:
    """Flat, actionable issue list.

    Each row carries the Employee Checkin name where available, so the console
    can offer a Fix button that opens the exact punch to correct — instead of
    printing a name and a date the operator has to go hunting for.
    """
    frappe.only_for(ALLOWED_ROLES)
    year, month = int(year), int(month)

    from kreativ_attendance.attendance.quality import get_month_issues
    issues = get_month_issues(year, month)

    from kreativ_attendance.attendance.calendar_util import period_bounds
    p_start, p_end = period_bounds(year, month)
    month_from, month_to = str(p_start), str(frappe.utils.add_days(p_end, -1))

    rows = []
    for a in issues.get("anomalies", []):
        rows.append({
            "kind": "anomaly",
            "severity": "red",
            "employee": a["employee"],
            "employee_name": a.get("employee_name") or a["employee"],
            "date": str(a["shift_date"]),
            "what": a["status"],
            "detail": a.get("anomaly_reason") or "",
            "advice": _advice_for(a.get("anomaly_reason"), a["status"]),
            "shift": a["name"],
            # Navigation context. The console sends the operator to the FULL
            # punch list for this employee, not to one record: when a punch is
            # missing there is no record to open, and the surrounding punches
            # are what you need to see to work out what is wrong.
            "month_from": month_from,
            "month_to": month_to,
            "verified": 0,
            "can_verify": 0,
        })

    for s in issues.get("long_sessions", []):
        hours = round(float(s.get("worked_seconds") or 0) / 3600.0, 2) \
            if s.get("worked_seconds") is not None else s.get("worked_hours")
        rows.append({
            "kind": "long_session",
            "severity": "orange",
            "employee": s["employee"],
            "employee_name": s.get("employee_name") or s["employee"],
            "date": str(s["shift_date"]),
            "what": _("Long session: {0} hrs").format(hours),
            "detail": "",
            "advice": _("Usually a missed middle punch that merged two days. "
                        "If the hours are genuine, Verify it and the month close "
                        "will stop blocking on it."),
            "shift": s["name"],
            "month_from": month_from,
            "month_to": month_to,
            "verified": 0,
            "can_verify": 1,
        })

    for emp in issues.get("missing_standard_hours", []):
        rows.append({
            "kind": "config", "severity": "orange",
            "employee": emp,
            "employee_name": frappe.db.get_value("Employee", emp, "employee_name") or emp,
            "date": "", "what": _("No Working Hours set"), "detail": "",
            "advice": _("Set Working Hours on the Employee record. Without it "
                        "the system falls back to the default and Pay Days may be wrong."),
            "shift": None, "employee_link": emp,
            "verified": 0, "can_verify": 0,
        })

    for emp in issues.get("missing_holiday_list", []):
        rows.append({
            "kind": "config", "severity": "orange",
            "employee": emp,
            "employee_name": frappe.db.get_value("Employee", emp, "employee_name") or emp,
            "date": "", "what": _("No Holiday List"), "detail": "",
            "advice": _("Assign a Holiday List to this Employee. Weekly offs and "
                        "public holidays cannot be counted without it."),
            "shift": None, "employee_link": emp,
            "verified": 0, "can_verify": 0,
        })

    rows.sort(key=lambda r: (r["severity"] != "red", r["employee_name"], r["date"]))
    return rows


def _advice_for(reason: str, status: str) -> str:
    reason = (reason or "").lower()
    if "missing_checkout" in reason or status == "Missing Check-Out":
        return _("The employee punched IN but never punched OUT. Add the missing "
                 "OUT punch, or delete the stray IN if they never worked.")
    if "carryover" in reason:
        return _("An OUT punch with no matching IN — usually a night shift that "
                 "started last month. Normally safe to ignore.")
    if "break" in reason:
        return _("A Break key was pressed instead of Check In/Out. Correct the "
                 "punch type on the Employee Checkin record.")
    return _("Unpaired punch. Open it and correct the time or type.")


# ---------------------------------------------------------------------------
# Bulk actions — the click-count fix
# ---------------------------------------------------------------------------

@frappe.whitelist()
def review_summaries(year: int = None, month: int = None,
                     employees: str = None, only_clean: int = 1) -> dict:
    """Mark Draft summaries as Reviewed, in bulk.

    Reviewing 29 employees one form at a time was ~116 clicks. This is one.

    `only_clean=1` (the default) refuses to review any row that still has
    unresolved anomalies, because an anomaly means unpaid hours and a reviewed
    row is what unlocks payroll. Pass 0 to override deliberately.
    """
    frappe.only_for(ALLOWED_ROLES)
    year, month = int(year), int(month)
    only_clean = int(only_clean or 0)

    filters = {"period_year": year, "period_month": month, "status": "Draft"}
    if employees:
        names = frappe.parse_json(employees) if isinstance(employees, str) else employees
        if names:
            filters["employee"] = ["in", names]

    rows = frappe.get_all(SUMMARY_DOCTYPE, filters=filters,
                          fields=["name", "employee_name", "anomaly_count"])

    reviewed, skipped = 0, []
    for r in rows:
        if only_clean and (r.anomaly_count or 0) > 0:
            skipped.append(r.employee_name)
            continue
        doc = frappe.get_doc(SUMMARY_DOCTYPE, r.name)
        doc.status = "Reviewed"
        doc.save(ignore_permissions=True)
        reviewed += 1

    frappe.db.commit()
    return {"reviewed": reviewed, "skipped": skipped, "period": f"{year}-{month:02d}"}


@frappe.whitelist()
def unreview_summaries(year: int = None, month: int = None) -> dict:
    """Send Reviewed rows back to Draft so a rebuild can refresh them.

    Locked rows are never touched — those are payroll-final.
    """
    frappe.only_for(ALLOWED_ROLES)
    year, month = int(year), int(month)
    names = frappe.get_all(
        SUMMARY_DOCTYPE,
        filters={"period_year": year, "period_month": month, "status": "Reviewed"},
        pluck="name",
    )
    for n in names:
        frappe.db.set_value(SUMMARY_DOCTYPE, n, "status", "Draft", update_modified=False)
    frappe.db.commit()
    return {"unreviewed": len(names)}


@frappe.whitelist()
def build_summary(year: int = None, month: int = None) -> dict:
    """Build/refresh the monthly summary rows (Draft only)."""
    frappe.only_for(ALLOWED_ROLES)
    from kreativ_attendance.attendance.summary import build_month
    return build_month(int(year), int(month))


@frappe.whitelist()
def summary_rows(year: int = None, month: int = None) -> list:
    """Summary table for the console, with a per-row warning flag."""
    frappe.only_for(ALLOWED_ROLES)
    year, month = int(year), int(month)
    rows = frappe.get_all(
        SUMMARY_DOCTYPE,
        filters={"period_year": year, "period_month": month},
        fields=["name", "employee", "employee_name", "status", "standard_hours",
                "total_hours", "wd", "wo", "ph", "pd", "pay_days",
                "ot_hours", "ot_amount", "anomaly_count", "hours_source",
                "days_in_month"],
        order_by="employee_name",
    )
    for r in rows:
        warn = []
        if (r.anomaly_count or 0) > 0:
            warn.append(_("{0} unresolved anomaly(s) — hours are understated")
                        .format(r.anomaly_count))
        if r.hours_source and r.hours_source != "Employee.working_hours":
            warn.append(_("Using the default standard hours"))
        if (r.pay_days or 0) > (r.days_in_month or 0):
            warn.append(_("Pay Days exceeds days in month"))
        r["warnings"] = warn
    return rows


@frappe.whitelist()
def verified_long_sessions(year: int = None, month: int = None) -> list:
    """Long sessions an operator has accepted as genuine.

    Shown separately from open issues so the decision stays visible and
    reversible, rather than silently disappearing from the screen.
    """
    frappe.only_for(ALLOWED_ROLES)
    year, month = int(year), int(month)

    from kreativ_attendance.attendance.calendar_util import period_bounds
    start, end = period_bounds(year, month)

    rows = frappe.get_all(
        SHIFT_DOCTYPE,
        filters=[
            ["shift_date", ">=", start],
            ["shift_date", "<", end],
            ["long_session_verified", "=", 1],
        ],
        fields=["name", "employee", "employee_name", "shift_date",
                "worked_seconds", "verification_note", "verified_by", "verified_at"],
        order_by="shift_date",
    )
    for r in rows:
        r["hours"] = round(float(r.get("worked_seconds") or 0) / 3600.0, 2)
    return rows
