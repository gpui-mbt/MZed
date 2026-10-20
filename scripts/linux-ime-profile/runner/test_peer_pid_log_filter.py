import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from peer_pid_log_filter import filter_stream


MARKER = b"MOZC_TEST_AUTHENTICATED_UNIX_PEER_PID="


class PeerPidLogFilterTests(unittest.TestCase):
    def run_filter(self, data):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        peer_log = root / "peer-pids.log"
        status_path = root / "filter-status.json"
        status = filter_stream(io.BytesIO(data), peer_log, status_path)
        output = peer_log.read_bytes() if peer_log.exists() else b""
        result = json.loads(status_path.read_text(encoding="utf-8"))
        return status, output, result

    def test_discards_other_output_and_keeps_only_numeric_peer_line(self):
        code, output, status = self.run_filter(
            b"ordinary log text with no input\n" + MARKER + b"321\n"
        )
        self.assertEqual(code, 0)
        self.assertEqual(output, MARKER + b"321\n")
        self.assertEqual(status["status"], "clean-eof")
        self.assertEqual(status["discarded_nonmarker_line_count"], 1)
        self.assertIsInstance(status["heartbeat_monotonic_ns"], int)
        self.assertNotIn(b"ordinary log", output)

    def test_malformed_marker_fails_without_persisting_line(self):
        code, output, status = self.run_filter(
            MARKER + b"not-a-pid\n" + MARKER + b"654\n"
        )
        self.assertEqual(code, 1)
        self.assertEqual(output, MARKER + b"0\n")
        self.assertEqual(status["reason"], "malformed-peer-pid")
        self.assertEqual(status["status"], "failed")
        self.assertNotIn(b"654", output)

    def test_prefix_or_oversized_line_fails_closed(self):
        code, output, status = self.run_filter(b"prefix " + MARKER + b"321\n")
        self.assertEqual(code, 1)
        self.assertEqual(output, MARKER + b"0\n")
        self.assertEqual(status["reason"], "malformed-peer-marker-line")

    def test_oversized_line_is_drained_and_later_peer_is_not_admitted(self):
        code, output, status = self.run_filter(
            b"x" * (16 * 1024 + 100) + b"\n" + MARKER + b"321\n"
        )
        self.assertEqual(code, 1)
        self.assertEqual(output, MARKER + b"0\n")
        self.assertEqual(status["reason"], "fcitx-output-line-size-limit")
        self.assertNotIn(b"321", output)

    def test_record_count_is_bounded(self):
        source = MARKER + b"321\n"
        code, output, status = self.run_filter(source * 257)
        self.assertEqual(code, 1)
        self.assertEqual(status["reason"], "peer-record-count-limit")
        self.assertEqual(output[-2:], b"0\n")

    def test_output_open_failure_still_drains_the_input(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        status_path = root / "filter-status.json"
        code = filter_stream(
            io.BytesIO(b"ordinary diagnostic\n" + MARKER + b"321\n"),
            root / "missing" / "peer-pids.log",
            status_path,
        )
        self.assertEqual(code, 1)
        status = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["reason"], "peer-log-open-failed")

    def test_process_pipe_drains_after_malformed_record(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        peer_log = root / "peer-pids.log"
        status_path = root / "filter-status.json"
        stop_path = root / "filter.stop"
        source = Path(__file__).with_name("peer_pid_log_filter.py")
        process = subprocess.Popen(
            [sys.executable, str(source), str(peer_log), str(status_path), str(stop_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _stdout, stderr = process.communicate(
            b"ordinary output\n" + MARKER + b"bad\n" + MARKER + b"321\n",
            timeout=5,
        )
        self.assertEqual(process.returncode, 1, stderr.decode("utf-8", "replace"))
        self.assertEqual(peer_log.read_bytes(), MARKER + b"0\n")
        status = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["reason"], "malformed-peer-pid")

    def test_stop_marker_bounds_reader_with_inherited_writer_still_open(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        peer_log = root / "peer-pids.log"
        status_path = root / "filter-status.json"
        stop_path = root / "filter.stop"
        source = Path(__file__).with_name("peer_pid_log_filter.py")
        process = subprocess.Popen(
            [sys.executable, str(source), str(peer_log), str(status_path), str(stop_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.monotonic() + 2
        status = {}
        while time.monotonic() < deadline:
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(0.01)
                continue
            if status.get("status") == "running":
                break
            time.sleep(0.01)
        self.assertEqual(status.get("status"), "running")
        assert process.stdin is not None
        process.stdin.write(MARKER + b"321\n")
        process.stdin.flush()
        stop_path.touch()
        self.assertEqual(process.wait(timeout=3), 0)
        process.stdin.close()
        status = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "stopped-after-fcitx-reaped")
        self.assertEqual(peer_log.read_bytes(), MARKER + b"321\n")

    def test_stop_marker_bounds_continuously_writing_inherited_producer(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        peer_log = root / "peer-pids.log"
        status_path = root / "filter-status.json"
        stop_path = root / "filter.stop"
        source = Path(__file__).with_name("peer_pid_log_filter.py")
        process = subprocess.Popen(
            [sys.executable, str(source), str(peer_log), str(status_path), str(stop_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.monotonic() + 2
        status = {}
        while time.monotonic() < deadline:
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(0.01)
                continue
            if status.get("status") == "running":
                break
            time.sleep(0.01)
        self.assertEqual(status.get("status"), "running")
        assert process.stdin is not None
        stop_writer = threading.Event()

        def write_forever():
            block = b"x\n" * 2048
            while not stop_writer.is_set():
                try:
                    process.stdin.write(block)
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    return

        writer = threading.Thread(target=write_forever, daemon=True)
        writer.start()
        time.sleep(0.05)
        stop_path.touch()
        stop_started = time.monotonic()
        return_code = process.wait(timeout=3)
        elapsed = time.monotonic() - stop_started
        stop_writer.set()
        writer.join(timeout=1)
        try:
            process.stdin.close()
        except OSError:
            pass
        self.assertEqual(return_code, 1)
        self.assertLess(elapsed, 2)
        status = json.loads(status_path.read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["reason"], "fcitx-output-stop-drain-limit")
        self.assertEqual(peer_log.read_bytes(), MARKER + b"0\n")


if __name__ == "__main__":
    unittest.main()
