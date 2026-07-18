"""Install / post-rebuild hook for kreativ_attendance.

Ensures Scheduled Job Types from hooks.py are always synced to the DB.
Run on every `bench migrate` (after_install) and `bench install-app`.
"""

import frappe


def after_install():
	"""Called during `bench install-app kreativ_attendance`."""
	sync_scheduled_jobs()


def after_migrate():
	"""Called during `bench migrate` after all patches run."""
	sync_scheduled_jobs()


def sync_scheduled_jobs():
	"""Sync scheduler_events from hooks.py to tabScheduled Job Type.

	This mirrors what `frappe.desk.doctype.scheduled_job_type.scheduled_job_type.sync_jobs`
	does internally, but runs *after* app patches so custom cron jobs are registered
	before the scheduler picks them up.
	"""
	hooks = frappe.get_hooks("scheduler_events", app_name="kreativ_attendance")
	if not hooks:
		return

	from frappe.desk.doctype.scheduled_job_type.scheduled_job_type import sync_jobs
	sync_jobs(hooks)
	frappe.db.commit()


def validate_scheduled_jobs():
	"""Optional: run as a Scheduled Job itself to alert on drift.

	Add to hooks.py scheduler_events:
	    "daily": ["kreativ_attendance.install.validate_scheduled_jobs"]

	Or call manually: `bench execute kreativ_attendance.install.validate_scheduled_jobs`
	"""
	hooks = frappe.get_hooks("scheduler_events", app_name="kreativ_attendance")
	if not hooks:
		return

	# Query DB directly for registered job methods
	db_methods = frappe.get_all("Scheduled Job Type", pluck="method", filters={"stopped": 0})
	db_jobs = set(db_methods)

	expected = set()
	for freq, methods in hooks.items():
		if isinstance(methods, list):
			expected.update(methods)

	missing = expected - db_jobs
	if missing:
		frappe.log_error(
			title="Scheduled Job Drift Detected",
			message=(
				f"The following kreativ_attendance jobs are defined in hooks.py "
				f"but MISSING from tabScheduled Job Type:\n\n"
				f"{chr(10).join(sorted(missing))}\n\n"
				f"Run `bench --site {frappe.local.site} migrate` to sync."
			),
		)