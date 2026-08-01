"""Add Salary Sheet ID custom field on Employee and populate from the June 2026 sheet."""
import frappe


def execute():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    # Step 1: Create the custom field
    create_custom_fields({
        "Employee": [
            {
                "fieldname": "salary_sheet_id",
                "fieldtype": "Data",
                "label": "Salary Sheet ID",
                "insert_after": "attendance_device_id",
                "read_only": 1,
                "no_copy": 1,
                "description": "KR ID from the approved salary sheet (e.g. KR011). Used for payroll mapping.",
            }
        ]
    }, ignore_validate=True)
    print("Custom field 'Salary Sheet ID' created on Employee.")

    # Step 2: Mapping — salary_sheet_id -> employee name (from confirmed name matching)
    MAPPING = {
        "KR011": "Dablu Prasad",
        "KR016": "PATIL KAILASH",
        "KR018": "Mayank Dixit",
        "KR019": "Pritesh Pandey",
        "KR052": "Jwala Prasad",
        "KR053": "HEMANT KAPATEL",
        "KR057": "GOUTAM MEHTA",
        "KR059": "SHIVAM MEHTA",
        "KR074": "Anilkumar Patel",
        "KR084": "Mohan Khachane",
        "KR090": "Laxman Talpada",
        "KR097": "VISHAL PRAJAPATI",
        "KR098": "MITESH PANCHAL",
        "KR099": "Bipin Patel",
        "KR104": "PRADEEP PANDAY",
        "KR105": "Kamlesh Talpada",
        "KR106": "Prakash Talpada",
        "KR107": "Jigneshbhai Khambhu",
        "KR111": "SUDHIR MEHTA",
        "KR112": "Vishnu Dabhi",
        "KR113": "ROHIT JAISWAR",
        "KR114": "ASHVIN TURI",
        "KR115": "VRAJ RAVAL",
        "KR116": "MALEK IRSAD",
        "KR119": "MUKESH CHAVDA",
    }

    updated = 0
    not_found = []
    ambiguous = []

    for sheet_id, emp_name in MAPPING.items():
        matches = frappe.get_all(
            "Employee",
            filters={"employee_name": emp_name},
            fields=["name", "employee_name"],
        )
        if len(matches) == 0:
            not_found.append(f"{sheet_id} -> '{emp_name}'")
        elif len(matches) > 1:
            ambiguous.append(f"{sheet_id} -> '{emp_name}' -> {[m.name for m in matches]}")
        else:
            frappe.db.set_value("Employee", matches[0].name, "salary_sheet_id", sheet_id)
            updated += 1
            print(f"  {sheet_id} -> {matches[0].name} = {matches[0].employee_name}")

    frappe.db.commit()

    print(f"\nDone: {updated} employees updated.")
    if not_found:
        print(f"\nNot found ({len(not_found)}):")
        for nf in not_found:
            print(f"  {nf}")
    if ambiguous:
        print(f"\nAmbiguous ({len(ambiguous)}):")
        for am in ambiguous:
            print(f"  {am}")
