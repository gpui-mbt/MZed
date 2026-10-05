import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import island_interactions as interaction


class PixelEvidenceTests(unittest.TestCase):
    def test_repeated_same_position_is_verified_without_waiting_for_motion(self):
        with patch.object(interaction, 'run', side_effect=['Absolute upper-left X: 10\nAbsolute upper-left Y: 20\nBorder width: 0\n', '', 'X=15\nY=27\n']) as run:
            interaction.move('42', (5, 7))
            self.assertEqual(run.call_args_list[1].args[0], ['xdotool', 'mousemove', '--window', '42', '5', '7'])
            self.assertEqual(run.call_args_list[2].args[0], ['xdotool', 'getmouselocation', '--shell'])

    def test_unreached_pointer_position_fails_closed(self):
        with patch.object(interaction, 'run', side_effect=['Absolute upper-left X: 10\nAbsolute upper-left Y: 20\nBorder width: 0\n', '', 'X=0\nY=0\n']), \
                patch.object(interaction.time, 'monotonic', side_effect=[0, 0, 3]), \
                patch.object(interaction.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'did not reach'):
                interaction.move('42', (5, 7))

    def test_unqualified_client_border_fails_before_motion(self):
        with patch.object(interaction, 'run', return_value='Absolute upper-left X: 10\nAbsolute upper-left Y: 20\nBorder width: 1\n') as run:
            with self.assertRaisesRegex(RuntimeError, 'zero-border'):
                interaction.move('42', (5, 7))
            self.assertEqual(run.call_count, 1)

    def test_restoration_waits_for_mapping_and_actual_focus(self):
        from unittest.mock import Mock
        process = Mock()
        process.poll.return_value = None
        with patch.object(interaction, 'run', side_effect=['Map State: IsUnMapped', 'window state: Iconic', 'Map State: IsViewable', 'window state: Normal', '42', '99', 'Map State: IsViewable', 'window state: Normal', '42', '42']), \
                patch.object(interaction.time, 'sleep'):
            interaction.wait_window_state('42', process, visible=True)
        self.assertEqual(process.poll.call_count, 3)

    def test_hidden_gate_observes_actual_unmapping(self):
        from unittest.mock import Mock
        process = Mock()
        process.poll.return_value = None
        with patch.object(interaction, 'run', side_effect=['Map State: IsViewable', 'window state: Normal', 'Map State: IsUnMapped', 'window state: Iconic']), \
                patch.object(interaction.time, 'sleep'):
            interaction.wait_window_state('42', process, visible=False)
        self.assertEqual(process.poll.call_count, 2)

    def test_editor_exit_is_not_window_readiness(self):
        from unittest.mock import Mock
        process = Mock()
        process.poll.return_value = -11
        with patch.object(interaction, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'editor exited.*-11'):
                interaction.wait_window_state('42', process, visible=True)
        run.assert_not_called()

    def test_temporary_capture_failure_is_not_absence(self):
        failure = interaction.subprocess.CalledProcessError(1, ['import'], stderr='Resource temporarily unavailable')
        diagnostics = {}
        with patch.object(interaction, 'rectangle', side_effect=[failure, None]) as capture, \
                patch.object(interaction.time, 'sleep'):
            self.assertIsNone(interaction.wait_rectangle('42', Path('.'), 'disabled', (40, 96, 160), False, diagnostics))
        self.assertEqual(capture.call_count, 2)
        self.assertEqual(diagnostics, {'disabled': 1})

    def test_persistent_capture_failure_cannot_pass_absence(self):
        failure = interaction.subprocess.CalledProcessError(1, ['import'], stderr='Resource temporarily unavailable')
        with patch.object(interaction, 'rectangle', side_effect=failure), \
                patch.object(interaction.time, 'monotonic', side_effect=[0, 0, 11]), \
                patch.object(interaction.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'last capture error'):
                interaction.wait_rectangle('42', Path('.'), 'disabled', (40, 96, 160), False)

    def test_empty_scene_is_not_success(self):
        with patch.object(interaction, 'run', side_effect=['', '4 4']), patch.object(
                interaction.subprocess, 'check_output', return_value=bytes(48)):
            self.assertIsNone(interaction.rectangle('42', Path('proof.png'), (40, 96, 160)))

    def test_requires_one_solid_rectangle(self):
        pixels = bytes((40, 96, 160)) * 50
        with patch.object(interaction, 'run', side_effect=['', '10 5']), patch.object(
                interaction.subprocess, 'check_output', return_value=pixels):
            self.assertEqual(interaction.rectangle('42', Path('proof.png'), (40, 96, 160)), (0, 0, 9, 4))

    def test_scattered_matching_pixels_are_not_a_control(self):
        pixels = bytes((40, 96, 160)) * 49 + bytes((0, 0, 0)) + bytes((40, 96, 160)) * 50
        with patch.object(interaction, 'run', side_effect=['', '10 10']), patch.object(
                interaction.subprocess, 'check_output', return_value=pixels):
            with self.assertRaisesRegex(RuntimeError, 'not one solid'):
                interaction.rectangle('42', Path('proof.png'), (40, 96, 160))

    def test_finds_process_owned_toplevels_without_title_filter(self):
        with patch.object(interaction, 'run', side_effect=['42 43 44', 'WM_STATE: window state: Normal',
                'WM_STATE: not found.', 'WM_STATE: window state: Normal']) as run:
            self.assertEqual(interaction.toplevels(123), ['42', '44'])
            self.assertNotIn('--name', run.call_args_list[0].args[0])


