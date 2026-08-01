"""Monthly close orchestration.

Runs on the 2nd of the following month by default (configurable in KG Attendance
Settings). The previous schedule of 02:30 on the 1st was too early: an employee
starting a night shift at 20:00 on the last day of the month has not punched out
yet, so pairing recorded an unpaired IN, the quality gate saw a blocking anomaly,
and the close aborted every month that had a night shift on the final day.

Sequence:
    1. recalculate shifts for the period
    2. run the quality gate  -> abort and alert if blocking
    3. build KG Monthly Attendance Summary rows
    4. write Attendance + Overtime to HRMS      [skipped in Shadow Mode]
    5. create a draft Payroll Entry             [skipped in Shadow Mode]

Steps 4 and 5 are the only ones that write payroll documents. While Shadow Mode
is on, this job is completely non-destructive: it recalculates, checks, reports
and stops.
"""
import frappe
from frappe.utils import getdate, now_datetime

from kreativ_attendance.attendance import settings as kg_settings
from kreativ_attendance.attendance.calendar_util import period_bounds


# ---------------------------------------------------------------------------
# Scheduler entry point
# ---------------------------------------------------------------------------

def scheduled_monthly_close():
    """Hourly cron. Fires the close when the configured day and hour match.

    Running hourly rather than as a fixed monthly cron makes the schedule
    editable from the settings page without a deploy, and lets the close retry
    itself: if the 2nd was blocked by an uncorrected punch, the 3rd and 4th will
    try again automatically and succeed once HR fixes it.
    """
    if not int(kg_settings.get("monthly_auto_close", 1) or 0):
        return

    now = now_datetime()
    close_day = int(kg_settings.get("close_day", 2) or 2)
    close_hour = int(kg_settings.get("close_hour", 10) or 10)

    # Retry window: the configured day plus the two following days.
    if not (close_day <= now.day <= close_day + 2):
        return
    if now.hour != close_hour:
        return

    year, month = previous_period(now.date())
    period = f"{year}-{month:02d}"

    if kg_settings.already_closed(period):
        return

    monthly_close(year, month)


def previous_period(today=None):
    """Return (year, month) of the month before `today`.

    Written so it is correct on any day of the month, not only the 1st.
    """
    today = getdate(today or frappe.utils.nowdate())
    first_of_this_month = today.replace(day=1)
    last_of_prev = frappe.utils.add_days(first_of_this_month, -1)
    return last_of_prev.year, last_of_prev.month


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------

def monthly_close(year: int, month: int) -> dict:
    from kreativ_attendance.attendance.service import recalculate_period
    from kreativ_attendance.attendance.quality import get_month_issues, format_issues
    from kreativ_attendance.attendance.summary import build_month
    from kreativ_attendance.attendance.hrms import sync_month_to_hrms

    year, month = int(year), int(month)
    period = f"{year}-{month:02d}"
    shadow = kg_settings.is_shadow_mode()
    result = {"period": period, "shadow_mode": shadow, "status": "running"}

    banner = "[SHADOW MODE] " if shadow else ""

    # --- 1. Recalculate ----------------------------------------------------
    try:
        result["recalculate"] = recalculate_period(year, month)
    except Exception as e:
        _notify(f"{banner}Monthly close {period}: recalculation FAILED - {e}")
        frappe.log_error(title=f"Monthly close {period}: recalculation failed",
                         message=frappe.get_traceback())
        result["status"] = "recalculate_failed"
        kg_settings.set_state(period, result["status"])
        return result

    # --- 2. Quality gate ---------------------------------------------------
    issues = get_month_issues(year, month)
    result["issues"] = issues
    if issues.get("blocking"):
        msg = (f"{banner}Monthly close {period} BLOCKED - payroll data is not clean.\n\n"
               + format_issues(issues)
               + "\n\nFix the punches, then the close will retry automatically "
                 "tomorrow at the same hour.")
        _notify(msg)
        _notification_log(f"Attendance close {period} BLOCKED", msg)
        result["status"] = "blocked"
        kg_settings.set_state(period, result["status"])
        return result

    # --- 3. Monthly summary ------------------------------------------------
    try:
        result["summary"] = build_month(year, month)
    except Exception as e:
        _notify(f"{banner}Monthly close {period}: summary build FAILED - {e}")
        frappe.log_error(title=f"Monthly close {period}: summary build failed",
                         message=frappe.get_traceback())
        result["status"] = "summary_failed"
        kg_settings.set_state(period, result["status"])
        return result

    # --- 4. HRMS sync (payroll write) --------------------------------------
    try:
        result["hrms_sync"] = sync_month_to_hrms(year, month)
    except Exception as e:
        _notify(f"{banner}Monthly close {period}: HRMS sync FAILED - {e}")
        frappe.log_error(title=f"Monthly close {period}: HRMS sync failed",
                         message=frappe.get_traceback())
        result["status"] = "hrms_sync_failed"
        kg_settings.set_state(period, result["status"])
        return result

    # --- 5. Draft Payroll Entry --------------------------------------------
    payroll_msg = "skipped (Shadow Mode)" if shadow else "skipped (Create Draft Payroll Entry is off)"
    if not shadow and int(kg_settings.get("auto_create_payroll", 0) or 0):
        try:
            pe = _create_payroll_entry(year, month)
            result["payroll_entry"] = pe
            payroll_msg = pe or "not created"
        except Exception as e:
            payroll_msg = f"FAILED ({e}) - create it manually from the dashboard"
            frappe.log_error(title=f"Monthly close {period}: Payroll Entry failed",
                             message=frappe.get_traceback())

    # --- Report ------------------------------------------------------------
    s = result["summary"]
    lines = [
        f"{banner}Monthly close {period} complete.",
        f"Shifts: {result['recalculate'].get('paired', 0)} paired, "
        f"{result['recalculate'].get('anomalies', 0)} anomaly rows.",
        f"Summaries: {s['created']} created, {s['updated']} updated, "
        f"{s['preserved']} already reviewed/locked.",
    ]
    if shadow:
        lines.append("No payroll documents were written (Shadow Mode).")
        lines.append("Review the KG Monthly Attendance Summary list and compare "
                     "against your salary sheet.")
    else:
        h = result["hrms_sync"]
        if h.get("skipped"):
            lines.append(h.get("message", "HRMS sync skipped."))
        else:
            lines.append(f"Attendance created: {h.get('attendance_created', 0)}, "
                         f"Overtime rows: {h.get('overtime_created', 0)}.")
        lines.append(f"Payroll Entry: {payroll_msg}")
        lines.append("Reminder: enter the monthly Production Bonus (Incentive) as "
                     "Additional Salary before submitting payroll.")

    if s.get("using_default_standard_hours"):
        lines.append("WARNING - no Working Hours set, using the settings default for: "
                     + ", ".join(s["using_default_standard_hours"]))
    if issues.get("missing_holiday_list"):
        lines.append("WARNING - no Holiday List, so WD/WO/PH may be wrong for: "
                     + ", ".join(issues["missing_holiday_list"]))

    ok = "\n".join(lines)
    _notify(ok)
    _notification_log(f"Attendance close {period} complete", ok)
    result["status"] = "ok"
    kg_settings.set_state(period, "ok")
    return result


