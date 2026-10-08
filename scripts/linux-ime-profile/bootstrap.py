#!/usr/bin/env python3
"""Fetch, verify, and privately extract the pinned Debian IME profile inputs.

This tool never invokes apt, dpkg maintainer scripts, a compiler, a compositor,
an IME, or Zed. Mozc compilation and the optional diagnostic run are separate
explicit steps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request


HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "manifest.lock.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_DOWNLOAD_HOSTS = {"deb.debian.org"}
DPKG_DEB = Path("/usr/bin/dpkg-deb")


class BootstrapError(Exception):
    pass


class _NoCrossHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if (new.scheme != "https" or new.hostname not in ALLOWED_DOWNLOAD_HOSTS
                or new.username is not None or new.password is not None):
            raise BootstrapError("download redirect left the pinned official Debian host")
        if (old.scheme != "https" or old.hostname not in ALLOWED_DOWNLOAD_HOSTS):
            raise BootstrapError("download origin is not the pinned official Debian host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_lock(path: Path = LOCK_PATH) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "mzed-linux-ime-profile-lock-v1":
        raise BootstrapError("unsupported profile lock schema")
    packages = data.get("packages")
    layers = data.get("layers")
    if not isinstance(packages, dict) or not isinstance(layers, dict):
        raise BootstrapError("profile lock is missing package layers")
    if len(layers.get("wayland_profile", [])) != 219 or len(layers.get("mozc_build", [])) != 321:
        raise BootstrapError("pinned Debian package closure counts changed")
    excluded = set(data.get("overrides", {}).get("runtime_excluded_packages", []))
    if not {"mozc-server", "fcitx5-mozc"}.issubset(excluded):
        raise BootstrapError("runtime must exclude stock Mozc outputs replaced by the private build")
    aliases = data.get("overrides", {}).get("build_prefix_relative_aliases")
    if aliases != {"lib": "usr/lib", "lib64": "usr/lib64"}:
        raise BootstrapError("private build prefix must define the locked merged-/usr aliases")
    config_files = data.get("components", {}).get("mozc", {}).get("fcitx5_config_files")
    if config_files != {
        "src/unix/fcitx5/mozc-addon.conf": "usr/share/fcitx5/addon/mozc.conf",
        "src/unix/fcitx5/mozc.conf": "usr/share/fcitx5/inputmethod/mozc.conf",
    }:
        raise BootstrapError("pinned Mozc Fcitx descriptor mappings changed")
    for source, destination in config_files.items():
        for relative in (source, destination):
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts:
                raise BootstrapError("unsafe relative Fcitx config path in profile lock")
    seen_filenames: dict[str, tuple[str, str]] = {}
    for key, item in packages.items():
        if not isinstance(item, dict) or key != f"{item.get('package')}={item.get('version')}={item.get('architecture')}":
            raise BootstrapError("invalid package identity record")
        if not SHA256_RE.fullmatch(str(item.get("sha256", ""))):
            raise BootstrapError(f"invalid package digest for {key}")
        url = urllib.parse.urlsplit(str(item.get("url", "")))
        if url.scheme != "https" or url.hostname not in ALLOWED_DOWNLOAD_HOSTS or url.username or url.password:
            raise BootstrapError(f"non-Debian package URL in lock: {key}")
        filename = str(item.get("filename", ""))
        if Path(filename).name != filename or not filename.endswith(".deb"):
            raise BootstrapError(f"invalid package filename for {key}")
        prior = seen_filenames.setdefault(filename, (str(item["sha256"]), str(item["url"])))
        if prior != (str(item["sha256"]), str(item["url"])):
            raise BootstrapError(f"conflicting package archive identities: {filename}")
    for layer, refs in layers.items():
        if not isinstance(refs, list) or any(ref not in packages for ref in refs):
            raise BootstrapError(f"invalid package references in layer {layer}")
    sources = data.get("source_packages", {}).get("mozc", {}).get("files", [])
    if len(sources) != 3:
        raise BootstrapError("Mozc source package must contain the pinned .dsc and two source archives")
    for item in sources:
        if not SHA256_RE.fullmatch(str(item.get("sha256", ""))):
            raise BootstrapError("invalid Mozc source archive digest")
        url = urllib.parse.urlsplit(str(item.get("url", "")))
        if url.scheme != "https" or url.hostname not in ALLOWED_DOWNLOAD_HOSTS:
            raise BootstrapError("non-Debian Mozc source URL in lock")
    return data


def selected_package_rows(lock: dict[str, object], layer: str) -> list[dict[str, object]]:
    packages = lock["packages"]
    refs = lock["layers"][layer]
    skipped = set(lock.get("overrides", {}).get("runtime_excluded_packages", [])) if layer == "wayland_profile" else set()
    rows = [packages[key] for key in refs if packages[key]["package"] not in skipped]
    # Build/runtime layers can share archives; only return one extraction per filename.
    unique: dict[str, dict[str, object]] = {}
    for row in rows:
        unique.setdefault(str(row["filename"]), row)
    return [unique[name] for name in sorted(unique)]


def source_rows(lock: dict[str, object]) -> list[dict[str, object]]:
    return list(lock["source_packages"]["mozc"]["files"])


def _check_cache_root(cache_root: Path, *, create: bool) -> None:
    if not cache_root.is_absolute() or cache_root == Path("/") or cache_root.is_symlink():
        raise BootstrapError("cache root must be an absolute non-root path and not a symlink")
    if any(parent.is_symlink() for parent in cache_root.parents if parent.exists()):
        raise BootstrapError("cache root may not traverse a symlinked parent")
    if create:
        cache_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not cache_root.is_dir() or cache_root.stat().st_uid != os.getuid():
        raise BootstrapError("cache root must be an existing directory owned by this user")


def _download(row: dict[str, object], destination: Path) -> None:
    url = str(row["url"])
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise BootstrapError("refusing non-HTTPS or non-Debian download URL")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_file():
            raise BootstrapError(f"cache target is not a regular file: {destination.name}")
        if destination.stat().st_size == int(row["size_bytes"]) and sha256_file(destination) == row["sha256"]:
            return
        raise BootstrapError(f"refusing to overwrite a mismatched cached input: {destination.name}")
    request = urllib.request.Request(url, headers={"User-Agent": "MZed-private-profile-bootstrap/1"})
    opener = urllib.request.build_opener(_NoCrossHostRedirect())
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as output, opener.open(request, timeout=60) as response:
            shutil.copyfileobj(response, output)
        if temp.stat().st_size != int(row["size_bytes"]) or sha256_file(temp) != row["sha256"]:
            raise BootstrapError(f"download did not match the locked size and SHA256: {destination.name}")
        os.chmod(temp, 0o600)
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def fetch(cache_root: Path, lock: dict[str, object]) -> int:
    _check_cache_root(cache_root, create=True)
    rows: dict[str, dict[str, object]] = {}
    for layer in ("wayland_profile", "mozc_build"):
        for row in selected_package_rows(lock, layer):
            rows[str(row["filename"])] = row
    for row in source_rows(lock):
        rows[str(row["filename"])] = row
    for filename in sorted(rows):
        row = rows[filename]
        category = "sources" if filename.endswith((".dsc", ".orig.tar.xz", ".debian.tar.xz")) else "packages"
        _download(row, cache_root / category / filename)
    return len(rows)


def verify(cache_root: Path, lock: dict[str, object]) -> tuple[int, int]:
    _check_cache_root(cache_root, create=False)
    package_rows: dict[str, dict[str, object]] = {}
    for layer in ("wayland_profile", "mozc_build"):
        for row in selected_package_rows(lock, layer):
            package_rows[str(row["filename"])] = row
    for filename, row in package_rows.items():
        path = cache_root / "packages" / filename
        if path.is_symlink() or not path.is_file():
            raise BootstrapError(f"locked Debian package is missing: {filename}")
        if path.stat().st_size != int(row["size_bytes"]) or sha256_file(path) != row["sha256"]:
            raise BootstrapError(f"locked Debian package size or SHA256 mismatch: {filename}")
        if not DPKG_DEB.is_file():
            raise BootstrapError("/usr/bin/dpkg-deb is required to inspect package metadata")
        control = subprocess.run([str(DPKG_DEB), "-f", str(path), "Package", "Version", "Architecture"],
                                 check=True, capture_output=True, text=True).stdout
        expected = f"Package: {row['package']}\nVersion: {row['version']}\nArchitecture: {row['architecture']}\n"
        if control != expected:
            raise BootstrapError(f"Debian package control identity mismatch: {filename}")
    sources = source_rows(lock)
    for row in sources:
        path = cache_root / "sources" / str(row["filename"])
        if path.is_symlink() or not path.is_file():
            raise BootstrapError(f"locked Mozc source file is missing: {row['filename']}")
        if path.stat().st_size != int(row["size_bytes"]) or sha256_file(path) != row["sha256"]:
            raise BootstrapError(f"locked Mozc source size or SHA256 mismatch: {row['filename']}")
    return len(package_rows), len(sources)


def private_prefix(path: Path) -> None:
    if not path.is_absolute() or path == Path("/") or path.is_symlink():
        raise BootstrapError("prefix must be absolute, non-root, and not a symlink")
    if any(parent.is_symlink() for parent in path.parents if parent.exists()):
        raise BootstrapError("prefix may not traverse a symlinked parent")
    for system_root in ("/bin", "/sbin", "/lib", "/lib64", "/usr", "/etc", "/var", "/opt", "/root", "/home"):
        if path == Path(system_root) or Path(system_root) in path.parents:
            raise BootstrapError("refusing a system prefix; use a private user-owned directory")
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink() or parent.stat().st_uid != os.getuid():
        raise BootstrapError("prefix parent must exist, be real, and be owned by this user")
    if path.exists() and (not path.is_dir() or path.stat().st_uid != os.getuid() or any(path.iterdir())):
        raise BootstrapError("prefix must be new or an empty user-owned directory; existing work is preserved")


def private_executable(path: Path, prefix: Path) -> bool:
    """Accept a private tool, including Debian's relative links within its prefix."""
    try:
        root_lexical = Path(os.path.abspath(prefix))
        candidate = Path(os.path.abspath(path))
        candidate.relative_to(root_lexical)
        root_real = prefix.resolve(strict=True)
        if path.is_symlink() and Path(os.readlink(path)).is_absolute():
            return False
        resolved = path.resolve(strict=True)
        resolved.relative_to(root_real)
        return resolved.is_file() and os.access(path, os.X_OK)
    except (OSError, RuntimeError, ValueError):
        return False


