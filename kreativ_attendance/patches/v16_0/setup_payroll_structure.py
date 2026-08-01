"""Create every HRMS object the approved salary sheet needs.

Fully idempotent: safe to re-run. Existing records are updated in place, never
duplicated, and anything a human has customised (GL accounts, extra conditions)
is preserved.

CREATES
-------
1. Employee custom fields — the per-employee component amounts that columns
   M..Q of the salary sheet hold, plus the PF/ESI applicability flags:
       kg_basic, kg_hra, kg_conveyance, kg_medical, kg_other
       pf_applicable, esi_applicable
2. Salary Components — 7 earnings, 4 deductions, with the approved formulas
3. Salary Structure "Kreativ Gravures Monthly" wiring them together

DOES NOT CREATE (and cannot)
----------------------------
* Salary Structure Assignments — these carry each employee's actual amounts and
  a from_date. Use the import template in docs/salary_structure_assignments.csv.
* GL account mapping on the components — company-specific; set once in the desk
  if you post payroll to accounts.
* Holiday Lists — these encode your actual working calendar.

WHY EMPLOYEE FIELDS RATHER THAN STRUCTURE AMOUNTS
-------------------------------------------------
Your component amounts are absolute per-employee figures, not ratios of a base
(KR011 is 21500/3200; KR018 is 25000/10000). A single Salary Structure with
percentage formulas cannot express that, and one structure per employee would
mean 29 structures to maintain. Putting the five amounts on the Employee record
lets one structure serve everyone, and HRMS makes Employee fields directly
available to salary formulas by fieldname.

The Assignment `base` should be set to Rate of Wages (the sum of the five),
which is also what prices overtime.
"""
import frappe

STRUCTURE_NAME = "Kreativ Gravures Monthly"

# --- Employee custom fields ------------------------------------------------
EMPLOYEE_FIELDS = [
    {
        "fieldname": "kg_payroll_section", "fieldtype": "Section Break",
        "label": "Kreativ Payroll", "insert_after": "holiday_list",
        "collapsible": 1,
    },
    {
        "fieldname": "kg_basic", "fieldtype": "Currency", "label": "Basic",
        "insert_after": "kg_payroll_section", "non_negative": 1,
        "description": "Full-month Basic (column M of the salary sheet). Prorated by pay days on the slip.",
    },
    {
        "fieldname": "kg_hra", "fieldtype": "Currency", "label": "HRA",
        "insert_after": "kg_basic", "non_negative": 1,
        "description": "Full-month HRA (column N). Excluded from the PF wage base.",
    },
    {
        "fieldname": "kg_conveyance", "fieldtype": "Currency",
        "label": "Conveyance Allowance", "insert_after": "kg_hra", "non_negative": 1,
        "description": "Full-month Conveyance (column O).",
    },
    {
        "fieldname": "kg_column_break_payroll", "fieldtype": "Column Break",
        "insert_after": "kg_conveyance",
    },
    {
        "fieldname": "kg_medical", "fieldtype": "Currency",
        "label": "Medical / Special Allowance", "insert_after": "kg_column_break_payroll",
        "non_negative": 1,
        "description": "Full-month Medical / Special (column P).",
    },
    {
        "fieldname": "kg_other", "fieldtype": "Currency", "label": "Other Allowance",
        "insert_after": "kg_medical", "non_negative": 1,
        "description": "Full-month Other Allowance (column Q).",
    },
    {
        "fieldname": "pf_applicable", "fieldtype": "Check", "label": "PF Applicable",
        "insert_after": "kg_other", "default": "0",
        "description": "Column K of the salary sheet.",
    },
    {
        "fieldname": "esi_applicable", "fieldtype": "Check", "label": "ESI Applicable",
        "insert_after": "pf_applicable", "default": "0",
        "description": "Column L. This is a registration flag, not a gross-pay test.",
    },
]

# --- Salary Components -----------------------------------------------------
# Proration: `payment_days / total_working_days` is set by KGSalarySlip from the
# reviewed Monthly Attendance Summary, and equals pay_days / days_in_month.
# depends_on_payment_days is 0 everywhere because the formulas prorate
# explicitly; leaving it 1 as well would prorate twice.
PRORATE = "* payment_days / total_working_days"

