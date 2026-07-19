"""kreativ_attendance.attendance — attendance engine for Kreativ.

Exports:
    pairing: pure pairing logic (testable without Frappe)
    service:  glue between pairing and Frappe DB + doctypes
    lock:     Employee Shift Lock implementation
    api:      whitelisted endpoints (recalculate, unlock, sync-to-HRMS)
    hrms:     bridge to HRMS Attendance + Additional Salary
"""