import frappe
from frappe.modules.import_file import import_file_by_path
import os

def execute():
    app_path = '/home/mitesh/frappe-bench-v16/apps/kreativ_attendance/kreativ_attendance'
    for module in ['kreativ_attendance', 'attendance', 'config', 'fixtures', 'page', 'public', 'report']:
        module_path = os.path.join(app_path, module)
        if os.path.exists(module_path):
            doctype_path = os.path.join(module_path, 'doctype')
            if os.path.exists(doctype_path):
                for dt in os.listdir(doctype_path):
                    dt_path = os.path.join(doctype_path, dt)
                    if os.path.isdir(dt_path):
                        json_file = os.path.join(dt_path, f'{dt}.json')
                        if os.path.exists(json_file):
                            try:
                                import_file_by_path(json_file)
                                print(f"Imported: {dt} from {module}")
                            except Exception as e:
                                print(f"Error {dt}: {e}")
    frappe.db.commit()
