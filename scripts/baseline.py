#!/usr/bin/env python3
"""Pinned source acquisition and bounded Linux/macOS baseline build. Never pushes."""
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


def select_metal_toolchain():
    """Return an installed Metal toolchain only after its compiler runs."""
    identifier = None
    build_version = None
    try:
        component = json.loads(command(['xcodebuild', '-showComponent', 'MetalToolchain', '-json']))
        if component.get('status') == 'installed':
            identifier = component.get('toolchainIdentifier')
            build_version = component.get('buildVersion')
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
        pass

    env = dict(os.environ)
    if identifier:
        env['TOOLCHAINS'] = identifier
    try:
        version = command(['xcrun', 'metal', '--version'], env=env).splitlines()[0]
    except (subprocess.CalledProcessError, FileNotFoundError, IndexError):
        return {'identifier': None, 'build_version': build_version, 'version': None}
    return {'identifier': identifier, 'build_version': build_version, 'version': version}


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
        subprocess.run(['git', 'clone', '--depth=1', '--single-branch', '--branch', LOCK['release'],
                        LOCK['repository'], str(source)], check=True, timeout=600)
    verify_source(source)
    # Prevent accidental pushes without changing the tracked source baseline.
    command(['git', '-C', str(source), 'remote', 'set-url', '--push', 'origin', 'DISABLED_UPSTREAM_READ_ONLY'])


def inspect_environment():
    system = platform.system()
    build_storage = ROOT / '_build'
    if not build_storage.exists():
        build_storage = ROOT
    tools_to_check = ['git', 'rustc', 'cargo']
    if system == 'Linux':
        tools_to_check += ['clang', 'cmake', 'pkg-config']
    elif system == 'Darwin':
        tools_to_check += ['clang', 'cmake', 'xcodebuild', 'xcrun', 'xcode-select']
    tools = {}
    for tool in tools_to_check:
        executable = shutil.which(tool)
        tools[tool] = {'path': executable}
        if executable:
            try:
                version_args = ['-version'] if tool == 'xcodebuild' else ['--version']
                if tool in ('rustc', 'cargo'):
                    version_args.insert(0, '+' + LOCK['rust'])
                tools[tool]['version'] = command([executable, *version_args]).splitlines()[0]
            except (subprocess.CalledProcessError, OSError) as error:
                tools[tool]['error'] = str(error)
    packages = {}
    if system == 'Linux':
        for package in ['alsa', 'fontconfig', 'openssl', 'libva', 'wayland-client', 'xkbcommon', 'xkbcommon-x11', 'sqlite3']:
            try:
                packages[package] = command(['pkg-config', '--modversion', package])
            except (subprocess.CalledProcessError, FileNotFoundError):
                packages[package] = None
    elif system == 'Darwin':
        try:
            packages['xcode_developer_dir'] = command(['xcode-select', '-p'])
        except (subprocess.CalledProcessError, FileNotFoundError):
            packages['xcode_developer_dir'] = None
        try:
            packages['macos_sdk'] = command(['xcrun', '--show-sdk-path'])
        except (subprocess.CalledProcessError, FileNotFoundError):
            packages['macos_sdk'] = None
        metal = select_metal_toolchain()
        packages['metal_toolchain'] = metal['version']
        packages['metal_toolchain_identifier'] = metal['identifier']
        packages['metal_toolchain_build_version'] = metal['build_version']
    return {'system_name': system, 'system': platform.platform(), 'machine': platform.machine(),
            'tools': tools, 'packages': packages,
            'build_storage_path': str(build_storage.resolve()),
            'free_disk_bytes': shutil.disk_usage(build_storage).free,
            'meminfo': Path('/proc/meminfo').read_text() if Path('/proc/meminfo').exists() else None,
            'display': {key: os.environ.get(key) for key in ['DISPLAY', 'WAYLAND_DISPLAY', 'XDG_SESSION_TYPE']}}


def missing_prerequisites(environment):
    system = environment.get('system_name', 'Linux')
    if system not in ('Linux', 'Darwin'):
        return [f'unsupported host {system}']
    missing = [name for name, tool in environment['tools'].items() if not tool['path']]
    package_names = (list(environment['packages']) if system == 'Linux' else
                     ['xcode_developer_dir', 'macos_sdk', 'metal_toolchain'])
    missing += [name for name in package_names if environment['packages'].get(name) is None]
    return missing


