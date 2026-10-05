"""Real X11 input plus pixels and process-owned toplevel evidence; no simulated UI."""
import subprocess
import time
import re
from pathlib import Path
from smoke_x11 import run

COLORS = [(40, 96, 160), (200, 96, 160)]
FALLBACK_ADDITION = 'MZed native edit save verified'
POST_FAILURE_ADDITION = 'MZed post-failure remount verified'
PROBE_MARKER = 'MZed island dispatch rejection probe'
DISPATCH_FAILURE = 'MZed island dispatch failed: -8; disabling'
MOUNT_MARKER = 'MZed island mounted: gpui.mbt bf965ae, copied scene v1'


def wait_for_fixture_bytes(fixture, expected, description, timeout=10):
    fixture = Path(fixture)
    deadline = time.monotonic() + timeout
    actual = b''
    while time.monotonic() < deadline:
        if fixture.exists():
            actual = fixture.read_bytes()
            if actual == expected:
                return actual
        time.sleep(0.1)
    raise RuntimeError(
        f'{description}: saved fixture bytes did not match exactly; expected {expected!r}, got {actual!r}')


def scene_label(window, output, stage, reference_rect, scale, expected):
    screenshot = output / (stage + '.png')
    left, top, right, bottom = reference_rect
    # The compact MB label follows the colored quad in the status bar.
    crop = output / (stage + '-label.png')
    geometry = f'{70 * scale}x{(bottom - top + 1) + 4 * scale}+{right + scale}+{top - 2 * scale}'
    run(['convert', str(screenshot), '-crop', geometry, '+repage', '-resize', f'{400 // scale}%', str(crop)])
    text = run(['tesseract', str(crop), 'stdout', '--psm', '7'])
    normalized = re.sub(r'[^a-z0-9]', '', text.casefold())
    valid = {
        'MB 0': {'mb0', 'mbo'},
        'MB 1': {'mb1'},
        'MB off': {'mboff'},
    }
    if normalized not in valid[expected]:
        raise RuntimeError(f'{stage}: expected status label {expected!r}, got OCR {text!r}')
    return text


def assert_process_window(window, process, record, stage):
    status = process.poll()
    if status is not None:
        raise RuntimeError(f'editor exited during {stage}: {status}')
    owned = toplevels(process.pid)
    if owned != [window]:
        raise RuntimeError(f'{stage}: expected the same single process-owned toplevel {window}, got {owned}')
    active = run(['xdotool', 'getactivewindow'])
    focused = run(['xdotool', 'getwindowfocus'])
    record[stage + '_window_id'] = window
    record[stage + '_toplevels'] = owned
    record[stage + '_active_window_id'] = active
    record[stage + '_focused_window_id'] = focused
    if active != window or focused != window:
        raise RuntimeError(f'{stage}: original editor window lost activation/focus: active={active}, focus={focused}')


def native_events(output):
    path = output / 'data/logs/Zed.log'
    if not path.is_file():
        raise RuntimeError('native log is missing from data/logs/Zed.log')
    return re.findall(r'MZed (?:island|native) [^\n]*', path.read_text())


def wait_for_rejection_log(output, process):
    expected = [MOUNT_MARKER, PROBE_MARKER, DISPATCH_FAILURE]
    deadline = time.monotonic() + 10
    last = []
    while time.monotonic() < deadline:
        status = process.poll()
        if status is not None:
            raise RuntimeError(f'editor exited during native rejection probe: {status}')
        last = native_events(output)
        if last == expected:
            return last
        if len(last) > len(expected) or last[:len(expected)] != expected[:len(last)]:
            raise RuntimeError(f'native rejection diagnostics were unexpected or duplicated: {last!r}')
        time.sleep(0.05)
    raise RuntimeError(f'exact probe marker and -8 failure were not observed; native events: {last!r}')


def assert_probe_still_armed(output):
    events = native_events(output)
    if events != [MOUNT_MARKER]:
        raise RuntimeError(f'unsupported input consumed the one-shot probe or changed the native scene: {events!r}')


