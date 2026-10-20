#!/usr/bin/env python3
"""Own one Zed child with Popen + pidfd while checking one known Mozc child."""

import datetime as _datetime
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
from typing import Any


def read_process_identity(pid: int) -> dict[str, int | str]:
    process_dir = Path("/proc") / str(pid)

    def sample() -> dict[str, int | str]:
        stat_text = (process_dir / "stat").read_text()
        stat_fields = stat_text[stat_text.rfind(")") + 2 :].split()
        status_text = (process_dir / "status").read_text()
        uid_line = next(line for line in status_text.splitlines() if line.startswith("Uid:"))
        return {
            "pid": pid,
            "state": stat_fields[0],
            "parent_pid": int(stat_fields[1]),
            "start_time": int(stat_fields[19]),
            "uid": int(uid_line.split()[2]),
            "executable": os.readlink(process_dir / "exe"),
        }

    before = sample()
    after = sample()
    if (
        before["pid"], before["parent_pid"], before["start_time"], before["uid"]
    ) != (
        after["pid"], after["parent_pid"], after["start_time"], after["uid"]
    ):
        raise RuntimeError("Zed process identity changed during sample")
    return after


def emit(record: dict[str, Any], event_stream) -> None:
    record = {
        "timestamp_utc": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        **record,
    }
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    event_stream.write((line + "\n").encode("utf-8"))
    event_stream.flush()
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def guard_sample(args, pid: int) -> dict[str, Any]:
    command = [
        sys.executable,
        args.owner_check,
        str(args.owner_pid),
        str(args.owner_start_time),
        str(args.owner_parent_pid),
        args.server_path,
        args.client_path,
    ]
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3.0,
            close_fds=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "admitted": False,
            "reason": "owner-check-execution-failed",
            "detail": type(error).__name__,
            "zed_pid": pid,
        }
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        report = {"admitted": False, "reason": "owner-check-output-invalid"}
    if not isinstance(report, dict):
        report = {"admitted": False, "reason": "owner-check-output-invalid"}
    report["returncode"] = result.returncode
    report["zed_pid"] = pid
    if result.returncode != 0:
        report["admitted"] = False
    return report


def send_pidfd(pidfd: int, signum: int) -> None:
    signal.pidfd_send_signal(pidfd, signum, None, 0)


def reap_if_pidfd_ready_after_identity_error(
    child: subprocess.Popen, pidfd: int, *, exec_verified: bool
) -> dict[str, Any] | None:
    """Reap an exited owned child when /proc vanished before the next poll cycle."""
    poller = select.poll()
    poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
    ready = {fd: events for fd, events in poller.poll(0)}
    events = ready.get(pidfd, 0)
    if not events & (select.POLLIN | select.POLLHUP | select.POLLERR):
        return None
    returncode = child.wait()
    return {
        "event": "reaped",
        "pid": child.pid,
        "returncode": returncode,
        "wait_completed": True,
        "pidfd_used": True,
        "reason": "process-exit-after-identity-read-error" if exec_verified else "startup-exit-after-identity-read-error",
    }


