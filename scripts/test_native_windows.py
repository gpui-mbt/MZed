#!/usr/bin/env python3
"""Link and run the copied-scene ABI against an MSVC-built archive on Windows."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from build_native import _environment_value, load_msvc_environment
from baseline import LOCK

ROOT = Path(__file__).resolve().parents[1]


def test(native, output):
    if os.name != 'nt':
        raise RuntimeError('the Windows ABI test must run on Windows')
    native = native.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if not (native / 'mzed_native.lib').is_file():
        raise FileNotFoundError('MSVC archive is missing: ' + str(native / 'mzed_native.lib'))
    environment = load_msvc_environment()
    path = _environment_value(environment, 'PATH')
    rustup = shutil.which('rustup.exe', path=path) or shutil.which('rustup')
    rustc = shutil.which('rustc.exe', path=path) or shutil.which('rustc')
    if rustup is None or rustc is None:
        raise FileNotFoundError('rustup and rustc must be installed')
    target = 'x86_64-pc-windows-msvc'
    subprocess.run([rustup, 'target', 'list', '--installed', '--toolchain', LOCK['rust']],
                   check=True, env=environment, capture_output=True, text=True)
    installed = subprocess.check_output([rustup, 'target', 'list', '--installed', '--toolchain', LOCK['rust']],
                                        env=environment, text=True)
    if target not in installed.splitlines():
        raise RuntimeError(f'{target} is not installed for Rust {LOCK["rust"]}')
    executable = output / 'protocol-tests.exe'
    argv = [rustc, '+' + LOCK['rust'], '--edition', '2024', '-D', 'warnings', '--test',
            str(ROOT / 'native/protocol.rs'), '--target', target,
            '-L', 'native=' + str(native), '-l', 'static=mzed_native', '-o', str(executable)]
    with (output / 'rustc.log').open('w') as log:
        subprocess.run(argv, check=True, env=environment, stdout=log, stderr=subprocess.STDOUT, text=True)
    with (output / 'runtime.log').open('w') as log:
        subprocess.run([str(executable), '--nocapture'], check=True, env=environment,
                       stdout=log, stderr=subprocess.STDOUT, text=True)
    record = {'schema': 1, 'target': target, 'rust': LOCK['rust'], 'native_library': str(native / 'mzed_native.lib'),
              'harness': 'native/protocol.rs', 'runtime': 'passed', 'executable': str(executable)}
    (output / 'test.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    test(args.native, args.output)
