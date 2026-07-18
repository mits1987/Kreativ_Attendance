"""WhatsApp checkin notifications via a self-hosted OpenWA gateway
(https://github.com/rmyndharis/OpenWA).

Configured in the "OpenWA Settings" single doctype. Fired from the
Employee Checkin after_insert hook — new punches only, never edits.
Failures are logged (Error Log), never allowed to break the device sync.

Notification Flow:
    ZKTeco device → sync → Employee Checkin created
    → after_insert hook → enqueue notify_checkin (background job)
    → _post() sends via OpenWA API → WhatsApp delivered

whatsapp_sent field values:
    0 = not sent (initial state, or reset for retry)
    1 = sent successfully
    2 = failed (will be retried by retry_missed_notifications)
    3 = permanently failed — invalid number (stop retrying to save resources)

whatsapp_retry_count field:
    Tracks how many times send has been attempted. After MAX_RETRY_ATTEMPTS,
    the checkin is marked as status 3 and a Comment is added explaining why.

Key Safety Features:
    - Status 3 stops infinite retry loops for invalid numbers
    - retry_missed_notifications enqueues jobs (doesn't block scheduler)
    - Stale session detection prevents retrying when OpenWA is down
    - Test messages always go to admin only (never to employees)
"""
import frappe
import requests
from frappe.utils import format_datetime, get_datetime
from datetime import datetime, timedelta
from gravures_custom.overrides.whatsapp_queue import OpenWAClient, _breaker_key, _get_failure_streak

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# After this many failed attempts, mark the checkin as permanently failed
# (whatsapp_sent=3). This prevents infinite retry loops for invalid numbers.
MAX_RETRY_ATTEMPTS = 5

# Hardcoded admin chat_id for test messages. Test messages should ONLY go
# to the developer/admin, never to employees. This is a safety measure to
# prevent accidental notifications during testing.
ADMIN_CHAT_ID = "919106526195@c.us"


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

