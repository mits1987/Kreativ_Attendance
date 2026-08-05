"""Remove the dead Employee.overtime_rate_per_hour custom field.

WHY
---
The field's own description says it is "Used by 'Sync to HRMS' to create
monthly Overtime Additional Salary". That stopped being true when hrms.py was
rewritten: the overtime rate is now DERIVED, not stored --

    hourly_rate = rate_of_wages / (WD * standard_hours)

Deriving it means the rate cannot drift out of step with a salary revision.
The stored field is read nowhere in the codebase, but it still renders on the
Employee form as an editable, plausible-looking control. Anyone who finds it
and sets a value will reasonably expect overtime to change. It will not.

A working-looking switch that does nothing is worse than no switch.

SAFETY
------
Any non-zero values are printed before deletion, so the data is recoverable
from the migrate log if it turns out someone was relying on it. Nothing else
reads the column, so no calculation changes.

The fixture in fixtures/custom_field.json must be removed in the same change,
otherwise the next `bench migrate` re-creates the field.
"""
import frappe

FIELDNAME = "overtime_rate_per_hour"
CUSTOM_FIELD_NAME = "Employee-overtime_rate_per_hour"


def execute():
    if not frappe.db.exists("Custom Field", CUSTOM_FIELD_NAME):
        print(f"{CUSTOM_FIELD_NAME} not present — nothing to remove.")
        return

    # Record anything non-zero before it goes, so the values survive in the log.
    try:
        if frappe.db.has_column("Employee", FIELDNAME):
            rows = frappe.db.sql(
                f"""SELECT name, employee_name, `{FIELDNAME}` AS rate
                    FROM `tabEmployee`
                    WHERE IFNULL(`{FIELDNAME}`, 0) != 0""",
                as_dict=True,
            )
            if rows:
                print(f"\n{len(rows)} employee(s) had a non-zero {FIELDNAME}. "
                      "The value was unused; recording it here for reference:")
                for r in rows:
                    print(f"    {r['name']:20} {r.get('employee_name') or '':30} {r['rate']}")
                print("")
    except Exception:
        # Reporting must never block the cleanup.
        frappe.log_error(title="Could not report overtime_rate_per_hour values",
                         message=frappe.get_traceback())

    frappe.delete_doc("Custom Field", CUSTOM_FIELD_NAME,
                      ignore_missing=True, force=True)
    frappe.db.commit()

    print(f"Removed {CUSTOM_FIELD_NAME}.")
    print("Overtime is now derived: rate_of_wages / (WD * standard_hours). "
          "Set the component amounts on the Employee record instead.")
