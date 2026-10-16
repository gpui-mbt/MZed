from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from update_source_lock import collect_files


class SourceLockTests(unittest.TestCase):
    def test_hashes_are_sorted_repeatable_and_exclude_generated_lock_and_cache(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mzed-source-lock-") as temporary:
            root = Path(temporary)
            (root / "b.py").write_text("print('b')\n", encoding="utf-8")
            (root / "a.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (root / "source-lock.json").write_text("generated\n", encoding="utf-8")
            cache = root / "__pycache__"
            cache.mkdir()
            (cache / "ignored.pyc").write_bytes(b"cache")

            first = collect_files(root)
            second = collect_files(root)

            self.assertEqual(list(first), ["a.sh", "b.py"])
            self.assertEqual(first, second)
            self.assertEqual(first["b.py"], hashlib.sha256(b"print('b')\n").hexdigest())

    def test_rejects_symlinked_profile_inputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mzed-source-lock-") as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("input\n", encoding="utf-8")
            (root / "link").symlink_to(target)
            with self.assertRaisesRegex(ValueError, "must not contain symlinks"):
                collect_files(root)

    def test_committed_tree_has_a_nonempty_lockable_source_set(self) -> None:
        files = collect_files()
        self.assertIn("runner/runner-fragment.sh", files)
        self.assertIn("patches/mozc-authenticated-peer-pid-test-only.patch", files)
        self.assertIn("tests/peer_pid_log_gate_test.cc", files)
        self.assertNotIn("source-lock.json", files)


if __name__ == "__main__":
    unittest.main()
