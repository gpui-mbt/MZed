"""Physical X11 selection and independent settings-buffer observation."""
import hashlib
import re


def verify_dispatch_log(text, expected):
    records = re.findall(r'MZed palette dispatch=([^\s]+) epoch=(\d+) origin=([^\n]+)', text)
    if text.count('MZed palette dispatch=') != expected or len(records) != expected or any(row[0] != 'open-settings-file' for row in records):
        raise RuntimeError(f'palette dispatches must be exactly {expected} settings calls: {records}')
    return records


def exercise_palette(window, output, record, run, wait_for_text):
    log = output / 'data/logs/Zed.log'
    verify_dispatch_log(log.read_text(), 0)

    def key(*args):
        if run(['xdotool', 'getwindowfocus']) != window:
            raise RuntimeError('palette smoke lost its process-owned window focus')
        run(['xdotool', *args])

    def open_filtered(name):
        key('key', '--clearmodifiers', 'ctrl+alt+shift+z')
        wait_for_text(window, output / f'{name}-open.png',
                      lambda t: 'open settings file' in t, 'palette command row')
        key('type', '--clearmodifiers', '--delay', '30', 'Settings')
        wait_for_text(window, output / f'{name}-filtered.png',
                      lambda t: 'open settings file' in t and 'toggle full screen' not in t,
                      'filtered settings command')

    open_filtered('palette-cancel')
    key('key', '--clearmodifiers', 'Escape')
    wait_for_text(window, output / 'palette-cancelled.png',
                  lambda t: 'open settings file' not in t and 'mzed pinned baseline fixture' in t,
                  'Escape cancellation in the fixture window')
    verify_dispatch_log(log.read_text(), 0)

    open_filtered('palette-select')
    for _ in range(3):
        key('keydown', 'Return')
    # Reading a rendered frame fences the no-dispatch-before-release observation.
    wait_for_text(window, output / 'palette-held.png',
                  lambda t: 'open settings file' in t, 'palette while Enter is held')
    verify_dispatch_log(log.read_text(), 0)
    key('keyup', 'Return')
    observed = wait_for_text(window, output / 'palette-settings.png',
                            lambda t: 'mzed palette settings probe' in t and 'open settings file' not in t,
                            'actual settings buffer in the original window')
    key('keyup', 'Return')
    wait_for_text(window, output / 'palette-extra-release.png',
                  lambda t: 'mzed palette settings probe' in t, 'settings after unmatched release')
    dispatches = verify_dispatch_log(log.read_text(), 1)
    record['palette'] = {
        'status': 'passed', 'window_id': window, 'dispatches': dispatches,
        'settings_buffer_ocr': observed,
        'image_sha256': hashlib.sha256((output / 'palette-settings.png').read_bytes()).hexdigest(),
        'physical_ime': 'not-tested',
    }
    key('key', '--clearmodifiers', 'ctrl+w')
    wait_for_text(window, output / 'palette-returned.png',
                  lambda t: 'mzed pinned baseline fixture' in t and 'mzed palette settings probe' not in t,
                  'original document after closing settings')
