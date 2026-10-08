from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import unittest

from monitor_deadline import (
    MONITOR_TIMEOUT_SECONDS,
    NANOSECONDS_PER_SECOND,
    deadline_after,
    is_expired,
)


class MonitorDeadlineTests(unittest.TestCase):
    def test_budget_is_one_fixed_fifteen_minute_deadline(self) -> None:
        start = 123_456_789
        deadline = deadline_after(start)
        self.assertEqual(MONITOR_TIMEOUT_SECONDS, 900)
        self.assertEqual(deadline, start + 900 * NANOSECONDS_PER_SECOND)
        self.assertEqual(deadline_after(start), deadline)

    def test_deadline_is_active_before_boundary(self) -> None:
        deadline = deadline_after(10)
        self.assertFalse(is_expired(10, deadline))
        self.assertFalse(is_expired(deadline - 1, deadline))

    def test_deadline_expires_at_boundary(self) -> None:
        deadline = deadline_after(10)
        self.assertTrue(is_expired(deadline, deadline))

    def test_deadline_stays_expired_after_boundary(self) -> None:
        deadline = deadline_after(10)
        self.assertTrue(is_expired(deadline + 1, deadline))
        self.assertTrue(is_expired(deadline + 60 * NANOSECONDS_PER_SECOND, deadline))

    def test_deadline_cannot_be_extended_by_later_activity(self) -> None:
        start = 10
        original_deadline = deadline_after(start)
        later_activity = start + 901 * NANOSECONDS_PER_SECOND
        self.assertEqual(original_deadline, deadline_after(start))
        self.assertTrue(is_expired(later_activity, original_deadline))

    def test_rejects_invalid_start_and_clock_values(self) -> None:
        for invalid in (-1, True, 1.5, "10"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    deadline_after(invalid)  # type: ignore[arg-type]
        for invalid in (-1, True, 1.5, "10"):
            with self.subTest(now=invalid):
                with self.assertRaises(ValueError):
                    is_expired(invalid, 100)  # type: ignore[arg-type]
            with self.subTest(deadline=invalid):
                with self.assertRaises(ValueError):
                    is_expired(1, invalid)  # type: ignore[arg-type]

    def test_cli_start_returns_one_fixed_deadline(self) -> None:
        helper = Path(__file__).with_name("monitor_deadline.py")
        result = subprocess.run(
            [sys.executable, str(helper), "start"],
            check=True,
            capture_output=True,
            text=True,
        )
        start, deadline = (int(value) for value in result.stdout.strip().split("\t"))
        self.assertEqual(deadline - start, 900 * NANOSECONDS_PER_SECOND)

    def test_cli_check_has_distinct_active_expired_and_invalid_states(self) -> None:
        helper = Path(__file__).with_name("monitor_deadline.py")
        active = subprocess.run(
            [sys.executable, str(helper), "check", str(10**30)],
            capture_output=True,
            text=True,
        )
        expired = subprocess.run(
            [sys.executable, str(helper), "check", "0"],
            capture_output=True,
            text=True,
        )
        invalid = subprocess.run(
            [sys.executable, str(helper), "check", "-1"],
            capture_output=True,
            text=True,
        )
        self.assertEqual((active.returncode, active.stdout.strip()), (0, "active"))
        self.assertEqual((expired.returncode, expired.stdout.strip()), (1, "expired"))
        self.assertEqual((invalid.returncode, invalid.stdout.strip()), (2, "invalid"))

    def test_shell_enforces_deadline_before_stop_success_and_keeps_strict_checks(self) -> None:
        inner = Path(__file__).parents[1] / "run-session-inner.sh"
        source = inner.read_text(encoding="utf-8")
        body = source.split("monitor_fcitx_context() {", 1)[1].split("\ncleanup() {", 1)[0]
        deadline_check = body.index('"$MONITOR_DEADLINE" check "$monitor_deadline_ns"')
        stop_check = body.index('if [[ -e "$LOG/fcitx-context-monitor.stop" ]]')
        self.assertLess(deadline_check, stop_check)
        self.assertIn("verify_owned_mozc", body)
        self.assertIn("read_fcitx_child_identity", body)
        self.assertIn("$PEER_PID_CHECK", body)
        self.assertIn("fcitx_engine_class", body)
        self.assertIn('fcitx_peer_guard_fail "fcitx-peer-monitor-timeout-after-admission"', body)


if __name__ == "__main__":
    unittest.main()
