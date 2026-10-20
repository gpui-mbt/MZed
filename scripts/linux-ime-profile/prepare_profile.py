#!/usr/bin/env python3
"""Create a fresh, empty XDG profile for the optional nested IME diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import stat
import sys

from socket_path_preflight import build_socket_budget


HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "profile-template"
DIRECTORIES = (
    "home",
    "xdg-config",
    "xdg-config/fcitx5",
    "xdg-config/fontconfig",
    "xdg-cache",
    "xdg-cache/fontconfig",
    "xdg-cache/mesa_shader_cache",
    "xdg-data",
    "xdg-data/fcitx5",
    "xdg-state",
    "xdg-runtime",
    "labwc",
    "zed-stateless-user-data",
    "zed-stateless-user-data/config",
    "zed-stateless-user-data/logs",
    "empty-project",
    "logs",
    "evidence",
)
TEMPLATES = {
    "labwc/rc.xml": "labwc/rc.xml",
    "xdg-config/fcitx5/profile": "xdg-config/fcitx5/profile",
    "zed-stateless-user-data/config/settings.json": "zed/settings.json",
    "zed-stateless-user-data/config/keymap.json": "zed/keymap.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_private_parent(path: Path) -> None:
    parent = path.parent
    if (not parent.is_dir() or any(ancestor.is_symlink() for ancestor in path.parents if ancestor.exists())):
        raise ValueError("run-root parent must exist and the path may not traverse symlinks")
    info = parent.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise ValueError("run-root parent must be owned by this user and not group/world writable")


def prepare(run_root: Path, runtime_prefix: Path) -> dict[str, object]:
    if not run_root.is_absolute() or run_root == Path("/"):
        raise ValueError("run root must be an absolute non-root path")
    if not runtime_prefix.is_absolute() or runtime_prefix == Path("/"):
        raise ValueError("runtime prefix must be an absolute non-root path")
    if any(ancestor.is_symlink() for ancestor in runtime_prefix.parents if ancestor.exists()):
        raise ValueError("runtime prefix may not traverse a symlinked parent")
    if run_root.exists() or run_root.is_symlink():
        raise FileExistsError("refusing to reuse an existing run root")
    if runtime_prefix.is_symlink() or not runtime_prefix.is_dir():
        raise ValueError("runtime prefix must be an existing real directory")
    if runtime_prefix.stat().st_uid != os.getuid():
        raise ValueError("runtime prefix must be owned by this user")
    require_private_parent(run_root)

    # This read-only check uses the intended final path before any profile files exist.
    budget = build_socket_budget(run_root)
    if budget["status"] != "pass":
        raise ValueError("Unix socket or filesystem path budget failed")

    profile = run_root / "profile"
    run_root.mkdir(mode=0o700)
    for relative in DIRECTORIES:
        (run_root / relative if relative in {"empty-project", "logs", "evidence"} else profile / relative).mkdir(
            parents=True, exist_ok=True, mode=0o700
        )
    os.chmod(profile / "xdg-runtime", 0o700)
    (run_root / "socket-path-budget.json").write_text(
        json.dumps(budget, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    file_records: dict[str, dict[str, object]] = {}
    for destination_relative, source_relative in TEMPLATES.items():
        source = TEMPLATE / source_relative
        destination = profile / destination_relative
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(f"profile template is absent or not regular: {source_relative}")
        shutil.copyfile(source, destination)
        file_records[destination_relative] = {
            "template": source_relative,
            "sha256": sha256(destination),
            "bytes": destination.stat().st_size,
        }

    font_template = TEMPLATE / "xdg-config/fontconfig/fonts.conf.in"
    font_text = font_template.read_text(encoding="utf-8")
    font_text = font_text.replace("@RUNTIME_PREFIX@", html.escape(str(runtime_prefix), quote=True))
    font_text = font_text.replace("@PROFILE@", html.escape(str(profile), quote=True))
    if "@RUNTIME_PREFIX@" in font_text or "@PROFILE@" in font_text:
        raise ValueError("unexpanded fontconfig template token")
    font_path = profile / "xdg-config/fontconfig/fonts.conf"
    font_path.write_text(font_text, encoding="utf-8")
    file_records["xdg-config/fontconfig/fonts.conf"] = {
        "template": "xdg-config/fontconfig/fonts.conf.in",
        "sha256": sha256(font_path),
        "bytes": font_path.stat().st_size,
    }

    manifest = {
        "schema": "mzed-test-profile-v1",
        "scope": "test-only fresh nested Wayland IME profile",
        "runtime_backend": "nested labwc over owner-provided X11 using Pixman",
        "zed_mode": "ZED_STATELESS=1",
        "copied_user_data": False,
        "runtime_directory_mode": "0700",
        "profile_directories": [item for item in DIRECTORIES if item not in {"empty-project", "logs", "evidence"}],
        "socket_budget_sha256": sha256(run_root / "socket-path-budget.json"),
        "profile_files": file_records,
    }
    (run_root / "profile-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {
        "prepared": True,
        "profile_manifest_sha256": sha256(run_root / "profile-manifest.json"),
        "socket_budget_status": budget["status"],
        "runtime_directory_mode": "0700",
        "copied_user_data": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--runtime-prefix", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare(args.run_root, args.runtime_prefix)
    except (OSError, ValueError) as error:
        print(f"profile preparation failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
