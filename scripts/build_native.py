#!/usr/bin/env python3
"""Build the one-process copied-scene ABI from pinned portable gpui.mbt sources."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
GPUI_COMMIT = 'bf965aebbeb1dfdfed26373d4a7a58bb51a5ad01'
COMPILER = '0.10.14+7d59c7ec9'


def run(argv):
    print(' '.join(map(str, argv)), flush=True)
    subprocess.run(list(map(str, argv)), check=True)


def build(source, output, mode):
    actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != GPUI_COMMIT:
        raise ValueError('gpui.mbt source does not match reviewed pin')
    if subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True).strip():
        raise ValueError('preserve modified gpui.mbt; use a fresh checkout')
    if not (ROOT / "native/island.mbt").is_file():
        raise ValueError("native/island.mbt is required")
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
    packages = [('primitives', []), ('layout', ['primitives']), ('scene', ['primitives']),
                ('element', ['primitives', 'layout', 'scene'])]
    for package, dependencies in packages:
        files = sorted(p for p in (source / package).glob('*.mbt') if not p.name.endswith('_test.mbt'))
        argv = [compiler, 'build-package', *files, '-pkg', 'f4ah6o/gpui/' + package,
                '-pkg-type', 'library', '-target', 'native', '-std-path', core,
                '-i', str(core / 'prelude/prelude.mi') + ':prelude', '-o', output / (package + '.core')]
        for dependency in dependencies:
            argv += ['-i', str(output / (dependency + '.mi')) + ':' + dependency]
        run(argv)
    argv = [compiler, 'build-package', ROOT / 'native/island.mbt', '-pkg', 'mzed/native_island',
            '-pkg-type', 'foreign_library', '-target', 'native', '-std-path', core,
            '-i', str(core / 'prelude/prelude.mi') + ':prelude', '-o', output / 'island.core']
    for package, _ in packages:
        argv += ['-i', str(output / (package + '.mi')) + ':' + package]
    run(argv)
    run([compiler, 'link-core', core / 'abort/abort.core', core / 'core.core',
         *[output / (package + '.core') for package, _ in packages], output / 'island.core',
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
    (output / 'build.json').write_text(json.dumps({'schema': 1, 'gpui_commit': actual,
        'compiler': version, 'core_version': core_version, 'mode': mode, 'native_library': str(output / 'libmzed_native.a')}, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--mode', choices=['normal', 'ubsan', 'asan_ubsan'], default='normal')
    args = parser.parse_args()
    build(args.source.resolve(), args.output.resolve(), args.mode)