def rectangle(window, screenshot, color):
    run(['import', '-window', window, str(screenshot)])
    width, height = map(int, run(['identify', '-format', '%w %h', str(screenshot)]).split())
    pixels = subprocess.check_output(['convert', str(screenshot), '-depth', '8', 'rgb:-'], timeout=15)
    locations = [(index % width, index // width) for index in range(width * height)
                 if pixels[index * 3:index * 3 + 3] == bytes(color)]
    if len(locations) < 40:
        return None
    left, right = min(x for x, _ in locations), max(x for x, _ in locations)
    top, bottom = min(y for _, y in locations), max(y for _, y in locations)
    # Refuse a color scattered elsewhere instead of accepting an invented control location.
    if len(locations) != (right - left + 1) * (bottom - top + 1):
        raise RuntimeError('native scene color is not one solid bounded rectangle')
    return (left, top, right, bottom)


def wait_rectangle(window, output, stage, color, expected=True, diagnostics=None):
    deadline = time.monotonic() + 10
    last_capture_error = None
    while time.monotonic() < deadline:
        try:
            found = rectangle(window, output / (stage + '.png'), color)
        except subprocess.CalledProcessError as error:
            if error.cmd[:1] != ['import'] or 'Resource temporarily unavailable' not in str(error.stderr):
                raise
            # A failed capture is never evidence that the scene is absent.
            last_capture_error = str(error.stderr)[-1000:]
            if diagnostics is not None:
                diagnostics[stage] = diagnostics.get(stage, 0) + 1
            time.sleep(0.15)
            continue
        if bool(found) == expected:
            return found
        time.sleep(0.15)
    raise RuntimeError(f'{stage}: expected scene pixels were not observed; last capture error: {last_capture_error}')


def center(rect):
    left, top, right, bottom = rect
    return ((left + right) // 2, (top + bottom) // 2)


def shell_coordinates(output):
    values = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    try:
        return int(values['X']), int(values['Y'])
    except (KeyError, ValueError) as error:
        raise RuntimeError('pointer query did not return numeric X/Y') from error


def move(window, position):
    geometry = run(['xwininfo', '-id', window])
    x = re.search(r'Absolute upper-left X:\s*(-?\d+)', geometry)
    y = re.search(r'Absolute upper-left Y:\s*(-?\d+)', geometry)
    if x is None or y is None:
        raise RuntimeError('window query did not return an absolute client origin')
    border = re.search(r'Border width:\s*(\d+)', geometry)
    if border is None or int(border.group(1)) != 0:
        raise RuntimeError('pointer proof requires the pinned zero-border client window')
    origin = (int(x.group(1)), int(y.group(1)))
    target = (origin[0] + position[0], origin[1] + position[1])
    # --sync waits for movement, which is unsuitable for repeated same-position
    # window-relative moves on the pinned X11 driver. Verify actual coordinates.
    run(['xdotool', 'mousemove', '--window', window, *map(str, position)])
    deadline = time.monotonic() + 2
    current = None
    while time.monotonic() < deadline:
        current = shell_coordinates(run(['xdotool', 'getmouselocation', '--shell']))
        if current == target:
            return
        time.sleep(0.05)
    raise RuntimeError(f'pointer did not reach verified root coordinates {target}; got {current}')


def click(window, position, button='1'):
    move(window, position)
    run(['xdotool', 'click', button])
    time.sleep(0.15)


def toplevels(pid):
    candidates = run(['xdotool', 'search', '--all', '--onlyvisible', '--pid', str(pid)]).split()
    return [window for window in candidates
            if 'window state:' in run(['xprop', '-id', window, 'WM_STATE']).lower()]


def wait_window_state(window, process, visible):
    deadline = time.monotonic() + 10
    last = ''
    while time.monotonic() < deadline:
        status = process.poll()
        if status is not None:
            raise RuntimeError(f'editor exited while waiting for window visibility: {status}')
        geometry = run(['xwininfo', '-id', window])
        map_state = re.search(r'Map State:\s*(IsViewable|IsUnMapped|IsUnviewable)', geometry)
        if map_state is None:
            raise RuntimeError('window query did not return a recognized map state')
        manager_state = run(['xprop', '-id', window, 'WM_STATE'])
        last = geometry + '\n' + manager_state
        mapped = map_state.group(1) == 'IsViewable'
        expected_manager = 'window state: normal' if visible else 'window state: iconic'
        if mapped == visible and expected_manager in manager_state.lower():
            if not visible:
                return
            active = run(['xdotool', 'getactivewindow'])
            focus = run(['xdotool', 'getwindowfocus'])
            last += f'\nactive={active}; focus={focus}'
            if active == window and focus == window:
                return
        time.sleep(0.05)
    raise RuntimeError(f'window did not reach visible={visible} with focus when required; last state: {last}')


def exercise(window, output, process, record):
    scale = int(record['scale'])
    record['capture_retries'] = {}
    def scene(stage, color, expected=True):
        return wait_rectangle(window, output, stage, color, expected, record['capture_retries'])
    owned = toplevels(process.pid)
    if owned != [window]:
        raise RuntimeError(f'expected one process-owned toplevel, got {owned}')
    record['toplevels_before'] = owned
    rect = scene('island-initial', COLORS[0])
    left, top, right, bottom = rect
    if abs((right-left+1) - 120*scale) > 2 or abs((bottom-top+1) - 18*scale) > 2:
        raise RuntimeError(f'logical scene was not scaled exactly once: {rect}, scale={scale}')
    record['initial_scene_rectangle'] = rect
    click(window, center(rect), '3')
    run(['xdotool', 'keydown', 'shift'])
    try:
        click(window, center(rect))
    finally:
        run(['xdotool', 'keyup', 'shift'])
    click(window, center(rect), '4')
    scene('island-unsupported-input', COLORS[0])
    record['operations'].append('right, modified-left and wheel did not activate native scene')
    move(window, center(rect))
    run(['xdotool', 'mousedown', '1'])
    time.sleep(0.4)
    run(['xdotool', 'windowsize', '--sync', window, str(1120*scale), str(720*scale)])
    time.sleep(0.4)
    rect = scene('island-held-after-resize', COLORS[0])
    move(window, center(rect))
    run(['xdotool', 'mouseup', '1'])
    rect = scene('island-clicked', COLORS[1])
    record['operations'].append('one real plain-left sequence survived redraw/window resize and changed MoonBit scene')
    move(window, center(rect))
    run(['xdotool', 'mousedown', '1'])
    move(window, (400, 250))
    run(['xdotool', 'mouseup', '1'])
    scene('island-outside-release', COLORS[1])
    record['operations'].append('owned outside release canceled without native activation')
    move(window, center(rect))
    run(['xdotool', 'mousedown', '1'])
    run(['xdotool', 'windowminimize', window])
    wait_window_state(window, process, visible=False)
    run(['xdotool', 'mouseup', '1'])
    run(['xdotool', 'windowmap', window])
    run(['xdotool', 'windowactivate', window])
    wait_window_state(window, process, visible=True)
    rect = scene('island-interrupted-press', COLORS[1])
    click(window, (400, 250))
    record['operations'].append('minimized during owned press, released outside, restored without stale ownership')
    # A paired sequence followed by a bare late up must commit only once.
    click(window, center(rect))
    run(['xdotool', 'mouseup', '1'])
    rect = scene('island-repeat', COLORS[0])
    toggle = (rect[0] - 18*scale, center(rect)[1])
    click(window, toggle)
    scene('island-disabled', COLORS[0], expected=False)
    scene('island-disabled-pink', COLORS[1], expected=False)
    # The host controller stays mounted to swallow late owned sequences, but no MoonBit state exists.
    click(window, center(rect))
    click(window, toggle)
    rect = scene('island-remounted', COLORS[0])
    run(['xdotool', 'mouseup', '1'])
    scene('island-late-release', COLORS[0])
    click(window, center(rect))
    rect = scene('island-remount-clicked', COLORS[1])
    click(window, (rect[0] - 18*scale, center(rect)[1]))
    scene('island-final-disabled', COLORS[0], expected=False)
    scene('island-final-disabled-pink', COLORS[1], expected=False)
    record['operations'].append('disabled, clicked inactive region, remounted fresh state, rejected bare late release, disabled again')
    owned = toplevels(process.pid)
    record['toplevels_after'] = owned
    if owned != [window]:
        raise RuntimeError(f'island created another native toplevel: {owned}')
    record['native_interaction_pixels'] = 'passed'


def exercise_dispatch_rejection(window, output, process, record):
    scale = int(record['scale'])
    record['capture_retries'] = {}

    def scene(stage, color, expected=True):
        return wait_rectangle(window, output, stage, color, expected, record['capture_retries'])

    owned = toplevels(process.pid)
    if owned != [window]:
        raise RuntimeError(f'expected one process-owned toplevel, got {owned}')
    record['toplevels_before'] = owned
    rect = scene('rejection-initial-blue-mb0', COLORS[0])
    if rect is None:
        raise RuntimeError('initial blue MB0 scene was not observed')
    scene('rejection-initial-no-pink', COLORS[1], expected=False)
    record['initial_scene_rectangle'] = rect
    record['initial_scene_label'] = scene_label(
        window, output, 'rejection-initial-blue-mb0', rect, scale, 'MB 0')
    assert_probe_still_armed(output)

    click(window, center(rect), '3')
    run(['xdotool', 'keydown', 'shift'])
    try:
        click(window, center(rect))
    finally:
        run(['xdotool', 'keyup', 'shift'])
    click(window, center(rect), '4')
    scene('rejection-unsupported-input-blue', COLORS[0])
    scene('rejection-unsupported-input-no-pink', COLORS[1], expected=False)
    assert_probe_still_armed(output)
    record['operations'].append('right, modified-left and wheel left the blue MB0 scene and one-shot probe armed')

    move(window, center(rect))
    run(['xdotool', 'mousedown', '1'])
    run(['xdotool', 'mouseup', '1'])
    scene('rejection-dispatched-no-blue', COLORS[0], expected=False)
    scene('rejection-dispatched-no-pink', COLORS[1], expected=False)
    events = wait_for_rejection_log(output, process)
    record['probe_events_after_rejection'] = events
    record['rejection_scene_label'] = scene_label(
        window, output, 'rejection-dispatched-no-blue', rect, scale, 'MB off')
    assert_process_window(window, process, record, 'after_rejection')
    record['operations'].append('one accepted plain-left release called the real invalid opcode and disabled only the island')

    fixture = output / 'mzed-baseline-fixture.txt'
    expected = b'MZed pinned baseline fixture\n' + FALLBACK_ADDITION.encode() + b'\n'
    run(['xdotool', 'key', '--clearmodifiers', 'ctrl+End'])
    run(['xdotool', 'type', '--clearmodifiers', '--delay', '30', FALLBACK_ADDITION])
    run(['xdotool', 'key', '--clearmodifiers', 'ctrl+s'])
    actual = wait_for_fixture_bytes(fixture, expected, 'immediate editor fallback save')
    record['immediate_editor_fallback'] = {
        'expected_utf8': expected.decode(),
        'expected_hex': expected.hex(),
        'actual_utf8': actual.decode(),
        'actual_hex': actual.hex(),
        'passed': True,
    }
    record['operations'].append('immediately typed and saved exact expected bytes in the same focused editor window')
    run(['import', '-window', window, str(output / 'rejection-editor-fallback.png')])
    assert_process_window(window, process, record, 'after_immediate_editor_fallback')
    record['checkpoint_order'] = ['dispatch-rejected', 'immediate-editor-save']

    click(window, center(rect))
    run(['xdotool', 'mouseup', '1'])
    scene('rejection-late-up-no-blue', COLORS[0], expected=False)
    scene('rejection-late-up-no-pink', COLORS[1], expected=False)
    if native_events(output) != events:
        raise RuntimeError(f'inactive quad click or bare late-up changed native diagnostics: {native_events(output)!r}')
    assert_process_window(window, process, record, 'after_inactive_click_and_late_up')
    record['operations'].append('inactive former quad and bare late mouse-up produced no scene or extra dispatch')
    record['checkpoint_order'].append('inactive-click-and-late-up')

    toggle = (rect[0] - 18 * scale, center(rect)[1])
    click(window, toggle)
    rect = scene('rejection-remounted-blue-mb0', COLORS[0])
    if rect is None:
        raise RuntimeError('fresh remount did not paint the blue MB0 scene')
    record['remounted_scene_rectangle'] = rect
    record['remounted_scene_label'] = scene_label(
        window, output, 'rejection-remounted-blue-mb0', rect, scale, 'MB 0')
    assert_process_window(window, process, record, 'after_remount')

    click(window, center(rect))
    rect = scene('rejection-remounted-pink-mb1', COLORS[1])
    scene('rejection-remounted-no-blue', COLORS[0], expected=False)
    record['remounted_counter_label'] = scene_label(
        window, output, 'rejection-remounted-pink-mb1', rect, scale, 'MB 1')
    assert_process_window(window, process, record, 'after_remounted_increment')

    toggle = (rect[0] - 18 * scale, center(rect)[1])
    click(window, toggle)
    scene('rejection-final-disabled-no-blue', COLORS[0], expected=False)
    scene('rejection-final-disabled-no-pink', COLORS[1], expected=False)
    record['final_disabled_label'] = scene_label(
        window, output, 'rejection-final-disabled-no-blue', rect, scale, 'MB off')
    assert_process_window(window, process, record, 'after_final_disable')
    record['operations'].append('remounted fresh MB0, incremented once to MB1, then disabled it in the same window')
    record['checkpoint_order'].append('fresh-remount-increment-disabled')
    record['toplevels_after'] = toplevels(process.pid)
    if record['toplevels_after'] != [window]:
        raise RuntimeError(f'island created another native toplevel: {record["toplevels_after"]}')
