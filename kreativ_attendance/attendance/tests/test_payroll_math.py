"""Payroll arithmetic tests. Pure Python -- no bench required.

    python -m unittest kreativ_attendance.attendance.tests.test_payroll_math

The June 2026 fixture below is the acceptance test: every figure is taken from
the salary slips and wage register actually issued to employees. If any of these
fail, the calculation has drifted and must not be used for payroll.
"""
import unittest

from kreativ_attendance.attendance.payroll_math import (
    compute_attendance,
    compute_overtime_amount,
    compute_pay_days,
    compute_salary,
    prorate,
    _round,
)


class TestRounding(unittest.TestCase):
    def test_half_rounds_away_from_zero_not_to_even(self):
        # Python's built-in round() gives 0 here (banker's rounding); Excel gives 1.
        self.assertEqual(_round(0.5), 1)
        self.assertEqual(_round(1.5), 2)
        self.assertEqual(_round(2.5), 3)

    def test_prorate_matches_excel(self):
        # KR057: 21500 / 30 * 28 = 20066.67 -> 20067
        self.assertEqual(prorate(21500, 30, 28), 20067)
        # KR105: 21500 / 30 * 16 = 11466.67 -> 11467
        self.assertEqual(prorate(21500, 30, 16), 11467)


class TestAttendance(unittest.TestCase):
    def test_worked_example_from_spec(self):
        """27 days open, 9h shift, worked 250h -> full attendance + 7h OT."""
        a = compute_attendance(total_hours=250, standard_hours=9, wd=27)
        self.assertEqual(a["required_hours"], 243)
        self.assertEqual(a["pd"], 27)
        self.assertEqual(a["ot_hours"], 7)
        self.assertEqual(compute_overtime_amount(7, 20000, 27, 9), 576)

    def test_pd_capped_at_working_days(self):
        a = compute_attendance(total_hours=400, standard_hours=9, wd=27)
        self.assertEqual(a["pd"], 27)

    def test_ot_and_partial_attendance_are_mutually_exclusive(self):
        a = compute_attendance(total_hours=200, standard_hours=9, wd=27)
        self.assertLess(a["pd"], 27)
        self.assertEqual(a["ot_hours"], 0)

    def test_pd_rounds_to_nearest_half(self):
        # 103.5 / 9 = 11.5 exactly
        self.assertEqual(compute_attendance(103.5, 9, 27)["pd"], 11.5)
        # 100 / 9 = 11.11 -> 11.0
        self.assertEqual(compute_attendance(100, 9, 27)["pd"], 11.0)
        # 104 / 9 = 11.56 -> 11.5
        self.assertEqual(compute_attendance(104, 9, 27)["pd"], 11.5)

    def test_zero_standard_hours_is_safe(self):
        a = compute_attendance(total_hours=100, standard_hours=0, wd=27)
        self.assertEqual(a["pd"], 0)
        self.assertEqual(a["ot_hours"], 0)

    def test_per_day_and_monthly_aggregation_agree(self):
        """With a straight OT rate the two models give the same total.

        Per-day:  sum(min(1, hours/std))  +  sum(max(0, hours-std))/std
        Monthly:  total_hours / std

        Both collapse to total_hours/std, so choosing daily or monthly
        aggregation cannot change anyone's pay. (They would diverge only if
        overtime carried a premium rate, which it does not here.)
        """
        for days in ([12, 4, 9, 14, 3, 9, 9], [8, 8, 8], [20, 1, 30, 2], [9] * 7):
            std = 9
            monthly_unrounded = sum(days) / std
            daily_equiv = (
                sum(min(1.0, d / std) for d in days)
                + sum(max(0.0, d - std) for d in days) / std
            )
            self.assertAlmostEqual(monthly_unrounded, daily_equiv, places=9,
                                   msg=f"diverged for {days}")

    def test_half_day_rounding_drift_is_bounded(self):
        """PD rounds to the nearest 0.5, so it can differ from the exact
        quotient by at most 0.25 days in either direction. This matches the
        granularity used on the approved salary sheet."""
        std, wd = 9, 27
        for hours in range(0, 244):
            exact = min(wd, hours / std)
            pd = compute_attendance(hours, std, wd)["pd"]
            self.assertLessEqual(abs(pd - exact), 0.2501, msg=f"{hours}h")
            self.assertEqual(pd * 2, int(pd * 2), msg=f"{hours}h not on a half")


