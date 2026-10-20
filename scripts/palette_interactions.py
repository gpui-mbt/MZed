"""X11 same-window selected-command evidence; no physical IME qualification."""
import hashlib
import os
import re
import time

DISPATCH = re.compile(r'MZed palette dispatch token=(\S+) action=(\S+) window=([^\n]+)')
OPENED = re.compile(r'MZed palette settings-opened token=(\S+) window=([^\n]+)')


def has_settings_command(text):
    # Full-window OCR can join words or lose the narrow i in this fixed label.
    return re.search(r'opensettingsf(?:i)?le', ''.join(text.casefold().split())) is not None


def verify_dispatch_log(text, expected):
    records = DISPATCH.findall(text)
    if (text.count('MZed palette dispatch') != expected or len(records) != expected
            or any(row[1] != 'mzed.open-settings-file' for row in records)):
        raise RuntimeError(f'palette dispatches must be exactly {expected} settings calls: {records}')
    return records


def verify_execution(log):
    dispatches, opened = verify_dispatch_log(log, 1), OPENED.findall(log)
    if log.count('MZed palette settings-opened') != 1 or len(opened) != 1:
        raise RuntimeError(f'expected one dispatch and one settings completion: {dispatches!r}, {opened!r}')
    token, action, window = dispatches[0]
    if action != 'mzed.open-settings-file' or opened != [(token, window)]:
        raise RuntimeError('settings completion did not match the selected command owner')
    return {'token': token, 'action': action, 'gpui_window': window,
            'dispatch_count': 1, 'settings_opened_count': 1}


def exercise(window, output, process, record, run, wait_for_text):
    log_path = output / 'data/logs/Zed.log'

    def log():
        if process.poll() is not None:
            raise RuntimeError('editor exited during palette smoke')
        return log_path.read_text()

    def no_dispatch():
        if DISPATCH.findall(log()) or OPENED.findall(log()):
            raise RuntimeError('command ran before the matching Enter release')

    def key(*args):
        run(['xdotool', *args])

    def wait_for_palette(name, predicate, description):
        image = output / f'{name}.png'
        crop = output / f'{name}-row.png'
        scale = int(os.environ.get('GPUI_X11_SCALE_FACTOR', '1'))
        if scale not in (1, 2):
            raise RuntimeError('palette OCR crop is qualified only for the 1x/2x CI profiles')
        deadline = time.monotonic() + 30
        observed = ''
        while time.monotonic() < deadline:
            log()
            run(['import', '-window', window, str(image)])
            width = int(run(['identify', '-format', '%w', str(image)]))
            left = (width - 640 * scale) // 2
            if left < 0:
                raise RuntimeError('window is narrower than the pinned palette')
            # Full-window segmentation omits the highlighted command row. Keep
            # the raw frame and OCR only the pinned palette's query/row region.
            run(['convert', str(image), '-crop',
                 f'{640 * scale}x{128 * scale}+{left}+{64 * scale}',
                 '+repage', '-resize', '200%', str(crop)])
            observed = run(['tesseract', str(crop), 'stdout', '--psm', '6'])
            if predicate(' '.join(observed.split()).casefold()):
                return observed
            time.sleep(0.2)
        raise RuntimeError(f'{description} not observed in palette crop: {observed!r}')

    def open_filtered(name):
        if run(['xdotool', 'getwindowfocus']) != window:
            raise RuntimeError('palette input lost its owning X11 window')
        key('key', '--clearmodifiers', 'ctrl+alt+shift+z')
        wait_for_palette(f'{name}-open',
                      lambda text: has_settings_command(text), 'palette command row')
        key('type', '--clearmodifiers', '--delay', '40', 'Settings')
        wait_for_palette(f'{name}-filtered',
                      lambda text: has_settings_command(text) and 'settings' in text
                      and 'toggle full screen' not in text, 'filtered Settings command')

    open_filtered('palette-cancel')
    key('key', '--clearmodifiers', 'Escape')
    wait_for_text(window, output / 'palette-cancelled.png',
                  lambda text: 'mzed pinned baseline fixture' in text
                  and not has_settings_command(text), 'Escape dismissal')
    no_dispatch()
    open_filtered('palette-select')
    before = log().count('MZed palette close-pending')
    key('keydown', 'Return')
    # Repeated physical downs must remain in the pending sequence.
    key('keydown', 'Return')
    key('keydown', 'Return')
    deadline = time.monotonic() + 10
    while log().count('MZed palette close-pending') == before:
        if time.monotonic() > deadline:
            raise RuntimeError('Enter Down was not observed by the mounted palette')
        time.sleep(0.1)
    wait_for_palette('palette-enter-held',
                  lambda text: has_settings_command(text), 'palette while Enter held')
    no_dispatch()
    key('keyup', 'Return')
    record['palette_settings_text'] = wait_for_text(
        window, output / 'palette-settings-opened.png',
        lambda text: 'mzed settings command evidence' in text and not has_settings_command(text),
        'actual settings.json contents in the original Zed window')
    deadline = time.monotonic() + 15
    while not OPENED.findall(log()):
        if time.monotonic() > deadline:
            raise RuntimeError('settings open did not complete')
        time.sleep(0.1)
    # A duplicate release after modal destruction must not replay its command.
    key('keyup', 'Return')
    wait_for_text(window, output / 'palette-after-late-release.png',
                  lambda text: 'mzed settings command evidence' in text, 'settings after late keyup')
    execution = verify_execution(log())
    if run(['xdotool', 'getwindowfocus']) != window:
        raise RuntimeError('settings command moved to a different X11 window')
    windows = run(['xdotool', 'search', '--all', '--onlyvisible', '--pid', str(process.pid), '--class', '.*']).split()
    if windows != [window]:
        raise RuntimeError(f'expected original process window only: {windows}')
    execution.update({'status': 'passed', 'x11_window': window,
                      'physical_ime': 'not_run', 'platform': 'Linux X11 software rendering'})
    execution['images_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted(output.glob('palette-*.png'))}
    record['palette_execution'] = execution
    record['operations'].append('Escape cancelled; held/repeated Enter did not dispatch; release opened settings once; late keyup did not replay')
