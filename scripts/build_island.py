#!/usr/bin/env python3
"""Bounded build of the intentional derived-source island, separate from baseline."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import time
import shutil
import subprocess
from baseline import LOCK, ROOT, run_bounded, select_metal_toolchain
from build_native import load_msvc_environment


def editor_binary_path(target, windows=None):
    is_windows = os.name == 'nt' if windows is None else windows
    return target / 'debug' / ('zed.exe' if is_windows else 'zed')


def build_environment(native, target, metal_toolchain=None):
    if os.name == 'nt':
        env = load_msvc_environment()
        env.update(CI='true', CARGO_TARGET_DIR=str(target), RUSTUP_TOOLCHAIN=LOCK['rust'],
                   MZED_NATIVE_LIB_DIR=str(native),
                   ZED_UPDATE_EXPLANATION='MZed bounded derived-source experiment')
        return env
    names = ['PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR', 'TMP', 'TEMP',
             'CARGO_HOME', 'RUSTUP_HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'BINDGEN_EXTRA_CLANG_ARGS',
             'TOOLCHAINS']
    env = {name: os.environ[name] for name in names if name in os.environ}
    env.update(CI='true', CARGO_TARGET_DIR=str(target), RUSTUP_TOOLCHAIN=LOCK['rust'],
               MZED_NATIVE_LIB_DIR=str(native),
               ZED_UPDATE_EXPLANATION='MZed bounded derived-source experiment')
    if platform.system() == 'Darwin' and 'BINDGEN_EXTRA_CLANG_ARGS' not in env:
        sdk = subprocess.check_output(['xcrun', '--show-sdk-path'], text=True).strip()
        env['BINDGEN_EXTRA_CLANG_ARGS'] = '--sysroot=' + sdk
    if platform.system() == 'Darwin':
        metal_toolchain = metal_toolchain or select_metal_toolchain()
        if not metal_toolchain['version']:
            raise RuntimeError('Metal compiler did not run; install/select the Xcode Metal Toolchain component')
        if metal_toolchain['identifier']:
            env['TOOLCHAINS'] = metal_toolchain['identifier']
    return env


def run_build(argv, source, env, log, timeout, disk_path):
    if os.name != 'nt':
        return run_bounded(argv, source, env, log, timeout, disk_path)
    process = subprocess.Popen(argv, cwd=source, env=env, stdout=log,
                               stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if time.monotonic() >= deadline:
                raise TimeoutError(f'build exceeded {timeout} seconds')
            if shutil.disk_usage(disk_path).free < 2 * 1024**3:
                raise OSError('build stopped with less than 2 GiB free; artifacts preserved')
            time.sleep(1)
        return process.returncode
    finally:
        if process.poll() is None:
            subprocess.run(['taskkill.exe', '/T', '/F', '/PID', str(process.pid)],
                           capture_output=True, check=False)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def build(source, native, output, reuse_target=None):
    output.mkdir(parents=True, exist_ok=False)
    default_target = source / 'target/mzed-island'
    target = default_target if reuse_target is None else reuse_target.resolve()
    reused = reuse_target is not None
    if not target.resolve().is_relative_to(source.resolve()):
        raise ValueError('Cargo target must remain under the pinned Zed checkout')
    if reused:
        if not editor_binary_path(target).is_file():
            raise ValueError('--reuse-target must name a prebuilt target containing the platform editor binary')
    elif target.exists():
        raise ValueError('preserve existing target; choose a fresh source attempt')
    record = {'schema': 1, 'harness_commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(), 'kind': 'derived-same-window-island', 'upstream_commit': LOCK['commit'],
        'host_system': platform.system(), 'host_machine': platform.machine(),
        'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'build': 'failed',
        'target_directory': str(target), 'target_reused': reused,
        'provenance': json.loads((source / 'mzed-derived.json').read_text()),
        'moonbit': json.loads((native / 'build.json').read_text())}
    metal_toolchain = select_metal_toolchain() if platform.system() == 'Darwin' else None
    if metal_toolchain:
        record['metal_toolchain'] = metal_toolchain
    started = time.monotonic()
    argv = ['cargo', '+' + LOCK['rust'], 'build', '--locked', '-p', 'zed', '--bin', 'zed', '--jobs', '2',
        '--config', 'profile.dev.debug=0', '--config', 'profile.dev.build-override.debug=0',
        '--config', 'profile.dev.incremental=false']
    try:
        env = build_environment(native, target, metal_toolchain)
        # Per-run CI labels and undeclared compiler overrides are deliberately absent.
        record['cache_reused'] = False
        if shutil.disk_usage(source).free < 20 * 1024**3:
            raise OSError('less than 20 GiB free; existing work is preserved')
        with (output / 'cargo.log').open('w') as log:
            result = run_build(argv, source, env, log, 5400, source)
        record['exit_code'] = result
        if result == 0:
            binary = editor_binary_path(target)
            record.update(build='passed', binary=str(binary), binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
        return result
    finally:
        record['elapsed_seconds'] = round(time.monotonic() - started, 3)
        (output / 'build.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps(record, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['source', 'native', 'output']:
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--reuse-target', type=Path,
                        help='reuse a prebuilt Cargo target under source/target (for example the pinned baseline target)')
    args = parser.parse_args()
    reuse_target = args.reuse_target.resolve() if args.reuse_target else None
    raise SystemExit(build(args.source.resolve(), args.native.resolve(), args.output.resolve(), reuse_target))
