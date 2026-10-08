#!/usr/bin/env python3
"""Bounded build of the intentional derived-source island, separate from baseline."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import time
import shutil
import subprocess
from baseline import LOCK, ROOT, run_bounded


def build_environment(native, target):
    names = ['PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR', 'TMP', 'TEMP',
             'CARGO_HOME', 'RUSTUP_HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
             'PKG_CONFIG_PATH', 'PKG_CONFIG_LIBDIR', 'PKG_CONFIG_SYSROOT_DIR',
             'LD_LIBRARY_PATH', 'LIBRARY_PATH', 'LIBCLANG_PATH', 'CC', 'CXX',
             'AR', 'CFLAGS', 'CXXFLAGS', 'LDFLAGS', 'HTTP_PROXY', 'HTTPS_PROXY',
             'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy',
             'no_proxy']
    env = {name: os.environ[name] for name in names if name in os.environ}
    env.update(CI='true', CARGO_TARGET_DIR=str(target), RUSTUP_TOOLCHAIN=LOCK['rust'],
               MZED_NATIVE_LIB_DIR=str(native),
               ZED_UPDATE_EXPLANATION='MZed bounded derived-source experiment')
    return env


def build(source, native, output, jobs=1):
    if not 1 <= jobs <= 4:
        raise ValueError('jobs must be in 1..4')
    output.mkdir(parents=True, exist_ok=False)
    target = source / 'target/mzed-island'
    if target.exists():
        raise ValueError('preserve existing target; choose a fresh source attempt')
    record = {'schema': 2, 'harness_commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(), 'kind': 'derived-same-window-island', 'upstream_commit': LOCK['commit'],
        'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'build': 'failed',
        'provenance': json.loads((source / 'mzed-derived.json').read_text()),
        'moonbit': json.loads((native / 'build.json').read_text()),
        'jobs': jobs}
    started = time.monotonic()
    argv = ['cargo', '+' + LOCK['rust'], 'build', '--locked', '-p', 'zed', '--bin', 'zed', '--jobs', str(jobs),
        '--config', 'profile.dev.debug=0', '--config', 'profile.dev.build-override.debug=0',
        '--config', 'profile.dev.incremental=false']
    env = build_environment(native, target)
    record['command'] = argv
    record['inherited_environment_names'] = sorted(env)
    # Values, including proxy addresses, are deliberately omitted from evidence.
    record['cache_reused'] = False
    try:
        if shutil.disk_usage(source).free < 20 * 1024**3:
            raise OSError('less than 20 GiB free; existing work is preserved')
        with (output / 'cargo.log').open('w') as log:
            result = run_bounded(argv, source, env, log, 5400, source)
        record['exit_code'] = result
        if result == 0:
            binary = target / 'debug/zed'
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
    parser.add_argument('--jobs', type=int, choices=range(1, 5), default=1)
    args = parser.parse_args()
    raise SystemExit(build(args.source.resolve(), args.native.resolve(), args.output.resolve(), args.jobs))
