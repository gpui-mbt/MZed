import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import zed_supervisor


class ZedSupervisorPidfdTests(unittest.TestCase):
    def make_guard(self, directory: str, *, admitted: bool) -> str:
        path = Path(directory) / "fake_owner_check.py"
        path.write_text(
            "import json,sys\n"
            f"print(json.dumps({{'admitted': {admitted!r}, 'reason': 'test-guard'}}))\n"
            f"raise SystemExit({0 if admitted else 1})\n",
            encoding="utf-8",
        )
        return str(path)

    def start_supervisor(self, directory: str, *, guard_admitted: bool):
        log_path = str(Path(directory) / "zed.log")
        event_path = str(Path(directory) / "zed-events.jsonl")
        guard_log = str(Path(directory) / "guard.jsonl")
        guard_path = self.make_guard(directory, admitted=guard_admitted)
        executable = os.path.realpath(sys.executable)
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(zed_supervisor.__file__).resolve()),
                "--expected-exe", executable,
                "--child-log", log_path,
                "--event-log", event_path,
                "--guard-log", guard_log,
                "--owner-check", guard_path,
                "--owner-pid", "321",
                "--owner-start-time", "987654",
                "--owner-parent-pid", "123",
                "--server-path", "/private/mozc-prefix/usr/lib/mozc/mozc_server",
                "--client-path", "/private/mozc-prefix/usr/lib/mozc/mozc_server",
                "--",
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            close_fds=True,
        )
        self.addCleanup(process.stdin.close)
        self.addCleanup(process.stdout.close)
        self.addCleanup(process.stderr.close)
        return process, executable, log_path, guard_log

    def read_event(self, process):
        line = process.stdout.readline()
        self.assertTrue(line, "supervisor closed event pipe before terminal record")
        return json.loads(line)

    def test_pidfd_ready_after_proc_identity_error_is_normal_exit(self):
        class ReadyPoller:
            def register(self, fd, _events):
                self.fd = fd

            def poll(self, timeout):
                self.assert_zero_timeout = timeout == 0
                return [(self.fd, zed_supervisor.select.POLLIN)]

        class ExitedChild:
            pid = 456

            def __init__(self):
                self.wait_calls = 0

            def wait(self):
                self.wait_calls += 1
                return 0

        child = ExitedChild()
        poller = ReadyPoller()
        with mock.patch.object(zed_supervisor.select, "poll", return_value=poller):
            record = zed_supervisor.reap_if_pidfd_ready_after_identity_error(
                child, 17, exec_verified=True
            )

        self.assertTrue(poller.assert_zero_timeout)
        self.assertEqual(child.wait_calls, 1)
        self.assertEqual(record["event"], "reaped")
        self.assertEqual(record["pid"], child.pid)
        self.assertEqual(record["returncode"], 0)
        self.assertTrue(record["wait_completed"])
        self.assertTrue(record["pidfd_used"])
        self.assertEqual(record["reason"], "process-exit-after-identity-read-error")

    def test_proc_disappears_after_exit_between_poll_and_identity_sample(self):
        class FakePoller:
            def __init__(self, ready):
                self.ready = ready

            def register(self, _fd, _events):
                pass

            def poll(self, _timeout):
                return [(17, zed_supervisor.select.POLLIN)] if self.ready else []

        class ExitedChild:
            pid = 456
            returncode = None

            def __init__(self):
                self.wait_calls = 0

            def wait(self):
                self.wait_calls += 1
                self.returncode = 0
                return 0

        with tempfile.TemporaryDirectory() as directory:
            child = ExitedChild()
            events = []
            identity = {
                "pid": child.pid,
                "state": "S",
                "parent_pid": os.getpid(),
                "start_time": 987654,
                "uid": os.geteuid(),
                "executable": "/usr/bin/python3",
            }
            args = type("Args", (), {
                "child_log": str(Path(directory) / "zed.log"),
                "event_log": str(Path(directory) / "events.jsonl"),
                "guard_log": str(Path(directory) / "guard.jsonl"),
                "command": ["/usr/bin/true"],
                "expected_exe": "/usr/bin/python3",
                "owner_check": "/unused/owner-check.py",
                "owner_pid": 1,
                "owner_start_time": 1,
                "owner_parent_pid": 1,
                "server_path": "/private/mozc_server",
                "client_path": "/private/mozc_server",
                "exec_timeout": 30.0,
            })()
            pollers = [FakePoller(ready=False), FakePoller(ready=True)]
            with mock.patch.object(zed_supervisor.subprocess, "Popen", return_value=child), \
                 mock.patch.object(zed_supervisor.os, "pidfd_open", return_value=17), \
                 mock.patch.object(zed_supervisor.os, "close"), \
                 mock.patch.object(zed_supervisor, "read_process_identity", side_effect=[identity, identity, FileNotFoundError()]), \
                 mock.patch.object(zed_supervisor.select, "poll", side_effect=pollers), \
                 mock.patch.object(zed_supervisor, "guard_sample", return_value={"admitted": True}), \
                 mock.patch.object(zed_supervisor, "emit", side_effect=lambda record, _stream: events.append(record)):
                status = zed_supervisor.run(args)

        self.assertEqual(status, 0)
        self.assertEqual(child.wait_calls, 1)
        self.assertEqual([record["event"] for record in events], ["started", "exec_verified", "reaped"])
        terminal = events[-1]
        self.assertEqual(terminal["reason"], "process-exit-after-identity-read-error")
        self.assertEqual(terminal["returncode"], 0)
        self.assertTrue(terminal["pidfd_used"])
        self.assertTrue(terminal["wait_completed"])

    def test_identity_error_with_unready_pidfd_remains_fail_closed(self):
        class UnreadyPoller:
            def register(self, fd, _events):
                self.fd = fd

            def poll(self, timeout):
                self.timeout = timeout
                return []

        class LiveChild:
            pid = 789

            def wait(self):
                raise AssertionError("live child must not be waited on without pidfd readiness")

        poller = UnreadyPoller()
        with mock.patch.object(zed_supervisor.select, "poll", return_value=poller):
            record = zed_supervisor.reap_if_pidfd_ready_after_identity_error(
                LiveChild(), 23, exec_verified=True
            )

        self.assertEqual(poller.timeout, 0)
        self.assertIsNone(record)

    def test_owned_child_stops_and_reaps_via_pidfd(self):
        with tempfile.TemporaryDirectory() as directory:
            process, executable, _log_path, _guard_log = self.start_supervisor(directory, guard_admitted=True)
            started = self.read_event(process)
            self.assertEqual(started["event"], "started")
            self.assertTrue(started["pidfd_open"])
            self.assertEqual(started["supervisor_pid"], process.pid)
            self.assertEqual(started["parent_pid"], process.pid)
            self.assertEqual(started["initial_executable"], executable)
            exec_verified = self.read_event(process)
            self.assertEqual(exec_verified["event"], "exec_verified")
            self.assertEqual(exec_verified["proc_exe"], executable)
            process.stdin.write("stop\n")
            process.stdin.flush()
            terminal = self.read_event(process)
            self.assertEqual(terminal["event"], "reaped")
            self.assertEqual(terminal["pid"], started["pid"])
            self.assertTrue(terminal["wait_completed"])
            self.assertTrue(terminal["pidfd_used"])
            self.assertEqual(terminal["reason"], "runner-stop")
            self.assertEqual(process.wait(timeout=5), 0)

    def test_guard_failure_stops_only_supervised_child_via_pidfd(self):
        with tempfile.TemporaryDirectory() as directory:
            process, _executable, _log_path, guard_log = self.start_supervisor(directory, guard_admitted=False)
            started = self.read_event(process)
            self.assertEqual(started["event"], "started")
            events = []
            while True:
                event = self.read_event(process)
                events.append(event)
                if event["event"] == "reaped":
                    break
            failure = next(event for event in events if event["event"] == "owned_child_identity_failed")
            self.assertEqual(failure["pid"], started["pid"])
            terminal = events[-1]
            self.assertEqual(terminal["event"], "reaped")
            self.assertEqual(terminal["pid"], started["pid"])
            self.assertEqual(terminal["reason"], "owned-child-identity-failed")
            self.assertTrue(terminal["pidfd_used"])
            self.assertTrue(Path(guard_log).is_file())
            self.assertEqual(process.wait(timeout=5), 12)


if __name__ == "__main__":
    unittest.main()