def stop_and_reap(child: subprocess.Popen, pidfd: int, reason: str) -> dict[str, Any]:
    try:
        send_pidfd(pidfd, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        returncode = child.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        try:
            send_pidfd(pidfd, signal.SIGKILL)
        except ProcessLookupError:
            pass
        returncode = child.wait()
    return {
        "event": "reaped",
        "pid": child.pid,
        "returncode": returncode,
        "wait_completed": True,
        "pidfd_used": True,
        "reason": reason,
    }


def parse_cli(argv: list[str]):
    if "--" not in argv:
        raise ValueError("missing command separator")
    separator = argv.index("--")
    options = argv[1:separator]
    command = argv[separator + 1 :]
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-exe", required=True)
    parser.add_argument("--child-log", required=True)
    parser.add_argument("--event-log", required=True)
    parser.add_argument("--guard-log", required=True)
    parser.add_argument("--owner-check", required=True)
    parser.add_argument("--owner-pid", type=int, required=True)
    parser.add_argument("--owner-start-time", type=int, required=True)
    parser.add_argument("--owner-parent-pid", type=int, required=True)
    parser.add_argument("--server-path", required=True)
    parser.add_argument("--client-path", required=True)
    parser.add_argument("--exec-timeout", type=float, default=30.0)
    args = parser.parse_args(options)
    if not command or not os.path.isabs(args.expected_exe):
        raise ValueError("expected executable and command must be valid")
    args.command = command
    return args


def run(args) -> int:
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        print(json.dumps({"event": "error", "reason": "pidfd-not-supported"}), flush=True)
        return 2

    child = None
    pidfd = None
    child_waited = False
    supervisor_status = 0
    try:
        with open(args.child_log, "ab", buffering=0) as child_log, open(args.event_log, "ab", buffering=0) as event_log, open(args.guard_log, "a", encoding="utf-8", buffering=1) as guard_log:
            try:
                child = subprocess.Popen(
                    args.command,
                    stdin=subprocess.DEVNULL,
                    stdout=child_log,
                    stderr=subprocess.STDOUT,
                    env=os.environ.copy(),
                    close_fds=True,
                )
            except OSError as error:
                emit({"event": "error", "reason": "zed-spawn-failed", "errno": error.errno}, event_log)
                return 3

            # Open a stable handle before any poll/wait can reap the child.
            try:
                pidfd = os.pidfd_open(child.pid, 0)
            except OSError as error:
                # This remains a retained Popen-owned fallback, but it is not
                # accepted as the stable-handle runtime path.
                try:
                    child.terminate()
                except ProcessLookupError:
                    pass
                returncode = child.wait()
                child_waited = True
                emit({"event": "error", "reason": "zed-pidfd-open-failed", "errno": error.errno}, event_log)
                emit({
                    "event": "reaped", "pid": child.pid, "returncode": returncode,
                    "wait_completed": True, "pidfd_used": False, "reason": "pidfd-open-failed",
                }, event_log)
                return 4

            try:
                identity = read_process_identity(child.pid)
            except (OSError, StopIteration, ValueError, IndexError, RuntimeError):
                emit({"event": "error", "reason": "zed-identity-unreadable", "pid": child.pid}, event_log)
                record = stop_and_reap(child, pidfd, "startup-identity-unreadable")
                child_waited = True
                emit(record, event_log)
                return 5

            if identity["parent_pid"] != os.getpid() or identity["uid"] != os.geteuid():
                emit({"event": "error", "reason": "zed-child-identity-mismatch", **identity}, event_log)
                record = stop_and_reap(child, pidfd, "startup-identity-mismatch")
                child_waited = True
                emit(record, event_log)
                return 6

            emit({
                "event": "started",
                "pid": child.pid,
                "supervisor_pid": os.getpid(),
                "supervisor_parent_pid": os.getppid(),
                "parent_pid": identity["parent_pid"],
                "start_time": identity["start_time"],
                "uid": identity["uid"],
                "initial_executable": identity["executable"],
                "pidfd_open": True,
            }, event_log)

            poller = select.poll()
            poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
            input_fd = sys.stdin.fileno()
            poller.register(input_fd, select.POLLIN | select.POLLHUP | select.POLLERR)
            exec_verified = False
            next_guard_at = 0.0
            exec_deadline = time.monotonic() + args.exec_timeout
            last_identity = identity

            while True:
                ready = poller.poll(100)
                ready_fds = {fd: events for fd, events in ready}
                if pidfd in ready_fds:
                    returncode = child.wait()
                    child_waited = True
                    if not exec_verified:
                        emit({"event": "error", "reason": "zed-exited-before-exec-verification", "pid": child.pid}, event_log)
                    emit({
                        "event": "reaped", "pid": child.pid, "returncode": returncode,
                        "wait_completed": True, "pidfd_used": True,
                        "reason": "process-exit" if exec_verified else "startup-exit",
                    }, event_log)
                    return 0 if returncode == 0 else 7

                input_events = ready_fds.get(input_fd, 0)
                if input_events & (select.POLLIN | select.POLLHUP | select.POLLERR):
                    command = sys.stdin.readline()
                    if command == "" or command.strip().lower() == "stop":
                        record = stop_and_reap(child, pidfd, "runner-stop")
                        child_waited = True
                        emit(record, event_log)
                        return 0
                    emit({"event": "error", "reason": "unknown-control-command"}, event_log)
                    record = stop_and_reap(child, pidfd, "unknown-control-command")
                    child_waited = True
                    emit(record, event_log)
                    return 8

                now = time.monotonic()
                try:
                    last_identity = read_process_identity(child.pid)
                except (OSError, StopIteration, ValueError, IndexError, RuntimeError):
                    completed = reap_if_pidfd_ready_after_identity_error(
                        child, pidfd, exec_verified=exec_verified
                    )
                    if completed is not None:
                        child_waited = True
                        if not exec_verified:
                            emit({"event": "error", "reason": "zed-exited-before-exec-verification", "pid": child.pid}, event_log)
                        emit(completed, event_log)
                        return 0 if completed["returncode"] == 0 else 7
                    emit({"event": "error", "reason": "zed-identity-lost", "pid": child.pid}, event_log)
                    record = stop_and_reap(child, pidfd, "identity-lost")
                    child_waited = True
                    emit(record, event_log)
                    return 9
                if (
                    last_identity["parent_pid"] != identity["parent_pid"]
                    or last_identity["start_time"] != identity["start_time"]
                    or last_identity["uid"] != identity["uid"]
                ):
                    emit({"event": "error", "reason": "zed-child-identity-changed", "pid": child.pid}, event_log)
                    record = stop_and_reap(child, pidfd, "identity-changed")
                    child_waited = True
                    emit(record, event_log)
                    return 10

                if exec_verified and last_identity["executable"] != args.expected_exe:
                    emit({
                        "event": "error",
                        "reason": "zed-executable-changed",
                        "pid": child.pid,
                        "observed_executable": last_identity["executable"],
                    }, event_log)
                    record = stop_and_reap(child, pidfd, "executable-changed")
                    child_waited = True
                    emit(record, event_log)
                    return 14

                if not exec_verified and last_identity["executable"] == args.expected_exe:
                    exec_verified = True
                    emit({
                        "event": "exec_verified", "pid": child.pid,
                        "proc_exe": last_identity["executable"],
                        "parent_pid": last_identity["parent_pid"],
                        "start_time": last_identity["start_time"],
                    }, event_log)
                if not exec_verified and now >= exec_deadline:
                    emit({"event": "error", "reason": "zed-exec-timeout", "pid": child.pid}, event_log)
                    record = stop_and_reap(child, pidfd, "exec-timeout")
                    child_waited = True
                    emit(record, event_log)
                    return 11

                if now >= next_guard_at:
                    sample = guard_sample(args, child.pid)
                    guard_log.write(json.dumps(sample, sort_keys=True, separators=(",", ":")) + "\n")
                    if sample.get("admitted") is not True:
                        emit({"event": "owned_child_identity_failed", "pid": child.pid, "sample": sample}, event_log)
                        record = stop_and_reap(child, pidfd, "owned-child-identity-failed")
                        child_waited = True
                        emit(record, event_log)
                        return 12
                    next_guard_at = now + 0.5
    except BrokenPipeError:
        supervisor_status = 13
    finally:
        if child is not None and not child_waited:
            try:
                if pidfd is not None:
                    record = stop_and_reap(child, pidfd, "supervisor-finally")
                    try:
                        with open(args.event_log, "ab", buffering=0) as cleanup_log:
                            emit(record, cleanup_log)
                    except (OSError, BrokenPipeError):
                        pass
                else:
                    try:
                        child.terminate()
                    except ProcessLookupError:
                        pass
                    child.wait()
            except Exception:
                pass
        if pidfd is not None:
            os.close(pidfd)
    return supervisor_status


def main(argv: list[str]) -> int:
    try:
        args = parse_cli(argv)
    except (ValueError, SystemExit):
        return 2
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
