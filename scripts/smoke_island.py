#!/usr/bin/env python3
"""Same-window native island interactions followed by original editor input/save."""
from island_interactions import (
    FALLBACK_ADDITION,
    POST_FAILURE_ADDITION,
    exercise,
    exercise_dispatch_rejection,
    wait_for_fixture_bytes,
)
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import re
import subprocess
import time

SCENARIOS = ('normal', 'dispatch-rejection')
PROBE_SETTING = 'reject-first-increment'


def scenario_environment(inherited, scenario):
    if scenario not in SCENARIOS:
        raise ValueError(f'unsupported smoke scenario: {scenario}')
    env = dict(inherited)
    env.pop('MZED_NATIVE_ISLAND_PROBE', None)
    env.update(MZED_NATIVE_ISLAND='1', ZED_ALLOW_EMULATED_GPU='1',
        ZED_UPDATE_EXPLANATION='Pinned baseline experiment')
    if scenario == 'dispatch-rejection':
        env['MZED_NATIVE_ISLAND_PROBE'] = PROBE_SETTING
    return env


def validate_native_log(scenario, native_log):
    expected = {
        'normal': [
            'MZed island mounted: gpui.mbt bf965ae, copied scene v1',
            'MZed island counter=1',
            'MZed island counter=2',
            'MZed island disabled and native instance destroyed',
            'MZed island remounted',
            'MZed island counter=1',
            'MZed island disabled and native instance destroyed',
        ],
        'dispatch-rejection': [
            'MZed island mounted: gpui.mbt bf965ae, copied scene v1',
            'MZed island dispatch rejection probe',
            'MZed island dispatch failed: -8; disabling',
            'MZed island counter=1',
            'MZed island remounted',
            'MZed island disabled and native instance destroyed',
        ],
    }
    if scenario not in expected:
        raise ValueError(f'unsupported smoke scenario: {scenario}')
    events = re.findall(r'MZed (?:island|native) [^\n]*', native_log)
    if events != expected[scenario]:
        raise RuntimeError(
            f'{scenario} native log did not match the exact event sequence: {events!r}')
    return {
        'native_events': events,
        'native_dispatches': [int(event.rsplit('=', 1)[1]) for event in events
            if event.startswith('MZed island counter=')],
        'native_diagnostics': [event for event in events
            if ' failed:' in event or ' rejected:' in event],
    }


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, text=True, capture_output=True, timeout=15, **kwargs).stdout.strip()


def wait_for_text(window, screenshot, predicate, description):
    deadline = time.monotonic() + 30
    last_text = ''
    while time.monotonic() < deadline:
        run(['import', '-window', window, str(screenshot)])
        last_text = run(['tesseract', str(screenshot), 'stdout', '--psm', '11'])
        normalized = ' '.join(last_text.split()).casefold()
        if predicate(normalized):
            return last_text
        time.sleep(0.2)
    raise RuntimeError(f'{description} not observed within 30 seconds; last OCR: {last_text!r}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--scenario', choices=SCENARIOS, default='normal')
    args = parser.parse_args()
    binary, output = args.binary.resolve(), args.output.resolve()
    source = args.source.resolve()
    scenario = getattr(args, 'scenario', 'normal')
    env = scenario_environment(os.environ, scenario)
    output.mkdir(parents=True, exist_ok=False)
    fixture = output / 'mzed-baseline-fixture.txt'
    original = 'MZed pinned baseline fixture\n'
    record = {'schema': 1, 'scenario': scenario,
        'probe_setting': env.get('MZED_NATIVE_ISLAND_PROBE'),
        'editor_smoke': 'failed', 'same_window_island': 'failed',
        'backend': 'X11',
        'rendering_class': 'software-requested-not-hardware-qualified', 'scale': int(os.environ.get('GPUI_X11_SCALE_FACTOR', '1')),
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
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(output / 'config'), XDG_DATA_HOME=str(output / 'data'),
            XDG_CACHE_HOME=str(output / 'cache'), WAYLAND_DISPLAY='')
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
            record['startup_modal_text'] = wait_for_text(
                window, output / 'startup-modal.png',
                lambda text: 'unrecognized project' in text and 'unsupported gpu' not in text,
                'fresh-profile restricted-mode prompt')
            # Exact pinned keymap/action: dismisses the modal with trusted = Some(false).
            run(['xdotool', 'key', '--clearmodifiers', 'ctrl+alt+shift+s'])
            record['ready_editor_text'] = wait_for_text(
                window, output / 'before-input.png',
                lambda text: 'mzed pinned baseline fixture' in text
                and 'unrecognized project' not in text and 'unsupported gpu' not in text,
                'visible fixture after staying in Restricted Mode')
            record['operations'].append('dismissed project prompt into Restricted Mode without trusting worktree')
            if scenario == 'normal':
                exercise(window, output, process, record)
                run(['xdotool', 'key', '--clearmodifiers', 'ctrl+End'])
                run(['xdotool', 'type', '--clearmodifiers', '--delay', '30', FALLBACK_ADDITION])
                run(['xdotool', 'key', '--clearmodifiers', 'ctrl+s'])
                expected = (original + FALLBACK_ADDITION + '\n').encode()
                wait_for_fixture_bytes(fixture, expected, 'normal editor save')
                record['fixture_content'] = fixture.read_bytes().decode()
                record['operations'].append('typed once and saved expected bytes through native keyboard input')
            else:
                exercise_dispatch_rejection(window, output, process, record)
                run(['xdotool', 'key', '--clearmodifiers', 'ctrl+End'])
                run(['xdotool', 'type', '--clearmodifiers', '--delay', '30', POST_FAILURE_ADDITION])
                run(['xdotool', 'key', '--clearmodifiers', 'ctrl+s'])
                intermediate = (original + FALLBACK_ADDITION + '\n').encode()
                expected = intermediate + (POST_FAILURE_ADDITION + '\n').encode()
                wait_for_fixture_bytes(fixture, expected, 'post-remount editor save')
                actual = fixture.read_bytes()
                record['post_remount_save'] = {
                    'expected_utf8': expected.decode(),
                    'expected_hex': expected.hex(),
                    'actual_utf8': actual.decode(),
                    'actual_hex': actual.hex(),
                }
                record['fixture_content'] = actual.decode()
                record['operations'].append('appended and saved the post-failure remount line through native editor keys')
            run(['import', '-window', window, str(output / 'editor.png')])
            native_log = (output / 'data/logs/Zed.log').read_text()
            record.update(validate_native_log(scenario, native_log))
            record['editor_smoke'] = 'passed'
            record['same_window_island'] = 'passed'
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        record['editor_smoke'] = 'failed'
        record['same_window_island'] = 'failed'
        record['error'] = str(error)
        record['editor_exit_code_before_teardown'] = process.poll() if process is not None else None
        if isinstance(error, subprocess.CalledProcessError):
            record['failed_command'] = error.cmd
            record['command_return_code'] = error.returncode
            record['command_stdout'] = str(error.stdout or '')[-2000:]
        if getattr(error, 'stderr', None):
            record['command_stderr'] = str(error.stderr)[-4000:]
        if record.get('window_id'):
            try:
                record['window_state_on_failure'] = run(['xwininfo', '-id', record['window_id']])
            except (OSError, subprocess.SubprocessError) as state_error:
                record['window_state_error'] = str(state_error)
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
    return 0 if record['editor_smoke'] == 'passed' and record['same_window_island'] == 'passed' and 'error' not in record else 1


if __name__ == '__main__':
    raise SystemExit(main())
