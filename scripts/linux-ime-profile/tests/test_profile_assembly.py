from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from assemble_run import _require_fresh_run_root, assemble
import bootstrap
from doctor import profile_checks
from prepare_profile import prepare


ELF = b"\x7fELF" + b"MZed test fixture, not a runnable binary\n"


def write_identity(root: Path, runtime_prefix: Path) -> Path:
    server = runtime_prefix / "usr/lib/mozc/mozc_server"
    module = runtime_prefix / "usr/lib/x86_64-linux-gnu/fcitx5/fcitx5-mozc.so"
    server.parent.mkdir(parents=True)
    module.parent.mkdir(parents=True)
    server.write_bytes(ELF + b"server")
    module.write_bytes(ELF + b"module")
    config_hashes = {}
    for destination in bootstrap.load_lock()["components"]["mozc"]["fcitx5_config_files"].values():
        config_path = runtime_prefix / destination
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(f"fixture for {Path(destination).name}\n", encoding="utf-8")
        config_hashes[destination] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    graph_root = root / "source/src/out_linux/Release"
    graph_hashes = {}
    for relative, content in {
        "build.ninja": "subninja obj/base/base_core.ninja\nsubninja obj/ipc/ipc.ninja\n",
        "obj/base/base_core.ninja": "base graph fixture\n",
        "obj/ipc/ipc.ninja": "ipc graph fixture\n",
    }.items():
        graph_path = graph_root / relative
        graph_path.parent.mkdir(parents=True, exist_ok=True)
        graph_path.write_text(content, encoding="utf-8")
        graph_hashes[relative] = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    gyp = root / "gyp-configure-result.json"
    fake_target_commands = b"g++ -DMOZC_SERVER_DIR=runtime-prefix/usr/lib/mozc -DMOZC_TEST_AUTHENTICATED_PEER_PID\n"
    gyp.write_text(json.dumps({
        "peer_pid_hook_enabled": True,
        "server_dir_relative": "usr/lib/mozc",
        "server_dir_absolute": str(runtime_prefix / "usr/lib/mozc"),
        "source_tree_relative": "source",
        "build_graph_files_sha256": graph_hashes,
        "target_commands_sha256": hashlib.sha256(fake_target_commands).hexdigest(),
    }) + "\n", encoding="utf-8")
    identity = {
        "schema": "mzed-mozc-build-identity-v1",
        "source_package": "mozc 2.29.5160.102+dfsg-1.4",
        "server_path": "usr/lib/mozc/mozc_server",
        "server_sha256": hashlib.sha256(server.read_bytes()).hexdigest(),
        "configured_server_directory": str(runtime_prefix / "usr/lib/mozc"),
        "fcitx_compiled_server_path": "usr/lib/mozc/mozc_server",
        "fcitx5_mozc_path": "usr/lib/x86_64-linux-gnu/fcitx5/fcitx5-mozc.so",
        "fcitx5_mozc_sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
        "fcitx5_config_files_sha256": config_hashes,
        "server_and_fcitx_share_base_core": True,
        "test_only_authenticated_peer_pid_hook_enabled": True,
        "input_text_logged": False,
        "authentication_checks_changed": False,
        "peer_pid_emitter_environment": "MOZC_TEST_FCITX_EMITTER_PID",
        "peer_pid_log_record": "MOZC_TEST_AUTHENTICATED_UNIX_PEER_PID=<numeric-pid>",
        "gyp_configure_receipt": "gyp-configure-result.json",
        "gyp_configure_receipt_sha256": hashlib.sha256(gyp.read_bytes()).hexdigest(),
        "source_tree_relative": "source",
        "patch_sha256": bootstrap.load_lock()["diagnostic_profile"]["peer_patch_sha256"],
    }
    identity_path = root / "build-identity.json"
    identity_path.write_text(json.dumps(identity) + "\n", encoding="utf-8")
    return identity_path


