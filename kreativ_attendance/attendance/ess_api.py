"""Employee Self Service API endpoints for kreativ_ess PWA.

These endpoints mirror the employee_self_service.mobile.v1 API
but use kreativ_attendance data structures.
"""
import frappe
from frappe import _
import hashlib


@frappe.whitelist(allow_guest=True)
def login(usr: str = None, pwd: str = None) -> dict:
    """Authenticate employee and return session."""
    if not usr or not pwd:
        frappe.throw(_("Username and password required"))

    # Try Employee by cell_number or user_id
    emp = frappe.db.get_value(
        "Employee",
        {"cell_number": ["like", f"%{usr}%"]},
        ["name", "employee_name", "user_id", "company", "department", "designation", "image"],
        as_dict=True,
    )

    if not emp:
        # Try by user_id
        user = frappe.db.get_value("User", {"name": usr, "enabled": 1}, ["name", "full_name"], as_dict=True)
        if user:
            emp = frappe.db.get_value(
                "Employee",
                {"user_id": user.name},
                ["name", "employee_name", "user_id", "company", "department", "designation", "image"],
                as_dict=True,
            )

    if not emp:
        frappe.throw(_("Invalid credentials"))

    # Verify password
    user_id = emp.user_id or usr
    if not frappe.db.get_value("User", user_id, "enabled"):
        frappe.throw(_("Invalid credentials"))

    # Check password
    from frappe.auth import check_password
    if not check_password(user_id, pwd):
        frappe.throw(_("Invalid credentials"))

    # Login successful - create session
    frappe.local.login_manager.login_as(user_id)

    sid = frappe.session.sid

    return {
        "session": {"sid": sid, "user": frappe.session.user},
        "employee": {
            "name": emp.name,
            "employee_name": emp.employee_name,
            "company": emp.company,
            "department": emp.department,
            "designation": emp.designation,
            "user_id": emp.user_id,
        },
        "user": {"name": frappe.session.user},
    }


@frappe.whitelist()
def logout() -> dict:
    """Logout current session."""
    frappe.local.login_manager.logout()
    return {"message": "Logged out"}


@frappe.whitelist()
def get_dashboard() -> dict:
    """Get dashboard data for current employee."""
    emp = _get_current_employee()
    if not emp:
        return {}

    from kreativ_attendance.attendance.api_ui import daily_checkins
    from datetime import date

    today = date.today().isoformat()
    checkin_data = daily_checkins(date=today, employee=emp.name)

    # Leave balance
    leave_balance = _get_leave_balance(emp.name)

    # Quick stats
    return {
        "company_name": emp.company or "Kreativ Gravures",
        "employee_name": emp.employee_name,
        "total_days": checkin_data.get("stats", {}).get("total_employees", 0),
        "present": checkin_data.get("stats", {}).get("checked_in", 0),
        "absent": checkin_data.get("stats", {}).get("not_checked_in", 0),
        "late": 0,  # TODO: calculate from check-ins
        "leave_balance": leave_balance,
        "notice_board": _get_notice_board(),
    }


@frappe.whitelist()
def get_leave_balance_dashboard() -> list:
    """Get leave balance for current employee."""
    emp = _get_current_employee()
    return _get_leave_balance(emp.name) if emp else []


@frappe.whitelist()
def get_leave_application_list() -> list:
    """Get leave applications for current employee."""
    emp = _get_current_employee()
    if not emp:
        return []

    return frappe.get_all(
        "Leave Application",
        filters={"employee": emp.name, "docstatus": ["!=", 2]},
        fields=["name", "leave_type", "from_date", "to_date", "total_leave_days", "status"],
        order_by="from_date desc",
        limit=20,
    )


