#!/usr/bin/env python3
"""Verify one known runner-owned Mozc PID without enumerating /proc."""

import json
import os
import sys
from pathlib import Path


def process_state_running(state: str) -> bool:
    return state not in ("Z", "X", "x")


def _parse_stat(text: str, expected_pid: int) -> dict[str, int | str]:
    close = text.rfind(")")
    if close < 0:
        raise ValueError("stat-comm-delimiter-missing")
    fields = text[close + 2 :].split()
    if len(fields) <= 19:
        raise ValueError("stat-fields-missing")
    return {
        "pid": expected_pid,
        "state": fields[0],
        "parent_pid": int(fields[1]),
        "start_time": int(fields[19]),
    }


def read_owned_identity(proc_root: Path, owner_pid: int) -> dict[str, int | str]:
    """Read only /proc/<known-owner-pid>; never iterate the process table."""
    process_dir = proc_root / str(owner_pid)

    def sample() -> dict[str, int | str]:
        stat_before = _parse_stat((process_dir / "stat").read_text(), owner_pid)
        status_before = (process_dir / "status").read_text()
        uid_before_line = next(
            line for line in status_before.splitlines() if line.startswith("Uid:")
        )
        uid_before = int(uid_before_line.split()[2])
        executable_before = os.readlink(process_dir / "exe")

        status_after = (process_dir / "status").read_text()
        uid_after_line = next(
            line for line in status_after.splitlines() if line.startswith("Uid:")
        )
        uid_after = int(uid_after_line.split()[2])
        executable_after = os.readlink(process_dir / "exe")
        stat_after = _parse_stat((process_dir / "stat").read_text(), owner_pid)

        stable_fields = ("pid", "parent_pid", "start_time")
        if any(stat_before[key] != stat_after[key] for key in stable_fields):
            raise RuntimeError("owned-process-identity-changed-during-sample")
        if uid_before != uid_after or executable_before != executable_after:
            raise RuntimeError("owned-process-identity-changed-during-sample")
        return {
            **stat_after,
            "uid": uid_after,
            "executable": executable_after,
        }

    before = sample()
    after = sample()
    stable_fields = ("pid", "parent_pid", "start_time", "uid", "executable")
    if any(before[key] != after[key] for key in stable_fields):
        raise RuntimeError("owned-process-identity-changed-between-samples")
    return after


def verify_owned_child(
    *,
    owner_pid: int,
    expected_start_time: int,
    expected_parent_pid: int,
    effective_uid: int,
    expected_executable: str,
    client_expected_executable: str,
    proc_root: Path = Path("/proc"),
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "admitted": False,
        "reason": "not-verified",
        "verification_scope": "single-known-owned-child",
        "global_process_completeness_claimed": False,
        "owned_child_identity_verified": False,
        "owned_child": None,
    }
    if owner_pid <= 0:
        evidence["reason"] = "missing-owned-pid"
        return evidence
    if expected_start_time <= 0 or expected_parent_pid <= 0:
        evidence["reason"] = "invalid-owned-child-identity-input"
        return evidence
    if effective_uid < 0:
        evidence["reason"] = "invalid-effective-uid"
        return evidence
    if not expected_executable.startswith("/"):
        evidence["reason"] = "expected-path-not-absolute"
        return evidence
    if client_expected_executable != expected_executable:
        evidence["reason"] = "client-server-path-mismatch"
        return evidence

    try:
        identity = read_owned_identity(proc_root, owner_pid)
    except PermissionError:
        evidence["reason"] = "owned-process-permission-denied"
        return evidence
    except FileNotFoundError:
        evidence["reason"] = "owned-process-disappeared"
        return evidence
    except (OSError, StopIteration, ValueError, IndexError, RuntimeError):
        evidence["reason"] = "owned-process-identity-unreadable-or-unstable"
        return evidence

    evidence["owned_child"] = identity
    if not process_state_running(str(identity["state"])):
        evidence["reason"] = "owned-child-not-running"
        return evidence
    if identity["uid"] != effective_uid:
        evidence["reason"] = "owned-child-uid-mismatch"
        return evidence
    if identity["executable"] != expected_executable:
        evidence["reason"] = "owned-child-executable-mismatch"
        return evidence
    if identity["parent_pid"] != expected_parent_pid:
        evidence["reason"] = "owned-child-parent-mismatch"
        return evidence
    if identity["start_time"] != expected_start_time:
        evidence["reason"] = "owned-child-start-time-mismatch"
        return evidence

    evidence["admitted"] = True
    evidence["reason"] = "owned-child-identity-matches"
    evidence["owned_child_identity_verified"] = True
    return evidence


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        print(
            "usage: check_mozc_owner.py OWNER_PID START_TIME PARENT_PID EXPECTED_EXE CLIENT_EXPECTED_EXE",
            file=sys.stderr,
        )
        return 2
    try:
        evidence = verify_owned_child(
            owner_pid=int(argv[1]),
            expected_start_time=int(argv[2]),
            expected_parent_pid=int(argv[3]),
            effective_uid=os.geteuid(),
            expected_executable=argv[4],
            client_expected_executable=argv[5],
        )
    except ValueError:
        evidence = {
            "admitted": False,
            "reason": "invalid-cli-integer",
            "verification_scope": "single-known-owned-child",
            "global_process_completeness_claimed": False,
            "owned_child_identity_verified": False,
            "owned_child": None,
        }
    print(json.dumps(evidence, sort_keys=True))
    return 0 if evidence["admitted"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
