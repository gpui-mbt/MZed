from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.parse
import unittest
from contextlib import redirect_stdout

import bootstrap
from doctor import host_checks


class LockTests(unittest.TestCase):
    def test_locked_package_closures_and_official_component_pins(self):
        lock = bootstrap.load_lock()
        self.assertEqual(len(lock["layers"]["wayland_profile"]), 219)
        self.assertEqual(len(lock["layers"]["mozc_build"]), 321)
        self.assertEqual(lock["components"]["labwc"]["version"], "0.8.3-1")
        self.assertEqual(lock["components"]["fcitx5"]["version"], "5.1.12-2")
        self.assertEqual(lock["components"]["pixman"]["version"], "0.44.0-3")

    def test_all_package_and_source_pins_are_official_https_records(self):
        lock = bootstrap.load_lock()
        for package in lock["packages"].values():
            self.assertEqual(urllib.parse.urlsplit(package["url"]).hostname, "deb.debian.org")
            self.assertEqual(len(package["sha256"]), 64)
            self.assertGreater(package["size_bytes"], 0)
        for source in bootstrap.source_rows(lock):
            self.assertEqual(urllib.parse.urlsplit(source["url"]).hostname, "deb.debian.org")
            self.assertEqual(len(source["sha256"]), 64)

    def test_stock_server_and_fcitx_module_are_excluded_for_private_build(self):
        lock = bootstrap.load_lock()
        packages = bootstrap.selected_package_rows(lock, "wayland_profile")
        included = {item["package"] for item in packages}
        self.assertNotIn("mozc-server", included)
        self.assertNotIn("fcitx5-mozc", included)
        self.assertIn("mozc-data", included)
        self.assertEqual(
            lock["components"]["mozc"]["fcitx5_config_files"],
            {
                "src/unix/fcitx5/mozc-addon.conf": "usr/share/fcitx5/addon/mozc.conf",
                "src/unix/fcitx5/mozc.conf": "usr/share/fcitx5/inputmethod/mozc.conf",
            },
        )

    def test_peer_pid_hook_is_explicit_numeric_only_and_separate(self):
        lock = bootstrap.load_lock()
        diag = lock["diagnostic_profile"]
        patch = bootstrap.HERE / diag["peer_patch_path"]
        self.assertEqual(hashlib.sha256(patch.read_bytes()).hexdigest(), diag["peer_patch_sha256"])
        self.assertEqual(diag["output_record"], "MOZC_TEST_AUTHENTICATED_UNIX_PEER_PID=<numeric-pid>")
        self.assertFalse(diag["input_text_logged"])
        self.assertFalse(diag["authentication_changes"])
        self.assertFalse(diag["global_process_uniqueness_claimed"])

    def test_plan_is_read_only_and_never_claims_runtime_start(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(bootstrap.main(["plan"]), 0)
        result = json.loads(output.getvalue())
        self.assertFalse(result["writes_host_or_system_state"])
        self.assertFalse(result["launches_runtime"])

    def test_host_doctor_reports_read_only_prerequisite_checks(self):
        result = host_checks(bootstrap.load_lock())
        self.assertIn("sun_path_capacity_bytes", result)
        self.assertIn("sun_path_header", result)
        self.assertFalse(result["downloads_performed"])
        self.assertFalse(result["packages_installed"])
        self.assertFalse(result["processes_started"])

    def test_lock_contains_no_cloud_attempt_paths_or_pids(self):
        raw = bootstrap.LOCK_PATH.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"/workspace/(?:scratch|shared)/[A-Za-z0-9._-]+", raw))

    def test_private_executable_accepts_debian_relative_link_inside_prefix(self):
        with tempfile.TemporaryDirectory(prefix="mzed-prefix-") as temporary:
            prefix = Path(temporary) / "prefix"
            binary = prefix / "usr/bin/gcc-14"
            binary.parent.mkdir(parents=True)
            binary.write_text("fixture", encoding="utf-8")
            binary.chmod(0o755)
            command = binary.parent / "gcc"
            command.symlink_to("gcc-14")

            self.assertTrue(bootstrap.private_executable(command, prefix))

    def test_private_executable_rejects_missing_and_escaping_links(self):
        with tempfile.TemporaryDirectory(prefix="mzed-prefix-") as temporary:
            root = Path(temporary)
            prefix = root / "prefix"
            tool_dir = prefix / "usr/bin"
            outside = root / "outside-tool"
            tool_dir.mkdir(parents=True)
            outside.write_text("fixture", encoding="utf-8")
            outside.chmod(0o755)
            escaping = tool_dir / "escape"
            escaping.symlink_to(os.path.relpath(outside, tool_dir))

            self.assertFalse(bootstrap.private_executable(tool_dir / "missing", prefix))
            self.assertFalse(bootstrap.private_executable(escaping, prefix))

    def test_build_prefix_layout_creates_locked_relative_aliases(self):
        with tempfile.TemporaryDirectory(prefix="mzed-prefix-") as temporary:
            prefix = Path(temporary) / "prefix"
            (prefix / "usr/lib").mkdir(parents=True)
            (prefix / "usr/lib64").mkdir(parents=True)
            lock = bootstrap.load_lock()

            aliases = bootstrap.prepare_build_prefix_layout(prefix, lock)

            self.assertEqual(aliases, {"lib": "usr/lib", "lib64": "usr/lib64"})
            self.assertTrue(bootstrap.verify_build_prefix_layout(prefix, lock))
            self.assertEqual((prefix / "lib").readlink(), Path("usr/lib"))
            self.assertEqual((prefix / "lib64").readlink(), Path("usr/lib64"))

    def test_build_prefix_layout_rejects_unexpected_alias_occupant(self):
        with tempfile.TemporaryDirectory(prefix="mzed-prefix-") as temporary:
            prefix = Path(temporary) / "prefix"
            (prefix / "usr/lib").mkdir(parents=True)
            (prefix / "usr/lib64").mkdir(parents=True)
            (prefix / "lib").mkdir()
            with self.assertRaisesRegex(bootstrap.BootstrapError, "already occupied"):
                bootstrap.prepare_build_prefix_layout(prefix, bootstrap.load_lock())


if __name__ == "__main__":
    unittest.main()
