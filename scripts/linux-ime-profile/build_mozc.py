#!/usr/bin/env python3
"""Build Debian Mozc's server and Fcitx5 module in caller-owned private prefixes.

The authenticated peer-PID hook is opt-in and is never part of a normal build.
This script does not install system packages or launch an input method.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import bootstrap


HERE = Path(__file__).resolve().parent
HOOK_PATCH = HERE / "patches/mozc-authenticated-peer-pid-test-only.patch"
MIN_FREE_BYTES = 3 * 1024**3
GYP_DEFINES_BASE = "use_libprotobuf=1 use_libabseil=1 use_fcitx=NO use_fcitx5=YES"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_private_directory(path: Path, *, must_exist: bool = True) -> None:
    if not path.is_absolute() or path == Path("/") or path.is_symlink():
        raise ValueError("use an absolute non-root path without a symlink")
    if any(parent.is_symlink() for parent in path.parents if parent.exists()):
        raise ValueError("path may not traverse a symlinked parent")
    if must_exist and (not path.is_dir() or path.stat().st_uid != os.getuid()):
        raise ValueError("directory must exist and be owned by this user")


def source_file_hashes(source_tree: Path) -> dict[str, str]:
    paths = (
        "src/ipc/ipc.gyp",
        "src/ipc/unix_ipc.cc",
        "src/ipc/peer_pid_log_test.h",
        "src/base/system_util.cc",
        "src/unix/fcitx5/mozc-addon.conf",
        "src/unix/fcitx5/mozc.conf",
        "debian/fcitx5-mozc.install",
    )
    return {relative: sha256(source_tree / relative) for relative in paths if (source_tree / relative).is_file()}


def verify_diagnostic_patch(lock: dict[str, object], patch_path: Path = HOOK_PATCH) -> None:
    diagnostic = lock.get("diagnostic_profile", {})
    expected = diagnostic.get("peer_patch_sha256")
    if not isinstance(expected, str) or not patch_path.is_file() or patch_path.is_symlink():
        raise ValueError("the locked diagnostic patch is missing")
    if sha256(patch_path) != expected:
        raise ValueError("diagnostic patch SHA256 differs from the profile lock")


def validate_ninja_target_commands(commands: bytes, server_dir: Path, diagnostic_peer_pid: bool) -> str:
    text = commands.decode("utf-8", errors="replace")
    if not text.strip() or str(server_dir) not in text:
        raise ValueError("expanded Ninja target commands do not contain the configured private server directory")
    hook_enabled = "MOZC_TEST_AUTHENTICATED_PEER_PID" in text
    if hook_enabled != diagnostic_peer_pid:
        raise ValueError("expanded Ninja target commands do not match the requested diagnostic compile gate")
    return hashlib.sha256(commands).hexdigest()


def inspect_ninja_target_graph(build_dir: Path, ninja: Path, server_dir: Path,
                               diagnostic_peer_pid: bool, env: dict[str, str]) -> dict[str, object]:
    graph_files = ("build.ninja", "obj/base/base_core.ninja", "obj/ipc/ipc.ninja")
    graph_hashes: dict[str, str] = {}
    top_level = build_dir / "build.ninja"
    if not top_level.is_file() or top_level.is_symlink():
        raise RuntimeError("GYP did not create the expected top-level Ninja graph")
    top_text = top_level.read_text(encoding="utf-8")
    for relative in graph_files[1:]:
        if f"subninja {relative}" not in top_text:
            raise RuntimeError(f"top-level Ninja graph does not include {relative}")
    for relative in graph_files:
        graph_path = build_dir / relative
        if not graph_path.is_file() or graph_path.is_symlink():
            raise RuntimeError(f"GYP target graph file is missing or unsafe: {relative}")
        graph_hashes[relative] = sha256(graph_path)

    result = subprocess.run(
        [str(ninja), "-C", str(build_dir), "-t", "commands", "mozc_server", "fcitx5-mozc"],
        env=env,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError(f"Ninja target graph inspection failed with exit status {result.returncode}")
    commands_sha = validate_ninja_target_commands(result.stdout, server_dir, diagnostic_peer_pid)
    return {
        "graph_files_sha256": graph_hashes,
        "target_commands_sha256": commands_sha,
    }


def install_fcitx5_config_files(source_tree: Path, runtime_prefix: Path,
                               lock: dict[str, object]) -> dict[str, str]:
    mapping = lock["components"]["mozc"]["fcitx5_config_files"]
    recipe = source_tree / "debian/fcitx5-mozc.install"
    if not recipe.is_file() or recipe.is_symlink():
        raise FileNotFoundError("pinned Debian Fcitx install mapping is absent")
    recipe_lines = [line.strip().split() for line in recipe.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
    for source_relative, destination_relative in mapping.items():
        declared = None
        for tokens in recipe_lines:
            if tokens[0] != source_relative:
                continue
            if "=>" in tokens:
                separator = tokens.index("=>")
                if separator + 1 < len(tokens):
                    declared = tokens[separator + 1]
            elif len(tokens) > 1:
                declared = tokens[-1]
                if declared.endswith("/"):
                    declared = f"{declared}{Path(source_relative).name}"
            break
        if declared != destination_relative:
            raise ValueError(f"pinned Debian package mapping differs for {source_relative}")
    installed: dict[str, str] = {}
    for source_relative, destination_relative in mapping.items():
        source = source_tree / source_relative
        destination = runtime_prefix / destination_relative
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(f"pinned Fcitx descriptor is missing: {source_relative}")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"refusing to replace an existing Fcitx descriptor: {destination_relative}")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        shutil.copyfile(source, destination)
        digest = sha256(destination)
        if digest != sha256(source):
            raise RuntimeError(f"copied Fcitx descriptor hash mismatch: {destination_relative}")
        installed[destination_relative] = digest
    return installed


def run_logged(argv: list[str], cwd: Path, env: dict[str, str], log_path: Path, timeout: int) -> None:
    with log_path.open("wb") as output:
        result = subprocess.run(argv, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
                                check=False, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"build command failed with exit status {result.returncode}: {argv[0]}")


def run_ninja_guarded(argv: list[str], cwd: Path, env: dict[str, str], log_path: Path,
                      free_space_root: Path, deadline_seconds: int) -> None:
    started = time.monotonic()
    samples: list[int] = []
    with log_path.open("wb") as output:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        while process.poll() is None:
            if time.monotonic() - started > deadline_seconds:
                try:
                    os.killpg(process.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                raise TimeoutError("Mozc Ninja build exceeded its fixed wall-clock deadline")
            available = shutil.disk_usage(free_space_root).free
            samples.append(available)
            if available < MIN_FREE_BYTES:
                try:
                    os.killpg(process.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                raise OSError("disk reserve fell below 3 GiB; build stopped and partial work was preserved")
            time.sleep(1)
        if process.returncode != 0:
            raise RuntimeError(f"Ninja failed with exit status {process.returncode}")


def build(args: argparse.Namespace) -> dict[str, object]:
    lock = bootstrap.load_lock()
    if args.diagnostic_peer_pid:
        verify_diagnostic_patch(lock)
    package_count, source_count = bootstrap.verify(args.cache_root, lock)
    for directory in (args.cache_root, args.build_prefix, args.runtime_prefix, args.work_root):
        if not directory.is_absolute() or directory == Path("/"):
            raise ValueError("all roots must be absolute non-root paths")
    require_private_directory(args.build_prefix)
    require_private_directory(args.runtime_prefix)
    if not bootstrap.verify_build_prefix_layout(args.build_prefix, lock):
        raise ValueError("private build prefix is missing the locked merged-/usr aliases")
    if args.build_prefix == args.runtime_prefix or args.build_prefix in args.runtime_prefix.parents or args.runtime_prefix in args.build_prefix.parents:
        raise ValueError("build and runtime prefixes must be disjoint")
    if args.work_root.exists() or args.work_root.is_symlink():
        raise FileExistsError("refusing to reuse a build work root")
    if (not args.work_root.is_absolute() or args.work_root == Path("/")
            or not args.work_root.parent.is_dir() or args.work_root.parent.is_symlink()
            or args.work_root.parent.stat().st_uid != os.getuid()
            or any(parent.is_symlink() for parent in args.work_root.parents if parent.exists())):
        raise ValueError("work root must have an existing real parent owned by this user")
    if shutil.disk_usage(args.work_root.parent).free < MIN_FREE_BYTES:
        raise OSError("less than 3 GiB free before starting Mozc build")

    source_cache = args.cache_root / "sources"
    dsc = next(source_cache / item["filename"] for item in bootstrap.source_rows(lock)
               if str(item["filename"]).endswith(".dsc"))
    args.work_root.mkdir(mode=0o700)
    (args.work_root / "home").mkdir(mode=0o700)
    (args.work_root / "tmp").mkdir(mode=0o700)
    source_tree = args.work_root / "source"
    source_tree.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tool_paths = {
        name: args.build_prefix / "usr/bin" / name
        for name in ("gcc", "g++", "gyp", "ninja", "python3", "dpkg-source", "patch", "pkg-config")
    }
    for tool in tool_paths.values():
        if not bootstrap.private_executable(tool, args.build_prefix):
            raise FileNotFoundError(f"private build tool is missing: {tool.name}")
    dpkg_source = tool_paths["dpkg-source"]
    patch = tool_paths["patch"]
    ninja = tool_paths["ninja"]
    python = tool_paths["python3"]

    extract_env = {
        "PATH": f"{args.build_prefix}/usr/bin:/usr/bin:/bin",
        "HOME": str(args.work_root / "home"),
        "TMPDIR": str(args.work_root / "tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    run_logged([str(dpkg_source), "-x", str(dsc), str(source_tree)], source_cache,
               extract_env,
               args.work_root / "source-extract.log", 120)
    # dpkg-source derives the extracted directory from the .dsc's package name.
    if not source_tree.is_dir():
        candidates = [p for p in args.work_root.iterdir() if p.is_dir()]
        if len(candidates) != 1:
            raise RuntimeError("dpkg-source did not create one source tree")
        source_tree = candidates[0]
    if args.diagnostic_peer_pid:
        patch_env = {"PATH": f"{args.build_prefix}/usr/bin:/usr/bin:/bin",
                     "HOME": str(args.work_root / "home"), "TMPDIR": str(args.work_root / "tmp"),
                     "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        run_logged([str(patch), "--dry-run", "--batch", "--fuzz=0", "-p1", "-i", str(HOOK_PATCH)],
                   source_tree, patch_env,
                   args.work_root / "diagnostic-patch-check.log", 30)
        run_logged([str(patch), "--batch", "--fuzz=0", "-p1", "-i", str(HOOK_PATCH)],
                   source_tree, patch_env,
                   args.work_root / "diagnostic-patch-apply.log", 30)

    private_lib = ":".join(str(args.build_prefix / relative) for relative in (
        "usr/lib/x86_64-linux-gnu", "lib/x86_64-linux-gnu", "usr/lib64", "lib64", "usr/lib", "lib"))
    defines = GYP_DEFINES_BASE + (" mozc_test_log_authenticated_peer_pid=1" if args.diagnostic_peer_pid else "")
    env = {
        "PATH": f"{args.build_prefix}/usr/bin:{args.build_prefix}/bin:/usr/bin:/bin",
        "HOME": str(args.work_root / "home"),
        "TMPDIR": str(args.work_root / "tmp"),
        "PYTHONHOME": str(args.build_prefix / "usr"),
        "PYTHONPATH": f"{args.build_prefix}/usr/lib/python3/dist-packages",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "CC": f"{args.build_prefix}/usr/bin/gcc --sysroot={args.build_prefix}",
        "CXX": f"{args.build_prefix}/usr/bin/g++ --sysroot={args.build_prefix}",
        "GCC_EXEC_PREFIX": f"{args.build_prefix}/usr/lib/gcc/",
        "GYP_DEFINES": defines,
        "PKG_CONFIG_LIBDIR": f"{args.build_prefix}/usr/lib/x86_64-linux-gnu/pkgconfig:{args.build_prefix}/usr/share/pkgconfig",
        "PKG_CONFIG_SYSROOT_DIR": str(args.build_prefix),
        "LD_LIBRARY_PATH": private_lib,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    server_dir = args.runtime_prefix / "usr/lib/mozc"
    gyp_argv = [str(python), "build_mozc.py", "gyp", f"--gypdir={args.build_prefix}/usr/bin",
                "--target_platform=Linux", "--noqt", f"--server_dir={server_dir}", "--verbose"]
    gyp_log = args.work_root / "gyp.log"
    run_logged(gyp_argv, source_tree / "src", env, gyp_log, 180)
    ninja_dir = source_tree / "src/out_linux/Release"
    graph_receipt = inspect_ninja_target_graph(
        ninja_dir, ninja, server_dir, bool(args.diagnostic_peer_pid), env
    )
    source_tree_relative = source_tree.relative_to(args.work_root).as_posix()
    gyp_receipt = {
        "schema": "mzed-mozc-gyp-receipt-v1",
        "exit_status": 0,
        "source_version": lock["source_packages"]["mozc"]["version"],
        "source_tree_relative": source_tree_relative,
        "server_dir_relative": "usr/lib/mozc",
        "server_dir_absolute": str(server_dir),
        "gyp_defines": defines,
        "peer_pid_hook_enabled": bool(args.diagnostic_peer_pid),
        "source_file_sha256": source_file_hashes(source_tree),
        "build_graph_files_sha256": graph_receipt["graph_files_sha256"],
        "target_commands_sha256": graph_receipt["target_commands_sha256"],
        "gyp_log_sha256": sha256(gyp_log),
    }
    gyp_receipt_path = args.work_root / "gyp-configure-result.json"
    gyp_receipt_path.write_text(json.dumps(gyp_receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run_ninja_guarded([str(ninja), "-v", "-C", str(source_tree / "src/out_linux/Release"), "-j1",
                       "mozc_server", "fcitx5-mozc"], source_tree / "src", env,
                      args.work_root / "ninja.log", args.work_root, args.deadline_seconds)

    built_server = source_tree / "src/out_linux/Release/mozc_server"
    built_module = source_tree / "src/out_linux/Release/fcitx5-mozc.so"
    for artifact in (built_server, built_module):
        if not artifact.is_file() or artifact.is_symlink():
            raise FileNotFoundError(f"expected Mozc build output is absent: {artifact.name}")
    installed_server = server_dir / "mozc_server"
    installed_module = args.runtime_prefix / "usr/lib/x86_64-linux-gnu/fcitx5/fcitx5-mozc.so"
    if installed_server.exists() or installed_server.is_symlink():
        raise FileExistsError("refusing to replace an existing private Mozc server")
    if installed_module.exists() or installed_module.is_symlink():
        raise FileExistsError("refusing to replace an existing private Fcitx module")
    installed_server.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    installed_module.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    shutil.copyfile(built_server, installed_server)
    shutil.copyfile(built_module, installed_module)
    os.chmod(installed_server, 0o755)
    os.chmod(installed_module, 0o755)
    config_hashes = install_fcitx5_config_files(source_tree, args.runtime_prefix, lock)

    identity = {
        "schema": "mzed-mozc-build-identity-v1",
        "source_package": f"mozc {lock['source_packages']['mozc']['version']}",
        "source_files_sha256": {item["filename"]: item["sha256"] for item in bootstrap.source_rows(lock)},
        "source_build_file_sha256": source_file_hashes(source_tree),
        "test_only_authenticated_peer_pid_hook_enabled": bool(args.diagnostic_peer_pid),
        "input_text_logged": False,
        "authentication_checks_changed": False,
        "peer_pid_emitter_environment": "MOZC_TEST_FCITX_EMITTER_PID" if args.diagnostic_peer_pid else None,
        "peer_pid_log_record": "MOZC_TEST_AUTHENTICATED_UNIX_PEER_PID=<numeric-pid>" if args.diagnostic_peer_pid else None,
        "gyp_defines": defines,
        "server_path": "usr/lib/mozc/mozc_server",
        "server_sha256": sha256(installed_server),
        "configured_server_directory": str(server_dir),
        "fcitx_compiled_server_path": "usr/lib/mozc/mozc_server",
        "fcitx5_mozc_path": "usr/lib/x86_64-linux-gnu/fcitx5/fcitx5-mozc.so",
        "fcitx5_mozc_sha256": sha256(installed_module),
        "fcitx5_config_files_sha256": config_hashes,
        "server_and_fcitx_share_base_core": True,
        "server_dir_relative": "usr/lib/mozc",
        "source_tree_relative": source_tree_relative,
        "gyp_configure_receipt": "gyp-configure-result.json",
        "gyp_configure_receipt_sha256": sha256(gyp_receipt_path),
        "patch_sha256": sha256(HOOK_PATCH) if args.diagnostic_peer_pid else None,
        "build_targets": ["mozc_server", "fcitx5-mozc"],
        "jobs": 1,
        "disk_floor_bytes": MIN_FREE_BYTES,
        "package_count_verified": package_count,
        "source_file_count_verified": source_count,
        "artifacts_launched": False,
    }
    identity_path = args.work_root / "build-identity.json"
    identity_path.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return identity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--build-prefix", type=Path, required=True)
    parser.add_argument("--runtime-prefix", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--diagnostic-peer-pid", action="store_true",
                        help="opt into the separate test-only authenticated numeric peer-PID log hook")
    parser.add_argument("--deadline-seconds", type=int, default=5400)
    args = parser.parse_args(argv)
    if not 60 <= args.deadline_seconds <= 6 * 60 * 60:
        parser.error("--deadline-seconds must be between 60 and 21600")
    try:
        result = build(args)
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, bootstrap.BootstrapError) as error:
        print(f"Mozc build failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
