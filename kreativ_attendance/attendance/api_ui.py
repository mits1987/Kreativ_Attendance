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


# ---------------------------------------------------------------------------
# Daily check-in view
# ---------------------------------------------------------------------------

@frappe.whitelist()
def daily_checkins(date: str = None, employee: str = None) -> dict:
    """Get all check-in records for a specific date.
    
    Returns a list of employees with their check-in/out times, hours, and status.
    """
    frappe.only_for(ALLOWED_ROLES)
    if not date:
        frappe.throw(_("Date is required"))

    from kreativ_attendance.attendance.calendar_util import period_bounds
    
    # Get all active employees
    emp_filters = {"status": "Active"}
    if employee:
        emp_filters["name"] = ["like", f"%{employee}%"]
    
    employees = frappe.get_all("Employee", filters=emp_filters, 
                               fields=["name", "employee_name", "department", "default_shift", "user_id"])
    
    # Get checkins for the date
    checkins = frappe.get_all("Employee Checkin",
        filters=[
            ["time", ">=", f"{date} 00:00:00"],
            ["time", "<=", f"{date} 23:59:59"],
        ],
        fields=["employee", "time", "log_type", "device_id", "name"],
        order_by="employee, time"
    )
    
    # Group checkins by employee
    checkins_by_emp = {}
    for c in checkins:
        if c.employee not in checkins_by_emp:
            checkins_by_emp[c.employee] = []
        checkins_by_emp[c.employee].append(c)
    
    # Build rows
    rows = []
    stats = {"total_employees": 0, "checked_in": 0, "not_checked_in": 0, "missing_checkout": 0}
    
    for emp in employees:
        emp_checkins = checkins_by_emp.get(emp.name, [])
        
        # Find first IN and last OUT
        in_punches = [c for c in emp_checkins if c.log_type == "IN"]
        out_punches = [c for c in emp_checkins if c.log_type == "OUT"]
        
        check_in = in_punches[0]["time"] if in_punches else None
        check_out = out_punches[-1]["time"] if out_punches else None
        
        # Calculate hours
        work_hours = "—"
        if check_in and check_out:
            from datetime import datetime
            dt_in = datetime.fromisoformat(str(check_in).replace("Z", "+00:00"))
            dt_out = datetime.fromisoformat(str(check_out).replace("Z", "+00:00"))
            diff = dt_out - dt_in
            total_seconds = int(diff.total_seconds())
            h = total_seconds // 3600
            m = (total_seconds % 3600) // 60
            work_hours = f"{h:02d}:{m:02d}"
        
        # Determine status
        if check_in and check_out:
            status = "Present"
            stats["checked_in"] += 1
        elif check_in and not check_out:
            status = "Missing Check-out"
            stats["missing_checkout"] += 1
        else:
            status = "Absent"
            stats["not_checked_in"] += 1
        
        stats["total_employees"] += 1
        
        # Get device info
        devices = list(set(c.device_id for c in emp_checkins if c.device_id))
        
        rows.append({
            "employee": emp.name,
            "employee_name": emp.employee_name,
            "department": emp.department,
            "shift": emp.default_shift,
            "check_in": check_in,
            "check_out": check_out,
            "work_hours": work_hours,
            "status": status,
            "device": ", ".join(devices),
        })
    
    return {"rows": rows, "stats": stats}


# ---------------------------------------------------------------------------
# Employee self-service check-in history (for ESS PWA)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def ess_my_checkins(date: str = None) -> dict:
    """Get check-in history for the current logged-in employee.
    
    This endpoint is designed for Employee Self Service - it uses the session user
    to determine the employee and doesn't require HR Manager role.
    """
    if not date:
        frappe.throw(_("Date is required"))
    
    # Get employee for current user
    emp = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, 
                              ["name", "employee_name", "department", "default_shift"])
    if not emp:
        return {"logs": [], "message": "No employee linked to this user"}
    
    emp_name, emp_emp_name, emp_dept, emp_shift = emp
    
    # Get checkins for the date
    checkins = frappe.get_all("Employee Checkin",
        filters=[
            ["employee", "=", emp_name],
            ["time", ">=", f"{date} 00:00:00"],
            ["time", "<=", f"{date} 23:59:59"],
        ],
        fields=["employee", "employee_name", "time", "log_type", "device_id", "latitude", "longitude"],
        order_by="time asc"
    )
    
    return {"logs": checkins, "employee": emp_name, "employee_name": emp_emp_name}


@frappe.whitelist()
def employee_directory() -> list:
    """Get employee directory for ESS PWA."""
    return frappe.get_all(
        "Employee",
        filters={"status": "Active"},
        fields=["name", "employee_name", "designation", "department", "cell_number", "image", "user_id"],
        order_by="employee_name",
        limit=100,
    )


@frappe.whitelist()
def get_attendance_details_dashboard() -> dict:
    """Get attendance dashboard details for current employee (for ESS PWA)."""
    emp = _get_current_employee()
    if not emp:
        return {}

    from datetime import date
    from kreativ_attendance.attendance.api_ui import daily_checkins

    today = date.today().isoformat()
    checkin_data = daily_checkins(date=today, employee=emp.name)

    return {
        "total_days": checkin_data.get("stats", {}).get("total_employees", 0),
        "present": checkin_data.get("stats", {}).get("checked_in", 0),
        "absent": checkin_data.get("stats", {}).get("not_checked_in", 0),
        "late": 0,
        "missing_checkout": checkin_data.get("stats", {}).get("missing_checkout", 0),
        "logs": checkin_data.get("rows", []),
    }


