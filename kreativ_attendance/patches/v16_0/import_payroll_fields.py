"""Import employee payroll fields from the June 2026 salary sheet.
Matches by salary_sheet_id (KR011, KR016, etc.) — the reliable mapping.
"""
import frappe


# (salary_sheet_id, kg_basic, kg_hra, kg_conveyance, kg_medical, kg_other, pf_applicable, esi_applicable)
PAYROLL_DATA = [
    ("KR011", 21500, 3200, 0, 300, 1250, 0, 0),
    ("KR016", 13325, 0, 0, 0, 0, 1, 1),
    ("KR018", 25000, 10000, 0, 6000, 9000, 0, 0),
    ("KR019", 23000, 7300, 0, 2075, 5950, 0, 0),
    ("KR052", 14000, 1500, 0, 2700, 910, 1, 1),
    ("KR053", 25000, 7000, 0, 5000, 4000, 0, 0),
    ("KR057", 21500, 0, 0, 500, 1100, 0, 0),
    ("KR059", 21500, 0, 0, 500, 1100, 0, 0),
    ("KR074", 21500, 8200, 0, 6000, 7000, 0, 0),
    ("KR084", 21500, 8000, 0, 6000, 6500, 0, 0),
    ("KR090", 21500, 0, 0, 950, 863, 0, 0),
    ("KR097", 21500, 4500, 0, 4000, 1500, 0, 0),
    ("KR098", 22000, 8800, 0, 7000, 8400, 0, 0),
    ("KR099", 21500, 6300, 0, 3700, 1075, 0, 0),
    ("KR104", 21500, 4500, 0, 4000, 1125, 0, 0),
    ("KR105", 21500, 6800, 0, 5500, 200, 0, 0),
    ("KR106", 21500, 6480, 0, 4200, 220, 0, 0),
    ("KR107", 21500, 6480, 0, 4200, 220, 0, 0),
    ("KR111", 21500, 6200, 0, 300, 1400, 0, 0),
    ("KR112", 21500, 4200, 0, 300, 1300, 0, 0),
    ("KR113", 21500, 0, 0, 6500, 0, 0, 0),
    ("KR114", 21500, 0, 0, 4500, 0, 0, 0),
    ("KR115", 21500, 0, 0, 2500, 0, 0, 0),
    ("KR116", 21500, 0, 0, 2500, 0, 0, 0),
    ("KR119", 21500, 0, 0, 500, 0, 0, 0),
]


def execute():
    updated = 0
    not_found = []

    for sheet_id, basic, hra, con, med, other, pf, esi in PAYROLL_DATA:
        emp = frappe.db.get_value(
            "Employee",
            {"salary_sheet_id": sheet_id},
            ["name", "employee_name"],
            as_dict=True,
        )
        if not emp:
            not_found.append(sheet_id)
            continue

        frappe.db.set_value("Employee", emp.name, {
            "kg_basic": basic,
            "kg_hra": hra,
            "kg_conveyance": con,
            "kg_medical": med,
            "kg_other": other,
            "pf_applicable": pf,
            "esi_applicable": esi,
        })
        updated += 1
        rate_of_wages = basic + hra + con + med + other
        print(f"  {sheet_id} -> {emp.name} = {emp.employee_name} | RoW: {rate_of_wages}")

    frappe.db.commit()

    print(f"\nDone: {updated} employees updated with payroll fields.")
    if not_found:
        print(f"\nNot found ({len(not_found)}): {', '.join(not_found)}")
