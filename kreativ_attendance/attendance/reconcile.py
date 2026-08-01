"""Reconciliation harness: compare this system against an already-paid month.

THIS IS THE CUTOVER TEST. Do not turn Shadow Mode off until a month you have
already paid reconciles to the rupee.

    bench --site <site> execute \
      kreativ_attendance.attendance.reconcile.print_month \
      --kwargs "{'year': 2026, 'month': 6}"

Prints one line per employee: hours worked, derived PD, pay days, overtime, and
the gross/net this system would produce. Compare against your salary sheet.

Expected mismatches on a historical month:
  * employees who joined or left mid-month
  * months where punches were corrected on paper but never in the device
"""
import frappe

from kreativ_attendance.attendance.summary import build_month, SUMMARY_DOCTYPE
from kreativ_attendance.attendance.calendar_util import period_bounds


def print_month(year: int, month: int, rebuild: bool = True):
    year, month = int(year), int(month)
    if rebuild:
        res = build_month(year, month)
        print(f"Rebuilt summaries: {res['created']} created, {res['updated']} updated, "
              f"{res['preserved']} preserved")

    rows = frappe.get_all(
        SUMMARY_DOCTYPE,
        filters={"period_year": year, "period_month": month},
        fields=["employee", "employee_name", "standard_hours", "total_hours",
                "wd", "wo", "ph", "pd", "pay_days", "ot_hours", "ot_amount",
                "anomaly_count", "days_in_month", "hours_source"],
        order_by="employee",
    )
    if not rows:
        print(f"No summaries for {year}-{month:02d}.")
        return

    print(f"\n{year}-{month:02d}  ({len(rows)} employees)\n")
    hdr = (f"{'EMPLOYEE':12} {'NAME':26} {'STD':>5} {'HOURS':>8} {'WD':>5} "
           f"{'WO':>4} {'PH':>4} {'PD':>6} {'PAYD':>6} {'OT_HRS':>7} {'OT_AMT':>8} {'ANOM':>5}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        flag = " <-- ANOMALIES" if r.anomaly_count else ""
        print(f"{r.employee:12} {(r.employee_name or '')[:26]:26} "
              f"{r.standard_hours or 0:>5.1f} {r.total_hours or 0:>8.2f} "
              f"{r.wd or 0:>5.1f} {r.wo or 0:>4.1f} {r.ph or 0:>4.1f} "
              f"{r.pd or 0:>6.2f} {r.pay_days or 0:>6.2f} "
              f"{r.ot_hours or 0:>7.2f} {r.ot_amount or 0:>8.0f} "
              f"{r.anomaly_count or 0:>5}{flag}")

    anomalies = sum(1 for r in rows if r.anomaly_count)
    defaults = [r.employee for r in rows if r.hours_source != "Employee.working_hours"]
    print("-" * len(hdr))
    print(f"Totals: PD {sum(r.pd or 0 for r in rows):.2f}  "
          f"Pay Days {sum(r.pay_days or 0 for r in rows):.2f}  "
          f"OT {sum(r.ot_amount or 0 for r in rows):.0f}")
    if anomalies:
        print(f"\n{anomalies} employee(s) have unresolved anomalies. Their hours "
              "are understated and their PD will be too low.")
    if defaults:
        print(f"\nNo Working Hours set (using settings default): {', '.join(defaults)}")


def compare_with_slips(year: int, month: int, expected: dict):
    """Compare derived pay days against a dict of {employee_id: expected_pay_days}."""
    year, month = int(year), int(month)
    rows = frappe.get_all(
        SUMMARY_DOCTYPE,
        filters={"period_year": year, "period_month": month},
        fields=["employee", "employee_name", "pd", "pay_days"],
    )
    by_id = {}
    for r in rows:
        eid = frappe.db.get_value("Employee", r.employee, "employee_number") or r.employee
        by_id[eid] = r

    ok = bad = 0
    for eid, exp in sorted(expected.items()):
        r = by_id.get(eid)
        if not r:
            print(f"{eid:10} MISSING from summaries")
            bad += 1
            continue
        got = float(r.pay_days or 0)
        if abs(got - float(exp)) < 0.01:
            ok += 1
        else:
            bad += 1
            print(f"{eid:10} {(r.employee_name or '')[:24]:24} "
                  f"pay_days {got:.2f} expected {exp:.2f}  diff {got - float(exp):+.2f}")
    print(f"\n{ok} matched, {bad} differ")
