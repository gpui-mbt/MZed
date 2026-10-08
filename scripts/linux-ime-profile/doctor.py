#!/usr/bin/env python3
"""Read-only checks for the supported MZed build and test-only IME profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import platform
import re
import shutil
import stat
import sys

import bootstrap
from socket_path_preflight import DEFAULT_UN_H, build_socket_budget, sun_path_capacity


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def elf(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            return source.read(4) == b"\x7fELF"
    except (OSError, AttributeError):
        return False


def host_checks(lock: dict[str, object]) -> dict[str, object]:
    problems: list[str] = []
    if sys.platform != "linux":
        problems.append("linux-host-required")
    if platform.machine() not in {"x86_64", "amd64"}:
        problems.append("amd64-host-required")
    if sys.version_info < (3, 10):
        problems.append("python-3.10-or-newer-required")
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        release = {}
        problems.append("os-release-unavailable")
    if release.get("ID") != "debian" or release.get("VERSION_ID") != "13":
        problems.append("debian-13-host-required")
    for command in ("bash", "python3", "dpkg-deb", "perl"):
        if shutil.which(command) is None:
            problems.append(f"missing-host-tool:{command}")
    try:
        socket_capacity, socket_header = sun_path_capacity(DEFAULT_UN_H)
    except (OSError, RuntimeError):
        socket_capacity, socket_header = None, None
        problems.append("linux-unix-socket-header-unavailable")
    return {
        "mode": "host",
        "lock_schema": lock["schema"],
        "runtime_package_count": len(bootstrap.selected_package_rows(lock, "wayland_profile")),
        "build_package_count": len(bootstrap.selected_package_rows(lock, "mozc_build")),
        "checks_passed": not problems,
        "problems": problems,
        "sun_path_capacity_bytes": socket_capacity,
        "sun_path_header": socket_header,
        "host_distribution": release.get("PRETTY_NAME", "not-observed"),
        "downloads_performed": False,
        "packages_installed": False,
        "processes_started": False,
    }


def build_checks(args: argparse.Namespace) -> dict[str, object]:
    problems: list[str] = []
    lock = bootstrap.load_lock()
    runtime = args.runtime_prefix
    compiler = args.build_prefix
    identity_path = args.mozc_build_identity
    if runtime.is_symlink() or not runtime.is_dir() or runtime.stat().st_uid != os.getuid():
        problems.append("runtime-prefix-not-owned-real-directory")
    if compiler.is_symlink() or not compiler.is_dir() or compiler.stat().st_uid != os.getuid():
        problems.append("build-prefix-not-owned-real-directory")
    build_layout_ok = bootstrap.verify_build_prefix_layout(compiler, lock)
    if not build_layout_ok:
        problems.append("private-build-prefix-merged-usr-layout-invalid")
    for rel in ("usr/bin/labwc", "usr/bin/dbus-daemon", "usr/bin/fcitx5", "usr/bin/fcitx5-remote", "usr/bin/wayland-info"):
        path = runtime / rel
        if not bootstrap.private_executable(path, runtime):
            problems.append(f"missing-runtime-tool:{Path(rel).name}")
    for rel in ("usr/bin/gcc", "usr/bin/g++", "usr/bin/gyp", "usr/bin/ninja", "usr/bin/python3", "usr/bin/dpkg-source", "usr/bin/patch", "usr/bin/pkg-config"):
        path = compiler / rel
        if not bootstrap.private_executable(path, compiler):
            problems.append(f"missing-build-tool:{Path(rel).name}")
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        identity = {}
        problems.append("mozc-build-identity-unreadable")
    if identity.get("schema") != "mzed-mozc-build-identity-v1":
        problems.append("mozc-build-identity-schema-mismatch")
    test_hook = identity.get("test_only_authenticated_peer_pid_hook_enabled")
    if args.diagnostic_profile and test_hook is not True:
        problems.append("diagnostic-profile-requires-compile-gated-peer-hook")
    if not args.diagnostic_profile and test_hook is True:
        problems.append("test-only-peer-hook-present-outside-explicit-diagnostic-mode")
    if args.diagnostic_profile and identity.get("input_text_logged") is not False:
        problems.append("diagnostic-build-must-explicitly-disable-input-text-logging")
    defines = str(identity.get("gyp_defines", ""))
    if args.diagnostic_profile and "mozc_test_log_authenticated_peer_pid=1" not in defines:
        problems.append("diagnostic-gyp-compile-gate-not-enabled")
    if not args.diagnostic_profile and "mozc_test_log_authenticated_peer_pid=" in defines:
        problems.append("peer-pid-gyp-define-outside-explicit-diagnostic-mode")
    if identity.get("server_and_fcitx_share_base_core") is not True:
        problems.append("server-and-client-base-core-identity-missing")
    server = runtime / "usr/lib/mozc/mozc_server"
    module = runtime / "usr/lib/x86_64-linux-gnu/fcitx5/fcitx5-mozc.so"
    if not elf(server) or server.is_symlink():
        problems.append("built-mozc-server-missing-or-not-elf")
    elif sha256(server) != identity.get("server_sha256"):
        problems.append("built-mozc-server-hash-mismatch")
    if not elf(module) or module.is_symlink():
        problems.append("built-fcitx-module-missing-or-not-elf")
    elif sha256(module) != identity.get("fcitx5_mozc_sha256"):
        problems.append("built-fcitx-module-hash-mismatch")
    if identity.get("server_path") != "usr/lib/mozc/mozc_server":
        problems.append("server-path-in-identity-mismatch")
    if identity.get("fcitx_compiled_server_path") != "usr/lib/mozc/mozc_server":
        problems.append("compiled-fcitx-server-path-mismatch")
    if identity.get("configured_server_directory") != str(runtime / "usr/lib/mozc"):
        problems.append("configured-server-directory-path-mismatch")
    if identity.get("source_package") != f"mozc {lock['source_packages']['mozc']['version']}":
        problems.append("mozc-source-version-mismatch")
    expected_configs = lock["components"]["mozc"]["fcitx5_config_files"]
    config_hashes = identity.get("fcitx5_config_files_sha256")
    if not isinstance(config_hashes, dict) or set(config_hashes) != set(expected_configs.values()):
        problems.append("fcitx5-config-file-identity-mismatch")
        config_hashes = {}
    for relative in expected_configs.values():
        config_path = runtime / relative
        if (not config_path.is_file() or config_path.is_symlink()
                or sha256(config_path) != config_hashes.get(relative)):
            problems.append(f"fcitx5-config-file-missing-or-mismatched:{Path(relative).name}")
    expected_patch = lock.get("diagnostic_profile", {}).get("peer_patch_sha256")
    if args.diagnostic_profile and identity.get("patch_sha256") != expected_patch:
        problems.append("test-only-source-patch-hash-mismatch")
    if not args.diagnostic_profile and identity.get("patch_sha256") is not None:
        problems.append("test-only-source-patch-present-outside-diagnostic-mode")
    receipt_name = identity.get("gyp_configure_receipt")
    receipt_sha = identity.get("gyp_configure_receipt_sha256")
    if isinstance(receipt_name, str) and Path(receipt_name).name == receipt_name and isinstance(receipt_sha, str):
        receipt_path = identity_path.parent / receipt_name
        if not receipt_path.is_file() or receipt_path.is_symlink() or sha256(receipt_path) != receipt_sha:
            problems.append("gyp-configure-receipt-mismatch")
        else:
            try:
                gyp = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                gyp = {}
            if (gyp.get("server_dir_relative") != "usr/lib/mozc"
                    or gyp.get("server_dir_absolute") != str(runtime / "usr/lib/mozc")
                    or gyp.get("source_tree_relative") != identity.get("source_tree_relative")
                    or gyp.get("peer_pid_hook_enabled") is not bool(args.diagnostic_profile)):
                problems.append("gyp-configure-receipt-contract-mismatch")
            source_tree_relative = gyp.get("source_tree_relative")
            source_tree_path = PurePosixPath(source_tree_relative) if isinstance(source_tree_relative, str) else PurePosixPath("/")
            graph_hashes = gyp.get("build_graph_files_sha256")
            expected_graphs = {"build.ninja", "obj/base/base_core.ninja", "obj/ipc/ipc.ninja"}
            if (not source_tree_relative or source_tree_path.is_absolute() or ".." in source_tree_path.parts
                    or not isinstance(graph_hashes, dict) or set(graph_hashes) != expected_graphs):
                problems.append("gyp-target-graph-receipt-invalid")
            else:
                source_tree = identity_path.parent.joinpath(*source_tree_path.parts)
                if source_tree.is_symlink() or not source_tree.is_dir():
                    problems.append("gyp-source-tree-missing-or-symlinked")
                graph_root = source_tree / "src/out_linux/Release"
                for relative, expected in graph_hashes.items():
                    graph_file = graph_root / relative
                    if (not graph_file.is_file() or graph_file.is_symlink()
                            or not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None
                            or sha256(graph_file) != expected):
                        problems.append(f"gyp-target-graph-file-mismatch:{Path(relative).name}")
                target_commands_sha = gyp.get("target_commands_sha256")
                if not isinstance(target_commands_sha, str) or re.fullmatch(r"[0-9a-f]{64}", target_commands_sha) is None:
                    problems.append("gyp-target-command-hash-invalid")
    else:
        problems.append("gyp-configure-receipt-identity-missing")

    zed_ok = False
    if bool(args.zed_binary) != bool(args.zed_build_receipt):
        problems.append("zed-binary-and-build-receipt-must-be-provided-together")
    if args.zed_binary and args.zed_build_receipt:
        try:
            receipt = json.loads(args.zed_build_receipt.read_text(encoding="utf-8"))
            zed_ok = (receipt.get("build") == "passed" and elf(args.zed_binary)
                      and not args.zed_binary.is_symlink()
                      and sha256(args.zed_binary) == receipt.get("binary_sha256"))
        except (OSError, json.JSONDecodeError):
            zed_ok = False
        if not zed_ok:
            problems.append("zed-binary-does-not-match-successful-build-receipt")

    return {
        "mode": "diagnostic-profile" if args.diagnostic_profile else "runtime-prefix",
        "checks_passed": not problems,
        "problems": problems,
        "mozc_test_hook_enabled": test_hook is True,
        "private_build_prefix_layout_valid": build_layout_ok,
        "input_text_logging_enabled": False,
        "zed_build_receipt_matches": zed_ok if args.zed_binary else None,
        "global_process_completeness_claimed": False,
        "downloads_performed": False,
        "packages_installed": False,
        "processes_started": False,
    }


def profile_checks(run_root: Path, runtime_prefix: Path) -> dict[str, object]:
    problems: list[str] = []
    profile = run_root / "profile"
    manifest_path = run_root / "profile-manifest.json"
    budget_path = run_root / "socket-path-budget.json"
    if run_root.is_symlink() or not run_root.is_dir() or run_root.stat().st_uid != os.getuid():
        problems.append("run-root-not-owned-real-directory")
    if runtime_prefix.is_symlink() or not runtime_prefix.is_dir() or runtime_prefix.stat().st_uid != os.getuid():
        problems.append("runtime-prefix-not-owned-real-directory")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        budget = json.loads(budget_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest, budget = {}, {}
        problems.append("profile-or-socket-budget-receipt-unreadable")
    if manifest.get("schema") != "mzed-test-profile-v1" or manifest.get("copied_user_data") is not False:
        problems.append("profile-is-not-certified-fresh-and-empty")
    expected_files = manifest.get("profile_files", {})
    if not isinstance(expected_files, dict):
        expected_files = {}
        problems.append("profile-file-manifest-invalid")
    raw_dirs = manifest.get("profile_directories", [])
    if not isinstance(raw_dirs, list) or any(not isinstance(item, str) for item in raw_dirs):
        raw_dirs = []
        problems.append("profile-directory-manifest-invalid")
    expected_dirs = set(raw_dirs)
    actual_files: set[str] = set()
    actual_dirs: set[str] = set()
    if profile.is_dir() and not profile.is_symlink():
        for path in profile.rglob("*"):
            relative = path.relative_to(profile).as_posix()
            if path.is_symlink():
                problems.append("profile-contains-symlink")
            elif path.is_file():
                actual_files.add(relative)
            elif path.is_dir():
                actual_dirs.add(relative)
            else:
                problems.append("profile-contains-nonregular-entry")
    if actual_files != set(expected_files):
        problems.append("profile-file-set-differs-from-template")
    if actual_dirs != expected_dirs:
        problems.append("profile-directory-set-differs-from-template")
    for relative, record in expected_files.items():
        if not isinstance(relative, str) or not isinstance(record, dict) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            problems.append("profile-file-manifest-invalid")
            continue
        file_path = profile / relative
        if (not file_path.is_file() or file_path.is_symlink()
                or file_path.stat().st_size != record.get("bytes")
                or sha256(file_path) != record.get("sha256")):
            problems.append("profile-template-file-hash-mismatch")
    runtime_dir = profile / "xdg-runtime"
    if not runtime_dir.is_dir() or runtime_dir.is_symlink() or stat.S_IMODE(runtime_dir.stat().st_mode) != 0o700:
        problems.append("private-xdg-runtime-directory-not-mode-0700")
    if budget.get("status") != "pass":
        problems.append("socket-path-budget-not-passing")
    if any(not item.get("fits", False) for item in budget.get("unix_socket_candidates", []) + budget.get("related_filesystem_paths", [])):
        problems.append("socket-or-related-path-exceeds-budget")
    for basename in ("bus", "wayland-0", "wayland-0.lock"):
        if (runtime_dir / basename).exists() or (runtime_dir / basename).is_symlink():
            problems.append(f"stale-runtime-entry:{basename}")
    return {
        "mode": "profile",
        "checks_passed": not problems,
        "problems": problems,
        "fresh_run_required": False,
        "runtime_launched": False,
        "socket_path_budget_status": budget.get("status", "not-observed"),
        "runtime_prefix_present": runtime_prefix.is_dir() and not runtime_prefix.is_symlink(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("host", help="check Linux prerequisites and lock format")
    build = sub.add_parser("runtime", help="read-only check of private runtime/build prefixes")
    build.add_argument("--runtime-prefix", type=Path, required=True)
    build.add_argument("--build-prefix", type=Path, required=True)
    build.add_argument("--mozc-build-identity", type=Path, required=True)
    build.add_argument("--diagnostic-profile", action="store_true")
    build.add_argument("--zed-binary", type=Path)
    build.add_argument("--zed-build-receipt", type=Path)
    profile = sub.add_parser("profile", help="read-only check of one prepared profile")
    profile.add_argument("--run-root", type=Path, required=True)
    profile.add_argument("--runtime-prefix", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        lock = bootstrap.load_lock()
        if args.command == "host":
            result = host_checks(lock)
        elif args.command == "runtime":
            result = build_checks(args)
        else:
            result = profile_checks(args.run_root, args.runtime_prefix)
    except (OSError, ValueError, json.JSONDecodeError, bootstrap.BootstrapError) as error:
        print(json.dumps({"checks_passed": False, "problems": [f"doctor-error:{type(error).__name__}"]}, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
