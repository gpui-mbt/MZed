from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest

import bootstrap
from build_mozc import (
    inspect_ninja_target_graph,
    install_fcitx5_config_files,
    validate_ninja_target_commands,
)


class MozcBuildContractTests(unittest.TestCase):
    def test_target_commands_require_server_path_and_exact_hook_mode(self):
        server_dir = Path("/private/runtime/usr/lib/mozc")
        normal = f'c++ -DMOZC_SERVER_DIR="{server_dir}" -c base.cc\n'.encode()
        diagnostic = normal + b"c++ -DMOZC_TEST_AUTHENTICATED_PEER_PID -c unix_ipc.cc\n"

        digest = validate_ninja_target_commands(normal, server_dir, False)
        self.assertEqual(len(digest), 64)
        self.assertEqual(validate_ninja_target_commands(diagnostic, server_dir, True), hashlib.sha256(diagnostic).hexdigest())

    def test_target_commands_reject_missing_server_path_or_wrong_hook_mode(self):
        server_dir = Path("/private/runtime/usr/lib/mozc")
        with self.assertRaisesRegex(ValueError, "server directory"):
            validate_ninja_target_commands(b"c++ -c base.cc\n", server_dir, False)
        normal = f'c++ -DMOZC_SERVER_DIR="{server_dir}" -c base.cc\n'.encode()
        with self.assertRaisesRegex(ValueError, "compile gate"):
            validate_ninja_target_commands(normal, server_dir, True)

    def test_graph_inspection_reads_only_included_target_graphs(self):
        with tempfile.TemporaryDirectory(prefix="mzed-ninja-graph-") as temporary:
            root = Path(temporary)
            build_dir = root / "out"
            (build_dir / "obj/base").mkdir(parents=True)
            (build_dir / "obj/ipc").mkdir(parents=True)
            (build_dir / "build.ninja").write_text(
                "subninja obj/base/base_core.ninja\nsubninja obj/ipc/ipc.ninja\n", encoding="utf-8"
            )
            (build_dir / "obj/base/base_core.ninja").write_text("server compile config\n", encoding="utf-8")
            (build_dir / "obj/ipc/ipc.ninja").write_text("ipc compile config\n", encoding="utf-8")
            ninja = root / "ninja-test-double"
            ninja.write_text(
                "#!/bin/sh\nprintf '%s\\n' 'c++ -DMOZC_SERVER_DIR=/private/usr/lib/mozc' 'c++ -DMOZC_TEST_AUTHENTICATED_PEER_PID'\n",
                encoding="utf-8",
            )
            ninja.chmod(ninja.stat().st_mode | stat.S_IXUSR)
            result = inspect_ninja_target_graph(
                build_dir, ninja, Path("/private/usr/lib/mozc"), True, os.environ.copy()
            )

            self.assertEqual(set(result["graph_files_sha256"]), {
                "build.ninja", "obj/base/base_core.ninja", "obj/ipc/ipc.ninja"
            })
            self.assertEqual(len(result["target_commands_sha256"]), 64)

    def test_installs_only_pinned_fcitx_descriptors_from_mozc_source(self):
        with tempfile.TemporaryDirectory(prefix="mzed-mozc-config-") as temporary:
            root = Path(temporary)
            source_tree = root / "source"
            runtime_prefix = root / "runtime"
            runtime_prefix.mkdir()
            mapping = bootstrap.load_lock()["components"]["mozc"]["fcitx5_config_files"]
            recipe = source_tree / "debian/fcitx5-mozc.install"
            recipe.parent.mkdir(parents=True)
            recipe.write_text(
                "src/unix/fcitx5/mozc-addon.conf => usr/share/fcitx5/addon/mozc.conf\n"
                "src/unix/fcitx5/mozc.conf usr/share/fcitx5/inputmethod/\n",
                encoding="utf-8",
            )
            expected = {}
            for source_relative, destination_relative in mapping.items():
                source = source_tree / source_relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"config from {source_relative}\n", encoding="utf-8")
                expected[destination_relative] = hashlib.sha256(source.read_bytes()).hexdigest()

            actual = install_fcitx5_config_files(source_tree, runtime_prefix, bootstrap.load_lock())

            self.assertEqual(actual, expected)
            for relative, digest in expected.items():
                self.assertEqual(hashlib.sha256((runtime_prefix / relative).read_bytes()).hexdigest(), digest)
            with self.assertRaises(FileExistsError):
                install_fcitx5_config_files(source_tree, runtime_prefix, bootstrap.load_lock())


if __name__ == "__main__":
    unittest.main()
