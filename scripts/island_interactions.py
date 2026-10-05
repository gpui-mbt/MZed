"""Real X11 input plus pixels and process-owned toplevel evidence; no simulated UI."""
import subprocess
import time
import re
from smoke_x11 import run

COLORS = [(40, 96, 160), (200, 96, 160)]


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


def wait_rectangle(window, output, stage, color, expected=True):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found = rectangle(window, output / (stage + '.png'), color)
        if bool(found) == expected:
            return found
        time.sleep(0.15)
    raise RuntimeError(f'{stage}: expected scene pixels were not observed')


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
    owned = toplevels(process.pid)
    if owned != [window]:
        raise RuntimeError(f'expected one process-owned toplevel, got {owned}')
    record['toplevels_before'] = owned
    rect = wait_rectangle(window, output, 'island-initial', COLORS[0])
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
    wait_rectangle(window, output, 'island-unsupported-input', COLORS[0])
    record['operations'].append('right, modified-left and wheel did not activate native scene')
    move(window, center(rect))
    run(['xdotool', 'mousedown', '1'])
    time.sleep(0.4)
    run(['xdotool', 'windowsize', '--sync', window, str(1120*scale), str(720*scale)])
    time.sleep(0.4)
    rect = wait_rectangle(window, output, 'island-held-after-resize', COLORS[0])
    move(window, center(rect))
    run(['xdotool', 'mouseup', '1'])
    rect = wait_rectangle(window, output, 'island-clicked', COLORS[1])
    record['operations'].append('one real plain-left sequence survived redraw/window resize and changed MoonBit scene')
    move(window, center(rect))
    run(['xdotool', 'mousedown', '1'])
    move(window, (400, 250))
    run(['xdotool', 'mouseup', '1'])
    wait_rectangle(window, output, 'island-outside-release', COLORS[1])
    record['operations'].append('owned outside release canceled without native activation')
    move(window, center(rect))
    run(['xdotool', 'mousedown', '1'])
    run(['xdotool', 'windowminimize', window])
    wait_window_state(window, process, visible=False)
    run(['xdotool', 'mouseup', '1'])
    run(['xdotool', 'windowmap', window])
    run(['xdotool', 'windowactivate', window])
    wait_window_state(window, process, visible=True)
    rect = wait_rectangle(window, output, 'island-interrupted-press', COLORS[1])
    click(window, (400, 250))
    record['operations'].append('minimized during owned press, released outside, restored without stale ownership')
    # A paired sequence followed by a bare late up must commit only once.
    click(window, center(rect))
    run(['xdotool', 'mouseup', '1'])
    rect = wait_rectangle(window, output, 'island-repeat', COLORS[0])
    toggle = (rect[0] - 18*scale, center(rect)[1])
    click(window, toggle)
    wait_rectangle(window, output, 'island-disabled', COLORS[0], expected=False)
    wait_rectangle(window, output, 'island-disabled-pink', COLORS[1], expected=False)
    # The host controller stays mounted to swallow late owned sequences, but no MoonBit state exists.
    click(window, center(rect))
    click(window, toggle)
    rect = wait_rectangle(window, output, 'island-remounted', COLORS[0])
    run(['xdotool', 'mouseup', '1'])
    wait_rectangle(window, output, 'island-late-release', COLORS[0])
    click(window, center(rect))
    rect = wait_rectangle(window, output, 'island-remount-clicked', COLORS[1])
    click(window, (rect[0] - 18*scale, center(rect)[1]))
    wait_rectangle(window, output, 'island-final-disabled', COLORS[0], expected=False)
    wait_rectangle(window, output, 'island-final-disabled-pink', COLORS[1], expected=False)
    record['operations'].append('disabled, clicked inactive region, remounted fresh state, rejected bare late release, disabled again')
    owned = toplevels(process.pid)
    record['toplevels_after'] = owned
    if owned != [window]:
        raise RuntimeError(f'island created another native toplevel: {owned}')
    record['native_interaction_pixels'] = 'passed'
