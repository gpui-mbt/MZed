import subprocess
import os
import io
import json
import tempfile
import unittest
from unittest.mock import patch

import mozc_supervisor
from mozc_supervisor import (
    stop_and_reap,
    summarize_lifecycle,
    summary_tsv,
    verified_reaped_status,
)


class FakeChild:
    def __init__(self, *, running=True, timeout_once=False):
        self.running = running
        self.timeout_once = timeout_once
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def poll(self):
        return None if self.running else (-15 if self.terminated else 0)

    def terminate(self):
        self.terminated = True
        if not self.timeout_once:
            self.running = False

    def kill(self):
        self.killed = True
        self.running = False

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.timeout_once and self.wait_calls == 1:
            raise subprocess.TimeoutExpired("mozc_server", timeout)
        return -9 if self.killed else (-15 if self.terminated else 0)


class SupervisorLifecycleTests(unittest.TestCase):
    def test_summary_uses_typed_start_reap_and_wait_records(self):
        summary = summarize_lifecycle(
            {
                "event": "started",
                "pid": 321,
                "parent_pid": 222,
                "start_time": 987654,
                "supervisor_pid": 222,
                "supervisor_parent_pid": 111,
            },
            {"event": "reaped", "pid": 321, "returncode": -15, "wait_completed": True},
            0,
        )
        self.assertEqual(summary, {
            "child_started": True,
            "supervisor_pid": 222,
            "supervisor_parent_pid": 111,
            "child_pid": 321,
            "child_parent_pid": 222,
            "child_start_time": 987654,
            "child_wait_verified": True,
            "child_returncode": -15,
            "supervisor_waited": True,
            "supervisor_exit_status": 0,
        })
        self.assertEqual(
            summary_tsv(summary).splitlines(),
            [
                "child_started\tsupervisor_pid\tsupervisor_parent_pid\tchild_pid\tchild_parent_pid\tchild_start_time\tchild_wait_verified\tchild_returncode\tsupervisor_waited\tsupervisor_exit_status",
                "1\t222\t111\t321\t222\t987654\t1\t-15\t1\t0",
            ],
        )

    def test_summary_rejects_mismatched_or_untyped_lifecycle_records(self):
        started = {
            "event": "started", "pid": 321, "parent_pid": 222,
            "start_time": 987654, "supervisor_pid": 222,
            "supervisor_parent_pid": 111,
        }
        summary = summarize_lifecycle(
            started,
            {"event": "reaped", "pid": 322, "returncode": -15, "wait_completed": True},
            False,
        )
        self.assertTrue(summary["child_started"])
        self.assertFalse(summary["child_wait_verified"])
        self.assertEqual(summary["child_returncode"], "unverified-reaped-event")
        self.assertFalse(summary["supervisor_waited"])
        self.assertEqual(summary["supervisor_exit_status"], "not-observed")

    def test_summary_does_not_infer_child_start_from_reaped_event_alone(self):
        summary = summarize_lifecycle(
            None,
            {"event": "reaped", "pid": 321, "returncode": -15, "wait_completed": True},
            0,
        )
        self.assertEqual(summary["child_started"], "not-observed")
        self.assertIsNone(summary["child_pid"])
        self.assertFalse(summary["child_wait_verified"])
        self.assertFalse(summary["supervisor_waited"])
        self.assertEqual(summary["supervisor_exit_status"], "not-observed")
        self.assertEqual(summary_tsv(summary).splitlines()[1].split("\t", 1)[0], "not-observed")

    def test_reaped_event_requires_exact_child_and_completed_wait(self):
        self.assertEqual(
            verified_reaped_status(
                '{"event":"reaped","pid":321,"returncode":-15,"wait_completed":true}',
                321,
            ),
            -15,
        )
        for record in (
            '{"event":"status","pid":321,"returncode":-15,"wait_completed":true}',
            '{"event":"reaped","pid":322,"returncode":-15,"wait_completed":true}',
            '{"event":"reaped","pid":321,"returncode":-15,"wait_completed":false}',
            '{"event":"reaped","pid":321,"returncode":"-15","wait_completed":true}',
            '{"event":"reaped","pid":true,"returncode":0,"wait_completed":true}',
            'not-json',
        ):
            with self.subTest(record=record):
                self.assertIsNone(verified_reaped_status(record, 321))

    def test_running_child_is_terminated_then_waited(self):
        child = FakeChild()
        self.assertEqual(stop_and_reap(child), -15)
        self.assertTrue(child.terminated)
        self.assertFalse(child.killed)
        self.assertEqual(child.wait_calls, 1)

    def test_exited_child_is_waited_without_signal(self):
        child = FakeChild(running=False)
        self.assertEqual(stop_and_reap(child), 0)
        self.assertFalse(child.terminated)
        self.assertFalse(child.killed)
        self.assertEqual(child.wait_calls, 1)

    def test_timeout_kills_same_popen_child_then_waits(self):
        child = FakeChild(timeout_once=True)
        self.assertEqual(stop_and_reap(child), -9)
        self.assertTrue(child.terminated)
        self.assertTrue(child.killed)
        self.assertEqual(child.wait_calls, 2)

    def test_broken_start_handshake_still_stops_and_waits_child(self):
        class BrokenPipeOutput:
            def write(self, _value):
                raise BrokenPipeError()

            def flush(self):
                return None

        child = FakeChild()
        child.pid = 321
        with tempfile.TemporaryDirectory() as directory:
            server_path = os.path.join(directory, "mozc_server")
            log_path = os.path.join(directory, "mozc.log")
            with open(server_path, "w", encoding="utf-8") as stream:
                stream.write("fake executable path only; no process is launched\n")
            with patch.object(mozc_supervisor.subprocess, "Popen", return_value=child) as spawn:
                with patch.object(
                    mozc_supervisor,
                    "read_child_identity",
                    return_value={
                        "pid": child.pid,
                        "parent_pid": os.getpid(),
                        "start_time": 123,
                        "uid": os.geteuid(),
                        "executable": server_path,
                    },
                ):
                    with patch("sys.stdout", BrokenPipeOutput()):
                        status = mozc_supervisor.run(server_path, log_path)
        self.assertEqual(status, 6)
        self.assertTrue(child.terminated)
        self.assertEqual(child.wait_calls, 1)
        self.assertEqual(spawn.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_started_record_identifies_supervisor_parent(self):
        child = FakeChild()
        child.pid = 321
        captured = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            server_path = os.path.join(directory, "mozc_server")
            log_path = os.path.join(directory, "mozc.log")
            with open(server_path, "w", encoding="utf-8") as stream:
                stream.write("fake executable path only; no process is launched\n")
            with patch.object(mozc_supervisor.subprocess, "Popen", return_value=child):
                with patch.object(
                    mozc_supervisor,
                    "read_child_identity",
                    return_value={
                        "pid": child.pid,
                        "parent_pid": os.getpid(),
                        "start_time": 123,
                        "uid": os.geteuid(),
                        "executable": server_path,
                    },
                ), patch("sys.stdin", io.StringIO("stop\n")), patch("sys.stdout", captured):
                    self.assertEqual(mozc_supervisor.run(server_path, log_path), 0)
        started = json.loads(captured.getvalue().splitlines()[0])
        self.assertEqual(started["supervisor_pid"], os.getpid())
        self.assertEqual(started["supervisor_parent_pid"], os.getppid())


if __name__ == "__main__":
    unittest.main()
