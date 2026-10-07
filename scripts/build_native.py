#!/usr/bin/env python3
"""Build the one-process copied-scene ABI from pinned portable gpui.mbt sources."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
GPUI_COMMIT = '72d89475894e14f3fea9a3407e12a640da356ed9'
GPUI_TREE = 'f91c255f5bd18e2d7ca84df4e73d96f240837995'
COMPILER = '0.10.14+7d59c7ec9'


def run(argv):
    print(' '.join(map(str, argv)), flush=True)
    subprocess.run(list(map(str, argv)), check=True)


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(source, output, mode):
    actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != GPUI_COMMIT:
        raise ValueError('gpui.mbt source does not match reviewed pin')
    actual_tree = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
    if actual_tree != GPUI_TREE:
        raise ValueError('gpui.mbt source tree does not match qualified tree')
    if subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True).strip():
        raise ValueError('preserve modified gpui.mbt; use a fresh checkout')
    if not (ROOT / "native/island.mbt").is_file():
        raise ValueError("native/island.mbt is required")
    mzed_source_paths = [
        ROOT / "native/island.mbt",
        ROOT / "native/palette.mbt",
        ROOT / "native/bootstrap.c",
        ROOT / "native/mzed_palette_abi.h",
        ROOT / "scripts/build_native.py",
    ]
    mzed_commit = subprocess.check_output(
        ['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True
    ).strip()
    mzed_dirty = bool(
        subprocess.check_output(
            ['git', '-C', str(ROOT), 'status', '--porcelain'], text=True
        ).strip()
    )
    mzed_source_sha256 = {
        str(path.relative_to(ROOT)): file_sha256(path)
        for path in mzed_source_paths
    }
    output.mkdir(parents=True, exist_ok=False)
    home = Path(os.environ.get('MOON_HOME', str(Path.home() / '.moon')))
    compiler = home / 'bin/moonc'
    version = subprocess.check_output([str(compiler), '-v'], text=True, stderr=subprocess.STDOUT).strip()
    if COMPILER not in version:
        raise ValueError('MoonBit compiler does not match pinned version')
    core_version = tomllib.loads((home / 'lib/core/moon.mod').read_text())['version']
    if core_version != COMPILER:
        raise ValueError('MoonBit core does not match pinned version')
    core = home / 'lib/core/_build/native/release/bundle'
    core_utf8 = core / 'encoding/utf8'
    packages = [
        ('primitives', 'primitives', []),
        ('layout', 'layout', ['primitives']),
        ('scene', 'scene', ['primitives']),
        ('text', 'text', []),
        ('text_layout', 'text_layout', ['primitives', 'text']),
        ('element', 'element', ['primitives', 'layout', 'scene']),
        ('diagnostics', 'diagnostics', []),
        ('core', 'core', ['diagnostics']),
        ('capability', 'capability', ['core', 'diagnostics']),
        ('controls/text_field', 'text_field',
         ['primitives', 'text', 'text_layout', 'scene', 'element']),
        ('controls/command_palette', 'command_palette',
         ['primitives', 'text', 'text_layout', 'text_field', 'element',
          'capability', 'diagnostics', 'scene']),
    ]
    artifacts = {
        alias: artifact
        for package, artifact, _ in packages
        for alias in (package, artifact)
    }
    strict_import_packages = {'text', 'controls/text_field', 'controls/command_palette'}
    for package, artifact, dependencies in packages:
        files = sorted(
            p for p in (source / package).glob('*.mbt')
            if not p.name.endswith(('_test.mbt', '_wbtest.mbt'))
        )
        argv = [compiler, 'build-package', *files, '-pkg', 'f4ah6o/gpui/' + package,
                '-pkg-type', 'library', '-target', 'native', '-std-path', core,
                '-i', str(core / 'prelude/prelude.mi') + ':prelude', '-o', output / (artifact + '.core')]
        for dependency in dependencies:
            argv += ['-i', str(output / (artifacts[dependency] + '.mi')) + ':' + dependency]
        if package in strict_import_packages:
            # These package manifests declare moonbitlang/core/int. Keep the
            # manual build hermetic and reject implicit package resolution.
            argv += ['-i', str(core / 'int/int.mi') + ':int', '-w', '@a']
        run(argv)
    argv = [compiler, 'build-package', ROOT / 'native/island.mbt', ROOT / 'native/palette.mbt',
            '-pkg', 'mzed/native_island',
            '-pkg-type', 'foreign_library', '-target', 'native', '-std-path', core,
            '-i', str(core / 'prelude/prelude.mi') + ':prelude',
            '-i', str(core_utf8 / 'utf8.mi') + ':utf8', '-w', '@a',
            '-o', output / 'island.core']
    for package, artifact, _ in packages:
        argv += ['-i', str(output / (artifact + '.mi')) + ':' + package.split('/')[-1]]
    run(argv)
    run([compiler, 'link-core', core / 'abort/abort.core', core / 'core.core',
         *[output / (artifact + '.core') for _, artifact, _ in packages], output / 'island.core',
         '-main', 'mzed/native_island', '-target', 'native', '-o', output / 'island.c'])
    flags = ['-std=gnu11', '-O2', '-g', '-fwrapv', '-fno-strict-aliasing',
             '-ffunction-sections', '-fdata-sections', '-I' + str(home / 'include')]
    if mode != 'normal':
        flags += ['-fsanitize=' + ('undefined' if mode == 'ubsan' else 'address,undefined'),
                  '-fno-sanitize-recover=all']
    units = [output / 'island.c', ROOT / 'native/bootstrap.c']
    units += [home / 'lib/runtime' / (name + '.c') for name in ['runtime', 'env', 'backtrace', 'utf']]
    objects = []
    for unit in units:
        obj = output / (unit.stem + '.o')
        run(['cc', *flags, '-c', unit, '-o', obj])
        objects.append(obj)
    run(['ar', 'crs', output / 'libmzed_native.a', *objects])
    (output / 'build.json').write_text(json.dumps({
        'schema': 2,
        'gpui_commit': actual,
        'gpui_tree': actual_tree,
        'mzed_commit': mzed_commit,
        'mzed_dirty': mzed_dirty,
        'mzed_source_sha256': mzed_source_sha256,
        'compiler': version,
        'core_version': core_version,
        'mode': mode,
        'native_library': str(output / 'libmzed_native.a'),
    }, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--mode', choices=['normal', 'ubsan', 'asan_ubsan'], default='normal')
    args = parser.parse_args()
    build(args.source.resolve(), args.output.resolve(), args.mode)
