import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "smoke_macos", Path(__file__).resolve().parents[1] / "scripts/smoke_macos.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def evidence(*, blue=None, pink=None, log=""):
    return {
        "colors": {"blue": [] if blue is None else [blue],
                   "pink": [] if pink is None else [pink]},
        "log": log,
    }


def rectangle(x=11, y=23, width=120, height=18):
    return {"x": x, "y": y, "width": width, "height": height,
            "pixels": width * height}


class MacSceneEvidenceTests(unittest.TestCase):
    def test_scene_state_accepts_one_solid_blue_or_pink_component(self):
        self.assertEqual(smoke.scene_state(evidence(blue=rectangle())),
                         ("blue", (11, 23, 120, 18)))
        self.assertEqual(smoke.scene_state(evidence(pink=rectangle())),
                         ("pink", (11, 23, 120, 18)))
        self.assertEqual(smoke.scene_state(evidence()), ("off", None))

    def test_scene_rejects_both_colors_multiple_components_and_holes(self):
        with self.assertRaisesRegex(RuntimeError, "both native scene colors"):
            smoke.scene_state(evidence(blue=rectangle(), pink=rectangle()))
        multiple = evidence(blue=rectangle())
        multiple["colors"]["blue"].append(rectangle(200, 300))
        with self.assertRaisesRegex(RuntimeError, "expected one blue"):
            smoke.scene_state(multiple)
        holed = evidence(blue={**rectangle(), "pixels": 40})
        with self.assertRaisesRegex(RuntimeError, "not one solid"):
            smoke.scene_state(holed)

    def test_geometry_requires_exactly_once_logical_scene_dimensions(self):
        self.assertEqual(smoke.validate_scene_size((5, 7, 120, 18), 1.0), (120, 18))
        self.assertEqual(smoke.validate_scene_size((5, 7, 240, 36), 2.0), (120, 18))
        with self.assertRaisesRegex(RuntimeError, "scaled exactly once"):
            smoke.validate_scene_size((5, 7, 240, 36), 1.0)
        with self.assertRaisesRegex(RuntimeError, "inconsistent or invalid scale"):
            smoke.validate_scene_size((5, 7, 120, 18), 2.0, 1.0)

    def test_pointer_coordinates_use_capture_origin_and_scale(self):
        capture = {"x": -24.0, "y": 91.0, "scale_x": 2.0, "scale_y": 2.0}
        self.assertEqual(smoke.screen_point(capture, (12, 8)), (-18.0, 95.0))

    def test_late_release_requires_same_scene_and_dispatch_log(self):
        before = evidence(blue=rectangle(), log="MZed island counter=1\n")
        after = evidence(blue=rectangle(), log="MZed island counter=1\n")
        smoke.assert_scene_unchanged(before, after, "blue", [1], [1])
        with self.assertRaisesRegex(RuntimeError, "dispatch log changed"):
            smoke.assert_scene_unchanged(before,
                evidence(blue=rectangle(), log="MZed island counter=1\nMZed island counter=2\n"),
                "blue", [1], [1])
        with self.assertRaisesRegex(RuntimeError, "scene changed"):
            smoke.assert_scene_unchanged(before, evidence(pink=rectangle()), "blue", [1], [1])

    def test_dispatch_counter_parser_preserves_order_and_duplicates(self):
        log = ("MZed island counter=1\nMZed island disabled and native instance destroyed\n"
               "MZed island counter=2\nMZed island remounted\nMZed island counter=1\n")
        self.assertEqual(smoke.counters(log), [1, 2, 1])

    def test_dispatch_log_copy_keeps_only_native_contract_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = object.__new__(smoke.MacSmoke)
            capture.output = Path(directory)
            capture.record = {"dispatch_log_copies": []}
            log = ("unrelated editor path /private/user/project\n"
                   "INFO MZed island counter=1\n"
                   "INFO MZed island remounted\n")
            capture.save_dispatch_log("test-stage", log)
            entry = capture.record["dispatch_log_copies"][0]
            self.assertEqual(Path(entry["path"]).read_text(),
                             "INFO MZed island counter=1\nINFO MZed island remounted\n")
            self.assertEqual(entry["counters"], [1])

    def test_window_startup_poll_can_enumerate_before_activation(self):
        capture = object.__new__(smoke.MacSmoke)
        capture.helper = Path("unused")
        capture.pid = 123
        windows = [{"pid": 123, "window_id": 456, "title": "fixture.txt"}]
        with patch.object(smoke, "helper_json", return_value={"frontmost_pid": 1, "windows": windows}):
            self.assertEqual(capture.current_windows(require_frontmost=False), windows)
            with self.assertRaisesRegex(RuntimeError, "lost foreground ownership"):
                capture.current_windows()

    def test_fixture_window_selection_accepts_auxiliary_window_but_requires_unique_editor(self):
        editor = {"pid": 123, "window_id": 456,
                  "title": "fixture.txt — fixture.txt", "width": 1384, "height": 621,
                  "layer": 0, "visible_on_screen": True}
        auxiliary = {"pid": 123, "window_id": 455, "title": "",
                     "width": 234, "height": 202, "layer": 0, "visible_on_screen": True}
        self.assertEqual(smoke.select_fixture_editor([auxiliary, editor], "fixture.txt"), editor)
        with self.assertRaisesRegex(RuntimeError, "ambiguous visible fixture editor"):
            smoke.select_fixture_editor([editor, dict(editor, window_id=457)], "fixture.txt")
        self.assertIsNone(smoke.select_fixture_editor([auxiliary], "fixture.txt"))

    def test_window_identity_allows_auxiliary_window_but_rejects_id_churn(self):
        capture = object.__new__(smoke.MacSmoke)
        capture.pid = 123
        capture.window_id = 456
        capture.window_title = "fixture.txt — fixture.txt"
        capture.fixture = Path("fixture.txt")
        editor = {"pid": 123, "window_id": 456,
                  "title": "fixture.txt — fixture.txt", "width": 1384, "height": 621,
                  "layer": 0, "visible_on_screen": True}
        auxiliary = {"pid": 123, "window_id": 455, "title": "",
                     "width": 234, "height": 202, "layer": 0, "visible_on_screen": True}
        with patch.object(capture, "current_windows", return_value=[auxiliary, editor]):
            self.assertEqual(capture.assert_window_identity(), editor)
        with patch.object(capture, "current_windows", return_value=[auxiliary, dict(editor, window_id=457)]):
            with self.assertRaisesRegex(RuntimeError, "selected PID/window identity changed"):
                capture.assert_window_identity()

    def test_preflight_rejects_locked_or_non_console_session_before_ui(self):
        ready = {"accessibility": True, "screen_recording": True,
                 "session_locked": False, "on_console": True}
        self.assertIsNone(smoke.preflight_blocker(ready))
        self.assertIn("locked", smoke.preflight_blocker(dict(ready, session_locked=True)))
        self.assertIn("foreground", smoke.preflight_blocker(dict(ready, on_console=False)))
        self.assertIn("permissions", smoke.preflight_blocker(dict(ready, screen_recording=False)))


if __name__ == "__main__":
    unittest.main()
