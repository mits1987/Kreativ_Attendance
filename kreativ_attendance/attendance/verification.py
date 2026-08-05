"""Long-session verification — "I checked this, it is genuine."

THE PROBLEM
-----------
The >13h alert exists to catch a *missed middle punch* that silently merged two
days into one. But long shifts are sometimes real: a cylinder run that has to
finish, a machine that cannot be left. Until now the quality gate treated every
long session as blocking with no way to say "I looked at this one and it is
correct", so a legitimate 13.5h shift held up the entire month close.

An alert you cannot dismiss stops being an alert and becomes an obstacle —
people start turning the whole check off, which is exactly what you do not want.

THE RULE
--------
A verification is bound to the EXACT duration that was verified.

    verify(shift) records worked_seconds at the moment of verification.

If a later recalculation produces the same duration, the verification carries
over. If the duration changes — a punch was corrected, or a new one arrived —
the verification is dropped and the shift is flagged again.

That matters because verification survives `recalculate_period`, which deletes
and rebuilds every shift row. Without this, verifying ten shifts and then
correcting one punch elsewhere would silently wipe all ten and re-block the
close, with no explanation.
"""
import frappe
from frappe.utils import now_datetime

SHIFT_DOCTYPE = "KG Employee Attendance Shift"


# ---------------------------------------------------------------------------
# Snapshot / restore across a rebuild
# ---------------------------------------------------------------------------

def snapshot_verifications(employees, start, end) -> dict:
    """Capture verified long sessions before shifts are deleted.

    Returns {(employee, shift_date, worked_seconds): {...}} — keyed on the
    duration so a changed shift does not inherit an old verification.
    """
    if not employees:
        return {}

    rows = frappe.get_all(
        SHIFT_DOCTYPE,
        filters=[
            ["employee", "in", list(employees)],
            ["shift_date", ">=", start],
            ["shift_date", "<", end],
            ["long_session_verified", "=", 1],
        ],
        fields=["employee", "shift_date", "worked_seconds",
                "verification_note", "verified_by", "verified_at"],
    )
    out = {}
    for r in rows:
        key = (r["employee"], str(r["shift_date"]), int(r["worked_seconds"] or 0))
        out[key] = {
            "verification_note": r.get("verification_note"),
            "verified_by": r.get("verified_by"),
            "verified_at": r.get("verified_at"),
        }
    return out


def restore_verifications(snapshot: dict, start, end) -> dict:
    """Re-apply verifications to rebuilt shifts whose duration is unchanged.

    Returns {"restored": n, "dropped": [...]} so the caller can report which
    shifts need looking at again.
    """
    if not snapshot:
        return {"restored": 0, "dropped": []}

    employees = sorted({k[0] for k in snapshot})
    rebuilt = frappe.get_all(
        SHIFT_DOCTYPE,
        filters=[
            ["employee", "in", employees],
            ["shift_date", ">=", start],
            ["shift_date", "<", end],
        ],
        fields=["name", "employee", "shift_date", "worked_seconds"],
    )

    by_key = {}
    for r in rebuilt:
        by_key[(r["employee"], str(r["shift_date"]), int(r["worked_seconds"] or 0))] = r["name"]

    restored, dropped = 0, []
    for key, data in snapshot.items():
        docname = by_key.get(key)
        if not docname:
            # Duration changed (or the shift is gone). Verification does not
            # carry over — the operator must look again.
            dropped.append({"employee": key[0], "shift_date": key[1],
                            "old_seconds": key[2]})
            continue
        frappe.db.set_value(SHIFT_DOCTYPE, docname, {
            "long_session_verified": 1,
            "verification_note": data.get("verification_note"),
            "verified_by": data.get("verified_by"),
            "verified_at": data.get("verified_at"),
        }, update_modified=False)
        restored += 1

    return {"restored": restored, "dropped": dropped}


# ---------------------------------------------------------------------------
# Verify / un-verify
# ---------------------------------------------------------------------------

def _guard(doc):
    if doc.locked:
        frappe.throw(
            "This shift is payroll-locked and cannot be changed. "
            "Unlock the period first if a correction is genuinely needed."
        )


@frappe.whitelist()
def verify_shift(shift: str = None, note: str = None) -> dict:
    """Mark one long session as verified-genuine."""
    frappe.only_for(("System Manager", "HR Manager", "HR User"))
    if not shift:
        frappe.throw("A shift is required.")

    doc = frappe.get_doc(SHIFT_DOCTYPE, shift)
    _guard(doc)

    doc.db_set("long_session_verified", 1, update_modified=False)
    doc.db_set("verification_note", note or "", update_modified=False)
    doc.db_set("verified_by", frappe.session.user, update_modified=False)
    doc.db_set("verified_at", now_datetime(), update_modified=False)
    frappe.db.commit()

    return {
        "shift": shift,
        "employee": doc.employee,
        "hours": round((doc.worked_seconds or 0) / 3600.0, 2),
        "verified": 1,
    }


@frappe.whitelist()
def unverify_shift(shift: str = None) -> dict:
    """Withdraw a verification, putting the shift back in the blocking set."""
    frappe.only_for(("System Manager", "HR Manager", "HR User"))
    doc = frappe.get_doc(SHIFT_DOCTYPE, shift)
    _guard(doc)

    doc.db_set("long_session_verified", 0, update_modified=False)
    doc.db_set("verified_by", None, update_modified=False)
    doc.db_set("verified_at", None, update_modified=False)
    frappe.db.commit()
    return {"shift": shift, "verified": 0}


@frappe.whitelist()
def verify_shifts(shifts=None, note: str = None) -> dict:
    """Verify several long sessions at once."""
    frappe.only_for(("System Manager", "HR Manager", "HR User"))
    names = frappe.parse_json(shifts) if isinstance(shifts, str) else (shifts or [])
    if not names:
        frappe.throw("No shifts selected.")

    done, skipped = 0, []
    for n in names:
        try:
            verify_shift(n, note)
            done += 1
        except Exception as e:
            skipped.append(f"{n}: {e}")
    return {"verified": done, "skipped": skipped}
