#!/usr/bin/env python3
"""Bound the authenticated Fcitx peer monitor with a monotonic deadline."""

from __future__ import annotations

import sys
import time

MONITOR_TIMEOUT_SECONDS = 900
NANOSECONDS_PER_SECOND = 1_000_000_000


def deadline_after(start_monotonic_ns: int) -> int:
    if type(start_monotonic_ns) is not int or start_monotonic_ns < 0:
        raise ValueError("invalid monotonic start")
    return start_monotonic_ns + MONITOR_TIMEOUT_SECONDS * NANOSECONDS_PER_SECOND


def is_expired(now_monotonic_ns: int, deadline_monotonic_ns: int) -> bool:
    if type(now_monotonic_ns) is not int or now_monotonic_ns < 0:
        raise ValueError("invalid monotonic now")
    if type(deadline_monotonic_ns) is not int or deadline_monotonic_ns < 0:
        raise ValueError("invalid monotonic deadline")
    return now_monotonic_ns >= deadline_monotonic_ns


def main(argv: list[str]) -> int:
    if argv == ["start"]:
        start_ns = time.monotonic_ns()
        print(f"{start_ns}\t{deadline_after(start_ns)}")
        return 0
    if len(argv) == 2 and argv[0] == "check":
        try:
            deadline_ns = int(argv[1], 10)
            expired = is_expired(time.monotonic_ns(), deadline_ns)
        except (ValueError, OverflowError):
            print("invalid")
            return 2
        print("expired" if expired else "active")
        return 1 if expired else 0
    print("usage: monitor_deadline.py start | check DEADLINE_MONOTONIC_NS", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
