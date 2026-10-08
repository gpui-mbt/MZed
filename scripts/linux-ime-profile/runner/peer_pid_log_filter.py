#!/usr/bin/env python3
"""Persist only bounded numeric peer-PID records from the private Fcitx pipe."""

import json
import os
import re
import select
import sys
import time
from pathlib import Path

_MARKER = b"MOZC_TEST_AUTHENTICATED_UNIX_PEER_PID="
_PID = re.compile(rb"[1-9][0-9]*\Z")
_MAX_PID = (1 << 31) - 1
_MAX_LINE_BYTES = 16 * 1024
_MAX_PEER_RECORDS = 256
_MAX_COUNTER = (1 << 31) - 1
_HEARTBEAT_SECONDS = 0.5
_READ_CHUNK_BYTES = 64 * 1024
_MAX_STOP_DRAIN_BYTES = 1024 * 1024
_MAX_STOP_DRAIN_SECONDS = 0.5


def _write_status(
    path: Path,
    status: str,
    *,
    records: int,
    discarded: int,
    heartbeat_ns: int,
    reason: str | None = None,
) -> None:
    value = {
        "status": status,
        "peer_record_count": records,
        "discarded_nonmarker_line_count": discarded,
        "heartbeat_monotonic_ns": heartbeat_ns,
    }
    if reason is not None:
        value["reason"] = reason
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


