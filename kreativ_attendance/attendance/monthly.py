"""Monthly auto-close — the 1st-of-the-month automation.

Runs via scheduler on the 1st of every month (see hooks.py cron) and
processes the PREVIOUS month end-to-end:

    1. Recalculate all Employee Shifts for the month from raw checkins
       (payroll-locked employees are skipped automatically by the service).
    2. Run the quality gate (quality.get_month_issues).
    3a. If BLOCKED  -> notify HR/admin (WhatsApp + Error Log + Notification
        Log) with the full issue list. Nothing is synced. HR fixes the
        punches in Employee Checkin, shifts rebuild automatically, and then
        HR re-runs the close from the dashboard (or calls run_monthly_close).
    3b. If CLEAN    -> sync the month to HRMS (Attendance + Overtime
        Additional Salary) and, if enabled, create a DRAFT Payroll Entry so
        HR only has to review and submit to generate salary slips.

Salary slips are never auto-SUBMITTED — money leaving the company always
gets one human glance. (Set kreativ_auto_submit_payroll = 1 in
site_config.json only if you truly want zero-touch.)

Configuration (site_config.json, all optional):
    kreativ_monthly_auto_close       0/1, default 1  (master switch)
    kreativ_auto_create_payroll      0/1, default 1  (create draft Payroll Entry)
    kreativ_auto_submit_payroll      0/1, default 0  (submit it + salary slips)
    kreativ_close_notify_admin       0/1, default 1  (WhatsApp the result)
"""
from datetime import date, timedelta

import frappe
from frappe.utils import today, getdate

from kreativ_attendance.attendance import quality
from kreativ_attendance.attendance.service import recalculate_period
from kreativ_attendance.attendance.hrms import sync_month_to_hrms


# ---------------------------------------------------------------------------
# Scheduler entry point
# ---------------------------------------------------------------------------

def monthly_close():
    """Cron entry point (1st of month). Closes the previous month."""
    if not frappe.conf.get("kreativ_monthly_auto_close", 1):
        return

    t = getdate(today())
    prev_last_day = date(t.year, t.month, 1) - timedelta(days=1)
    run_monthly_close(prev_last_day.year, prev_last_day.month)


@frappe.whitelist()
def run_monthly_close(year: int = None, month: int = None, force: int = 0) -> dict:
    """Close a month: recalc -> quality gate -> HRMS sync -> draft Payroll Entry.

    Also whitelisted so HR can re-run it from the dashboard after fixing
    punches mid-month-close. `force=1` overrides the quality gate (audited).
    """
    if frappe.session.user != "Administrator" and not frappe.flags.in_scheduler:
        frappe.only_for(("System Manager", "HR Manager"))
    if not year or not month:
        frappe.throw("Both year and month are required")
    year, month = int(year), int(month)
    period = f"{year}-{month:02d}"
    result = {"period": period}

    # --- 1. Recalculate the month from raw checkins -----------------------
    try:
        recalc = recalculate_period(year, month)
        result["recalculate"] = recalc
    except Exception as e:
        _notify(f"❌ Monthly close {period}: recalculation FAILED — {e}")
        frappe.log_error(title=f"Monthly close {period}: recalc failed",
                         message=frappe.get_traceback())
        result["status"] = "recalc_failed"
        return result

    # --- 2. Quality gate ---------------------------------------------------
    issues = quality.get_month_issues(year, month)
    result["issues"] = {
        "anomalies": len(issues["anomalies"]),
        "long_sessions": len(issues["long_sessions"]),
        "break_punches": len(issues["break_punches"]),
        "missing_standard_hours": issues["missing_standard_hours"],
    }

    if issues["blocking"] and not int(force or 0):
        text = (
            f"⚠️ Monthly close {period} BLOCKED — fix these punches, then "
            f"re-run close from the Attendance Dashboard:\n\n"
            + quality.format_issues(issues)
        )
        _notify(text)
        _notification_log(f"Attendance close {period} blocked", text)
        frappe.log_error(title=f"Monthly close {period} blocked by quality gate",
                         message=quality.format_issues(issues))
        result["status"] = "blocked"
        return result

    if issues["blocking"] and int(force or 0):
        frappe.log_error(
            title=f"Monthly close {period}: quality gate FORCED by {frappe.session.user}",
            message=quality.format_issues(issues),
        )

    # --- 3. Sync to HRMS (Attendance + Overtime) ---------------------------
    try:
        sync = sync_month_to_hrms(year, month)
        result["hrms_sync"] = sync
    except Exception as e:
        _notify(f"❌ Monthly close {period}: HRMS sync FAILED — {e}")
        frappe.log_error(title=f"Monthly close {period}: HRMS sync failed",
                         message=frappe.get_traceback())
        result["status"] = "hrms_sync_failed"
        return result

    # --- 4. Draft Payroll Entry -------------------------------------------
    payroll_msg = "skipped (kreativ_auto_create_payroll=0)"
    if frappe.conf.get("kreativ_auto_create_payroll", 1):
        try:
            pe = _create_payroll_entry(year, month)
            result["payroll_entry"] = pe
            payroll_msg = pe or "not created"
        except Exception as e:
            payroll_msg = f"FAILED ({e}) — create it manually from the dashboard"
            frappe.log_error(title=f"Monthly close {period}: Payroll Entry failed",
                             message=frappe.get_traceback())

    ok = (
        f"✅ Monthly close {period} complete.\n"
        f"Shifts: {result['recalculate'].get('paired', 0)} paired, "
        f"{result['recalculate'].get('anomalies', 0)} anomaly rows.\n"
        f"Attendance created: {result['hrms_sync'].get('attendance_created', 0)}, "
        f"Overtime entries: {result['hrms_sync'].get('overtime_created', 0)}.\n"
        f"Payroll Entry: {payroll_msg}"
    )
    if issues["missing_standard_hours"]:
        ok += ("\n⚠️ Using 8h default (set Employee Standard Hours): "
               + ", ".join(issues["missing_standard_hours"]))
    _notify(ok)
    _notification_log(f"Attendance close {period} complete", ok)
    result["status"] = "ok"
    frappe.db.commit()
    return result