def verify_build_prefix_layout(prefix: Path, lock: dict[str, object] | None = None) -> bool:
    try:
        data = load_lock() if lock is None else lock
        if any(parent.is_symlink() for parent in prefix.parents if parent.exists()):
            return False
        root = prefix.resolve(strict=True)
        if prefix.is_symlink() or not prefix.is_dir() or prefix.stat().st_uid != os.getuid():
            return False
        aliases = data["overrides"]["build_prefix_relative_aliases"]
        for name, target in aliases.items():
            target_path = prefix / str(target)
            alias_path = prefix / str(name)
            if (target_path.is_symlink() or not target_path.is_dir()
                    or not alias_path.is_symlink() or os.readlink(alias_path) != str(target)):
                return False
            alias_path.resolve(strict=True).relative_to(root)
        return True
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return False


def prepare_build_prefix_layout(prefix: Path, lock: dict[str, object]) -> dict[str, str]:
    """Create only the locked relative merged-/usr aliases in a fresh prefix."""
    if not prefix.is_absolute() or prefix == Path("/") or prefix.is_symlink():
        raise BootstrapError("build prefix must be absolute, non-root, and real")
    if any(parent.is_symlink() for parent in prefix.parents if parent.exists()):
        raise BootstrapError("build prefix may not traverse a symlinked parent")
    if not prefix.is_dir() or prefix.stat().st_uid != os.getuid():
        raise BootstrapError("build prefix must exist and be owned by this user")
    aliases = lock["overrides"]["build_prefix_relative_aliases"]
    for name, target in aliases.items():
        target_path = prefix / str(target)
        alias_path = prefix / str(name)
        if target_path.is_symlink() or not target_path.is_dir():
            raise BootstrapError(f"build prefix alias target is missing or unsafe: {target}")
        if alias_path.is_symlink():
            if os.readlink(alias_path) != str(target):
                raise BootstrapError(f"build prefix alias has an unexpected target: {name}")
            continue
        if alias_path.exists():
            raise BootstrapError(f"build prefix alias path is already occupied: {name}")
        alias_path.symlink_to(str(target), target_is_directory=True)
    if not verify_build_prefix_layout(prefix, lock):
        raise BootstrapError("private merged-/usr alias verification failed")
    return {str(name): str(target) for name, target in aliases.items()}