class PeerPidLogFilter:
    def __init__(self, peer_output: Path, status_path: Path):
        self.peer_output = peer_output
        self.status_path = status_path
        self.records = 0
        self.discarded = 0
        self.failure: str | None = None
        self.output = None
        self.buffer = bytearray()
        self.dropping_oversized_line = False
        self.failure_sentinel_written = False
        self.last_heartbeat = 0.0

    def _write_failure_sentinel(self) -> None:
        if self.output is None or self.failure_sentinel_written:
            return
        try:
            # Fixed numeric sentinel: later peer validation rejects the stream
            # even if writing the status file also fails.
            self.output.write(_MARKER + b"0\n")
            self.failure_sentinel_written = True
        except OSError:
            pass

    def _latch(self, reason: str) -> None:
        if self.failure is None:
            self.failure = reason
            self._write_failure_sentinel()
            self.publish("failed", force=True)

    def publish(self, status: str, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_heartbeat < _HEARTBEAT_SECONDS:
            return
        self.last_heartbeat = now
        actual_status = "failed" if self.failure is not None else status
        try:
            _write_status(
                self.status_path,
                actual_status,
                records=self.records,
                discarded=self.discarded,
                heartbeat_ns=time.monotonic_ns(),
                reason=self.failure,
            )
        except OSError:
            if self.failure is None:
                self.failure = "peer-filter-status-write-failed"
                self._write_failure_sentinel()

    def start(self) -> None:
        try:
            fd = os.open(
                self.peer_output,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            self.output = os.fdopen(fd, "ab", buffering=0)
        except OSError:
            self._latch("peer-log-open-failed")
        self.publish("running", force=True)

    def _process_line(self, line: bytes) -> None:
        if len(line) > _MAX_LINE_BYTES:
            self.discarded = min(self.discarded + 1, _MAX_COUNTER)
            self._latch("fcitx-output-line-size-limit")
            return
        if _MARKER not in line:
            self.discarded = min(self.discarded + 1, _MAX_COUNTER)
            return
        if not line.startswith(_MARKER):
            self._latch("malformed-peer-marker-line")
            return
        value = line[len(_MARKER) :]
        if len(value) > 10 or _PID.fullmatch(value) is None:
            self._latch("malformed-peer-pid")
            return
        peer_pid = int(value)
        if peer_pid > _MAX_PID:
            self._latch("peer-pid-out-of-range")
            return
        if self.failure is not None:
            return
        if self.records >= _MAX_PEER_RECORDS:
            self._latch("peer-record-count-limit")
            return
        try:
            self.output.write(_MARKER + value + b"\n")
        except OSError:
            self._latch("peer-log-write-failed")
            return
        self.records += 1
        self.publish("running", force=True)

    def feed(self, chunk: bytes) -> None:
        while chunk:
            if self.dropping_oversized_line:
                end = chunk.find(b"\n")
                if end < 0:
                    return
                self.dropping_oversized_line = False
                chunk = chunk[end + 1 :]
                continue
            end = chunk.find(b"\n")
            if end < 0:
                self.buffer.extend(chunk)
                if len(self.buffer) > _MAX_LINE_BYTES:
                    self.buffer.clear()
                    self.dropping_oversized_line = True
                    self.discarded = min(self.discarded + 1, _MAX_COUNTER)
                    self._latch("fcitx-output-line-size-limit")
                return
            line = bytes(self.buffer) + chunk[:end]
            self.buffer.clear()
            self._process_line(line)
            chunk = chunk[end + 1 :]

    def finish(self, status: str) -> int:
        if self.buffer or self.dropping_oversized_line:
            self._latch("fcitx-output-unterminated-line")
            self.buffer.clear()
            self.dropping_oversized_line = False
        self.publish(status, force=True)
        if self.output is not None:
            try:
                self.output.close()
            except OSError:
                self._latch("peer-log-close-failed")
                self.publish("failed", force=True)
        return 1 if self.failure is not None else 0


def filter_stream(input_stream, peer_output: Path, status_path: Path) -> int:
    """Bounded stream adapter used by deterministic unit tests."""
    state = PeerPidLogFilter(peer_output, status_path)
    state.start()
    while True:
        try:
            chunk = input_stream.read(_READ_CHUNK_BYTES)
        except OSError:
            state._latch("fcitx-output-read-failed")
            return state.finish("failed")
        if not chunk:
            return state.finish("clean-eof")
        state.feed(chunk)


def filter_pipe(peer_output: Path, status_path: Path, stop_path: Path) -> int:
    """Read Fcitx without blocking it; the private stop marker bounds cleanup."""
    state = PeerPidLogFilter(peer_output, status_path)
    state.start()
    fd = sys.stdin.fileno()
    os.set_blocking(fd, False)
    while True:
        try:
            ready, _write, _exception = select.select([fd], [], [], 0.1)
        except OSError:
            state._latch("fcitx-output-poll-failed")
            ready = []
        if ready:
            try:
                chunk = os.read(fd, _READ_CHUNK_BYTES)
            except BlockingIOError:
                chunk = None
            except OSError:
                state._latch("fcitx-output-read-failed")
                chunk = None
            if chunk == b"":
                return state.finish("clean-eof")
            if chunk:
                state.feed(chunk)
        if stop_path.exists():
            # Drain all bytes already queued after the direct Fcitx child has
            # been waited, but a noisy inherited writer cannot hold cleanup.
            stop_started = time.monotonic()
            stop_bytes = 0
            while True:
                if (stop_bytes >= _MAX_STOP_DRAIN_BYTES
                        or time.monotonic() - stop_started >= _MAX_STOP_DRAIN_SECONDS):
                    state._latch("fcitx-output-stop-drain-limit")
                    return state.finish("failed")
                read_size = min(_READ_CHUNK_BYTES, _MAX_STOP_DRAIN_BYTES - stop_bytes)
                try:
                    chunk = os.read(fd, read_size)
                except BlockingIOError:
                    break
                except OSError:
                    state._latch("fcitx-output-read-failed")
                    break
                if chunk == b"":
                    return state.finish("clean-eof")
                stop_bytes += len(chunk)
                state.feed(chunk)
            return state.finish("stopped-after-fcitx-reaped")
        state.publish("running")


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: peer_pid_log_filter.py PEER_OUTPUT STATUS_JSON STOP_MARKER", file=sys.stderr)
        return 2
    return filter_pipe(Path(argv[1]), Path(argv[2]), Path(argv[3]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