class ProfileAssemblyTests(unittest.TestCase):
    def test_prepares_only_fresh_profile_with_private_runtime_directory(self):
        with tempfile.TemporaryDirectory(prefix="mzed-iac-") as temporary:
            parent = Path(temporary)
            run_root = parent / "run"
            runtime_prefix = parent / "runtime-prefix"
            runtime_prefix.mkdir(mode=0o700)
            result = prepare(run_root, runtime_prefix)
            self.assertTrue(result["prepared"])
            profile = run_root / "profile"
            runtime = profile / "xdg-runtime"
            self.assertEqual(stat.S_IMODE(runtime.stat().st_mode), 0o700)
            self.assertEqual((profile / "zed-stateless-user-data/config/keymap.json").read_text(), "[]\n")
            manifest = json.loads((run_root / "profile-manifest.json").read_text())
            self.assertFalse(manifest["copied_user_data"])
            self.assertEqual(profile_checks(run_root, runtime_prefix)["checks_passed"], True)

    def test_refuses_to_reuse_existing_run_root_without_mutation(self):
        with tempfile.TemporaryDirectory(prefix="mzed-iac-") as temporary:
            parent = Path(temporary)
            run_root = parent / "run"
            run_root.mkdir()
            marker = run_root / "keep.txt"
            marker.write_text("preserve")
            before = hashlib.sha256(marker.read_bytes()).hexdigest()
            with self.assertRaises(FileExistsError):
                prepare(run_root, parent)
            self.assertEqual(hashlib.sha256(marker.read_bytes()).hexdigest(), before)

    def test_rejects_run_root_through_symlinked_ancestor_before_writing(self):
        with tempfile.TemporaryDirectory(prefix="mzed-iac-") as temporary:
            parent = Path(temporary)
            real_parent = parent / "real"
            real_parent.mkdir()
            (real_parent / "existing").mkdir()
            alias = parent / "alias"
            alias.symlink_to(real_parent, target_is_directory=True)
            run_root = alias / "existing/run"
            runtime_prefix = parent / "runtime-prefix"
            runtime_prefix.mkdir(mode=0o700)

            with self.assertRaisesRegex(ValueError, "may not traverse symlinks"):
                prepare(run_root, runtime_prefix)
            with self.assertRaisesRegex(ValueError, "symlinked parent"):
                _require_fresh_run_root(run_root)
            self.assertFalse((real_parent / "existing/run").exists())

    def test_assembles_exact_source_inputs_and_mocked_artifact_identities(self):
        with tempfile.TemporaryDirectory(prefix="mzed-iac-") as temporary:
            parent = Path(temporary)
            runtime_prefix = parent / "runtime-prefix"
            runtime_prefix.mkdir(mode=0o700)
            run_root = parent / "run"
            prepare(run_root, runtime_prefix)

            zed = parent / "zed-fixture"
            zed.write_bytes(ELF + b"zed")
            zed_receipt = parent / "zed-build.json"
            zed_receipt.write_text(json.dumps({"build": "passed", "binary_sha256": hashlib.sha256(zed.read_bytes()).hexdigest()}) + "\n")
            identity = write_identity(parent, runtime_prefix)

            result = assemble(run_root, runtime_prefix, zed, zed_receipt, identity)
            self.assertTrue(result["assembled"])
            manifest = json.loads((run_root / "integration-manifest.json").read_text())
            self.assertFalse(manifest["zed_stateless"] is False)
            self.assertIn("runner/source-lock.json", manifest["runtime_inputs"])
            self.assertIn("run-session-inner.sh", manifest["runtime_inputs"])
            self.assertTrue((run_root / "runner/build-identity.json").is_file())
            self.assertEqual(profile_checks(run_root, runtime_prefix)["checks_passed"], True)

    def test_assembly_rejects_relocated_server_prefix_and_descriptor_hash_drift(self):
        with tempfile.TemporaryDirectory(prefix="mzed-iac-") as temporary:
            parent = Path(temporary)
            runtime_prefix = parent / "runtime-prefix"
            runtime_prefix.mkdir(mode=0o700)
            run_root = parent / "run"
            prepare(run_root, runtime_prefix)
            zed = parent / "zed-fixture"
            zed.write_bytes(ELF + b"zed")
            zed_receipt = parent / "zed-build.json"
            zed_receipt.write_text(json.dumps({"build": "passed", "binary_sha256": hashlib.sha256(zed.read_bytes()).hexdigest()}) + "\n")
            identity = write_identity(parent, runtime_prefix)
            identity_data = json.loads(identity.read_text())
            identity_data["configured_server_directory"] = str(parent / "moved-prefix/usr/lib/mozc")
            identity.write_text(json.dumps(identity_data) + "\n")

            with self.assertRaisesRegex(ValueError, "different absolute runtime prefix"):
                assemble(run_root, runtime_prefix, zed, zed_receipt, identity)
            self.assertFalse((run_root / "runner").exists())

            identity_data["configured_server_directory"] = str(runtime_prefix / "usr/lib/mozc")
            identity_data["fcitx5_config_files_sha256"]["usr/share/fcitx5/addon/mozc.conf"] = "0" * 64
            identity.write_text(json.dumps(identity_data) + "\n")
            with self.assertRaisesRegex(ValueError, "descriptor is missing or mismatched"):
                assemble(run_root, runtime_prefix, zed, zed_receipt, identity)
            self.assertFalse((run_root / "runner").exists())


if __name__ == "__main__":
    unittest.main()