def _get_shift_hours_for_out(employee: str, checkin_time: datetime) -> str:
    """Calculate shift hours for an OUT punch by finding the matching IN.

    Returns formatted string like '8:30' or empty string if not found.

    For overnight shifts, the IN may be on the previous day, so we look
    for the last IN before this OUT regardless of date.

    Strategy:
        1. Try Employee Shift's worked_hours (most accurate, if recalculation has run)
        2. Fall back to finding the last IN punch before this OUT
    """
    try:
        # First try to get worked_hours from Employee Shift (if recalculation has run)
        checkin_date = checkin_time.date()
        shift = frappe.db.get_value(
            "Employee Shift",
            {"employee": employee, "shift_date": checkin_date},
            "worked_hours",
        )
        if shift:
            return shift

        # Also check previous day for overnight shifts
        prev_date = checkin_date - timedelta(days=1)
        shift = frappe.db.get_value(
            "Employee Shift",
            {"employee": employee, "shift_date": prev_date},
            "worked_hours",
        )
        if shift:
            return shift

        # Fallback: find the last IN punch before this OUT
        # This works even if Employee Shift hasn't been recalculated yet
        last_in = frappe.db.get_value(
            "Employee Checkin",
            {
                "employee": employee,
                "log_type": "IN",
                "time": ["<", checkin_time],
            },
            "time",
            order_by="time desc",
        )

        if last_in:
            if isinstance(last_in, str):
                last_in = get_datetime(last_in)
            total_seconds = int((checkin_time - last_in).total_seconds())
            if total_seconds > 0:
                hours = int(total_seconds // 3600)
                minutes = int((total_seconds % 3600) // 60)
                return f"{hours}:{minutes:02d}"

        return ""
    except Exception:
        return ""


def _mark_failed(checkin_name: str, retry_count: int):
    """Mark a checkin as failed after an unsuccessful send attempt.

    This function implements the retry escalation logic:
        - If retry_count < MAX_RETRY_ATTEMPTS: mark as status 2 (will retry)
        - If retry_count >= MAX_RETRY_ATTEMPTS: mark as status 3 (stop retrying)

    When marking as status 3, a Comment is added to the checkin explaining
    why it was stopped. This makes it easy to understand in the UI without
    digging into error logs.

    Args:
        checkin_name: Employee Checkin document name
        retry_count: Current retry attempt number (incremented before calling)

    Returns:
        The new whatsapp_sent value (2 or 3)
    """
    if retry_count >= MAX_RETRY_ATTEMPTS:
        # --- PERMANENT FAILURE ---
        # The checkin has failed too many times. Mark as status 3 and add a
        # Comment so it's visible in the UI. Stop retrying to save resources.
        frappe.db.set_value(
            "Employee Checkin", checkin_name,
            {"whatsapp_sent": 3, "whatsapp_retry_count": retry_count},
            update_modified=False,
        )
        # Add a Comment on the checkin so anyone looking at it knows why
        # WhatsApp was not sent. This is more user-friendly than just a status.
        frappe.get_doc({
            "doctype": "Comment",
            "comment_type": "Info",
            "reference_doctype": "Employee Checkin",
            "reference_name": checkin_name,
            "content": (
                f"WhatsApp permanently failed after {retry_count} attempts. "
                f"Possible causes: invalid phone number, employee not on WhatsApp, "
                f"or OpenWA cannot reach this contact. Stopped retrying to save resources."
            ),
        }).insert(ignore_permissions=True)
        frappe.db.commit()
        return 3
    else:
        # --- RETRYABLE FAILURE ---
        # Mark as status 2 so retry_missed_notifications will pick it up.
        frappe.db.set_value(
            "Employee Checkin", checkin_name,
            {"whatsapp_sent": 2, "whatsapp_retry_count": retry_count},
            update_modified=False,
        )
        frappe.db.commit()
        return 2


# ---------------------------------------------------------------------------
# Core Notification Logic
# ---------------------------------------------------------------------------

def notify_checkin(checkin_name: str, test_mode: bool = False):
    """Background job: send one WhatsApp message for a new punch.

    This is the main entry point for notifications. It is called:
        1. From the after_insert hook (via enqueue) for new checkins
        2. From retry_missed_notifications (via enqueue) for missed ones

    Args:
        checkin_name: Employee Checkin document name
        test_mode: If True, sends to admin only (for testing OpenWA connection)
    """
    # --- Load Settings ---
    settings = frappe.get_cached_doc("OpenWA Settings")
    if not (settings.enabled and settings.base_url):
        return

    # --- Circuit Breaker: Rate-limit sends when OpenWA is known down ---
    # The health check tracks consecutive failures. After 3 failures,
    # it trips the breaker and enters exponential backoff.  Instead of
    # blocking all sends, we allow ONE probe per backoff period so that
    # recovery happens automatically when OpenWA comes back online.
    from kreativ_attendance.attendance.openwa_health import _can_attempt_probe
    if not _can_attempt_probe():
        return  # silent skip — probe already attempted recently

    # --- Load Checkin ---
    c = frappe.db.get_value(
        "Employee Checkin", checkin_name,
        ["employee", "employee_name", "log_type", "time",
         "whatsapp_sent", "whatsapp_retry_count"],
        as_dict=True,
    )
    if not c:
        return  # Checkin was deleted before the job ran

    # --- Deduplication ---
    # Skip if already sent (1) or permanently failed (3).
    # Status 0 and 2 are the only ones we process.
    if c.whatsapp_sent in (1, 3):
        return

    # --- Filter by notify_on setting ---
    # If settings say "IN only", skip OUT punches (and vice versa)
    if settings.notify_on == "IN only" and c.log_type != "IN":
        return
    if settings.notify_on == "OUT only" and c.log_type != "OUT":
        return

    # --- Build Message ---
    # Format: "🟢 IN — Employee Name at 11-07-2026 09:30"
    icon = "🟢 IN" if c.log_type == "IN" else "🔴 OUT"
    text = "{0} — {1} at {2}".format(
        icon,
        c.employee_name or c.employee,
        format_datetime(c.time, "dd-MM-yyyy HH:mm"),
    )

    # Add shift hours for OUT punches (e.g., "shift hours: 8:30")
    if c.log_type == "OUT":
        shift_hours = _get_shift_hours_for_out(c.employee, c.time)
        if shift_hours:
            text = "{0} shift hours: {1}".format(text, shift_hours)

    # --- TEST MODE: Send to admin only ---
    # Test messages should ONLY go to the developer/admin (Mitesh).
    # NEVER to employees. This prevents accidental notifications during testing.
    # Uses ADMIN_CHAT_ID hardcoded above, not the employee's phone number.
    if test_mode or settings.test_mode:
        _post(settings, "send-text", {"chatId": ADMIN_CHAT_ID, "text": text})
        return

    # --- Increment retry count BEFORE sending ---
    # This tracks how many attempts have been made. If it reaches
    # MAX_RETRY_ATTEMPTS, _mark_failed() will set status 3.
    retry_count = (c.whatsapp_retry_count or 0) + 1

    # --- PRODUCTION MODE: Send to employee's own mobile ---
    # Look up the employee's cell_number from the Employee doctype.
    # Format it as a WhatsApp chat_id (digits@c.us).
    mobile = frappe.db.get_value("Employee", c.employee, "cell_number") or ""
    digits = "".join(filter(str.isdigit, mobile))

    if len(digits) >= 10:
        # Prepend country code if not already present (e.g., 91 for India)
        cc = "".join(filter(str.isdigit, settings.default_country_code or ""))
        if cc and not digits.startswith(cc) and len(digits) <= 10:
            digits = cc + digits

        # WhatsApp chat_id format: digits@c.us (e.g., 919876543210@c.us)
        chat_id = digits + "@c.us"

        if _post(settings, "send-text", {"chatId": chat_id, "text": text}):
            # SUCCESS: Mark as sent (status 1)
            frappe.db.set_value(
                "Employee Checkin", checkin_name,
                {"whatsapp_sent": 1, "whatsapp_retry_count": retry_count},
                update_modified=False,
            )
            frappe.db.commit()
        else:
            # FAILED: Escalate (status 2 → retry, or status 3 → stop)
            _mark_failed(checkin_name, retry_count)
    else:
        # --- FALLBACK: No valid phone number → send to admin ---
        # If the employee has no cell_number or it's too short, fall back
        # to sending to the admin chat_id. This ensures the notification
        # is delivered somewhere rather than lost silently.
        if settings.chat_id:
            if _post(settings, "send-text", {"chatId": settings.chat_id, "text": text}):
                frappe.db.set_value(
                    "Employee Checkin", checkin_name,
                    {"whatsapp_sent": 1, "whatsapp_retry_count": retry_count},
                    update_modified=False,
                )
                frappe.db.commit()
            else:
                _mark_failed(checkin_name, retry_count)


# ---------------------------------------------------------------------------
# Salary Slip Notifications
# ---------------------------------------------------------------------------

def send_salary_slip(salary_slip: str):
    """Background job: render the Salary Slip PDF and WhatsApp it via OpenWAClient.

    This is triggered by the on_salary_slip_submit hook when
    settings.send_salary_slips is enabled.
    """
    import base64
    import re

    settings = frappe.get_cached_doc("OpenWA Settings")
    if not (settings.enabled and settings.send_salary_slips and settings.base_url):
        return

    slip = frappe.get_doc("Salary Slip", salary_slip)
    mobile = frappe.db.get_value("Employee", slip.employee, "cell_number") or ""
    digits = re.sub(r"\D", "", mobile)
    if not digits:
        frappe.log_error(
            title="Salary slip WhatsApp skipped: no mobile number",
            message=f"{slip.employee} ({slip.employee_name}) has no cell_number on the Employee record.",
        )
        return
    cc = re.sub(r"\D", "", settings.default_country_code or "")
    if cc and not digits.startswith(cc) and len(digits) <= 10:
        digits = cc + digits

    pdf = frappe.get_print(
        "Salary Slip", slip.name,
        print_format=settings.salary_slip_print_format or None,
        as_pdf=True,
    )
    period = frappe.utils.format_date(slip.start_date, "MMMM yyyy")
    filename = f"Salary Slip {period} - {slip.employee_name}.pdf"
    caption = f"Salary Slip — {period}"

    client = OpenWAClient()
    client.send_document(
        chat_id=f"{digits}@c.us",
        base64_data=base64.b64encode(pdf).decode(),
        filename=filename,
        mimetype="application/pdf",
        caption=caption,
    )


# ---------------------------------------------------------------------------
# OpenWA API Communication
# ---------------------------------------------------------------------------

def _post(settings, endpoint: str, payload: dict, raise_on_error: bool = False):
    """Send a POST request via OpenWAClient (consolidated — Item 15).

    Thin wrapper around OpenWAClient that maps old _post() calls to the
    consolidated client. Returns True on success, False on failure.
    """
    chat_id = payload.get("chatId", settings.chat_id)
    text = payload.get("text", "")
    base64_data = payload.get("base64", "")
    filename = payload.get("filename", "document")
    mimetype = payload.get("mimetype", "application/pdf")
    caption = payload.get("caption", "")

    client = OpenWAClient()
    try:
        if endpoint == "send-text":
            result = client.send_text(chat_id, text)
        elif endpoint == "send-document":
            result = client.send_document(chat_id, base64_data, filename,
                                          mimetype=mimetype, caption=caption)
        else:
            # Generic fallback — POST raw payload directly
            from gravures_custom.overrides.whatsapp_queue import _get_openwa_config
            base_url, api_key, session_id = _get_openwa_config()
            if not base_url:
                return False
            url = "{0}/api/sessions/{1}/messages/{2}".format(
                base_url.rstrip("/"), session_id or "default", endpoint)
            r = requests.post(url, json=payload,
                              headers={"X-API-Key": api_key}, timeout=10)
            if r.ok:
                return True
            frappe.log_error(title=f"OpenWA HTTP {r.status_code}",
                             message=r.text[:500])
            return False

        if result.get("success"):
            return True
        frappe.log_error(title="OpenWA send failed",
                         message=result.get("error", "Unknown error"))
        if raise_on_error:
            frappe.throw(result.get("error", "Could not send via OpenWA"))
        return False
    except Exception:
        frappe.log_error(title="OpenWA WhatsApp send failed",
                         message=frappe.get_traceback())
        if raise_on_error:
            frappe.throw("Could not send via OpenWA — check Base URL / API Key / session. "
                         "Details are in the Error Log.")
        return False


def send_text(settings: "frappe.model.document.Document", text: str, raise_on_error: bool = False) -> bool:
    """Send a plain text message via OpenWAClient.

    Used by the "Send Test Message" button on the OpenWA Settings form.
    Always sends to the admin chat_id (settings.chat_id), NOT to employees.

    Returns True on success, False on failure.
    """
    if not settings.chat_id:
        frappe.msgprint("Set Recipient Chat ID in OpenWA Settings and Save first.")
        return False
    client = OpenWAClient()
    result = client.send_text(settings.chat_id, text)
    if result.get("success"):
        return True
    if raise_on_error:
        frappe.throw(result.get("error", "Could not send via OpenWA"))
    return False


# ---------------------------------------------------------------------------
# Retry Mechanism (Safety Net)
# ---------------------------------------------------------------------------

def retry_missed_notifications():
    """Scheduled job: find Employee Checkins where whatsapp_sent was never
    set to 1 and retry sending. Catches notifications lost to:
        - Worker crashes (job dequeued but not completed)
        - Module load errors (e.g., stale Redis cache → Module not found)
        - Transient OpenWA failures (HTTP 500, timeout, etc.)

    Runs every 10 minutes via scheduler_events.

    IMPORTANT: This function enqueues jobs instead of running them
    synchronously. This prevents blocking the scheduler when there are
    many unsent checkins or OpenWA is slow to respond.

    Flow:
        1. Check if OpenWA is enabled and session is healthy
        2. Reset status 2 → 0 for checkins from last 7 days (retry window)
        3. Query for unsent checkins (status 0 or NULL) from last 24 hours
        4. Enqueue each as a background job on the short queue
        5. Each job runs notify_checkin() independently

    Safety:
        - Skips if session is stale (openwa_session_stale flag)
        - Only processes checkins from last 24 hours (avoids ancient retries)
        - Status 3 (permanently failed) is NEVER reset by this function
        - Status 2 → 0 reset is bounded to 7-day window
    """

    settings = frappe.get_single("OpenWA Settings")
    if not (settings.enabled and settings.base_url):
        return

    # --- Skip if session is stale ---
    # The health checker sets this flag when lastActive > 60 min.
    # If the session is down, there's no point retrying — we'd just
    # create more error logs and waste resources.
    if frappe.cache().get_value(_breaker_key("stale")):
        frappe.log_error(
            title="OpenWA Retry Skipped",
            message="Session is stale (lastActive > 60 min). Check Error Log for 'OpenWA Session Stale'.",
        )
        return

    # --- Reset retryable failures (status 2 → 0) ---
    # Only reset when circuit breaker is NOT tripped.  Status=2 means the
    # send actually attempted and failed — evidence OpenWA may be down.
    # Don't reset them while the breaker is active to avoid a wasted
    # reset → enqueue → fail → reset cycle.
    # NOTE: This does NOT touch status 3 (permanently failed).
    perm_fail_cutoff = get_datetime() - timedelta(days=7)
    if _get_failure_streak() < 3:
        frappe.db.sql(
            """
            UPDATE `tabEmployee Checkin`
            SET whatsapp_sent = 0
            WHERE whatsapp_sent = 2
              AND creation >= %s
            """,
            (perm_fail_cutoff,),
        )
        frappe.db.commit()

    # --- Find unsent checkins ---
    # Look for checkins with status 0 (never attempted) or NULL from
    # the last 24 hours. Order by creation ascending (oldest first).
    cutoff = get_datetime() - timedelta(hours=24)
    unsent = frappe.get_all(
        "Employee Checkin",
        filters={
            "whatsapp_sent": ["in", [0, None]],
            "creation": [">=", cutoff],
        },
        fields=["name", "employee_name", "log_type", "creation"],
        order_by="creation asc",
        limit_page_length=50,
    )

    if not unsent:
        return

    # --- Enqueue each retry as a background job ---
    # FIX #2: Instead of calling notify_checkin() synchronously (which
    # blocks the scheduler for N*timeout seconds), we enqueue each one
    # as a background job. This keeps the scheduler responsive.
    #
    # enqueue_after_commit=False because we're already in a scheduled job
    # and want the enqueue to happen immediately.
    enqueued = 0
    for c in unsent:
        try:
            frappe.enqueue(
                "kreativ_attendance.attendance.whatsapp.notify_checkin",
                queue="short",
                timeout=60,
                checkin_name=c.name,
                enqueue_after_commit=False,
            )
            enqueued += 1
        except Exception:
            frappe.log_error(
                title=f"WhatsApp retry enqueue failed for {c.name}",
                message=frappe.get_traceback(),
            )

    if enqueued:
        frappe.logger().info(
            f"WhatsApp retry: enqueued {enqueued}/{len(unsent)} missed notifications"
        )
