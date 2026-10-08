import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from check_mozc_owner import read_owned_identity, verify_owned_child


class OwnedChildCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.proc = Path(self.temp.name)
        self.pid = 321
        self.parent = 222
        self.start_time = 987654
        self.uid = os.geteuid()
        self.executable = "/private/prefix/usr/lib/mozc/mozc_server"
        self._write_process()

    def tearDown(self):
        self.temp.cleanup()

    def _write_process(self, *, state="S", parent=None, start=None, uid=None, executable=None):
        process = self.proc / str(self.pid)
        process.mkdir(exist_ok=True)
        fields = ["0"] * 20
        fields[0] = state
        fields[1] = str(self.parent if parent is None else parent)
        fields[19] = str(self.start_time if start is None else start)
        (process / "stat").write_text(f"{self.pid} (mozc_server) " + " ".join(fields))
        effective_uid = self.uid if uid is None else uid
        (process / "status").write_text(
            f"Name:\tmozc_server\nUid:\t{effective_uid}\t{effective_uid}\t{effective_uid}\t{effective_uid}\n"
        )
        exe = process / "exe"
        if exe.exists() or exe.is_symlink():
            exe.unlink()
        exe.symlink_to(self.executable if executable is None else executable)

    def _verify(self, **kwargs):
        args = {
            "owner_pid": self.pid,
            "expected_start_time": self.start_time,
            "expected_parent_pid": self.parent,
            "effective_uid": self.uid,
            "expected_executable": self.executable,
            "client_expected_executable": self.executable,
            "proc_root": self.proc,
        }
        args.update(kwargs)
        return verify_owned_child(**args)

    def test_admits_only_exact_known_owned_child(self):
        result = self._verify()
        self.assertTrue(result["admitted"])
        self.assertTrue(result["owned_child_identity_verified"])
        self.assertEqual(result["reason"], "owned-child-identity-matches")
        self.assertFalse(result["global_process_completeness_claimed"])
        self.assertEqual(result["verification_scope"], "single-known-owned-child")

    def test_unrelated_unreadable_pid_is_never_scanned(self):
        unrelated = self.proc / "999"
        unrelated.mkdir()
        result = self._verify()
        self.assertTrue(result["admitted"])

    def test_missing_owned_pid_fails_closed(self):
        (self.proc / str(self.pid)).rename(self.proc / "other")
        result = self._verify()
        self.assertFalse(result["admitted"])
        self.assertEqual(result["reason"], "owned-process-disappeared")

    def test_wrong_start_parent_uid_executable_or_dead_state_fails(self):
        variants = (
            {"expected_start_time": self.start_time + 1},
            {"expected_parent_pid": self.parent + 1},
            {"effective_uid": self.uid + 1},
            {"client_expected_executable": "/wrong/server"},
            {"expected_executable": "/wrong/server", "client_expected_executable": "/wrong/server"},
        )
        for kwargs in variants:
            with self.subTest(kwargs=kwargs):
                self.assertFalse(self._verify(**kwargs)["admitted"])
        self._write_process(state="Z")
        self.assertEqual(self._verify()["reason"], "owned-child-not-running")

    def test_unstable_identity_fails_closed(self):
        with patch("check_mozc_owner.os.readlink", side_effect=[self.executable, "/changed"]):
            result = self._verify()
        self.assertFalse(result["admitted"])
        self.assertEqual(result["reason"], "owned-process-identity-unreadable-or-unstable")

    def test_unreadable_owned_path_fails_closed(self):
        with patch("check_mozc_owner.Path.read_text", side_effect=PermissionError):
            result = self._verify()
        self.assertFalse(result["admitted"])
        self.assertEqual(result["reason"], "owned-process-permission-denied")

    def test_checker_contains_no_process_table_enumeration_or_global_claim(self):
        source = Path(__import__("check_mozc_owner").__file__).read_text(encoding="utf-8")
        self.assertNotIn(".iterdir(", source)
        self.assertNotIn("snapshot_complete", source)
        self.assertNotIn("matching_server_pids", source)


if __name__ == "__main__":
    unittest.main()
