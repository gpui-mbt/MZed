#!/usr/bin/env python3
"""Assemble a reviewed source set into one fresh private runtime directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys

import bootstrap
from update_source_lock import collect_files


HERE = Path(__file__).resolve().parent
SOURCE_LOCK = HERE / "source-lock.json"
RUNTIME_COPY_MAP = {
    "run-session-inner.sh": "run-session-inner.sh",
    "launch-w3moz": "launch-w3moz",
    "socket_path_preflight.py": "runner/socket_path_preflight.py",
    "prefix-env.py": "runner/scripts/prefix-env.py",
    "v3-smoke-gate.py": "runner/scripts/v3-smoke-gate.py",
    "runner/runner-fragment.sh": "runner/runner-fragment.sh",
    "runner/check_mozc_owner.py": "runner/check_mozc_owner.py",
    "runner/monitor_deadline.py": "runner/monitor_deadline.py",
    "runner/mozc_supervisor.py": "runner/mozc_supervisor.py",
    "runner/peer_pid_check.py": "runner/peer_pid_check.py",
    "runner/peer_pid_log_filter.py": "runner/peer_pid_log_filter.py",
    "runner/zed_supervisor.py": "runner/zed_supervisor.py",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def verify_repo_sources() -> tuple[dict[str, str], str]:
    source_lock = json.loads(SOURCE_LOCK.read_text(encoding="utf-8"))
    if source_lock.get("schema") != "mzed-ime-runtime-source-lock-v1":
        raise ValueError("runtime source lock has an unsupported schema")
    files = source_lock.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("runtime source lock is empty")
    actual_files = collect_files(HERE)
    if set(files) != set(actual_files):
        raise ValueError("runtime source lock does not cover the current profile source tree")
    for relative, expected in files.items():
        rel = PurePosixPath(relative)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("runtime source lock contains an unsafe path")
        path = HERE / Path(*rel.parts)
        if not regular(path) or sha256(path) != expected or actual_files[relative] != expected:
            raise ValueError(f"runtime source does not match its committed lock: {relative}")
    return files, sha256(SOURCE_LOCK)


def _require_fresh_run_root(run_root: Path) -> None:
    if not run_root.is_absolute() or run_root == Path("/") or run_root.is_symlink():
        raise ValueError("run root must be an absolute non-root path without a symlink")
    if any(parent.is_symlink() for parent in run_root.parents if parent.exists()):
        raise ValueError("run root may not traverse a symlinked parent")
    if not run_root.is_dir() or run_root.stat().st_uid != os.getuid():
        raise ValueError("run root must be a prepared directory owned by this user")
    if any((run_root / name).exists() or (run_root / name).is_symlink()
           for name in ("runner", "integration-manifest.json", "run-session-inner.sh", "launch-w3moz")):
        raise FileExistsError("refusing to reassemble a run root that already contains runner files")
    parent = run_root.parent
    if not parent.is_dir() or parent.is_symlink() or parent.stat().st_uid != os.getuid():
        raise ValueError("run-root parent must exist, be real, and be owned by this user")


def assemble(run_root: Path, runtime_prefix: Path, zed_binary: Path,
             zed_build_receipt: Path, mozc_identity_path: Path) -> dict[str, object]:
    _require_fresh_run_root(run_root)
    if not runtime_prefix.is_absolute() or runtime_prefix.is_symlink() or not runtime_prefix.is_dir():
        raise ValueError("runtime prefix must be an existing real absolute directory")
    if any(parent.is_symlink() for parent in runtime_prefix.parents if parent.exists()):
        raise ValueError("runtime prefix may not traverse a symlinked parent")
    if not zed_binary.is_absolute() or not regular(zed_binary):
        raise ValueError("Zed binary must be an existing absolute regular file")
    if not regular(zed_build_receipt) or not regular(mozc_identity_path):
        raise ValueError("Zed build receipt and Mozc identity must be existing regular files")

    profile_manifest_path = run_root / "profile-manifest.json"
    socket_budget_path = run_root / "socket-path-budget.json"
    if not regular(profile_manifest_path) or not regular(socket_budget_path):
        raise ValueError("profile must be freshly prepared before assembly")
    profile_manifest = json.loads(profile_manifest_path.read_text(encoding="utf-8"))
    socket_budget = json.loads(socket_budget_path.read_text(encoding="utf-8"))
    if profile_manifest.get("schema") != "mzed-test-profile-v1" or profile_manifest.get("copied_user_data") is not False:
        raise ValueError("profile manifest does not certify a fresh empty profile")
    if sha256(socket_budget_path) != profile_manifest.get("socket_budget_sha256"):
        raise ValueError("socket budget differs from the prepared profile receipt")
    if socket_budget.get("status") != "pass" or not all(
        item.get("fits") is True
        for item in socket_budget.get("unix_socket_candidates", []) + socket_budget.get("related_filesystem_paths", [])
    ):
        raise ValueError("Unix socket/path budget is missing or failed")

    zed_build = json.loads(zed_build_receipt.read_text(encoding="utf-8"))
    zed_sha = sha256(zed_binary)
    if zed_build.get("build") != "passed" or zed_build.get("binary_sha256") != zed_sha:
        raise ValueError("Zed binary does not match the successful derived package build receipt")

    mozc_identity = json.loads(mozc_identity_path.read_text(encoding="utf-8"))
    if mozc_identity.get("schema") != "mzed-mozc-build-identity-v1":
        raise ValueError("Mozc build identity schema mismatch")
    if mozc_identity.get("test_only_authenticated_peer_pid_hook_enabled") is not True:
        raise ValueError("test profile requires the explicit compile-gated peer diagnostic build")
    if mozc_identity.get("peer_pid_emitter_environment") != "MOZC_TEST_FCITX_EMITTER_PID":
        raise ValueError("Mozc identity does not match the test-only exact-emitter guard")
    if mozc_identity.get("peer_pid_log_record") != "MOZC_TEST_AUTHENTICATED_UNIX_PEER_PID=<numeric-pid>":
        raise ValueError("Mozc peer record contract differs from the numeric-only diagnostic")
    expected_hook_patch = bootstrap.load_lock()["diagnostic_profile"]["peer_patch_sha256"]
    if mozc_identity.get("patch_sha256") != expected_hook_patch:
        raise ValueError("Mozc test-only patch hash differs from the lock")
    expected_server = runtime_prefix / "usr/lib/mozc/mozc_server"
    expected_module = runtime_prefix / "usr/lib/x86_64-linux-gnu/fcitx5/fcitx5-mozc.so"
    if mozc_identity.get("server_path") != "usr/lib/mozc/mozc_server":
        raise ValueError("Mozc server identity must use the private server-dir-relative location")
    if mozc_identity.get("fcitx_compiled_server_path") != "usr/lib/mozc/mozc_server":
        raise ValueError("Fcitx's compiled server path differs from the private runtime path")
    expected_server_directory = str(runtime_prefix / "usr/lib/mozc")
    if mozc_identity.get("configured_server_directory") != expected_server_directory:
        raise ValueError("Mozc artifacts were built for a different absolute runtime prefix")
    if mozc_identity.get("fcitx5_mozc_path") != "usr/lib/x86_64-linux-gnu/fcitx5/fcitx5-mozc.so":
        raise ValueError("Fcitx module identity must use the private prefix module location")
    if not regular(expected_server) or not regular(expected_module):
        raise ValueError("built Mozc server or Fcitx module is missing from the runtime prefix")
    if sha256(expected_server) != mozc_identity.get("server_sha256"):
        raise ValueError("Mozc server binary hash differs from its build identity")
    if sha256(expected_module) != mozc_identity.get("fcitx5_mozc_sha256"):
        raise ValueError("Fcitx module hash differs from its build identity")
    if mozc_identity.get("server_and_fcitx_share_base_core") is not True:
        raise ValueError("Mozc server and Fcitx module must share the same base-core build")
    expected_configs = bootstrap.load_lock()["components"]["mozc"]["fcitx5_config_files"]
    config_hashes = mozc_identity.get("fcitx5_config_files_sha256")
    if not isinstance(config_hashes, dict) or set(config_hashes) != set(expected_configs.values()):
        raise ValueError("Fcitx descriptor identity does not match the pinned source mappings")
    for relative in expected_configs.values():
        config_path = runtime_prefix / relative
        if not regular(config_path) or sha256(config_path) != config_hashes[relative]:
            raise ValueError(f"Fcitx descriptor is missing or mismatched: {Path(relative).name}")

    source_hashes, source_lock_hash = verify_repo_sources()
    runner = run_root / "runner"
    runner.mkdir(mode=0o700)
    copied: dict[str, str] = {}
    for relative, destination_relative in RUNTIME_COPY_MAP.items():
        source = HERE / relative
        destination = run_root / destination_relative
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source, destination)
        if not regular(destination) or sha256(destination) != source_hashes[relative]:
            raise RuntimeError(f"assembled runtime source hash mismatch: {relative}")
        copied[destination_relative] = sha256(destination)
        if destination.suffix in {".sh", ""}:
            destination.chmod(0o700)

    gyp_receipt_name = str(mozc_identity.get("gyp_configure_receipt", ""))
    if Path(gyp_receipt_name).name != gyp_receipt_name or not gyp_receipt_name:
        raise ValueError("Mozc build identity must refer to its GYP receipt by basename")
    gyp_source = mozc_identity_path.parent / gyp_receipt_name
    if not regular(gyp_source) or sha256(gyp_source) != mozc_identity.get("gyp_configure_receipt_sha256"):
        raise ValueError("GYP receipt is missing or does not match the Mozc build identity")
    gyp_receipt = json.loads(gyp_source.read_text(encoding="utf-8"))
    graph_files = gyp_receipt.get("build_graph_files_sha256")
    expected_graph_files = {"build.ninja", "obj/base/base_core.ninja", "obj/ipc/ipc.ninja"}
    commands_sha = gyp_receipt.get("target_commands_sha256")
    source_tree_relative = mozc_identity.get("source_tree_relative")
    source_tree_path = PurePosixPath(source_tree_relative) if isinstance(source_tree_relative, str) else PurePosixPath("/")
    if (not source_tree_relative or source_tree_path.is_absolute() or ".." in source_tree_path.parts
            or gyp_receipt.get("source_tree_relative") != source_tree_relative
            or gyp_receipt.get("server_dir_relative") != "usr/lib/mozc"
            or gyp_receipt.get("server_dir_absolute") != expected_server_directory
            or gyp_receipt.get("peer_pid_hook_enabled") is not True
            or not isinstance(graph_files, dict) or set(graph_files) != expected_graph_files
            or any(not isinstance(value, str) or len(value) != 64 for value in graph_files.values())
            or not isinstance(commands_sha, str) or len(commands_sha) != 64):
        raise ValueError("GYP receipt does not match the private server-dir diagnostic build")
    source_tree = mozc_identity_path.parent.joinpath(*source_tree_path.parts)
    graph_root = source_tree / "src/out_linux/Release"
    for relative, expected in graph_files.items():
        graph_path = graph_root / relative
        if (not regular(graph_path) or not isinstance(expected, str) or sha256(graph_path) != expected):
            raise ValueError(f"GYP target graph file is missing or mismatched: {Path(relative).name}")
    identity_copy = runner / "build-identity.json"
    gyp_copy = runner / gyp_receipt_name
    shutil.copyfile(mozc_identity_path, identity_copy)
    shutil.copyfile(gyp_source, gyp_copy)
    copied["runner/build-identity.json"] = sha256(identity_copy)
    copied[f"runner/{gyp_receipt_name}"] = sha256(gyp_copy)
    shutil.copyfile(SOURCE_LOCK, runner / "source-lock.json")
    copied["runner/source-lock.json"] = sha256(runner / "source-lock.json")

    manifest = {
        "schema": "mzed-private-ime-run-v1",
        "run_root": str(run_root),
        "runtime_backend": "nested labwc over owner-provided X11 using Pixman",
        "zed_stateless": True,
        "profile_manifest_sha256": sha256(profile_manifest_path),
        "socket_path_budget_sha256": sha256(socket_budget_path),
        "source_lock_sha256": source_lock_hash,
        "runtime_inputs": copied,
        "zed_binary": {"path": str(zed_binary), "sha256": zed_sha},
        "mozc_identity": {
            "build_identity_sha256": sha256(identity_copy),
            "server_sha256": mozc_identity["server_sha256"],
            "fcitx5_mozc_sha256": mozc_identity["fcitx5_mozc_sha256"],
            "configured_server_directory": mozc_identity["configured_server_directory"],
            "fcitx5_config_files_sha256": mozc_identity["fcitx5_config_files_sha256"],
            "peer_pid_hook_enabled": True,
            "global_process_completeness_claimed": False,
        },
        "scope": "test-only one-run nested Wayland IME profile; no user profile copied; no global process uniqueness claim",
    }
    manifest_path = run_root / "integration-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"assembled": True, "runtime_input_count": len(copied), "integration_manifest_sha256": sha256(manifest_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--runtime-prefix", type=Path, required=True)
    parser.add_argument("--zed-binary", type=Path, required=True)
    parser.add_argument("--zed-build-receipt", type=Path, required=True)
    parser.add_argument("--mozc-build-identity", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = assemble(args.run_root, args.runtime_prefix, args.zed_binary,
                          args.zed_build_receipt, args.mozc_build_identity)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"runtime assembly failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