@frappe.whitelist()
def ess_checkin(log_type: str) -> dict:
    """Create a check-in or check-out for current employee."""
    emp = _get_current_employee()
    if not emp:
        frappe.throw(_("No employee linked to current user"))

    from kreativ_attendance.attendance.service import create_checkin

    log_type = log_type.upper()
    if log_type not in ("IN", "OUT"):
        frappe.throw(_("Invalid log type"))

    result = create_checkin(employee=emp.name, log_type=log_type)

    return {
        "success": True,
        "message": f"Checked {log_type} successfully",
        "time": result.get("time"),
        "log_type": result.get("log_type"),
    }


@frappe.whitelist()
def ess_get_dashboard() -> dict:
    """Get dashboard data for current employee (ESS)."""
    emp = _get_current_employee()
    if not emp:
        return {}

    from kreativ_attendance.attendance.api_ui import daily_checkins
    from datetime import date

    today = date.today().isoformat()
    checkin_data = daily_checkins(date=today, employee=emp.name)

    leave_balance = _get_leave_balance(emp.name)
    notice_board = _get_notice_board()

    return {
        "company_name": emp.company or "Kreativ Gravures",
        "employee_name": emp.employee_name,
        "total_days": checkin_data.get("stats", {}).get("total_employees", 0),
        "present": checkin_data.get("stats", {}).get("checked_in", 0),
        "absent": checkin_data.get("stats", {}).get("not_checked_in", 0),
        "late": 0,
        "leave_balance": leave_balance,
        "notice_board": notice_board,
    }


@frappe.whitelist()
def ess_get_leave_balance_dashboard() -> list:
    """Get leave balance for current employee."""
    emp = _get_current_employee()
    return _get_leave_balance(emp.name) if emp else []


@frappe.whitelist()
def ess_get_leave_application_list() -> list:
    """Get leave applications for current employee."""
    emp = _get_current_employee()
    if not emp:
        return []

    return frappe.get_all(
        "Leave Application",
        filters={"employee": emp.name, "docstatus": ["!=", 2]},
        fields=["name", "leave_type", "from_date", "to_date", "total_leave_days", "status"],
        order_by="from_date desc",
        limit=20,
    )


@frappe.whitelist()
def ess_get_expense_list() -> list:
    """Get expense claims for current employee."""
    emp = _get_current_employee()
    if not emp:
        return []

    claims = frappe.get_all(
        "Expense Claim",
        filters={"employee": emp.name, "docstatus": ["!=", 2]},
        fields=["name", "total_sanctioned_amount as total_amount", "status", "posting_date", "remark"],
        order_by="posting_date desc",
        limit=20,
    )
    # Get expense types from child table for each claim
    for claim in claims:
        claim.expense_type = frappe.get_all(
            "Expense Claim Detail",
            filters={"parent": claim.name},
            pluck="expense_type",
        )
        claim.expense_type = ", ".join(claim.expense_type) if claim.expense_type else "Various"
    return claims


@frappe.whitelist()
def ess_get_salary_sllip() -> list:
    """Get salary slips for current employee."""
    emp = _get_current_employee()
    if not emp:
        return []

    slips = frappe.get_all(
        "Salary Slip",
        filters={"employee": emp.name, "docstatus": 1},
        fields=["name", "start_date", "end_date", "net_pay", "total_deduction"],
        order_by="start_date desc",
        limit=12,
    )
    # Add month field for display
    for slip in slips:
        if slip.start_date:
            slip.month = slip.start_date.strftime("%b %Y")
    return slips


@frappe.whitelist()
def ess_get_task_list() -> list:
    """Get tasks assigned to current employee."""
    emp = _get_current_employee()
    if not emp:
        return []

    return frappe.get_all(
        "Task",
        filters={"_assign": ["like", f"%{frappe.session.user}%"], "status": ["!=", "Completed"]},
        fields=["name", "subject", "description", "exp_end_date", "status"],
        order_by="exp_end_date asc",
        limit=20,
    )


def _get_current_employee() -> dict | None:
    """Get employee record for current session user."""
    emp = frappe.db.get_value(
        "Employee",
        {"user_id": frappe.session.user},
        ["name", "employee_name", "company", "department", "designation", "user_id", "cell_number", "image"],
        as_dict=True,
    )
    return emp


def _get_leave_balance(employee: str) -> list:
    """Get leave balance for employee."""
    balances = frappe.get_all(
        "Leave Ledger Entry",
        filters={"employee": employee, "is_carry_forward": 0},
        fields=["leave_type", "leaves", "from_date", "to_date", "transaction_type"],
    )

    # Aggregate by leave type - leaves field is positive for allocation, negative for consumption
    # transaction_type: "Leave Allocation" = credit, "Leave Application" = debit, "Leave Encashment" = debit
    agg = {}
    for b in balances:
        lt = b.leave_type
        if lt not in agg:
            agg[lt] = {"leave_type": lt, "total_allocated": 0, "leaves_taken": 0, "remaining": 0}
        leaves_val = float(b.leaves or 0)
        if leaves_val > 0:
            agg[lt]["total_allocated"] += leaves_val
        else:
            agg[lt]["leaves_taken"] += abs(leaves_val)

    for v in agg.values():
        v["remaining"] = v["total_allocated"] - v["leaves_taken"]

    return list(agg.values())


def _get_notice_board() -> list:
    """Get recent notices/announcements."""
    return frappe.get_all(
        "Comment",
        filters={},
        fields=["subject", "content", "creation"],
        order_by="creation desc",
        limit=5,
    )