def extract_layer(cache_root: Path, prefix: Path, rows: list[dict[str, object]]) -> int:
    private_prefix(prefix)
    prefix.mkdir(mode=0o700, exist_ok=True)
    for row in rows:
        archive = cache_root / "packages" / str(row["filename"])
        subprocess.run([str(DPKG_DEB), "-x", str(archive), str(prefix)], check=True)
    return len(rows)


def plan(lock: dict[str, object]) -> dict[str, object]:
    diagnostic = lock["diagnostic_profile"]
    return {
        "lock_schema": lock["schema"],
        "platform": lock["platform"],
        "source_package": {
            "name": "mozc",
            "version": lock["source_packages"]["mozc"]["version"],
            "files": len(source_rows(lock)),
        },
        "package_counts": {
            "runtime_profile": len(selected_package_rows(lock, "wayland_profile")),
            "mozc_build": len(selected_package_rows(lock, "mozc_build")),
        },
        "components": lock["components"],
        "build_prefix_relative_aliases": lock["overrides"]["build_prefix_relative_aliases"],
        "diagnostic_compile_gate": diagnostic["gyp_define"],
        "writes_host_or_system_state": False,
        "launches_runtime": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="print locked package/source identity without writing")
    for name in ("fetch", "verify"):
        command = sub.add_parser(name)
        command.add_argument("--cache-root", type=Path, required=True)
    extract_cmd = sub.add_parser("extract", help="extract verified .debs into two fresh private prefixes")
    extract_cmd.add_argument("--cache-root", type=Path, required=True)
    extract_cmd.add_argument("--build-prefix", type=Path, required=True)
    extract_cmd.add_argument("--runtime-prefix", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        lock = load_lock()
        if args.command == "plan":
            result = plan(lock)
        elif args.command == "fetch":
            count = fetch(args.cache_root, lock)
            result = {"downloaded_or_verified_inputs": count, "verified": True}
        elif args.command == "verify":
            package_count, source_count = verify(args.cache_root, lock)
            result = {"verified_package_archives": package_count, "verified_source_archives": source_count}
        else:
            package_count, source_count = verify(args.cache_root, lock)
            private_prefix(args.build_prefix)
            private_prefix(args.runtime_prefix)
            if (args.build_prefix == args.runtime_prefix
                    or args.build_prefix in args.runtime_prefix.parents
                    or args.runtime_prefix in args.build_prefix.parents):
                raise BootstrapError("build and runtime prefixes must be separate and non-nesting")
            build_rows = selected_package_rows(lock, "mozc_build")
            runtime_rows = selected_package_rows(lock, "wayland_profile")
            # The source-built server replaces the stock package output; all other runtime packages stay official.
            build_count = extract_layer(args.cache_root, args.build_prefix, build_rows)
            runtime_count = extract_layer(args.cache_root, args.runtime_prefix, runtime_rows)
            aliases = prepare_build_prefix_layout(args.build_prefix, lock)
            result = {"verified_package_archives": package_count, "verified_source_archives": source_count,
                      "build_prefix_packages_extracted": build_count, "runtime_prefix_packages_extracted": runtime_count,
                      "build_prefix_relative_aliases": aliases,
                      "maintainer_scripts_run": False, "runtime_launched": False}
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError,
            urllib.error.URLError, BootstrapError) as error:
        print(f"bootstrap failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
