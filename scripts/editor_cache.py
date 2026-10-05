#!/usr/bin/env python3
"""Exact-input repository build cache. Never a release artifact or secret store."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time
from baseline import LOCK, ROOT
from build_island import build_environment

MAX_BYTES = 2 * 1024**3
BUILD_FILES = ['scripts/baseline.py', 'upstream.lock.json', 'patches/native-island.patch', 'scripts/build_native.py',
               'scripts/prepare_island.py', 'scripts/build_island.py', 'scripts/editor_cache.py']
COMPILE_ENV = ['RUSTFLAGS', 'CARGO_ENCODED_RUSTFLAGS', 'CC', 'CXX', 'AR', 'CFLAGS',
               'CXXFLAGS', 'LDFLAGS', 'CMAKE_GENERATOR', 'CARGO_BUILD_TARGET', 'RELEASE_CHANNEL',
               'ZED_COMMIT_SHA', 'ZED_RELEASE_CHANNEL', 'ZED_BUNDLE', 'ZTRACING', 'ZTRACING_WITH_MEMORY', 'MACOSX_DEPLOYMENT_TARGET', 'LIBCLANG_PATH', 'BINDGEN_EXTRA_CLANG_ARGS']


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def command(argv):
    return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT).strip()


def fingerprint(source, native):
    if platform.system() != 'Linux':
        raise ValueError('this repository cache is qualified only on the declared Linux CI environment')
    if command(['git', '-C', str(source), 'rev-parse', 'HEAD']) != LOCK['commit']:
        raise ValueError('wrong upstream source for cache')
    for name in ['LD_PRELOAD', 'LD_LIBRARY_PATH', 'RUSTC', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER']:
        if os.environ.get(name):
            raise ValueError('unsupported compiler/loader override for repository cache: ' + name)
    cargo_home = Path(os.environ.get('CARGO_HOME', str(Path.home() / '.cargo')))
    configs = [cargo_home / name for name in ['config', 'config.toml']]
    configs += [parent / '.cargo' / name for parent in source.parents for name in ['config', 'config.toml']]
    if any(path.exists() for path in configs):
        raise ValueError('external Cargo configuration requires separate cache qualification')
    files = BUILD_FILES + [str(path.relative_to(ROOT)) for path in sorted((ROOT / 'native').glob('*')) if path.is_file()]
    moon = Path(os.environ.get('MOON_HOME', str(Path.home() / '.moon')))
    untracked = command(['git', '-C', str(source), 'ls-files', '--others', '--exclude-standard']).splitlines()
    inputs = {
        'schema': 1,
        'source_commit': LOCK['commit'],
        'derived_diff': command(['git', '-C', str(source), 'diff', '--binary', 'HEAD']),
        'derived_extra': {path: digest(source / path) for path in untracked},
        'build_files': {path: digest(ROOT / path) for path in files},
        'native_archive': digest(native / 'libmzed_native.a'),
        'native_build': json.loads((native / 'build.json').read_text()),
        'moon_compiler': digest(moon / 'bin/moonc'),
        'moon_runtime': {str(path.relative_to(moon)): digest(path) for path in
            sorted((moon / 'lib/runtime').glob('*.c')) + [moon / 'include/moonbit.h',
            moon / 'lib/core/_build/native/release/bundle/core.core',
            moon / 'lib/core/_build/native/release/bundle/abort/abort.core']},
        'rust': command(['rustc', '+1.98.1', '-vV']),
        'cargo': command(['cargo', '+1.98.1', '-vV']),
        'cc': command(['cc', '--version']),
        'linker': command(['ld', '--version']),
        'cmake': command(['cmake', '--version']),
        'packages': command(['dpkg-query', '-W', '-f=${Package}=${Version}\n']),
        'platform': [platform.system(), platform.machine(), os.environ.get('ImageOS'), os.environ.get('ImageVersion')],
        'environment': {name: os.environ.get(name) for name in sorted(set(COMPILE_ENV) |
            {name for name in os.environ if name.startswith(('CARGO_PROFILE_', 'CARGO_TARGET_', 'CARGO_BUILD_', 'CARGO_UNSTABLE_'))})},
        'actual_build_environment': build_environment(native, source / 'target/mzed-island'),
        'source_path': str(source),
        'build_profile': 'dev/debug=0/build-override.debug=0/incremental=false/jobs=2/GITHUB_RUN_NUMBER=unset',
    }
    # The compiler output location is fixed by this lane, but does not identify its contents.
    inputs['native_build'].pop('native_library', None)
    key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    return {'key': 'mzed-editor-v1-' + key, 'inputs': inputs}


def system_library(path):
    resolved = Path(path).resolve(strict=True)
    if not any(resolved.is_relative_to(root) for root in [Path('/usr/lib'), Path('/lib'), Path('/lib64')]):
        raise ValueError(f'non-system runtime dependency requires separate cache qualification: {path}')
    return resolved


def save(source, build_output, cache, expected):
    record = json.loads((build_output / 'build.json').read_text())
    binary = source / 'target/mzed-island/debug/zed'
    if record.get('build') != 'passed' or digest(binary) != record.get('binary_sha256'):
        raise ValueError('only a verified successful editor build can enter the cache')
    size = binary.stat().st_size
    if not 0 < size <= MAX_BYTES:
        raise ValueError('editor exceeds the declared 2 GiB cache payload bound')
    dependencies = {}
    linkage = command(['ldd', str(binary)])
    if 'not found' in linkage:
        raise ValueError('unresolved runtime dependency')
    for name in re.findall(r'(/[^\s()]+)', linkage):
        path = system_library(name)
        dependencies[str(path)] = digest(path)
    if shutil.disk_usage(cache.parent).free < 3 * size + 1024**3:
        raise ValueError('insufficient disk for bounded cache round-trip; preserving the build')
    cache.mkdir(parents=True, exist_ok=False)
    shutil.copy2(binary, cache / 'zed')
    for name in ['LICENSE-GPL', 'LICENSE-APACHE']:
        shutil.copy2(source / name, cache / name)
    manifest = {'schema': 1, 'key': expected['key'], 'binary_sha256': digest(binary),
                'binary_bytes': size, 'system_libraries': dependencies, 'build_record': record,
                'source_url': 'https://github.com/gpui-mbt/MZed/tree/' + record['harness_commit'],
                'upstream_url': 'https://github.com/zed-industries/zed/tree/' + LOCK['commit']}
    (cache / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


def verify(cache, expected):
    if (cache / 'manifest.json').is_symlink():
        raise ValueError('invalid cached manifest')
    record = json.loads((cache / 'manifest.json').read_text())
    binary = cache / 'zed'
    if record.get('schema') != 1 or record.get('key') != expected['key']:
        raise ValueError('cache fingerprint mismatch; refusing stale executable')
    if binary.is_symlink() or not binary.is_file() or not 0 < binary.stat().st_size <= MAX_BYTES:
        raise ValueError('invalid cached executable')
    if not binary.stat().st_mode & 0o111 or binary.stat().st_mode & 0o7000:
        raise ValueError('invalid cached executable permissions')
    if record.get('build_record', {}).get('build') != 'passed' or record['build_record'].get('binary_sha256') != record.get('binary_sha256'):
        raise ValueError('cached build record does not establish a successful matching binary')
    if binary.stat().st_size != record.get('binary_bytes') or digest(binary) != record.get('binary_sha256'):
        raise ValueError('cached executable hash/size mismatch')
    if record['build_record'].get('upstream_commit') != expected['inputs']['source_commit']:
        raise ValueError('cached build source provenance mismatch')
    for name, checksum in record['system_libraries'].items():
        if digest(system_library(name)) != checksum:
            raise ValueError('cached runtime library mismatch')
    return record


def restore(source, output, cache, expected):
    started = time.monotonic()
    record = verify(cache, expected)
    target = source / 'target/mzed-island'
    if target.exists() or output.exists():
        raise ValueError('preserve existing target/evidence; restore requires a fresh destination')
    binary = target / 'debug/zed'
    binary.parent.mkdir(parents=True)
    shutil.copy2(cache / 'zed', binary)
    output.mkdir(parents=True)
    build = dict(record['build_record'], cache_reused=True, cache_key=expected['key'],
        binary_origin_harness_commit=record['build_record']['harness_commit'],
        harness_commit=command(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']), binary=str(binary),
        original_build_elapsed_seconds=record['build_record'].get('elapsed_seconds'),
        elapsed_seconds=round(time.monotonic() - started, 3))
    (output / 'build.json').write_text(json.dumps(build, indent=2) + '\n')
    (output / 'cargo.log').write_text('Verified repository build cache; no Cargo build was invoked.\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['fingerprint', 'save', 'verify', 'restore'])
    parser.add_argument('--source', type=Path, default=ROOT / '_build/zed-island')
    parser.add_argument('--native', type=Path, default=ROOT / '_build/native-normal')
    parser.add_argument('--output', type=Path, default=ROOT / '_build/island-build')
    parser.add_argument('--cache', type=Path, default=ROOT / '_build/editor-cache')
    parser.add_argument('--fingerprint', type=Path, default=ROOT / '_build/editor-fingerprint.json')
    args = parser.parse_args()
    if args.operation == 'fingerprint':
        result = fingerprint(args.source.resolve(), args.native.resolve())
        args.fingerprint.write_text(json.dumps(result, indent=2) + '\n')
        print(result['key'])
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                output.write('key=' + result['key'] + '\n')
    else:
        expected = json.loads(args.fingerprint.read_text())
        if args.operation == 'save':
            save(args.source.resolve(), args.output.resolve(), args.cache.resolve(), expected)
        elif args.operation == 'restore':
            restore(args.source.resolve(), args.output.resolve(), args.cache.resolve(), expected)
        else:
            verify(args.cache.resolve(), expected)
