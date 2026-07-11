"""Document-event hooks for Employee Checkin and Salary Slip."""
import frappe
from frappe.utils import get_datetime
from frappe.utils.background_jobs import enqueue

from kreativ_attendance.attendance.service import recalculate_around


def on_checkin_updated(doc, method=None):
    """Triggered after any Employee Checkin save/edit — rebuild that
    employee's shifts for the affected month(s)."""
    _enqueue_recalc(doc)


def on_checkin_trashed(doc, method=None):
    """Triggered when an Employee Checkin is DELETED (e.g. HR removes a
    double/bogus punch). Shifts must rebuild just like on edit."""
    _enqueue_recalc(doc)


def on_checkin_created(doc, method=None):
    """after_insert only — WhatsApp-notify NEW punches, never edits/recalcs.

    The enqueue is wrapped in try/except because after_insert has NO
    try/except in Frappe core (document.py line 482). An uncaught exception
    here propagates through insert() → create_employee_checkin() (zkteco_sync),
    and even though the checkin row is already db_inserted by then, the
    transaction can still be rolled back depending on error path. Falling
    through silently means retry_missed_notifications (cron */10) will pick
    the checkin up later — never lose a notification to a transient enqueue
    failure.
    """
    if not frappe.db.get_single_value("OpenWA Settings", "enabled"):
        return
    try:
        enqueue(
            "kreativ_attendance.attendance.whatsapp.notify_checkin",
            queue="short",
            timeout=60,
            checkin_name=doc.name,
            enqueue_after_commit=True,
        )
    except Exception:
        frappe.log_error(
            title="WhatsApp enqueue failed on checkin creation",
            message=(
                f"After-commit enqueue of notify_checkin failed for {doc.name}. "
                "retry_missed_notifications will retry within 10 minutes "
                "(only filters whatsapp_sent in [0, None])."
            ),
        )


def _enqueue_recalc(doc):
    # Deduplicate per employee-month: a 5-minute device sync inserts many
    # checkins for the same employee, and each full-month rebuild is
    # idempotent — one queued job per (employee, month) is enough. Without
    # this, N punches enqueue N identical rebuilds that can also race each
    # other on parallel workers (duplicate Employee Shift rows).
    #
    # Passes employee+time (not the checkin name) so the job also works
    # after the checkin row is gone (delete case).
    t = get_datetime(doc.time)
    try:
        enqueue(
            "kreativ_attendance.attendance.service.recalculate_around",
            queue="default",
            timeout=120,
            employee=doc.employee,
            time=str(t),
            now=False,
            enqueue_after_commit=True,
            deduplicate=True,
            job_id=f"gravures-shift-recalc-{doc.employee}-{t.year}-{t.month:02d}",
        )
    except Exception:
        # If enqueue fails (no worker available), run inline
        recalculate_around(doc.employee, t)


def on_salary_slip_whatsapp(doc, method=None):
    """Send the submitted Salary Slip PDF to the employee via WhatsApp
    (if enabled in OpenWA Settings)."""
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not (settings.enabled and settings.send_salary_slips):
        return
    enqueue(
        "kreativ_attendance.attendance.whatsapp.send_salary_slip",
        queue="short",
        timeout=120,
        salary_slip=doc.name,
        enqueue_after_commit=True,
    )


def on_salary_slip_submit(doc, method=None):
    """Triggered when a Salary Slip is submitted/finalized.

    Creates an Employee Shift Lock for the employee-month range, then
    locks all Employee Shift records in that period so they cannot be
    silently edited after payroll sign-off.

    Idempotent: if a lock already exists (unlocked_at is NULL), it's a no-op.
    """
    if not doc.employee or not doc.start_date:
        return

    from kreativ_attendance.attendance.lock import EmployeeShiftLock
    from frappe.utils import getdate
    import datetime

    start_date = getdate(doc.start_date)
    year = start_date.year
    month = start_date.month

    # Check if lock already exists (idempotency)
    existing = frappe.db.get_value(
        "Employee Shift Lock",
        [
            ["employee", "=", doc.employee],
            ["period_year", "=", year],
            ["period_month", "=", month],
            ["unlocked_at", "is", "not set"],
        ],
        "name",
    )
    if existing:
        return  # already locked

    # Create the lock
    lock_doc = EmployeeShiftLock.lock_period(
        employee=doc.employee,
        year=year,
        month=month,
        salary_slip=doc.name,
        locked_by=frappe.session.user,
        reason=f"Salary Slip {doc.name} submitted for {year}-{month:02d}",
    )

    # Apply lock flag to all Employee Shift records in this period
    period_start = datetime.date(year, month, 1)
    if month == 12:
        period_end = datetime.date(year + 1, 1, 1)
    else:
        period_end = datetime.date(year, month + 1, 1)

    shifts = frappe.get_all(
        "Employee Shift",
        filters=[
            ["employee", "=", doc.employee],
            ["shift_date", ">=", period_start],
            ["shift_date", "<", period_end],
        ],
        pluck="name",
    )
    for shift_name in shifts:
        frappe.db.set_value(
            "Employee Shift",
            shift_name,
            {"locked": 1, "lock_period": lock_doc.name},
            update_modified=False,
        )

    frappe.db.commit()
