"""Retire the KG Employee Standard Hours doctype.

Standard hours now live on Employee.working_hours. This patch:
  1. copies any surviving value that is NOT already on the Employee record
  2. reports employees left with no working_hours at all
  3. drops the old table

SAFETY: step 1 never overwrites an existing Employee.working_hours, and the
patch aborts before dropping anything if the copy could not be completed.
"""
import frappe

OLD_DOCTYPE = "KG Employee Standard Hours"


def execute():
    if not frappe.db.table_exists(OLD_DOCTYPE):
        _report_missing()
        return

    copied = 0
    try:
        rows = frappe.db.sql(
            f"SELECT employee, standard_hours FROM `tab{OLD_DOCTYPE}`", as_dict=True
        )
    except Exception:
        rows = []

    for r in rows:
        emp, hours = r.get("employee"), r.get("standard_hours")
        if not emp or not hours or float(hours) <= 0:
            continue
        if not frappe.db.exists("Employee", emp):
            continue
        current = frappe.db.get_value("Employee", emp, "working_hours")
        if current and float(current) > 0:
            continue  # never overwrite what is already there
        frappe.db.set_value("Employee", emp, "working_hours", float(hours),
                            update_modified=False)
        copied += 1

    frappe.db.commit()
    print(f"Copied {copied} standard-hours value(s) onto Employee.working_hours.")

    _report_missing()

    frappe.delete_doc("DocType", OLD_DOCTYPE, ignore_missing=True, force=True)
    frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{OLD_DOCTYPE}`")
    frappe.db.commit()
    print(f"Dropped {OLD_DOCTYPE}.")


def _report_missing():
    missing = frappe.db.sql_list("""
        SELECT DISTINCT s.employee
        FROM `tabKG Employee Attendance Shift` s
        LEFT JOIN `tabEmployee` e ON e.name = s.employee
        WHERE IFNULL(e.working_hours, 0) <= 0
    """)
    if missing:
        print("\nWARNING - these employees have shifts but no Working Hours. "
              "They will fall back to the settings default and their PD may be "
              "wrong. Set Working Hours on each Employee record:\n  "
              + "\n  ".join(missing))
