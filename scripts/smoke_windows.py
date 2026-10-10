#!/usr/bin/env python3
"""Run a bounded native Windows file-open/edit/save smoke for a derived Zed build."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
VK_CONTROL, VK_MENU, VK_SHIFT = 0x11, 0x12, 0x10
VK_END, VK_S = 0x23, 0x53
KEYEVENTF_KEYUP = 0x0002
GW_OWNER = 4
SW_RESTORE = 9


def _user32():
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
    user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
    user32.VkKeyScanW.restype = ctypes.c_short
    return user32


def process_windows(process_id):
    user32 = _user32()
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _):
        owner_process = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_process))
        if (owner_process.value == process_id and user32.IsWindowVisible(hwnd)
                and not user32.GetWindow(hwnd, GW_OWNER)):
            length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, len(title))
            windows.append({'handle': int(hwnd), 'title': title.value})
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return windows


def activate_window(hwnd, timeout=10):
    user32 = _user32()
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if user32.GetForegroundWindow() == hwnd:
            return
        time.sleep(0.1)
    raise RuntimeError('the process-owned editor window did not become foreground')


def key(vk, pressed):
    _user32().keybd_event(
        vk, 0, 0 if pressed else KEYEVENTF_KEYUP, 0)


def chord(modifiers, target):
    for modifier in modifiers:
        key(modifier, True)
    key(target, True)
    key(target, False)
    for modifier in reversed(modifiers):
        key(modifier, False)


def type_text(value):
    user32 = _user32()
    for character in value:
        mapping = user32.VkKeyScanW(character)
        if mapping == -1:
            raise RuntimeError(f'cannot type fixture character {character!r} with the active keyboard layout')
        vk, modifiers = mapping & 0xff, (mapping >> 8) & 0xff
        active = []
        if modifiers & 1:
            active.append(VK_SHIFT)
        if modifiers & 2:
            active.append(VK_CONTROL)
        if modifiers & 4:
            active.append(VK_MENU)
        for modifier in active:
            key(modifier, True)
        key(vk, True)
        key(vk, False)
        for modifier in reversed(active):
            key(modifier, False)
        time.sleep(0.015)


def capture(hwnd, path):
    powershell = os.environ.get('POWERSHELL', 'powershell.exe')
    subprocess.run([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                    str(ROOT / 'scripts/capture_windows_window.ps1'), '-Handle', str(hwnd),
                    '-Output', str(path)], check=True, capture_output=True, text=True, timeout=20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    binary, source, output = args.binary.resolve(), args.source.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixture = output / 'mzed-windows-fixture.txt'
    original = b'MZed pinned Windows fixture\n'
    addition = 'MZed native edit save verified'
    record = {'schema': 1, 'editor_smoke': 'not_run', 'same_window_island': 'not_qualified',
              'backend': 'Windows native window', 'operations': []}
    process = None
    hwnd = None
    try:
        record['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
        fixture.write_bytes(original)
        profile = output / 'profile'
        data = profile / 'data'
        config = data / 'config'
        config.mkdir(parents=True)
        (config / 'settings.json').write_text(json.dumps({
            'telemetry': {'diagnostics': False, 'metrics': False}, 'disable_ai': True,
            'auto_update': False, 'auto_install_extensions': {'html': False},
            'ensure_final_newline_on_save': True,
            'languages': {'Plain Text': {'enable_language_server': False}},
        }, indent=2) + '\n')
        home = profile / 'home'
        roaming = home / 'AppData/Roaming'
        local = home / 'AppData/Local'
        roaming.mkdir(parents=True)
        local.mkdir(parents=True)
        environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                           APPDATA=str(roaming), LOCALAPPDATA=str(local),
                           MZED_NATIVE_ISLAND='1', ZED_ALLOW_EMULATED_GPU='1',
                           ZED_UPDATE_EXPLANATION='Pinned Windows MZed experiment')
        with (output / 'editor.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen([str(binary), '--user-data-dir', str(data), str(fixture)],
                                       cwd=source, env=environment, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 120
            stable = None
            stable_since = None
            while time.monotonic() < deadline and process.poll() is None:
                candidates = process_windows(process.pid)
                if len(candidates) == 1 and candidates == stable:
                    if stable_since is not None and time.monotonic() - stable_since >= 1.0:
                        hwnd = candidates[0]['handle']
                        break
                else:
                    stable = candidates if len(candidates) == 1 else None
                    stable_since = time.monotonic() if stable else None
                time.sleep(0.25)
            if hwnd is None:
                raise RuntimeError('no unique stable visible top-level editor window appeared within 120 seconds')
            record['pid'] = process.pid
            record['window'] = stable[0]
            record['operations'].append('opened fixture in one visible process-owned native window')
            activate_window(hwnd)
            capture(hwnd, output / 'before-input.png')
            record['operations'].append('captured the focused editor window before native input')
            # Pinned Zed keymap action: choose Stay in Restricted Mode if first-run UI asks.
            chord([VK_CONTROL, VK_MENU, VK_SHIFT], VK_S)
            time.sleep(0.5)
            current_windows = process_windows(process.pid)
            if len(current_windows) != 1 or current_windows[0]['handle'] != hwnd:
                raise RuntimeError('editor window ownership changed after startup readiness input')
            if int(_user32().GetForegroundWindow() or 0) != hwnd:
                activate_window(hwnd)
            chord([VK_CONTROL], VK_END)
            type_text(addition)
            chord([VK_CONTROL], VK_S)
            expected = original + addition.encode('ascii') + b'\n'
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and fixture.read_bytes() != expected:
                if process.poll() is not None:
                    raise RuntimeError(f'editor exited during edit/save with code {process.returncode}')
                time.sleep(0.2)
            actual = fixture.read_bytes()
            record['fixture_content_utf8'] = actual.decode('utf-8', errors='replace')
            if actual != expected:
                raise RuntimeError('native editor input did not save the exact expected fixture bytes')
            record['editor_smoke'] = 'passed'
            record['operations'].append('typed once and saved expected bytes through native keyboard input')
            capture(hwnd, output / 'after-input.png')
            record['operations'].append('captured the focused editor window after save')
            record['same_window_island_reason'] = 'Windows smoke does not inspect island pixels or pointer ownership.'
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        record['editor_smoke'] = 'failed'
        record['error'] = str(error)
        if isinstance(error, subprocess.CalledProcessError):
            record['failed_command'] = error.cmd
            record['command_return_code'] = error.returncode
            record['command_stdout'] = str(error.stdout or '')[-3000:]
            record['command_stderr'] = str(error.stderr or '')[-3000:]
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill.exe', '/T', '/F', '/PID', str(process.pid)],
                               capture_output=True, check=False)
                process.wait(timeout=10)
        record['editor_exit_code'] = process.poll() if process is not None else None
        (output / 'smoke.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps(record, indent=2))
    return 0 if record['editor_smoke'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