class TestPayDays(unittest.TestCase):
    def test_pay_days_is_pd_plus_wo_plus_ph(self):
        self.assertEqual(compute_pay_days(26, 4, 0), 30)
        self.assertEqual(compute_pay_days(11.5, 4, 0), 15.5)


# ---------------------------------------------------------------------------
# June 2026 acceptance fixture
# (basic, hra, con, med, other, pd, wo, ph, incentive, pf, esi, it, adv, loan)
# expected gross / net are from the ISSUED slips.
# ---------------------------------------------------------------------------
JUNE = [
    ("KR011", 21500, 3200, 0, 300, 1250, 26, 4, 0, 1264, 0, 0, 0, 0, 0, 27514, 27308),
    ("KR016", 13325, 0, 0, 0, 0, 18, 4, 0, 0, 1, 1, 0, 0, 0, 9772, 8519),
    ("KR018", 25000, 10000, 0, 6000, 9000, 26, 4, 0, 0, 0, 0, 0, 0, 3000, 50000, 46794),
    ("KR019", 23000, 7300, 0, 2075, 5950, 26, 4, 0, 0, 0, 0, 0, 0, 10000, 38325, 28119),
    ("KR052", 14000, 1500, 0, 2700, 910, 26, 4, 0, 823, 1, 1, 0, 0, 0, 19933, 17822),
    ("KR053", 25000, 7000, 0, 5000, 4000, 26, 4, 0, 0, 0, 0, 0, 0, 0, 41000, 40794),
    ("KR057", 21500, 0, 0, 500, 1100, 24, 4, 0, 0, 0, 0, 0, 0, 2500, 21561, 18855),
    ("KR059", 21500, 0, 0, 500, 1100, 26, 4, 0, 5091, 0, 0, 0, 0, 0, 28191, 27985),
    ("KR074", 21500, 8200, 0, 6000, 7000, 25, 4, 0, 0, 0, 0, 0, 0, 3000, 41277, 38071),
    ("KR084", 21500, 8000, 0, 6000, 6500, 26, 4, 0, 0, 0, 0, 0, 0, 3000, 42000, 38794),
    ("KR090", 21500, 0, 0, 950, 863, 26, 4, 0, 2449, 0, 0, 0, 0, 2000, 25762, 23556),
    ("KR097", 21500, 4500, 0, 4000, 1500, 25, 4, 0, 0, 0, 0, 0, 0, 0, 30450, 30244),
    ("KR098", 22000, 8800, 0, 7000, 8400, 26, 4, 0, 0, 0, 0, 0, 0, 14000, 46200, 31994),
    ("KR099", 21500, 6300, 0, 3700, 1075, 26, 4, 0, 1484, 0, 0, 0, 0, 0, 34059, 33853),
    ("KR104", 21500, 4500, 0, 4000, 1125, 26, 4, 0, 0, 0, 0, 0, 0, 0, 31125, 30919),
    ("KR105", 21500, 6800, 0, 5500, 200, 12, 4, 0, 22, 0, 0, 0, 0, 0, 18156, 17950),
    ("KR106", 21500, 6480, 0, 4200, 220, 11.5, 4, 0, 0, 0, 0, 0, 0, 5013, 16740, 11521),
    ("KR107", 21500, 6480, 0, 4200, 220, 14.5, 4, 0, 306, 0, 0, 0, 0, 0, 20286, 20080),
    ("KR109", 21500, 6400, 0, 4100, 1600, 11, 4, 0, 0, 0, 0, 0, 0, 0, 16800, 16594),
    ("KR111", 21500, 6200, 0, 300, 1400, 13, 4, 0, 300, 0, 0, 0, 0, 0, 16959, 16753),
    ("KR112", 21500, 4200, 0, 300, 1300, 10.5, 4, 0, 0, 0, 0, 0, 0, 0, 13195, 12989),
    ("KR113", 21500, 0, 0, 6500, 0, 3.5, 0, 0, 372, 0, 0, 0, 0, 0, 3638, 3632),
    ("KR114", 21500, 0, 0, 4500, 0, 9, 4, 0, 627, 0, 0, 0, 0, 5000, 11894, 6888),
    ("KR115", 21500, 0, 0, 2500, 0, 11, 4, 0, 180, 0, 0, 0, 0, 0, 12180, 11974),
    ("KR116", 21500, 0, 0, 2500, 0, 12, 4, 0, 50, 0, 0, 0, 0, 0, 12850, 12644),
    ("KR117", 21500, 6200, 0, 6000, 1500, 26, 4, 0, 0, 0, 0, 0, 0, 0, 35200, 34994),
    ("KR118", 21500, 2200, 0, 300, 0, 7, 3, 0, 0, 0, 0, 0, 0, 0, 8000, 7994),
    ("KR119", 21500, 0, 0, 500, 0, 8, 3, 0, 206, 0, 0, 0, 0, 0, 8272, 8266),
    ("KR120", 21500, 10000, 0, 98500, 200, 26, 4, 0, 0, 0, 0, 0, 0, 0, 130200, 129994),
]


