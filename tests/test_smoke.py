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
                self.assertTrue(config['ensure_final_newline_on_save'])
                return process
            def native_command(argv, **kwargs):
                if argv[-1] == 'ctrl+s':
                    (output / 'mzed-baseline-fixture.txt').write_text(
                        'MZed pinned baseline fixture\nMZed native edit save verified\n')
                return 'mocked harness output'
            args = argparse.Namespace(binary=binary, output=output)
            with patch.object(smoke.argparse.ArgumentParser, 'parse_args', return_value=args), \
                    patch.object(smoke, 'run', side_effect=native_command), \
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
            args = argparse.Namespace(binary=Path(directory) / 'missing', output=output)
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
            args = argparse.Namespace(binary=output / 'missing', output=output)
            with patch.object(smoke.argparse.ArgumentParser, 'parse_args', return_value=args):
                with self.assertRaises(FileExistsError):
                    smoke.main()
            self.assertEqual(sentinel.read_text(), 'old evidence')
