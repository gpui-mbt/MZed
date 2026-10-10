#!/usr/bin/env python3
"""Pinned source acquisition and bounded Linux baseline build. Never pushes."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / 'upstream.lock.json').read_text())


def command(argv, **kwargs):
    return subprocess.run(argv, check=True, text=True, capture_output=True, **kwargs).stdout.strip()


def verify_source(source, lock=LOCK):
    actual = command(['git', '-C', str(source), 'rev-parse', 'HEAD'])
    if actual != lock['commit']:
        raise ValueError(f"source mismatch: expected {lock['commit']}, got {actual}")
    if command(['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=all']):
        raise ValueError('baseline source is dirty; preserve it and choose a fresh source directory')
    for name, expected in lock['files_sha256'].items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f'provenance mismatch: {name}')


def acquire(source):
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        # Pin the checkout policy before Git writes files. The source lock hashes
        # raw bytes (including lockfiles, license text and script/linux), so a
        # Windows user's global autocrlf setting must not rewrite those inputs.
        subprocess.run(['git', 'clone', '-c', 'core.autocrlf=false', '-c', 'core.eol=lf',
                        '--depth=1', '--single-branch', '--branch', LOCK['release'],
                        LOCK['repository'], str(source)], check=True, timeout=600)
    verify_source(source)
    # Prevent accidental pushes without changing the tracked source baseline.
    command(['git', '-C', str(source), 'remote', 'set-url', '--push', 'origin', 'DISABLED_UPSTREAM_READ_ONLY'])


def inspect_environment():
    tools = {}
    for tool in ['git', 'rustc', 'cargo', 'clang', 'cmake', 'pkg-config']:
        executable = shutil.which(tool)
        tools[tool] = {'path': executable}
        if executable:
            try:
                tools[tool]['version'] = command([executable, '--version']).splitlines()[0]
            except (subprocess.CalledProcessError, OSError) as error:
                tools[tool]['error'] = str(error)
    packages = {}
    for package in ['alsa', 'fontconfig', 'openssl', 'libva', 'wayland-client', 'xkbcommon', 'xkbcommon-x11', 'sqlite3']:
        try:
            packages[package] = command(['pkg-config', '--modversion', package])
        except (subprocess.CalledProcessError, FileNotFoundError):
            packages[package] = None
    return {'system': platform.platform(), 'machine': platform.machine(),
            'tools': tools, 'packages': packages,
            'free_disk_bytes': shutil.disk_usage(ROOT).free,
            'meminfo': Path('/proc/meminfo').read_text() if Path('/proc/meminfo').exists() else None,
            'display': {key: os.environ.get(key) for key in ['DISPLAY', 'WAYLAND_DISPLAY', 'XDG_SESSION_TYPE']}}


def run_bounded(argv, source, env, log, timeout, disk_path):
    process = subprocess.Popen(argv, cwd=source, env=env, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
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
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def build(source, output, jobs, timeout):
    if output.exists():
        raise ValueError('evidence directory already exists; use a new path to preserve previous evidence')
    output.mkdir(parents=True)
    started = time.monotonic()
    record = {'schema': 1, 'source_commit': LOCK['commit'], 'release': LOCK['release'],
              'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'environment': inspect_environment(), 'baseline_build': 'not_run',
              'editor_smoke': 'not_run', 'same_window_island': 'not_implemented'}
    argv = ['cargo', f"+{LOCK['rust']}", 'build', '--locked', '-p', 'zed', '--bin', 'zed',
            '--jobs', str(jobs), '--config', 'profile.dev.debug=0',
            '--config', 'profile.dev.build-override.debug=0', '--config', 'profile.dev.incremental=false']
    record['command'] = argv
    record['timeout_seconds'] = timeout
    try:
        verify_source(source)
        environment = record['environment']
        environment['free_disk_bytes'] = shutil.disk_usage(source).free
        missing = [name for name, tool in environment['tools'].items() if not tool['path']]
        missing += [name for name, version in environment['packages'].items() if version is None]
        if missing:
            record['baseline_build'] = 'blocked'
            raise ValueError('missing build prerequisites: ' + ', '.join(missing))
        if environment['free_disk_bytes'] < 20 * 1024**3:
            record['baseline_build'] = 'blocked'
            raise ValueError('less than 20 GiB free: choose a larger builder; no existing work is deleted')
        record['baseline_build'] = 'running'
        (output / 'build.json').write_text(json.dumps(record, indent=2) + '\n')
        # Dev assets resolve the first .git ancestor of the executable before cwd.
        # Keep the binary under the exact Zed checkout, not the harness checkout.
        target = source / 'target/mzed-baseline'
        if target.exists():
            record['baseline_build'] = 'blocked'
            raise ValueError('baseline target already exists; preserve it and use a fresh source checkout')
        record['target_directory'] = str(target)
        env = dict(os.environ, CARGO_TARGET_DIR=str(target), RUSTUP_TOOLCHAIN=LOCK['rust'],
                   ZED_UPDATE_EXPLANATION='Pinned MZed baseline experiment; no automatic updates')
        with (output / 'cargo.log').open('w') as log:
            exit_code = run_bounded(argv, source, env, log, timeout, source)
        record['exit_code'] = exit_code
        record['baseline_build'] = 'passed' if exit_code == 0 else 'failed'
        if exit_code == 0:
            binary = target / 'debug/zed'
            record['binary'] = str(binary)
            record['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
        verify_source(source)
        return 0 if exit_code == 0 else 1
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        record['error'] = str(error)
        if record['baseline_build'] not in ['blocked']:
            record['baseline_build'] = 'failed'
        return 2
    finally:
        record['elapsed_seconds'] = round(time.monotonic() - started, 3)
        (output / 'build.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps(record, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['source', 'verify', 'inspect', 'build'])
    parser.add_argument('--source', type=Path, default=ROOT / '_build/zed')
    parser.add_argument('--output', type=Path, default=ROOT / '_build/baseline')
    parser.add_argument('--jobs', type=int, choices=range(1, 5), default=2)
    parser.add_argument('--timeout-seconds', type=int, default=5400)
    args = parser.parse_args()
    if not 1 <= args.timeout_seconds <= 7200:
        parser.error('timeout must be 1..7200 seconds')
    source = args.source.resolve()
    if args.operation == 'source':
        acquire(source)
    elif args.operation == 'verify':
        verify_source(source)
    elif args.operation == 'inspect':
        print(json.dumps(inspect_environment(), indent=2))
    else:
        return build(source, args.output.resolve(), args.jobs, args.timeout_seconds)
    return 0


if __name__ == '__main__':
    sys.exit(main())