class TestJune2026Reconciliation(unittest.TestCase):
    """Every issued June 2026 slip must reproduce exactly."""

    def _run(self, row):
        (_id, basic, hra, con, med, other, pd, wo, ph,
         inc, pf, esi, it, adv, loan, _g, _n) = row
        return compute_salary(
            basic=basic, hra=hra, con=con, med=med, other=other,
            pd=pd, wo=wo, ph=ph, days_in_month=30,
            incentive=inc, overtime=0,
            pf_applicable=bool(pf), esi_applicable=bool(esi),
            it=it, advance=adv, loan=loan, lwf=6,
        )

    def test_all_employees_gross_and_net(self):
        failures = []
        for row in JUNE:
            res = self._run(row)
            eid, eg, en = row[0], row[-2], row[-1]
            if abs(res["gross"] - eg) > 0.5 or abs(res["net"] - en) > 0.5:
                failures.append(f"{eid}: gross {res['gross']} vs {eg}, "
                                f"net {res['net']} vs {en}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_total_net_matches_wage_register(self):
        total = sum(self._run(r)["net"] for r in JUNE)
        self.assertAlmostEqual(total, 755900, places=0)

    def test_pf_base_excludes_hra_and_incentive(self):
        """KR052: PF wage = basic+con+med+other = 17610, capped 15000 -> 1800.

        The spreadsheet cell AA9 reads `=T9` (basic only) which would give 1680.
        The issued slip shows 1800, so `=T9` is a typing error and the row-6
        definition is authoritative.
        """
        res = self._run([r for r in JUNE if r[0] == "KR052"][0])
        self.assertEqual(res["pf_wage"], 17610)
        self.assertEqual(res["pf"], 1800)

    def test_esi_rounds_up_not_to_nearest(self):
        """KR016: 9772 * 0.75% = 73.29. Sheet ROUND() gives 73; statute gives 74."""
        res = self._run([r for r in JUNE if r[0] == "KR016"][0])
        self.assertEqual(res["esi"], 74)

    def test_esi_base_is_basic_only(self):
        """KR052: ESI 105 = 0.75% of 14000 (basic), not of basic+incentive."""
        res = self._run([r for r in JUNE if r[0] == "KR052"][0])
        self.assertEqual(res["esi_wage"], 14000)
        self.assertEqual(res["esi"], 105)

    def test_pt_slab(self):
        self.assertEqual(compute_salary(basic=12000, pd=30, wo=0, ph=0,
                                        days_in_month=30)["pt"], 200)
        self.assertEqual(compute_salary(basic=11999, pd=30, wo=0, ph=0,
                                        days_in_month=30)["pt"], 0)


if __name__ == "__main__":
    unittest.main()