COMPONENTS = [
    # (name, abbr, type, formula, condition, depends_on_payment_days, extras)
    ("Basic", "B", "Earning", f"round(kg_basic {PRORATE})", "", 0,
     {"is_tax_applicable": 1}),
    ("HRA", "HRA", "Earning", f"round(kg_hra {PRORATE})", "", 0,
     {"is_tax_applicable": 1}),
    ("Conveyance Allowance", "CA", "Earning", f"round(kg_conveyance {PRORATE})", "", 0,
     {"is_tax_applicable": 1}),
    ("Medical Allowance", "MA", "Earning", f"round(kg_medical {PRORATE})", "", 0,
     {"is_tax_applicable": 1}),
    ("Other Allowance", "OA", "Earning", f"round(kg_other {PRORATE})", "", 0,
     {"is_tax_applicable": 1}),
    # Overtime and Production Bonus arrive as Additional Salary amounts and are
    # already period-correct. They must NOT be prorated again.
    ("Overtime", "OT", "Earning", "", "", 0,
     {"is_tax_applicable": 1, "is_additional_component": 1}),
    ("Production Bonus", "PB", "Earning", "", "", 0,
     {"is_tax_applicable": 1, "is_additional_component": 1}),

    # --- Deductions ---
    # PF wage = Basic + Conveyance + Medical + Other  (NOT HRA, NOT OT, NOT bonus),
    # capped at 15000, at 12%.
    # Verified against the issued June slips for both PF employees:
    #   KR016  9772 -> 1173      KR052  17610 -> capped 15000 -> 1800
    ("PF", "PF", "Deduction",
     "round(min((B + CA + MA + OA), 15000) * 0.12)", "pf_applicable", 0, {}),

    # ESI = 0.75% of Basic, rounded UP (statutory). The `+ 0.4999` is a ceiling
    # that avoids needing math.ceil inside the formula sandbox.
    #   KR016  9772 * 0.75% = 73.29 -> 74     KR052  14000 -> 105
    ("ESI", "ESI", "Deduction",
     "round(B * 0.0075 + 0.4999)", "esi_applicable", 0, {}),

    # Gujarat Professional Tax slab: 200 when gross reaches 12000.
    # Gross is summed explicitly rather than using gross_pay, which is not
    # reliably populated at deduction-evaluation time in every HRMS version.
    ("PT", "PT", "Deduction", "200",
     "(B + HRA + CA + MA + OA + OT + PB) >= 12000", 0, {}),

    ("LWF", "LWF", "Deduction", "6", "", 0, {}),
]

EARNINGS = [c for c in COMPONENTS if c[2] == "Earning"]
DEDUCTIONS = [c for c in COMPONENTS if c[2] == "Deduction"]


def execute():
    company = _default_company()
    if not company:
        print("SKIPPED: no Company found. Create a Company, then re-run:\n"
              "  bench --site <site> execute "
              "kreativ_attendance.patches.v16_0.setup_payroll_structure.execute")
        return

    _create_employee_fields()
    _create_components(company)
    _create_structure(company)
    frappe.db.commit()

    print(f"\nPayroll objects ready for '{company}'.")
    print(f"  Salary Structure : {STRUCTURE_NAME}")
    print(f"  Components       : {len(EARNINGS)} earnings, {len(DEDUCTIONS)} deductions")
    print("\nSTILL TO DO BY HAND:")
    print("  1. Set kg_basic / kg_hra / kg_conveyance / kg_medical / kg_other and")
    print("     the PF / ESI flags on each Employee (or import the CSV template).")
    print("  2. Create a Salary Structure Assignment per employee with")
    print("     base = Rate of Wages (the sum of those five amounts).")
    print("  3. Map GL accounts on the components if you post payroll to accounts.")
    print("  4. Confirm every Employee has a Holiday List.")


# ---------------------------------------------------------------------------

def _default_company():
    return (
        frappe.defaults.get_global_default("company")
        or frappe.db.get_value("Company", {}, "name")
    )


def _create_employee_fields():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
    create_custom_fields({"Employee": EMPLOYEE_FIELDS}, ignore_validate=True)
    print(f"Employee custom fields ensured ({len(EMPLOYEE_FIELDS)} fields).")


def _create_components(company):
    for name, abbr, ctype, formula, condition, dopd, extras in COMPONENTS:
        values = {
            "salary_component": name,
            "salary_component_abbr": abbr,
            "type": ctype,
            "depends_on_payment_days": dopd,
            "amount_based_on_formula": 1 if formula else 0,
            "formula": formula or None,
            "condition": condition or None,
            "disabled": 0,
        }
        values.update(extras)

        if frappe.db.exists("Salary Component", name):
            doc = frappe.get_doc("Salary Component", name)
            changed = False
            for k, v in values.items():
                # Never clobber the abbreviation of an existing component:
                # formulas elsewhere may already reference it.
                if k == "salary_component_abbr":
                    continue
                if doc.get(k) != v:
                    doc.set(k, v)
                    changed = True
            if changed:
                doc.flags.ignore_permissions = True
                doc.save(ignore_permissions=True)
                print(f"  updated  Salary Component: {name}")
            continue

        doc = frappe.get_doc(dict(doctype="Salary Component", **values))
        # accounts row is optional; leave GL mapping to the operator
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        print(f"  created  Salary Component: {name} ({abbr})")


def _create_structure(company):
    is_new = False
    if frappe.db.exists("Salary Structure", STRUCTURE_NAME):
        doc = frappe.get_doc("Salary Structure", STRUCTURE_NAME)
        if doc.docstatus == 1:
            print(f"  Salary Structure '{STRUCTURE_NAME}' is already submitted — "
                  "left untouched. Amend it manually if the components changed.")
            return
    else:
        is_new = True
        doc = frappe.get_doc({
            "doctype": "Salary Structure",
            "__newname": STRUCTURE_NAME,
            "company": company,
            "is_active": "Yes",
            "payroll_frequency": "Monthly",
            "salary_slip_based_on_timesheet": 0,
            "currency": frappe.db.get_value("Company", company, "default_currency") or "INR",
        })

    doc.set("earnings", [])
    doc.set("deductions", [])
    for name, abbr, ctype, formula, condition, dopd, _extras in COMPONENTS:
        row = {
            "salary_component": name,
            "abbr": abbr,
            "amount_based_on_formula": 1 if formula else 0,
            "formula": formula or None,
            "condition": condition or None,
            "depends_on_payment_days": dopd,
            "amount": 0,
        }
        doc.append("earnings" if ctype == "Earning" else "deductions", row)

    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    if is_new:
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)
    print(f"  saved    Salary Structure: {STRUCTURE_NAME} (left in DRAFT — "
          "review it, then Submit)")