def preserve_baseline_binary(source, record):
    target = source / 'target/mzed-baseline'
    recorded_target = Path(record.get('target_directory', ''))
    if recorded_target.resolve() != target.resolve():
        raise ValueError('baseline evidence target does not match the pinned checkout')
    original = target / 'debug/zed'
    if not original.is_file():
        raise ValueError('baseline executable is missing from its Cargo target')
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    if digest != record.get('binary_sha256'):
        raise ValueError('baseline executable changed before it could be preserved')
    preserved = source / 'target/mzed-baseline-preserved/debug/zed'
    destination_components = [source / 'target', source / 'target/mzed-baseline-preserved',
                              source / 'target/mzed-baseline-preserved/debug']
    if any(component.is_symlink() for component in destination_components) or preserved.is_symlink():
        raise ValueError('preserved baseline path contains a symlink; refusing to follow it')
    if preserved.exists():
        if not preserved.is_file() or hashlib.sha256(preserved.read_bytes()).hexdigest() != digest:
            raise ValueError('preserved baseline executable differs; evidence will not be overwritten')
    else:
        preserved.parent.mkdir(parents=True, exist_ok=True)
        if not preserved.parent.resolve().is_relative_to(source.resolve()):
            raise ValueError('preserved baseline path escaped the pinned checkout')
        shutil.copy2(original, preserved)
    if hashlib.sha256(preserved.read_bytes()).hexdigest() != digest:
        raise OSError('preserved baseline executable hash does not match the build output')
    record['target_binary'] = str(original)
    record['binary'] = str(preserved)
    return record


def preserve_existing_baseline_binary(source, output):
    verify_source(source)
    evidence_path = output / 'build.json'
    if not evidence_path.is_file():
        raise ValueError('baseline build evidence is missing')
    record = json.loads(evidence_path.read_text())
    if record.get('baseline_build') != 'passed':
        raise ValueError('only a passed baseline build can be preserved')
    preserve_baseline_binary(source, record)
    evidence_path.write_text(json.dumps(record, indent=2) + '\n')
    return record


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


def build(source, output, jobs, timeout, resume_target=False):
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
        environment['build_storage_path'] = str(source)
        environment['free_disk_bytes'] = shutil.disk_usage(source).free
        missing = missing_prerequisites(environment)
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
        target_exists = target.exists()
        if target_exists and not resume_target:
            record['baseline_build'] = 'blocked'
            raise ValueError('baseline target already exists; preserve it and use a fresh source checkout')
        if resume_target and (target.is_symlink() or not target.is_dir()
                              or not target.resolve().is_relative_to(source.resolve())):
            record['baseline_build'] = 'blocked'
            raise ValueError('--resume-target requires an existing target/mzed-baseline directory under the pinned checkout')
        record['target_directory'] = str(target)
        record['target_reused'] = target_exists
        env = dict(os.environ, CARGO_TARGET_DIR=str(target), RUSTUP_TOOLCHAIN=LOCK['rust'],
                   ZED_UPDATE_EXPLANATION='Pinned MZed baseline experiment; no automatic updates')
        if platform.system() == 'Darwin' and 'BINDGEN_EXTRA_CLANG_ARGS' not in env:
            sdk = command(['xcrun', '--show-sdk-path'])
            env['BINDGEN_EXTRA_CLANG_ARGS'] = '--sysroot=' + sdk
        if platform.system() == 'Darwin':
            metal_identifier = environment['packages'].get('metal_toolchain_identifier')
            if metal_identifier:
                env['TOOLCHAINS'] = metal_identifier
        with (output / 'cargo.log').open('w') as log:
            exit_code = run_bounded(argv, source, env, log, timeout, source)
        record['exit_code'] = exit_code
        record['baseline_build'] = 'passed' if exit_code == 0 else 'failed'
        if exit_code == 0:
            binary = target / 'debug/zed'
            record['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
            preserve_baseline_binary(source, record)
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
    parser.add_argument('operation', choices=['source', 'verify', 'inspect', 'build', 'preserve'])
    parser.add_argument('--source', type=Path, default=ROOT / '_build/zed')
    parser.add_argument('--output', type=Path, default=ROOT / '_build/baseline')
    parser.add_argument('--jobs', type=int, choices=range(1, 5), default=2)
    parser.add_argument('--timeout-seconds', type=int, default=5400)
    parser.add_argument('--resume-target', action='store_true',
                        help='resume the preserved target/mzed-baseline directory after an interrupted build')
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
    elif args.operation == 'preserve':
        print(json.dumps(preserve_existing_baseline_binary(source, args.output.resolve()), indent=2))
    else:
        return build(source, args.output.resolve(), args.jobs, args.timeout_seconds, args.resume_target)
    return 0


if __name__ == '__main__':
    sys.exit(main())
