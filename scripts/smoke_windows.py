#!/usr/bin/env python3
"""Qualify the copied scene with native Windows input and pixels, then edit a fixture."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
VK_CONTROL, VK_MENU, VK_SHIFT = 0x11, 0x12, 0x10
VK_END, VK_S = 0x23, 0x53
KEYEVENTF_KEYUP = 0x0002
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL = 0x0800
SW_RESTORE, SW_MINIMIZE = 9, 6
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
SCENE_COLORS = {'blue': (40, 96, 160), 'pink': (200, 96, 160)}


class POINT(ctypes.Structure):
    _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG),
        ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD),
        ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ('uMsg', wintypes.DWORD),
        ('wParamL', wintypes.WORD),
        ('wParamH', wintypes.WORD),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', wintypes.DWORD), ('u', INPUTUNION)]


def _user32():
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    set_dpi_context = user32.SetProcessDpiAwarenessContext
    set_dpi_context.argtypes = [ctypes.c_void_p]
    set_dpi_context.restype = wintypes.BOOL
    # SetProcessDpiAwarenessContext returns false when another library set it first.
    set_dpi_context(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
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
                and not user32.GetWindow(hwnd, 4)):
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


def _require_foreground(hwnd):
    if int(_user32().GetForegroundWindow() or 0) != hwnd:
        raise RuntimeError('refusing to send native input because the editor is not foreground')


def _send_input(input_event):
    user32 = _user32()
    events = (INPUT * 1)(input_event)
    sent = user32.SendInput(1, events, ctypes.sizeof(INPUT))
    if sent != 1:
        error = ctypes.get_last_error()
        raise OSError(error, f'SendInput accepted {sent}/1 native input events')


def key(vk, pressed):
    flags = 0 if pressed else KEYEVENTF_KEYUP
    _send_input(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, 0, flags, 0, 0)))


def chord(modifiers, target):
    for modifier in modifiers:
        key(modifier, True)
    try:
        key(target, True)
        key(target, False)
    finally:
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
        try:
            key(vk, True)
            key(vk, False)
        finally:
            for modifier in reversed(active):
                key(modifier, False)
        time.sleep(0.015)


def _mouse(flags, data=0):
    _send_input(INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, data, flags, 0, 0)))


def mouse_button(button, pressed):
    flags = {
        ('left', True): MOUSEEVENTF_LEFTDOWN,
        ('left', False): MOUSEEVENTF_LEFTUP,
        ('right', True): MOUSEEVENTF_RIGHTDOWN,
        ('right', False): MOUSEEVENTF_RIGHTUP,
        ('middle', True): MOUSEEVENTF_MIDDLEDOWN,
        ('middle', False): MOUSEEVENTF_MIDDLEUP,
    }.get((button, pressed))
    if flags is None:
        raise ValueError(f'unsupported mouse button state: {button!r}, pressed={pressed}')
    _mouse(flags)


def screen_point(snapshot, position):
    return (int(snapshot['left']) + int(position[0]),
            int(snapshot['top']) + int(position[1]))


def cursor_position():
    actual = POINT()
    if not _user32().GetCursorPos(ctypes.byref(actual)):
        raise ctypes.WinError(ctypes.get_last_error())
    return actual.x, actual.y


def move_pointer(hwnd, snapshot, position, require_foreground=True):
    user32 = _user32()
    if require_foreground:
        _require_foreground(hwnd)
    expected = screen_point(snapshot, position)
    if not user32.SetCursorPos(*expected):
        raise ctypes.WinError(ctypes.get_last_error())
    actual = cursor_position()
    if actual != expected:
        raise RuntimeError(f'pointer did not reach requested screen point {expected}; got {actual}')


def click(hwnd, snapshot, position, button='left', shift=False):
    _require_foreground(hwnd)
    move_pointer(hwnd, snapshot, position)
    if shift:
        key(VK_SHIFT, True)
    pressed = False
    try:
        mouse_button(button, True)
        pressed = True
        time.sleep(0.04)
        mouse_button(button, False)
        pressed = False
    finally:
        if pressed:
            mouse_button(button, False)
        if shift:
            key(VK_SHIFT, False)
    time.sleep(0.15)


def wheel(hwnd, snapshot, position):
    _require_foreground(hwnd)
    move_pointer(hwnd, snapshot, position)
    _mouse(MOUSEEVENTF_WHEEL, 120)
    time.sleep(0.15)


def window_rect(hwnd):
    rect = wintypes.RECT()
    if not _user32().GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (rect.left, rect.top, rect.right, rect.bottom)


def resize_window(hwnd, scale):
    user32 = _user32()
    left, top, right, bottom = window_rect(hwnd)
    width, height = right - left, bottom - top
    target_width = width + round(240 * scale)
    target_height = height + round(160 * scale)
    if not user32.SetWindowPos(
            hwnd, None, left, top, target_width, target_height,
            SWP_NOZORDER | SWP_NOACTIVATE):
        raise ctypes.WinError(ctypes.get_last_error())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = window_rect(hwnd)
        if current[2] - current[0] >= target_width and current[3] - current[1] >= target_height:
            return {'width': current[2] - current[0], 'height': current[3] - current[1]}
        time.sleep(0.05)
    raise RuntimeError(f'window did not reach requested resized bounds {target_width}x{target_height}')


def color_rectangle(snapshot, name):
    color = snapshot.get('colors', {}).get(name)
    if color is None:
        raise RuntimeError(f'Windows capture is missing {name!r} pixel evidence')
    count = int(color.get('count', -1))
    if count < 0:
        raise RuntimeError(f'Windows capture has an invalid {name!r} pixel count')
    if count == 0:
        return None
    left, top = int(color['left']), int(color['top'])
    right, bottom = int(color['right']), int(color['bottom'])
    if right < left or bottom < top or count != (right - left + 1) * (bottom - top + 1):
        raise RuntimeError(f'{name} scene color is not one solid bounded rectangle: {color}')
    return (left, top, right, bottom)


def scene_state(snapshot):
    blue = color_rectangle(snapshot, 'blue')
    pink = color_rectangle(snapshot, 'pink')
    if blue is not None and pink is not None:
        raise RuntimeError('both native scene colors are visible in one Windows capture')
    if blue is not None:
        return 'blue', blue
    if pink is not None:
        return 'pink', pink
    return None, None


def scene_matches(snapshot, expected):
    return scene_state(snapshot)[0] == expected


def native_log_path(output):
    return output / 'profile' / 'data' / 'logs' / 'Zed.log'


def read_native_log(path):
    if not path.is_file():
        raise RuntimeError('editor log is missing; cannot verify native island lifecycle')
    return path.read_text(encoding='utf-8', errors='replace')


def press_ack_generations(native_log):
    return [int(value) for value in re.findall(r'MZed island press owned generation=(\d+)', native_log)]


def native_dispatches(native_log):
    return [int(value) for value in re.findall(r'MZed island counter=(\d+)', native_log)]


def wait_for_press_ack(process, log_path, previous_count, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'editor exited before acknowledging island press: {process.returncode}')
        acknowledgments = press_ack_generations(read_native_log(log_path))
        if len(acknowledgments) > previous_count + 1:
            raise RuntimeError('one mouse down produced multiple native island press acknowledgments')
        if len(acknowledgments) == previous_count + 1:
            return acknowledgments[-1]
        time.sleep(0.05)
    raise RuntimeError('native island did not acknowledge owned press before minimize')


def assert_scene_unchanged(snapshot, native_log, expected_color, expected_dispatches):
    actual_color, _ = scene_state(snapshot)
    if actual_color != expected_color:
        raise RuntimeError(f'bare late release changed scene from {expected_color!r} to {actual_color!r}')
    actual_dispatches = native_dispatches(native_log)
    if actual_dispatches != expected_dispatches:
        raise RuntimeError(
            f'bare late release changed native dispatches from {expected_dispatches} to {actual_dispatches}')


def observe_scene_unchanged(hwnd, output, log_path, stage, expected_color, expected_dispatches, duration=1.5):
    deadline = None
    observations = 0
    last_snapshot = None
    while True:
        last_snapshot = capture(hwnd, output / f'{stage}.png')
        assert_scene_unchanged(last_snapshot, read_native_log(log_path), expected_color, expected_dispatches)
        observations += 1
        if deadline is None:
            deadline = time.monotonic() + duration
        remaining = deadline - time.monotonic()
        if remaining <= 0 and observations >= 2:
            break
        time.sleep(min(0.15, max(0, remaining)))
    _, rectangle = scene_state(last_snapshot)
    return last_snapshot, rectangle, observations


def scene_center(rectangle):
    left, top, right, bottom = rectangle
    return ((left + right) // 2, (top + bottom) // 2)


def validate_scene_size(rectangle, scale):
    if scale <= 0:
        raise RuntimeError(f'Windows capture returned invalid window DPI scale: {scale}')
    width, height = rectangle[2] - rectangle[0] + 1, rectangle[3] - rectangle[1] + 1
    if abs(width - 120 * scale) > 2 or abs(height - 18 * scale) > 2:
        raise RuntimeError(f'copied scene was not scaled exactly once: {rectangle}, scale={scale}')
    return width, height


def wait_scene(hwnd, output, stage, expected, timeout=15):
    capture_path = output / f'{stage}.png'
    deadline = time.monotonic() + timeout
    last_state = None
    last_snapshot = None
    while time.monotonic() < deadline:
        last_snapshot = capture(hwnd, capture_path)
        last_state, rectangle = scene_state(last_snapshot)
        if last_state == expected:
            return last_snapshot, rectangle
        time.sleep(0.15)
    raise RuntimeError(f'{stage}: expected scene color {expected!r}, got {last_state!r}')


def wait_minimized(hwnd, process, timeout=10):
    user32 = _user32()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'editor exited while minimizing its window: {process.returncode}')
        if user32.IsIconic(hwnd):
            return
        time.sleep(0.05)
    raise RuntimeError('editor window did not enter the minimized state')


def outside_point(snapshot, rectangle):
    width, height = int(snapshot['width']), int(snapshot['height'])
    candidates = [(width // 2, height // 2), (width // 3, height // 3),
                  (width * 3 // 4, height // 3)]
    left, top, right, bottom = rectangle
    for point in candidates:
        if not (left <= point[0] <= right and top <= point[1] <= bottom):
            return point
    raise RuntimeError('could not find a window point outside the native island')


def exercise_island(hwnd, process, output, record):
    owned = process_windows(process.pid)
    if len(owned) != 1 or owned[0]['handle'] != hwnd:
        raise RuntimeError(f'expected one process-owned top-level window, got {owned}')
    record['toplevels_before'] = owned

    snapshot, rect = wait_scene(hwnd, output, 'island-initial', 'blue')
    scale = int(snapshot['dpi']) / 96.0
    validate_scene_size(rect, scale)
    record['scale'] = scale
    record['initial_scene_rectangle'] = rect

    click(hwnd, snapshot, scene_center(rect), button='right')
    snapshot, _ = wait_scene(hwnd, output, 'island-after-right-click', 'blue')
    click(hwnd, snapshot, scene_center(rect), shift=True)
    snapshot, _ = wait_scene(hwnd, output, 'island-after-modified-click', 'blue')
    wheel(hwnd, snapshot, scene_center(rect))
    snapshot, rect = wait_scene(hwnd, output, 'island-unsupported-input', 'blue')
    record['operations'].append('right, Shift-left, and wheel input did not activate the native scene')

    _require_foreground(hwnd)
    move_pointer(hwnd, snapshot, scene_center(rect))
    mouse_button('left', True)
    try:
        time.sleep(0.3)
        record['resize'] = resize_window(hwnd, scale)
        snapshot, rect = wait_scene(hwnd, output, 'island-held-after-resize', 'blue')
        move_pointer(hwnd, snapshot, scene_center(rect))
        mouse_button('left', False)
    finally:
        # Do not leave the desktop button held if resize, capture, or assertion fails.
        mouse_button('left', False)
    snapshot, rect = wait_scene(hwnd, output, 'island-clicked', 'pink')
    record['operations'].append('one plain-left release after window resize changed the native scene')

    move_pointer(hwnd, snapshot, scene_center(rect))
    mouse_button('left', True)
    try:
        move_pointer(hwnd, snapshot, outside_point(snapshot, rect))
        mouse_button('left', False)
    finally:
        mouse_button('left', False)
    snapshot, rect = wait_scene(hwnd, output, 'island-outside-release', 'pink')
    record['operations'].append('owned outside release canceled without activating the native scene')

    log_path = native_log_path(output)
    previous_acknowledgments = len(press_ack_generations(read_native_log(log_path)))
    minimize_pointer = screen_point(snapshot, scene_center(rect))
    move_pointer(hwnd, snapshot, scene_center(rect))
    mouse_button('left', True)
    try:
        generation = wait_for_press_ack(process, log_path, previous_acknowledgments)
        record['minimize_press_owned_generation'] = generation
        _user32().ShowWindow(hwnd, SW_MINIMIZE)
        wait_minimized(hwnd, process)
        # Release while the window is hidden, then restore and probe with no new down.
        mouse_button('left', False)
    finally:
        mouse_button('left', False)
    activate_window(hwnd)
    snapshot, rect = wait_scene(hwnd, output, 'island-interrupted-press', 'pink')
    restored_center = screen_point(snapshot, scene_center(rect))
    if restored_center != minimize_pointer or cursor_position() != restored_center:
        raise RuntimeError('pointer or island moved during restore; bare-release cancellation oracle cannot proceed safely')
    dispatches_before_late_release = native_dispatches(read_native_log(log_path))
    mouse_button('left', False)
    snapshot, rect, observations = observe_scene_unchanged(
        hwnd, output, log_path, 'island-minimize-late-release', 'pink',
        dispatches_before_late_release)
    record['minimize_late_release_observations'] = observations
    click(hwnd, snapshot, outside_point(snapshot, rect))
    snapshot, rect = wait_scene(hwnd, output, 'island-after-minimize', 'pink')
    record['operations'].append(
        'acknowledged an owned press before minimize, then restored and rejected a bare center release before any new down')

    click(hwnd, snapshot, scene_center(rect))
    snapshot, rect = wait_scene(hwnd, output, 'island-repeat', 'blue')
    mouse_button('left', False)
    snapshot, rect = wait_scene(hwnd, output, 'island-late-release', 'blue')
    toggle = (rect[0] - round(18 * scale), (rect[1] + rect[3]) // 2)
    click(hwnd, snapshot, toggle)
    snapshot, _ = wait_scene(hwnd, output, 'island-disabled', None)
    click(hwnd, snapshot, scene_center(rect))
    snapshot, _ = wait_scene(hwnd, output, 'island-inactive-region', None)
    click(hwnd, snapshot, toggle)
    snapshot, rect = wait_scene(hwnd, output, 'island-remounted', 'blue')
    mouse_button('left', False)
    snapshot, rect = wait_scene(hwnd, output, 'island-remount-late-release', 'blue')
    click(hwnd, snapshot, scene_center(rect))
    snapshot, rect = wait_scene(hwnd, output, 'island-remount-clicked', 'pink')
    toggle = (rect[0] - round(18 * scale), (rect[1] + rect[3]) // 2)
    click(hwnd, snapshot, toggle)
    snapshot, _ = wait_scene(hwnd, output, 'island-final-disabled', None)
    record['operations'].append(
        'disabled the island, ignored an inactive click and late release, remounted fresh state, and disabled it again')

    owned = process_windows(process.pid)
    record['toplevels_after'] = owned
    if len(owned) != 1 or owned[0]['handle'] != hwnd:
        raise RuntimeError(f'native island created or removed a process-owned top-level window: {owned}')
    native_log = read_native_log(native_log_path(output))
    dispatches = native_dispatches(native_log)
    if dispatches != [1, 2, 1]:
        raise RuntimeError(f'native dispatch sequence was not exactly [1, 2, 1]: {dispatches}')
    if native_log.count('MZed island disabled and native instance destroyed') != 2:
        raise RuntimeError('native island disable/destroy lifecycle count mismatch')
    if native_log.count('MZed island remounted') != 1:
        raise RuntimeError('native island remount lifecycle count mismatch')
    if re.search(r'MZed island [^\n]*failed', native_log):
        raise RuntimeError('native island reported an error')
    record['native_dispatches'] = dispatches
    record['same_window_island'] = 'passed'


def capture(hwnd, path):
    powershell = os.environ.get('POWERSHELL', 'powershell.exe')
    result = subprocess.run(
        [powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
         str(ROOT / 'scripts/capture_windows_window.ps1'), '-Handle', str(hwnd),
         '-Output', str(path)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise RuntimeError(f'Windows capture returned invalid pixel evidence: {result.stdout!r}') from error


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
    record = {'schema': 2, 'editor_smoke': 'not_run', 'same_window_island': 'not_run',
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
            exercise_island(hwnd, process, output, record)

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
            record['same_window_island_reason'] = 'Windows smoke verifies scene pixels, pointer ownership, cancellation, and remount lifecycle.'
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        if record['editor_smoke'] != 'passed':
            record['editor_smoke'] = 'failed'
        if record['same_window_island'] != 'passed':
            record['same_window_island'] = 'failed'
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
    return 0 if record['editor_smoke'] == 'passed' and record['same_window_island'] == 'passed' and 'error' not in record else 1


if __name__ == '__main__':
    raise SystemExit(main())
