import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'smoke_windows', Path(__file__).resolve().parents[1] / 'scripts/smoke_windows.py')
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def capture(blue=None, pink=None, *, left=100, top=200, dpi=96):
    colors = {}
    for name, rect in [('blue', blue), ('pink', pink)]:
        if rect is None:
            colors[name] = {'count': 0, 'left': 0, 'top': 0, 'right': 0, 'bottom': 0}
        else:
            x0, y0, x1, y1 = rect
            colors[name] = {
                'count': (x1 - x0 + 1) * (y1 - y0 + 1),
                'left': x0, 'top': y0, 'right': x1, 'bottom': y1,
            }
    return {'left': left, 'top': top, 'dpi': dpi, 'colors': colors}


class WindowsSceneEvidenceTests(unittest.TestCase):
    def test_scene_state_returns_solid_blue_and_pink_rectangles(self):
        self.assertEqual(
            smoke.scene_state(capture(blue=(10, 20, 129, 37))),
            ('blue', (10, 20, 129, 37)),
        )
        self.assertEqual(
            smoke.scene_state(capture(pink=(3, 7, 122, 24))),
            ('pink', (3, 7, 122, 24)),
        )

    def test_scene_disable_requires_both_colors_to_be_absent(self):
        self.assertTrue(smoke.scene_matches(capture(), None))
        self.assertFalse(smoke.scene_matches(capture(blue=(0, 0, 119, 17)), None))

    def test_both_scene_colors_in_one_capture_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'both native scene colors'):
            smoke.scene_state(capture(blue=(0, 0, 119, 17), pink=(0, 0, 119, 17)))

    def test_scattered_color_pixels_are_not_accepted_as_scene_evidence(self):
        value = capture(blue=(2, 3, 5, 3))
        value['colors']['blue']['count'] = 2
        with self.assertRaisesRegex(RuntimeError, 'not one solid bounded rectangle'):
            smoke.scene_state(value)

    def test_pointer_coordinates_use_the_captured_window_screen_origin(self):
        self.assertEqual(smoke.screen_point(capture(left=-24, top=91), (12, 8)), (-12, 99))

    def test_scene_geometry_accepts_one_dpi_scale_and_rejects_double_scaling(self):
        self.assertEqual(smoke.validate_scene_size((15, 22, 134, 39), 1.0), (120, 18))
        self.assertEqual(smoke.validate_scene_size((15, 22, 254, 57), 2.0), (240, 36))
        with self.assertRaisesRegex(RuntimeError, 'scaled exactly once'):
            smoke.validate_scene_size((15, 22, 254, 57), 1.0)

    def test_minimize_waits_until_the_native_press_acknowledgment_advances(self):
        class RunningProcess:
            @staticmethod
            def poll():
                return None

        log_before = 'MZed island press owned generation=4\n'
        log_after = log_before + 'MZed island press owned generation=4\n'
        with patch.object(smoke, 'read_native_log', side_effect=[log_before, log_after]):
            self.assertEqual(smoke.wait_for_press_ack(
                RunningProcess(), Path('unused'), previous_count=1, timeout=0.2), 4)

    def test_minimize_rejects_an_old_press_acknowledgment(self):
        class RunningProcess:
            @staticmethod
            def poll():
                return None

        log = 'MZed island press owned generation=4\n'
        with patch.object(smoke, 'read_native_log', return_value=log):
            with self.assertRaisesRegex(RuntimeError, 'did not acknowledge owned press'):
                smoke.wait_for_press_ack(RunningProcess(), Path('unused'), previous_count=1, timeout=0.01)

    def test_late_release_oracle_requires_unchanged_pixels_and_dispatches(self):
        snapshot = capture(pink=(0, 0, 119, 17))
        smoke.assert_scene_unchanged(snapshot, 'MZed island counter=2\n', 'pink', [2])
        with self.assertRaisesRegex(RuntimeError, 'changed native dispatches'):
            smoke.assert_scene_unchanged(
                snapshot, 'MZed island counter=2\nMZed island counter=3\n', 'pink', [2])
        with self.assertRaisesRegex(RuntimeError, 'changed scene'):
            smoke.assert_scene_unchanged(
                capture(blue=(0, 0, 119, 17)), 'MZed island counter=2\n', 'pink', [2])


if __name__ == '__main__':
    unittest.main()
