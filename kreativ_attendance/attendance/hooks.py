"""Document-event hooks for Employee Checkin and Salary Slip.

CHANGED: WhatsApp notification handlers REMOVED. They now live in
kreativ_notification/hooks.py (on_checkin_created, on_salary_slip_whatsapp).
This app owns ONLY shift recalculation and payroll lock.
"""
import frappe
from frappe.utils import get_datetime
from frappe.utils.background_jobs import enqueue

from kreativ_attendance.attendance.service import recalculate_period


def on_checkin_updated(doc, method=None):
    """Triggered after any Employee Checkin save/edit — rebuild that
    employee's shifts for the affected month(s)."""
    _enqueue_recalc(doc)


def on_checkin_trashed(doc, method=None):
    """Triggered when an Employee Checkin is DELETED (e.g. HR removes a
    double/bogus punch). Shifts must rebuild just like on edit."""
    _enqueue_recalc(doc)


def _enqueue_recalc(doc):
    # Deduplicate per employee-month: a 5-minute device sync inserts many
    # checkins for the same employee, and each full-month rebuild is
    # idempotent — one queued job per (employee, month) is enough. Without
    # this, N punches enqueue N identical rebuilds that can also race each
    # other on parallel workers (duplicate KG Employee Attendance Shift rows).
    #
    # Passes employee+time (not the checkin name) so the job also works
    # after the checkin row is gone (delete case).
    t = get_datetime(doc.time)
    try:
        enqueue(
            "kreativ_attendance.attendance.service.recalculate_period",
            queue="default",
            timeout=120,
            year=t.year,
            month=t.month,
            employee=doc.employee,
            now=False,
            enqueue_after_commit=True,
            deduplicate=True,
            job_id=f"kreativ-shift-recalc-{doc.employee}-{t.year}-{t.month:02d}",
        )
    except Exception:
        # If enqueue fails (no worker available), run inline
        recalculate_period(t.year, t.month, employee=doc.employee)


def on_salary_slip_submit(doc, method=None):
    """Triggered when a Salary Slip is submitted/finalized.

    Creates a KG Employee Shift Lock for the employee-month range, then
    locks all KG Employee Attendance Shift records in that period so they cannot be
    silently edited after payroll sign-off.

    Idempotent: if a lock already exists (unlocked_at is NULL), it's a no-op.

    CHANGED: REMOVED frappe.db.commit() — mid-transaction commit in a
    doc event hook breaks transaction atomicity and can corrupt the submit
    transaction. The lock creation uses frappe.db.set_value which persists
    within the submit transaction; no manual commit needed.
    """
    if not doc.employee or not doc.start_date:
        return

    from kreativ_attendance.attendance.lock import lock_period
    from frappe.utils import getdate
    import datetime

    start_date = getdate(doc.start_date)
    year = start_date.year
    month = start_date.month

    # Check if lock already exists (idempotency)
    existing = frappe.db.get_value(
        "KG Employee Shift Lock",
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
    lock_doc = lock_period(
        employee=doc.employee,
        year=year,
        month=month,
        salary_slip=doc.name,
        locked_by=frappe.session.user,
        reason=f"Salary Slip {doc.name} submitted for {year}-{month:02d}",
    )

    # Apply lock flag to all KG Employee Attendance Shift records in this period
    period_start = datetime.date(year, month, 1)
    if month == 12:
        period_end = datetime.date(year + 1, 1, 1)
    else:
        period_end = datetime.date(year, month + 1, 1)

    shifts = frappe.get_all(
        "KG Employee Attendance Shift",
        filters=[
            ["employee", "=", doc.employee],
            ["shift_date", ">=", period_start],
            ["shift_date", "<", period_end],
        ],
        pluck="name",
    )
    for shift_name in shifts:
        frappe.db.set_value(
            "KG Employee Attendance Shift",
            shift_name,
            {"locked": 1, "lock_period": lock_doc.name},
            update_modified=False,
        )


def on_checkin_rollback(doc, method=None):
    """Detect when an Employee Checkin transaction is rolled back.

    When the ZKTeco sync creates multiple checkins in a single transaction,
    if ANY checkin fails (e.g., duplicate), the entire transaction can be
    rolled back. This means:
        - All checkins in that batch are lost (not committed)
        - All enqueue_after_commit hooks are lost
        - No notifications are sent for any of those checkins

    This handler logs the lost checkins so we can:
        1. Investigate what went wrong
        2. Manually retry if needed
        3. Ensure retry_missed_notifications picks them up

    The checkin is still in memory (doc object) even though it won't be
    committed to the database. We log the details for debugging.
    """
    try:
        frappe.log_error(
            title="Employee Checkin Transaction Rolled Back",
            message=(
                f"Checkin {doc.name} for employee {doc.employee} "
                f"({doc.employee_name}) at {doc.time} was rolled back. "
                f"Log type: {doc.log_type}. "
                f"This usually means the ZKTeco sync transaction failed "
                f"mid-batch. The checkin will NOT be in the database and "
                f"no notification will be sent. Check ZKTeco sync logs for "
                f"the root cause."
            ),
        )
    except Exception:
        pass  # Don't let logging errors propagate


# ---------------------------------------------------------------------------
# LEGACY SHIMS (for backward compatibility with kreativ_notification)
# These delegate to the canonical kreativ_notification implementation.
# ---------------------------------------------------------------------------

def notify_checkin(checkin_name: str, test_mode: bool = False):
    """Legacy shim: delegate to kreativ_notification."""
    from kreativ_notification.notification.employee_notifications import notify_checkin
    return notify_checkin(checkin_name, test_mode)


def retry_missed_notifications():
    """Legacy shim: delegate to kreativ_notification."""
    from kreativ_notification.notification.employee_notifications import retry_missed_notifications
    return retry_missed_notifications()


def send_salary_slip(salary_slip: str):
    """Legacy shim: delegate to kreativ_notification."""
    from kreativ_notification.notification.employee_notifications import send_salary_slip
    return send_salary_slip(salary_slip)