# ---------------------------------------------------------------------------
# Payroll Entry creation
# ---------------------------------------------------------------------------

def _create_payroll_entry(year: int, month: int):
    """Create a DRAFT Payroll Entry for the month and pull employees in.

    Returns the Payroll Entry name, or None if one already exists for the
    period. Submission (which generates the salary slips) is left to HR
    unless kreativ_auto_submit_payroll=1.
    """
    start = date(year, month, 1)
    end = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)

    existing = frappe.db.get_value(
        "Payroll Entry",
        {"start_date": start, "end_date": end, "docstatus": ["<", 2]},
        "name",
    )
    if existing:
        return existing

    company = frappe.defaults.get_global_default("company") or \
        frappe.db.get_value("Company", {}, "name")

    pe = frappe.new_doc("Payroll Entry")
    pe.company = company
    pe.posting_date = end
    pe.payroll_frequency = "Monthly"
    pe.start_date = start
    pe.end_date = end
    pe.exchange_rate = 1
    pe.payroll_payable_account = frappe.db.get_value(
        "Company", company, "default_payroll_payable_account"
    )
    pe.currency = frappe.db.get_value("Company", company, "default_currency")

    # Pull in every employee with an active Salary Structure Assignment
    pe.set_start_end_dates()
    pe.fill_employee_details()
    if not pe.employees:
        frappe.log_error(
            title=f"Monthly close: Payroll Entry {year}-{month:02d} has no employees",
            message="No active Salary Structure Assignments found for the period. "
                    "Assign salary structures, then create the Payroll Entry manually.",
        )
        return None

    pe.insert(ignore_permissions=True)

    if frappe.conf.get("kreativ_auto_submit_payroll", 0):
        pe.submit()
        pe.create_salary_slips()

    return pe.name


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _notify(text: str):
    """Best-effort WhatsApp to the admin chat via the existing OpenWA setup.
    Never raises — monthly close must not die because WhatsApp is down."""
    if not frappe.conf.get("kreativ_close_notify_admin", 1):
        return
    try:
        from kreativ_attendance.attendance.whatsapp import _post, ADMIN_CHAT_ID
        settings = frappe.get_cached_doc("OpenWA Settings")
        if settings.enabled:
            _post(settings, "send-text", {"chatId": ADMIN_CHAT_ID, "text": text})
    except Exception:
        pass  # Error Log + Notification Log still carry the message


def _notification_log(subject: str, text: str):
    """In-app bell notification for every HR Manager. Best-effort."""
    try:
        hr_users = frappe.get_all(
            "Has Role",
            filters={"role": "HR Manager", "parenttype": "User"},
            pluck="parent",
        )
        for user in set(hr_users):
            if user in ("Administrator", "Guest"):
                continue
            frappe.get_doc({
                "doctype": "Notification Log",
                "for_user": user,
                "type": "Alert",
                "subject": subject,
                "email_content": text.replace("\n", "<br>"),
            }).insert(ignore_permissions=True)
    except Exception:
        pass
