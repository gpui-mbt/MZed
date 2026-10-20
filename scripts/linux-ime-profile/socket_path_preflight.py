#!/usr/bin/env python3
"""Read-only Unix-socket and path-length preflight for an isolated profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

DEFAULT_UN_H = Path("/usr/include/linux/un.h")


def _existing_ancestor(path: Path) -> Path:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            raise FileNotFoundError("no existing ancestor for path preflight")
        current = parent
    return current


def sun_path_capacity(header: Path = DEFAULT_UN_H) -> tuple[int, dict[str, str]]:
    data = header.read_bytes()
    match = re.search(rb"^#define\s+UNIX_PATH_MAX\s+(\d+)\s*$", data, re.MULTILINE)
    if match is None:
        raise RuntimeError("Linux AF_UNIX path capacity was not found in linux/un.h")
    return int(match.group(1)), {
        "header": header.name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "macro": "UNIX_PATH_MAX",
    }


def filesystem_socket(name: str, path: Path, capacity: int) -> dict[str, object]:
    encoded = os.fsencode(path)
    required = len(encoded) + 1  # pathname sockets include a trailing NUL
    return {
        "name": name,
        "transport": "filesystem-unix-socket",
        "path_bytes": len(encoded),
        "required_sun_path_bytes": required,
        "sun_path_capacity_bytes": capacity,
        "fits": required <= capacity,
    }


def abstract_socket(name: str, payload: bytes, capacity: int) -> dict[str, object]:
    # The leading NUL is part of a Linux abstract address; there is no terminator.
    return {
        "name": name,
        "transport": "linux-abstract-unix-socket",
        "address_bytes": len(payload),
        "sun_path_capacity_bytes": capacity,
        "fits": len(payload) <= capacity,
    }


def filesystem_path(name: str, path: Path, path_max: int, name_max: int) -> dict[str, object]:
    encoded = os.fsencode(path)
    components = [len(os.fsencode(part)) for part in path.parts if part != "/"]
    longest = max(components, default=0)
    return {
        "name": name,
        "transport": "filesystem-path-not-a-socket",
        "path_bytes": len(encoded),
        "path_max_bytes_including_nul": path_max,
        "longest_component_bytes": longest,
        "name_max_bytes": name_max,
        "fits": len(encoded) + 1 <= path_max and longest <= name_max,
    }


def build_socket_budget(run_root: Path, header: Path = DEFAULT_UN_H) -> dict[str, object]:
    if not run_root.is_absolute() or run_root == Path("/"):
        raise ValueError("run root must be an absolute non-root path")
    capacity, header_info = sun_path_capacity(header)
    runtime = run_root / "profile/xdg-runtime"
    config = run_root / "profile/xdg-config"
    home = run_root / "profile/home"
    zed_data = run_root / "profile/zed-stateless-user-data"
    path_anchor = _existing_ancestor(run_root)
    path_max = os.pathconf(path_anchor, "PC_PATH_MAX")
    name_max = os.pathconf(path_anchor, "PC_NAME_MAX")

    mozc_abstract = b"\0tmp/.mozc." + (b"0" * 32) + b".session"
    sockets = [
        filesystem_socket("D-Bus session bus", runtime / "bus", capacity),
        filesystem_socket("nested Wayland display", runtime / "wayland-0", capacity),
        filesystem_socket("Zed stable-channel listener candidate", zed_data / "zed-stable.sock", capacity),
        abstract_socket("Mozc session IPC (random key omitted)", mozc_abstract, capacity),
    ]
    related = [
        filesystem_path("XDG_RUNTIME_DIR", runtime, path_max, name_max),
        filesystem_path("D-Bus runtime state directory", runtime / "dbus-1", path_max, name_max),
        filesystem_path("Wayland lock file", runtime / "wayland-0.lock", path_max, name_max),
        filesystem_path("Mozc session key file", config / "mozc/.session.ipc", path_max, name_max),
        filesystem_path("HOME compatibility path", home / ".mozc", path_max, name_max),
    ]
    entries = sockets + related
    return {
        "schema": "mzed-unix-socket-path-budget-v2",
        "status": "pass" if all(item["fits"] for item in entries) else "fail",
        "sun_path": {"capacity_bytes": capacity, "header": header_info},
        "filesystem_path_limits": {
            "path_max_bytes_including_nul": path_max,
            "name_max_bytes": name_max,
        },
        "unix_socket_candidates": sockets,
        "related_filesystem_paths": related,
        "protocol_routes": {
            "Fcitx5": "private D-Bus and nested Wayland sockets",
            "Mozc": "Linux abstract IPC; the random session key is not stored",
            "Zed CLI": "single-instance listener is skipped in the ZED_STATELESS=1 diagnostic profile",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--header", type=Path, default=DEFAULT_UN_H)
    args = parser.parse_args(argv)
    try:
        result = build_socket_budget(args.run_root, args.header)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"socket preflight failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
