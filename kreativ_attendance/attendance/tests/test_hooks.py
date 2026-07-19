"""Tests for the hooks module."""
import unittest
from unittest.mock import patch, MagicMock


class TestHooks(unittest.TestCase):

    def test_on_checkin_updated_enqueues_recalc(self):
        from kreativ_attendance.attendance.hooks import on_checkin_updated

        fake_frappe = MagicMock()
        captured = {}

        def fake_enqueue(method, **kwargs):
            captured["method"] = method
            captured["kwargs"] = kwargs

        fake_doc = type("FakeDoc", (), {
            "name": "EC-TEST-123",
            "employee": "HR-EMP-1",
            "time": "2026-07-05 10:00:00",
        })()

        with patch("kreativ_attendance.attendance.hooks.frappe", fake_frappe):
            with patch("kreativ_attendance.attendance.hooks.enqueue", fake_enqueue):
                on_checkin_updated(fake_doc, method="on_update")

        self.assertEqual(captured["method"],
                         "kreativ_attendance.attendance.service.recalculate_period")
        self.assertEqual(captured["kwargs"]["employee"], "HR-EMP-1")
        self.assertIs(captured["kwargs"]["now"], False,
                       "should be queued, not run synchronously")
        self.assertIs(captured["kwargs"].get("enqueue_after_commit"), True,
                       "must enqueue AFTER commit so worker sees fresh values")

    def test_fallback_on_enqueue_failure(self):
        from kreativ_attendance.attendance.hooks import on_checkin_updated

        fake_frappe = MagicMock()
        called = {}

        def fake_enqueue_fail(*args, **kwargs):
            raise Exception("no worker")

        def fake_recalc(year, month, employee):
            called["rec"] = True

        fake_doc = type("FakeDoc", (), {
            "name": "EC-FALLBACK-9",
            "employee": "HR-EMP-1",
            "time": "2026-07-05 10:00:00",
        })()

        with patch("kreativ_attendance.attendance.hooks.frappe", fake_frappe):
            with patch("kreativ_attendance.attendance.hooks.enqueue", fake_enqueue_fail):
                with patch("kreativ_attendance.attendance.hooks.recalculate_period", fake_recalc):
                    # Should not raise — fallback calls recalculate_period inline
                    on_checkin_updated(fake_doc, method="on_update")

        self.assertTrue(called.get("rec"), "fallback should call recalculate_period inline")

    def test_reentrance_guard_removed(self):
        """Reentrance guard was removed in latest hooks. Verify hook still
        fires even if the sentinel is present."""
        from kreativ_attendance.attendance.hooks import on_checkin_updated

        call_count = {"count": 0}

        def fake_enqueue(*args, **kwargs):
            call_count["count"] += 1

        fake_doc = type("FakeDoc", (), {
            "name": "EC-X",
            "employee": "HR-EMP-1",
            "time": "2026-07-05 10:00:00",
            "_gravures_attendance_recalc_triggered": True,
        })()

        fake_frappe = MagicMock()
        with patch("kreativ_attendance.attendance.hooks.frappe", fake_frappe):
            with patch("kreativ_attendance.attendance.hooks.enqueue", fake_enqueue):
                on_checkin_updated(fake_doc)

        self.assertEqual(call_count["count"], 1,
                         "hook should fire even with sentinel set")