# ---------------------------------------------------------------------------
# Payroll Entry
# ---------------------------------------------------------------------------

def _create_payroll_entry(year: int, month: int):
    """Create a DRAFT Payroll Entry for the month and pull employees in."""
    start, end = period_bounds(year, month)
    last_day = frappe.utils.add_days(end, -1)

    existing = frappe.db.get_value(
        "Payroll Entry",
        {"start_date": start, "end_date": last_day, "docstatus": ["<", 2]},
        "name",
    )
    if existing:
        return existing

    company = frappe.defaults.get_global_default("company")
    if not company:
        frappe.throw(
            "No default Company is set. Set one in Global Defaults, or turn off "
            "'Create Draft Payroll Entry' in KG Attendance Settings."
        )

    pe = frappe.get_doc({
        "doctype": "Payroll Entry",
        "company": company,
        "posting_date": last_day,
        "start_date": start,
        "end_date": last_day,
        "payroll_frequency": "Monthly",
        "exchange_rate": 1,
    })
    pe.insert(ignore_permissions=True)
    try:
        pe.fill_employee_details()
        pe.save(ignore_permissions=True)
    except Exception:
        frappe.log_error(title=f"Payroll Entry {pe.name}: could not fill employees",
                         message=frappe.get_traceback())

    if int(kg_settings.get("auto_submit_payroll", 0) or 0):
        pe.submit()

    return pe.name


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def _notify(message: str):
    """Send an admin alert through the notification platform.

    FIXED: this previously imported kreativ_attendance.attendance.whatsapp, a
    module removed during the notification consolidation, inside a bare
    `except: pass`. The BLOCKED alert therefore failed silently -- the one
    message that most needed to arrive. Failures are now logged.
    """
    if not int(kg_settings.get("notify_admin", 1) or 0):
        return
    try:
        from kreativ_notification.notification.send import send_text_via_whatsapp
        send_text_via_whatsapp(
            to=kg_settings.get("admin_whatsapp") or None,
            message=message,
            source_doctype="KG Attendance Settings",
        )
    except Exception:
        frappe.log_error(
            title="KG Attendance: monthly close alert could not be sent",
            message=f"{message}\n\n{frappe.get_traceback()}",
        )


def _notification_log(subject: str, message: str):
    """Always leave a desk Notification Log, even if WhatsApp is down."""
    try:
        for user in frappe.get_all(
            "Has Role", filters={"role": "HR Manager", "parenttype": "User"},
            pluck="parent", distinct=True,
        ):
            frappe.get_doc({
                "doctype": "Notification Log",
                "subject": subject,
                "email_content": f"<pre>{frappe.utils.escape_html(message)}</pre>",
                "for_user": user,
                "type": "Alert",
                "document_type": "KG Attendance Settings",
            }).insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(title="KG Attendance: notification log failed",
                         message=frappe.get_traceback())
