#!/usr/bin/env python3
"""Validate numeric authenticated-peer records from the private Fcitx log."""

import json
import re
import sys
from pathlib import Path

_MARKER = "MOZC_TEST_AUTHENTICATED_UNIX_PEER_PID="
_PID = re.compile(r"[1-9][0-9]*\Z")
_MAX_PID = (1 << 31) - 1
_MAX_LOG_BYTES = 4 * 1024 * 1024


def inspect_peer_log(
    text: str,
    *,
    expected_peer_pid: int,
    fcitx_pid: int,
    emitter_pid_hint: int,
    require_record: bool = True,
) -> dict[str, object]:
    result: dict[str, object] = {
        "admitted": False,
        "pending": False,
        "reason": "not-verified",
        "verification_scope": "authenticated-fcitx-peer-to-owned-child",
        "global_process_completeness_claimed": False,
        "observed_peer_pids": [],
        "record_count": 0,
        "emitter_pid_matches_fcitx": False,
    }
    if any(type(value) is not int or value <= 0 for value in (
        expected_peer_pid, fcitx_pid, emitter_pid_hint
    )):
        result["reason"] = "invalid-positive-pid-input"
        return result
    if emitter_pid_hint != fcitx_pid:
        result["reason"] = "emitter-pid-does-not-match-retained-fcitx-child"
        return result
    result["emitter_pid_matches_fcitx"] = True

    peers: list[int] = []
    for line in text.splitlines():
        if _MARKER not in line:
            continue
        if not line.startswith(_MARKER):
            result["reason"] = "malformed-peer-marker-line"
            return result
        value = line[len(_MARKER) :]
        if len(value) > 10:
            result["reason"] = "peer-pid-out-of-range"
            return result
        if _PID.fullmatch(value) is None:
            result["reason"] = "malformed-peer-pid"
            return result
        try:
            peer_pid = int(value)
        except ValueError:
            result["reason"] = "malformed-peer-pid"
            return result
        if peer_pid > _MAX_PID:
            result["reason"] = "peer-pid-out-of-range"
            return result
        peers.append(peer_pid)

    result["observed_peer_pids"] = peers
    result["record_count"] = len(peers)
    if not peers:
        result["pending"] = not require_record
        result["reason"] = "authenticated-peer-record-pending" if not require_record else "authenticated-peer-record-missing"
        return result
    if any(peer_pid != expected_peer_pid for peer_pid in peers):
        result["reason"] = "authenticated-peer-does-not-match-owned-child"
        return result

    result["admitted"] = True
    result["reason"] = "all-observed-fcitx-peers-match-owned-child"
    return result


def inspect_peer_log_file(
    path: str,
    *,
    expected_peer_pid: int,
    fcitx_pid: int,
    emitter_pid_hint: int,
    require_record: bool = True,
) -> dict[str, object]:
    inputs = inspect_peer_log(
        "",
        expected_peer_pid=expected_peer_pid,
        fcitx_pid=fcitx_pid,
        emitter_pid_hint=emitter_pid_hint,
        require_record=False,
    )
    if inputs.get("reason") in (
        "invalid-positive-pid-input",
        "emitter-pid-does-not-match-retained-fcitx-child",
    ):
        return inputs
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(_MAX_LOG_BYTES + 1)
        if len(raw) > _MAX_LOG_BYTES:
            return {
                "admitted": False,
                "pending": False,
                "reason": "fcitx-log-exceeds-bound",
                "verification_scope": "authenticated-fcitx-peer-to-owned-child",
                "global_process_completeness_claimed": False,
                "observed_peer_pids": [],
                "record_count": 0,
                "emitter_pid_matches_fcitx": True,
            }
        text = raw.decode("utf-8")
    except FileNotFoundError:
        inputs["reason"] = "fcitx-log-not-created"
        inputs["pending"] = not require_record
        return inputs
    except (OSError, UnicodeError):
        return {
            "admitted": False,
            "pending": False,
            "reason": "fcitx-log-unreadable",
            "verification_scope": "authenticated-fcitx-peer-to-owned-child",
            "global_process_completeness_claimed": False,
            "observed_peer_pids": [],
            "record_count": 0,
            "emitter_pid_matches_fcitx": emitter_pid_hint == fcitx_pid,
        }
    return inspect_peer_log(
        text,
        expected_peer_pid=expected_peer_pid,
        fcitx_pid=fcitx_pid,
        emitter_pid_hint=emitter_pid_hint,
        require_record=require_record,
    )


def main(argv: list[str]) -> int:
    if len(argv) not in (5, 6):
        print(
            "usage: peer_pid_check.py FCITX_LOG OWNED_PID FCITX_PID EMITTER_PID [--allow-pending]",
            file=sys.stderr,
        )
        return 2
    allow_pending = len(argv) == 6 and argv[5] == "--allow-pending"
    if len(argv) == 6 and not allow_pending:
        print("unknown option", file=sys.stderr)
        return 2
    try:
        evidence = inspect_peer_log_file(
            argv[1],
            expected_peer_pid=int(argv[2]),
            fcitx_pid=int(argv[3]),
            emitter_pid_hint=int(argv[4]),
            require_record=not allow_pending,
        )
    except ValueError:
        evidence = {
            "admitted": False,
            "pending": False,
            "reason": "invalid-cli-integer",
            "verification_scope": "authenticated-fcitx-peer-to-owned-child",
            "global_process_completeness_claimed": False,
            "observed_peer_pids": [],
            "record_count": 0,
            "emitter_pid_matches_fcitx": False,
        }
    print(json.dumps(evidence, sort_keys=True))
    return 0 if evidence["admitted"] or evidence["pending"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
