#!/usr/bin/env python3
"""Isolated X11 file-open/edit/save smoke. A pass does not qualify an island."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, text=True, capture_output=True, timeout=15, **kwargs).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source', required=True, type=Path)
    args = parser.parse_args()
    binary, output = args.binary.resolve(), args.output.resolve()
    source = args.source.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixture = output / 'mzed-baseline-fixture.txt'
    original = 'MZed pinned baseline fixture\n'
    addition = 'MZed native edit save verified'
    record = {'schema': 1, 'editor_smoke': 'failed', 'same_window_island': 'not_implemented',
        'backend': 'X11',
        'rendering_class': 'software-requested-not-hardware-qualified', 'scale': 1,
        'display': os.environ.get('DISPLAY'), 'operations': []}
    process = None
    try:
        record['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
        fixture.write_text(original)
        config = output / 'data/config'
        config.mkdir(parents=True)
        (config / 'settings.json').write_text(json.dumps({'telemetry': {'diagnostics': False, 'metrics': False},
            'disable_ai': True, 'auto_update': False, 'auto_install_extensions': {'html': False}, 'ensure_final_newline_on_save': True, 'languages': {'Plain Text': {'enable_language_server': False}}}))
        home = output / 'home'
        home.mkdir()
        env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(output / 'config'), XDG_DATA_HOME=str(output / 'data'),
            XDG_CACHE_HOME=str(output / 'cache'), WAYLAND_DISPLAY='', ZED_UPDATE_EXPLANATION='Pinned baseline experiment')
        record['xrandr'] = run(['xrandr', '--current'])
        record['vulkaninfo'] = run(['vulkaninfo', '--summary'])
        with (output / 'editor.log').open('w') as log:
            process = subprocess.Popen([str(binary), '--user-data-dir', str(output / 'data'), str(fixture)],
                cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + 90
            window = None
            while time.monotonic() < deadline and process.poll() is None:
                result = subprocess.run(['xdotool', 'search', '--all', '--onlyvisible', '--pid', str(process.pid),
                    '--name', 'mzed-baseline-fixture'], text=True, capture_output=True, timeout=5)
                candidates = result.stdout.split()
                if result.returncode == 0 and len(candidates) == 1:
                    window = candidates[0]
                    break
                time.sleep(0.5)
            if window is None:
                record['editor_exit_code'] = process.poll()
                if record['editor_exit_code'] is not None:
                    raise RuntimeError(f"editor exited before its window appeared: {record['editor_exit_code']}; inspect editor.log")
                raise RuntimeError('no unique visible editor window for this process and fixture within 90 seconds')
            record['window_id'] = window
            record['operations'].append('opened fixture in process-owned native window')
            run(['xdotool', 'windowactivate', '--sync', window])
            focused = run(['xdotool', 'getwindowfocus'])
            record['focused_window_id'] = focused
            if focused != window:
                raise RuntimeError(f'focus mismatch: expected {window}, got {focused}')
            run(['import', '-window', window, str(output / 'before-input.png')])
            run(['xdotool', 'key', '--clearmodifiers', 'ctrl+End'])
            run(['xdotool', 'type', '--clearmodifiers', '--delay', '30', addition])
            run(['xdotool', 'key', '--clearmodifiers', 'ctrl+s'])
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and fixture.read_text() != original + addition + '\n':
                time.sleep(0.2)
            actual = fixture.read_text()
            record['fixture_content'] = actual
            if actual != original + addition + '\n':
                raise RuntimeError('saved file does not contain exactly the expected native edit')
            record['operations'].append('typed once and saved expected bytes through native keyboard input')
            run(['import', '-window', window, str(output / 'editor.png')])
            record['editor_smoke'] = 'passed'
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        record['error'] = str(error)
    finally:
        if record.get('window_id'):
            try:
                run(['import', '-window', record['window_id'], str(output / 'after-input.png')])
            except (OSError, subprocess.SubprocessError) as error:
                record['capture_error'] = str(error)
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        (output / 'smoke.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps(record, indent=2))
    return 0 if record['editor_smoke'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
