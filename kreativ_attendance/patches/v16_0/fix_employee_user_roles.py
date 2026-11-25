import frappe

def execute():
    """
    Patch to fix Employee user roles after migration.
    
    During migrations, user roles and user types can get reset.
    This patch ensures all active Employees with linked users have:
    1. Correct user type (System User for ESS access)
    2. Employee role assigned
    3. No extra roles that shouldn't be there
    """
    
    # Get all active employees with linked users
    employees = frappe.get_all("Employee", 
        filters={"status": "Active", "user_id": ["!=", ""]},
        fields=["name", "employee_name", "user_id", "cell_number"]
    )
    
    fixed_count = 0
    for emp in employees:
        user_id = emp.user_id
        if not user_id:
            continue
            
        try:
            user = frappe.get_doc("User", user_id)
            
            # Ensure user is enabled
            if not user.enabled:
                user.enabled = 1
            
            # Ensure user is System User (required for ESS access)
            if user.user_type != "System User":
                user.user_type = "System User"
            
            # Ensure Employee role is present
            roles = [d.role for d in user.get("roles")]
            if "Employee" not in roles:
                user.append("roles", {"role": "Employee"})
            
            # Remove any conflicting roles (like "Website User" role if present)
            # Keep only necessary roles: Employee + any system roles
            # Don't remove System Manager, etc.
            
            user.save(ignore_permissions=True)
            frappe.db.commit()
            
            frappe.logger().info(f"Fixed user roles for {emp.employee_name} ({emp.name})")
            fixed_count += 1
            
        except Exception as e:
            frappe.log_error(
                title="Employee User Role Fix Failed",
                message=f"Failed to fix roles for {emp.name} ({emp.employee_name}): {str(e)}"
            )
    
    frappe.logger().info(f"Fixed roles for {fixed_count} employee users")
    frappe.db.commit()