if __name__ == '__main__':
    unittest.main()

class NativeSmokeEvidenceTests(unittest.TestCase):
    def run_smoke(self, log_text):
        import argparse
        import json
        import tempfile
        from unittest.mock import Mock
        import smoke_island
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'fake-editor'
            binary.write_bytes(b'unit test; never native acceptance')
            output = root / 'evidence'
            process = Mock(pid=12345)
            process.poll.side_effect = [None, 0, 0]
            def start(*args, **kwargs):
                if log_text is not None:
                    logs = output / 'data/logs'
                    logs.mkdir(parents=True)
                    (logs / 'Zed.log').write_text(log_text)
                return process
            def command(argv, **kwargs):
                if argv[-1] == 'getwindowfocus':
                    return '42'
                if argv[-1] == 'ctrl+s':
                    (output / 'mzed-baseline-fixture.txt').write_text(
                        'MZed pinned baseline fixture\nMZed native edit save verified\n')
                return 'mocked evidence'
            def exercise(window, path, child, record):
                record['same_window_island'] = 'passed'
            args = argparse.Namespace(source=root, binary=binary, output=output)
            with patch.object(smoke_island.argparse.ArgumentParser, 'parse_args', return_value=args), \
                    patch.object(smoke_island, 'run', side_effect=command), \
                    patch.object(smoke_island, 'wait_for_text', return_value='mocked readiness'), \
                    patch.object(smoke_island, 'exercise', side_effect=exercise), \
                    patch.object(smoke_island.subprocess, 'Popen', side_effect=start), \
                    patch.object(smoke_island.subprocess, 'run', return_value=interaction.subprocess.CompletedProcess([], 0, '42\n')):
                result = smoke_island.main()
            return result, json.loads((output / 'smoke.json').read_text())

    def test_missing_native_log_cannot_pass_after_saved_editor(self):
        result, record = self.run_smoke(None)
        self.assertEqual(result, 1)
        self.assertEqual(record['editor_smoke'], 'failed')
        self.assertEqual(record['same_window_island'], 'failed')
        self.assertIn('error', record)

    def test_duplicate_dispatch_cannot_pass(self):
        result, record = self.run_smoke('MZed island counter=1\nMZed island counter=2\nMZed island counter=2\n')
        self.assertEqual(result, 1)
        self.assertIn('not exactly', record['error'])

    def test_exact_dispatch_and_lifecycle_required(self):
        result, record = self.run_smoke('MZed island counter=1\nMZed island counter=2\n'
            'MZed island disabled\nMZed island remounted\nMZed island counter=1\nMZed island disabled\n')
        self.assertEqual(result, 0)
        self.assertEqual(record['native_dispatches'], [1, 2, 1])
