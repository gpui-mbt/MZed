import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('smoke', Path(__file__).resolve().parents[1] / 'scripts/smoke_x11.py')
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class SmokeHarnessTests(unittest.TestCase):
    def test_settings_path_and_final_newline_match_pinned_editor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'fake-binary'
            binary.write_bytes(b'fixture, not an editor')
            output = root / 'evidence'
            process = Mock(pid=12345)
            process.poll.side_effect = [None, 0]
            def start(argv, **kwargs):
                self.assertIn('--user-data-dir', argv)
                config = json.loads((output / 'data/config/settings.json').read_text())
                self.assertFalse(config['telemetry']['metrics'])
                self.assertFalse(config['telemetry']['diagnostics'])
                self.assertTrue(config['disable_ai'])
                self.assertFalse(config['auto_update'])
                self.assertFalse(config['auto_install_extensions']['html'])
                self.assertEqual(kwargs['cwd'], root.resolve())
                self.assertEqual(kwargs['env']['HOME'], str((output / 'home').resolve()))
                self.assertEqual(kwargs['env']['ZED_ALLOW_EMULATED_GPU'], '1')
                self.assertTrue(config['ensure_final_newline_on_save'])
                return process
            def native_command(argv, **kwargs):
                if argv[-1] == 'getwindowfocus':
                    return '42'
                if argv[-1] == 'ctrl+s':
                    (output / 'mzed-baseline-fixture.txt').write_text(
                        'MZed pinned baseline fixture\nMZed native edit save verified\n')
                return 'mocked harness output'
            args = argparse.Namespace(source=Path(directory), binary=binary, output=output)
            with patch.object(smoke.argparse.ArgumentParser, 'parse_args', return_value=args), \
                    patch.object(smoke, 'run', side_effect=native_command), \
                    patch.object(smoke, 'wait_for_text', return_value='observed expected UI'), \
                    patch.object(smoke.subprocess, 'Popen', side_effect=start), \
                    patch.object(smoke.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '42\n')):
                self.assertEqual(smoke.main(), 0)
            evidence = json.loads((output / 'smoke.json').read_text())
            self.assertEqual(evidence['editor_smoke'], 'passed')
            self.assertEqual(evidence['same_window_island'], 'not_implemented')
            self.assertEqual(evidence['window_id'], '42')

    def test_missing_binary_records_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'evidence'
            args = argparse.Namespace(source=Path(directory), binary=Path(directory) / 'missing', output=output)
            with patch.object(smoke.argparse.ArgumentParser, 'parse_args', return_value=args), \
                    patch.object(smoke.subprocess, 'Popen') as start:
                self.assertEqual(smoke.main(), 1)
            start.assert_not_called()
            evidence = json.loads((output / 'smoke.json').read_text())
            self.assertEqual(evidence['editor_smoke'], 'failed')
            self.assertIn('error', evidence)

    def test_existing_evidence_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            sentinel = output / 'smoke.json'
            sentinel.write_text('old evidence')
            args = argparse.Namespace(source=Path(directory), binary=output / 'missing', output=output)
            with patch.object(smoke.argparse.ArgumentParser, 'parse_args', return_value=args):
                with self.assertRaises(FileExistsError):
                    smoke.main()
            self.assertEqual(sentinel.read_text(), 'old evidence')

    def test_focus_mismatch_never_types_and_captures_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'fake-binary'
            binary.write_bytes(b'fixture')
            output = root / 'evidence'
            args = argparse.Namespace(source=root, binary=binary, output=output)
            process = Mock(pid=12345)
            process.poll.side_effect = [None, 0]
            commands = []
            def native_command(argv, **kwargs):
                commands.append(argv)
                return '99' if argv[-1] == 'getwindowfocus' else ''
            with patch.object(smoke.argparse.ArgumentParser, 'parse_args', return_value=args), \
                    patch.object(smoke, 'run', side_effect=native_command), \
                    patch.object(smoke, 'wait_for_text', return_value='observed expected UI'), \
                    patch.object(smoke.subprocess, 'Popen', return_value=process), \
                    patch.object(smoke.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '42\n')):
                self.assertEqual(smoke.main(), 1)
            self.assertFalse(any(c[:2] in [['xdotool', 'key'], ['xdotool', 'type']] for c in commands))
            self.assertTrue(any(c[-1].endswith('after-input.png') for c in commands))
            record = json.loads((output / 'smoke.json').read_text())
            self.assertIn('focus mismatch', record['error'])

    def test_managed_session_refuses_existing_evidence_before_start(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/managed_x11_smoke.sh'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '_build/smoke').mkdir(parents=True)
            log = root / '_build/openbox.log'
            log.write_text('keep previous evidence')
            result = subprocess.run(['bash', str(script)], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('already exists', result.stderr)
            self.assertEqual(log.read_text(), 'keep previous evidence')

    def test_unchanged_bytes_fail_and_capture_after_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'fake-binary'
            binary.write_bytes(b'fixture')
            output = root / 'evidence'
            args = argparse.Namespace(source=root, binary=binary, output=output)
            process = Mock(pid=12345)
            process.poll.side_effect = [None, 0]
            commands = []
            def native_command(argv, **kwargs):
                commands.append(argv)
                return '42' if argv[-1] == 'getwindowfocus' else ''
            with patch.object(smoke.argparse.ArgumentParser, 'parse_args', return_value=args), \
                    patch.object(smoke, 'run', side_effect=native_command), \
                    patch.object(smoke, 'wait_for_text', return_value='observed expected UI'), \
                    patch.object(smoke.time, 'monotonic', side_effect=[0, 1, 2, 20]), \
                    patch.object(smoke.subprocess, 'Popen', return_value=process), \
                    patch.object(smoke.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '42\n')):
                self.assertEqual(smoke.main(), 1)
            record = json.loads((output / 'smoke.json').read_text())
            self.assertEqual(record['editor_smoke'], 'failed')
            self.assertIn('saved file', record['error'])
            self.assertTrue(any(c[-1].endswith('after-input.png') for c in commands))

    def test_readiness_waits_for_observed_text_not_window_existence(self):
        observations = iter(['', 'Unrecognized Project\nStay in Restricted Mode'])
        def native_command(argv, **kwargs):
            return next(observations) if argv[0] == 'tesseract' else ''
        with patch.object(smoke, 'run', side_effect=native_command), \
                patch.object(smoke.time, 'sleep'):
            text = smoke.wait_for_text('42', Path('/fixture.png'),
                lambda value: 'unrecognized project' in value, 'modal')
        self.assertIn('Restricted Mode', text)

    def test_missing_readiness_fails_closed(self):
        with patch.object(smoke, 'run', return_value='unexpected dialog'), \
                patch.object(smoke.time, 'sleep'), \
                patch.object(smoke.time, 'monotonic', side_effect=[0, 1, 31]):
            with self.assertRaisesRegex(RuntimeError, 'not observed'):
                smoke.wait_for_text('42', Path('/fixture.png'),
                    lambda value: 'unrecognized project' in value, 'modal')
