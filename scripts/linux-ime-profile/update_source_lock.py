#!/usr/bin/env python3
"""Regenerate or verify the profile's runtime/source integrity lock."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "source-lock.json"
SCHEMA = "mzed-ime-runtime-source-lock-v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_files(root: Path = HERE) -> dict[str, str]:
    """Hash every profile input except this generated lock and Python caches."""
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError(f"profile source tree must not contain symlinks: {relative.as_posix()}")
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_dir() or relative.as_posix() == "source-lock.json":
            continue
        if not path.is_file():
            raise ValueError(f"profile source tree contains a non-regular entry: {relative.as_posix()}")
        files[relative.as_posix()] = sha256(path)
    if not files:
        raise ValueError("profile source tree is empty")
    return files


def payload(root: Path = HERE) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "purpose": "Pins the reusable private profile bootstrap, optional diagnostic patch, runtime orchestration, profile templates and tests. It contains no build output or runtime evidence.",
        "files": collect_files(root),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if source-lock.json is stale")
    args = parser.parse_args(argv)
    try:
        data = payload()
        expected = json.dumps(data, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not LOCK_PATH.is_file() or LOCK_PATH.is_symlink():
                raise ValueError("source lock is missing or not a regular file")
            if LOCK_PATH.read_text(encoding="utf-8") != expected:
                raise ValueError("source-lock.json is stale; rerun update_source_lock.py")
            print("source lock matches the current profile source tree")
        else:
            LOCK_PATH.write_text(expected, encoding="utf-8")
            print(f"updated source lock for {len(data['files'])} files")
    except (OSError, ValueError) as error:
        print(f"source lock update failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
