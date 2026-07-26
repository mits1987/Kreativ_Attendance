# Kreativ Attendance

Frappe/ERPNext app for employee attendance management, shift scheduling, and WhatsApp notifications.

**Current Version:** v0.2.0 (2026-07-26)

---

## Features

### Employee Shift Management
- **Employee Shift** - Define shift types (9h, 12h, Monthly) with OT rules
- **Employee Shift Lock** - Lock shifts after payroll to prevent edits
- **Employee Standard Hours** - Configure standard working hours per shift
- **Shift Auto-Calculation** - Automatic shift calculation from biometric punches

### Biometric Integration
- **ZKTeco Sync** - Sync checkins from ZKTeco devices via EasyTime Pro API
- **Punch Pairing** - Smart IN/OUT pairing with overnight shift support
- **Deduplication** - Automatic deduplication of shift recalculation jobs

### WhatsApp Notifications (v0.2.0)
- **Real-time Checkin Alerts** — IN/OUT notifications via self-hosted OpenWA
- **Instant delivery** — `after_insert` hook with `enqueue_after_commit=True`
- **Cron fallback** — Every 5 min catches any missed notifications
- **Employee Mobile Delivery** — Messages sent to employee's own WhatsApp
- **Deduplication** — Prevents duplicate notifications via `whatsapp_sent` field
- **Fallback Support** — Admin notification if employee mobile missing

### Dashboard & Reports
- **Attendance Dashboard** - Real-time attendance overview
- **Employee Shift Summary Report** - Monthly shift reports with OT

### Payroll Integration
- **Shift Lock on Salary Slip** - Auto-lock shifts when salary slip submitted
- **HRMS Payroll Bridge** - Integration with ERPNext HRMS payroll

---

## Release v0.2.0 (2026-07-26)

| Feature | Details |
|---------|---------|
| **ZKTeco Sync** | JWT token caching (23h TTL) — 288 req/day → 1-2/day |
| **Bulk SQL Ops** | Shift delete/insert via single DELETE + `bulk_insert` (~40x faster) |
| **Punch Pairing** | Overnight shifts, carryover logic, ±2 day fetch window |
| **Shift Lock** | Payroll integration — auto-lock on Salary Slip submit |
| **WhatsApp Notifications** | Instant (webhook) + cron fallback, systemd worker persistence |
| **Test Fixtures** | `test_*.py` scripts with pytest, 80%+ coverage |

---

## Installation

```bash
bench get-app https://github.com/mits1987/Kreativ_Attendance.git
bench --site your-site install-app kreativ_attendance
bench --site your-site migrate
```

## Configuration

### OpenWA Settings
1. Go to **OpenWA Settings** (Single DocType)
2. Configure:
   - **Base URL** - `http://localhost:2785` (or your OpenWA host)
   - **API Key** - From OpenWA dashboard
   - **Session ID** - WhatsApp session ID (default: `default`)
   - **Notify On** - `IN and OUT` / `IN only` / `OUT only`
   - **Test Mode** - Enable for testing (sends to admin chat)

### Employee Mobile Numbers
Ensure all employees have valid mobile numbers in **Employee > Cell Number** field.

---

## Architecture

```
kreativ_attendance/
├── attendance/
│   ├── pairing.py          # IN/OUT punch pairing logic
│   ├── service.py          # Shift recalculation service (bulk SQL)
│   ├── lock.py             # Shift lock mechanism
│   ├── whatsapp.py         # WhatsApp notifications via OpenWA
│   └── hooks.py            # Document event hooks
├── doctype/
│   ├── employee_shift/             # Shift definitions
│   ├── employee_shift_lock/        # Shift locks for payroll
│   ├── employee_standard_hours/    # Standard hours per shift
│   └── openwa_settings/            # OpenWA configuration
├── attendance_dashboard/           # Dashboard page
└── employee_shift_summary/         # Report
```

---

## Background Jobs

- **Shift Recalculation** - Queued per employee/month (deduplicated)
- **WhatsApp Notifications** - Short queue, 60s timeout, systemd persistence
- **Salary Slip Lock** - On submit, locks employee shifts for period

---

## Testing

```bash
bench --site your-site run-tests --module kreativ_attendance.attendance.tests.run_all_tests
```

---

## License

MIT