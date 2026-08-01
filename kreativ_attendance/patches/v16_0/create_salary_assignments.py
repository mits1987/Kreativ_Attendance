"""Create Salary Structure Assignments for all employees with payroll data.
Matches by salary_sheet_id. Creates a DRAFT assignment with base = Rate of Wages.
"""
import frappe

STRUCTURE = "Kreativ Gravures Monthly"
FROM_DATE = "2026-08-01"

# (salary_sheet_id, base = Rate of Wages)
ASSIGNMENTS = [
    ("KR011", 26250),
    ("KR016", 13325),
    ("KR018", 50000),
    ("KR019", 38325),
    ("KR052", 19110),
    ("KR053", 41000),
    ("KR057", 23100),
    ("KR059", 23100),
    ("KR074", 42700),
    ("KR084", 42000),
    ("KR090", 23313),
    ("KR097", 31500),
    ("KR098", 46200),
    ("KR099", 32575),
    ("KR104", 31125),
    ("KR105", 34000),
    ("KR106", 32400),
    ("KR107", 32400),
    ("KR111", 29400),
    ("KR112", 27300),
    ("KR113", 28000),
    ("KR114", 26000),
    ("KR115", 24000),
    ("KR116", 24000),
    ("KR119", 22000),
]


def execute():
    company = frappe.defaults.get_global_default("company")
    if not company:
        company = frappe.db.get_value("Company", {}, "name")
    if not company:
        print("ERROR: No company found")
        return

    created = 0
    skipped = 0
    not_found = []

    for sheet_id, base in ASSIGNMENTS:
        emp = frappe.db.get_value(
            "Employee",
            {"salary_sheet_id": sheet_id},
            ["name", "employee_name"],
            as_dict=True,
        )
        if not emp:
            not_found.append(sheet_id)
            continue

        # Check if assignment already exists
        existing = frappe.db.get_value(
            "Salary Structure Assignment",
            {
                "employee": emp.name,
                "salary_structure": STRUCTURE,
                "docstatus": ["<", 2],
            },
            "name",
        )
        if existing:
            # Update base if draft
            doc = frappe.get_doc("Salary Structure Assignment", existing)
            if doc.docstatus == 0:
                doc.base = base
                doc.from_date = FROM_DATE
                doc.company = company
                doc.save(ignore_permissions=True)
                created += 1
                print(f"  UPDATED {sheet_id} -> {emp.name} = {emp.employee_name} | base: {base}")
            else:
                skipped += 1
                print(f"  SKIPPED {sheet_id} -> {emp.name} (submitted)")
            continue

        doc = frappe.get_doc({
            "doctype": "Salary Structure Assignment",
            "employee": emp.name,
            "salary_structure": STRUCTURE,
            "from_date": FROM_DATE,
            "base": base,
            "company": company,
        })
        doc.insert(ignore_permissions=True)
        created += 1
        print(f"  CREATED {sheet_id} -> {emp.name} = {emp.employee_name} | base: {base}")

    frappe.db.commit()

    print(f"\nDone: {created} created/updated, {skipped} skipped (already submitted).")
    if not_found:
        print(f"\nNot found ({len(not_found)}): {', '.join(not_found)}")
