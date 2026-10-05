import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import island_interactions as interaction
import smoke_island


class PixelEvidenceTests(unittest.TestCase):
    def test_intermediate_save_requires_exact_fixture_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'fixture.txt'
            expected = b'MZed pinned baseline fixture\nMZed native edit save verified\n'
            fixture.write_bytes(expected)
            self.assertEqual(interaction.wait_for_fixture_bytes(fixture, expected, 'checkpoint'), expected)
            fixture.write_bytes(expected + b'extra')
            with patch.object(interaction.time, 'monotonic', side_effect=[0, 0, 11]), \
                    patch.object(interaction.time, 'sleep'):
                with self.assertRaisesRegex(RuntimeError, 'did not match exactly'):
                    interaction.wait_for_fixture_bytes(fixture, expected, 'checkpoint')

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
    def run_smoke(self, log_text, scenario='normal'):
        import argparse
        import json
        import tempfile
        from unittest.mock import Mock
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
                    final = 'MZed pinned baseline fixture\nMZed native edit save verified\n'
                    if scenario == 'dispatch-rejection':
                        final += 'MZed post-failure remount verified\n'
                    (output / 'mzed-baseline-fixture.txt').write_text(final)
                return 'mocked evidence'
            def exercise(window, path, child, record):
                record['same_window_island'] = 'passed'
            def exercise_rejection(window, path, child, record):
                (output / 'mzed-baseline-fixture.txt').write_text(
                    'MZed pinned baseline fixture\nMZed native edit save verified\n')
                record['immediate_editor_fallback'] = {'expected_utf8':
                    'MZed pinned baseline fixture\nMZed native edit save verified\n', 'passed': True}
            args = argparse.Namespace(source=root, binary=binary, output=output, scenario=scenario)
            with patch.object(smoke_island.argparse.ArgumentParser, 'parse_args', return_value=args), \
                    patch.object(smoke_island, 'run', side_effect=command), \
                    patch.object(smoke_island, 'wait_for_text', return_value='mocked readiness'), \
                    patch.object(smoke_island, 'exercise', side_effect=exercise), \
                    patch.object(smoke_island, 'exercise_dispatch_rejection', side_effect=exercise_rejection) as rejection, \
                    patch.object(smoke_island.subprocess, 'Popen', side_effect=start), \
                    patch.object(smoke_island.subprocess, 'run', return_value=interaction.subprocess.CompletedProcess([], 0, '42\n')):
                result = smoke_island.main()
            return result, json.loads((output / 'smoke.json').read_text()), rejection.call_count

    def test_missing_native_log_cannot_pass_after_saved_editor(self):
        result, record, _ = self.run_smoke(None)
        self.assertEqual(result, 1)
        self.assertEqual(record['editor_smoke'], 'failed')
        self.assertEqual(record['same_window_island'], 'failed')
        self.assertIn('error', record)

    def test_duplicate_dispatch_cannot_pass(self):
        result, record, _ = self.run_smoke('MZed island counter=1\nMZed island counter=2\nMZed island counter=2\n')
        self.assertEqual(result, 1)
        self.assertIn('exact event sequence', record['error'])

    def test_exact_dispatch_and_lifecycle_required(self):
        result, record, _ = self.run_smoke('MZed island mounted: gpui.mbt bf965ae, copied scene v1\n'
            'MZed island counter=1\nMZed island counter=2\n'
            'MZed island disabled and native instance destroyed\nMZed island remounted\n'
            'MZed island counter=1\nMZed island disabled and native instance destroyed\n')
        self.assertEqual(result, 0)
        self.assertEqual(record['native_dispatches'], [1, 2, 1])

    def test_dispatch_rejection_selects_scenario_and_checks_final_save(self):
        log = ('MZed island mounted: gpui.mbt bf965ae, copied scene v1\n'
            'MZed island dispatch rejection probe\n'
            'MZed island dispatch failed: -8; disabling\n'
            'MZed island counter=1\nMZed island remounted\n'
            'MZed island disabled and native instance destroyed\n')
        result, record, rejection_calls = self.run_smoke(log, 'dispatch-rejection')
        self.assertEqual(result, 0)
        self.assertEqual(rejection_calls, 1)
        self.assertEqual(record['scenario'], 'dispatch-rejection')
        self.assertEqual(record['probe_setting'], 'reject-first-increment')
        self.assertEqual(record['immediate_editor_fallback']['expected_utf8'],
            'MZed pinned baseline fixture\nMZed native edit save verified\n')
        self.assertEqual(record['post_remount_save']['expected_utf8'],
            'MZed pinned baseline fixture\nMZed native edit save verified\nMZed post-failure remount verified\n')


class ScenarioLogTests(unittest.TestCase):
    def rejection_log(self):
        return ('MZed island mounted: gpui.mbt bf965ae, copied scene v1\n'
            'MZed island dispatch rejection probe\n'
            'MZed island dispatch failed: -8; disabling\n'
            'MZed island counter=1\nMZed island remounted\n'
            'MZed island disabled and native instance destroyed\n')

    def test_exact_rejection_log_passes(self):
        record = smoke_island.validate_native_log('dispatch-rejection', self.rejection_log())
        self.assertEqual(record['native_dispatches'], [1])
        self.assertEqual(record['native_diagnostics'], ['MZed island dispatch failed: -8; disabling'])

    def test_missing_rejection_error_fails(self):
        log = self.rejection_log().replace('MZed island dispatch failed: -8; disabling\n', '')
        with self.assertRaisesRegex(RuntimeError, 'exact event sequence'):
            smoke_island.validate_native_log('dispatch-rejection', log)

    def test_wrong_rejection_status_fails(self):
        log = self.rejection_log().replace('dispatch failed: -8', 'dispatch failed: -9')
        with self.assertRaisesRegex(RuntimeError, 'exact event sequence'):
            smoke_island.validate_native_log('dispatch-rejection', log)

    def test_duplicate_rejection_error_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'exact event sequence'):
            smoke_island.validate_native_log('dispatch-rejection', self.rejection_log() +
                'MZed island dispatch failed: -8; disabling\n')

    def test_unexpected_native_failure_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'exact event sequence'):
            smoke_island.validate_native_log('dispatch-rejection', self.rejection_log() +
                'MZed island scene failed: -22; disabling\n')

    def test_wrong_counter_sequence_fails(self):
        log = self.rejection_log().replace('MZed island counter=1', 'MZed island counter=2')
        with self.assertRaisesRegex(RuntimeError, 'exact event sequence'):
            smoke_island.validate_native_log('dispatch-rejection', log)

    def test_destroy_rejection_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'exact event sequence'):
            smoke_island.validate_native_log('dispatch-rejection', self.rejection_log() +
                'MZed native destroy rejected: -6\n')

    def test_normal_smoke_sanitizes_inherited_probe(self):
        inherited = {'MZED_NATIVE_ISLAND_PROBE': 'reject-first-increment', 'PATH': '/bin'}
        normal = smoke_island.scenario_environment(inherited, 'normal')
        self.assertNotIn('MZED_NATIVE_ISLAND_PROBE', normal)
        self.assertEqual(normal['PATH'], '/bin')
        rejection = smoke_island.scenario_environment(inherited, 'dispatch-rejection')
        self.assertEqual(rejection['MZED_NATIVE_ISLAND_PROBE'], 'reject-first-increment')
