#!/usr/bin/env python3
"""Patch a separate pinned Zed checkout; never touch the baseline checkout."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from baseline import ROOT, verify_source
from check_palette_shortcut import ensure_no_default_linux_collision


def prepare(source):
    verify_source(source)
    patch = ROOT / 'patches/native-island.patch'
    palette_shortcut = ensure_no_default_linux_collision(source, patch.read_text())
    subprocess.run(['git', '-C', str(source), 'apply', '--check', str(patch)], check=True)
    subprocess.run(['git', '-C', str(source), 'apply', str(patch)], check=True)
    destination = source / 'crates/zed/src/zed/native_island'
    destination.mkdir(exist_ok=False)
    zed_source = source / 'crates/zed/src/zed'
    shutil.copyfile(ROOT / 'native/island_view.rs', zed_source / 'native_island.rs')
    shutil.copyfile(ROOT / 'native/protocol.rs', destination / 'protocol.rs')
    shutil.copyfile(ROOT / 'native/palette_protocol.rs', zed_source / 'palette_protocol.rs')
    shutil.copyfile(ROOT / 'native/palette_view.rs', zed_source / 'palette_view.rs')
    fence = source / 'crates/gpui_linux/src/linux/wayland/ime_fence.rs'
    provenance = {'upstream_commit': json.loads((ROOT / 'upstream.lock.json').read_text())['commit'],
        'patch_sha256': hashlib.sha256(patch.read_bytes()).hexdigest(),
        'palette_shortcut': palette_shortcut,
        'license': 'GPL-3.0-or-later; retain upstream LICENSE-GPL and Apache component notices',
        'derived_files': {
            'crates/gpui_linux/src/linux/wayland/ime_fence.rs': hashlib.sha256(fence.read_bytes()).hexdigest(),
            'Cargo.lock': hashlib.sha256((source / 'Cargo.lock').read_bytes()).hexdigest(),
        },
        'files': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                  for name in [
                      'native/island_view.rs', 'native/protocol.rs', 'native/island.mbt',
                      'native/palette_protocol.rs', 'native/palette_view.rs',
                  ]}}
    (source / 'mzed-derived.json').write_text(json.dumps(provenance, indent=2) + '\n')
    return provenance


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source.resolve()), indent=2))
