"""Tests for the payroll quality gate (quality.py)."""
import unittest


class TestQuality(unittest.TestCase):

    def test_format_issues_empty(self):
        from kreativ_attendance.attendance.quality import format_issues
        out = format_issues({
            "anomalies": [], "long_sessions": [], "break_punches": [],
            "missing_standard_hours": [],
        })
        assert out == "No issues."

    def test_format_issues_lists_everything(self):
        from kreativ_attendance.attendance.quality import format_issues
        out = format_issues({
            "anomalies": [{"employee": "HR-1", "employee_name": "Dablu",
                           "shift_date": "2026-06-14", "status": "Missing Check-Out",
                           "anomaly_reason": "missing_checkout", "name": "S1"}],
            "long_sessions": [{"employee": "HR-2", "employee_name": "Shivam",
                               "shift_date": "2026-06-09", "worked_hours": "20:26",
                               "worked_seconds": 73560, "name": "S2"}],
            "break_punches": [{"employee": "HR-3", "time": "2026-06-05 18:01:00",
                               "punch_state_raw": "2", "name": "C1"}],
            "missing_standard_hours": ["HR-9"],
            "long_session_threshold_hours": 13,
        })
        assert "Dablu" in out and "Missing Check-Out" in out
        assert "Shivam" in out and "20:26" in out
        assert "HR-3" in out and "raw state 2" in out
        assert "HR-9" in out

    def test_default_threshold_is_13h(self):
        import frappe
        from kreativ_attendance.attendance.quality import long_session_seconds
        # If site has no override, default must be 13h = 46800s (the old
        # Excel script's yellow-row threshold).
        if not frappe.conf.get("kreativ_long_session_hours"):
            assert long_session_seconds() == 13 * 3600

    def test_get_month_issues_runs(self):
        """Smoke: the scanner runs against the live schema without error and
        returns all expected keys."""
        from kreativ_attendance.attendance.quality import get_month_issues
        issues = get_month_issues(2026, 6)
        for key in ("anomalies", "long_sessions", "break_punches",
                    "missing_standard_hours", "blocking", "period"):
            assert key in issues
        assert issues["period"] == "2026-06"

    def test_punch_state_map_direction(self):
        """Break/OT states must preserve direction, never all collapse to IN."""
        from kreativ_attendance.attendance.zkteco_sync import PUNCH_STATE_TO_LOG_TYPE
        assert PUNCH_STATE_TO_LOG_TYPE["0"] == "IN"
        assert PUNCH_STATE_TO_LOG_TYPE["1"] == "OUT"
        assert PUNCH_STATE_TO_LOG_TYPE["2"] == "OUT"   # Break Out
        assert PUNCH_STATE_TO_LOG_TYPE["3"] == "IN"    # Break In
        assert PUNCH_STATE_TO_LOG_TYPE["5"] == "OUT"   # Overtime Out