@frappe.whitelist()
def get_expense_list() -> list:
    """Get expense claims for current employee."""
    emp = _get_current_employee()
    if not emp:
        return []

    return frappe.get_all(
        "Expense Claim",
        filters={"employee": emp.name, "docstatus": ["!=", 2]},
        fields=["name", "total_claimed_amount", "total_sanctioned_amount", "status", "posting_date"],
        order_by="posting_date desc",
        limit=20,
    )


@frappe.whitelist()
def get_salary_sllip() -> list:
    """Get salary slips for current employee."""
    emp = _get_current_employee()
    if not emp:
        return []

    return frappe.get_all(
        "Salary Slip",
        filters={"employee": emp.name, "docstatus": 1},
        fields=["name", "start_date", "end_date", "net_pay", "total_deduction"],
        order_by="start_date desc",
        limit=12,
    )


@frappe.whitelist()
def get_task_list() -> list:
    """Get tasks assigned to current employee."""
    emp = _get_current_employee()
    if not emp:
        return []

    return frappe.get_all(
        "Task",
        filters={"_assign": ["like", f"%{frappe.session.user}%"], "status": ["!=", "Completed"]},
        fields=["name", "subject", "description", "exp_end_date", "status"],
        order_by="exp_end_date asc",
        limit=20,
    )


@frappe.whitelist()
def get_directory() -> list:
    """Get employee directory."""
    return frappe.get_all(
        "Employee",
        filters={"status": "Active"},
        fields=["name", "employee_name", "designation", "department", "cell_number", "image", "user_id"],
        order_by="employee_name",
        limit=100,
    )


def _get_current_employee() -> dict | None:
    """Get employee record for current session user."""
    emp = frappe.db.get_value(
        "Employee",
        {"user_id": frappe.session.user},
        ["name", "employee_name", "company", "department", "designation", "user_id", "cell_number", "image"],
        as_dict=True,
    )
    return emp


def _get_leave_balance(employee: str) -> list:
    """Get leave balance for employee."""
    # Get Leave Ledger Entries - use 'leaves' field (not 'leaves_taken')
    # transaction_type distinguishes: Leave Allocation = positive, Leave Application = negative
    balances = frappe.get_all(
        "Leave Ledger Entry",
        filters={"employee": employee, "is_carry_forward": 0},
        fields=["leave_type", "leaves", "transaction_type", "from_date", "to_date"],
    )

    # Aggregate by leave type
    agg = {}
    for b in balances:
        lt = b.leave_type
        if lt not in agg:
            agg[lt] = {"leave_type": lt, "total_allocated": 0, "leaves_taken": 0, "remaining": 0}
        leaves_val = float(b.leaves or 0)
        if b.transaction_type == "Leave Allocation":
            agg[lt]["total_allocated"] += leaves_val
        else:
            # Leave Application, Leave Encashment, etc. - treat as taken
            agg[lt]["leaves_taken"] += abs(leaves_val)

    # Also get Leave Allocation records as fallback (in case ledger is incomplete)
    allocations = frappe.get_all(
        "Leave Allocation",
        filters={"employee": employee, "docstatus": 1},
        fields=["leave_type", "total_leaves_allocated"],
    )
    for a in allocations:
        lt = a.leave_type
        if lt not in agg:
            agg[lt] = {"leave_type": lt, "total_allocated": 0, "leaves_taken": 0, "remaining": 0}
        agg[lt]["total_allocated"] += float(a.total_leaves_allocated or 0)

    for v in agg.values():
        v["remaining"] = v["total_allocated"] - v["leaves_taken"]

    return list(agg.values())


def _get_notice_board() -> list:
    """Get recent notices/announcements."""
    doctype = "KG Notice Board" if frappe.db.exists("DocType", "KG Notice Board") else "Comment"
    fields = ["subject", "content", "creation"]
    if doctype == "KG Notice Board" and frappe.db.has_column("KG Notice Board", "published_on"):
        fields.append("published_on")
    return frappe.get_all(
        doctype,
        filters={},
        fields=fields,
        order_by="creation desc",
        limit=5,
    )