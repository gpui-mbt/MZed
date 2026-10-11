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

    def test_wait_for_counters_waits_for_expected_sequence_to_arrive(self):
        capture = object.__new__(smoke.MacSmoke)
        with patch.object(capture, "read_log", side_effect=["", "MZed island counter=1\n"]), \
                patch.object(smoke.time, "sleep"):
            self.assertEqual(capture.wait_for_counters([1], timeout=1), "MZed island counter=1\n")
        with patch.object(capture, "read_log", side_effect=[
                "MZed island counter=1\n",
                "MZed island counter=1\nMZed island counter=2\n"]), \
                patch.object(smoke.time, "sleep"):
            self.assertEqual(capture.wait_for_counters([1, 2], timeout=1),
                             "MZed island counter=1\nMZed island counter=2\n")

    def test_wait_for_counters_rejects_wrong_prefix_and_extra_dispatch(self):
        capture = object.__new__(smoke.MacSmoke)
        with patch.object(capture, "read_log", return_value="MZed island counter=2\n"):
            with self.assertRaisesRegex(RuntimeError, r"expected \[1\], got \[2\]"):
                capture.wait_for_counters([1], timeout=1)
        with patch.object(capture, "read_log", return_value="MZed island counter=1\nMZed island counter=2\n"):
            with self.assertRaisesRegex(RuntimeError, r"expected \[1\], got \[1, 2\]"):
                capture.wait_for_counters([1], timeout=1)

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

    def test_accessibility_state_is_bound_to_the_selected_frontmost_fixture(self):
        capture, _window, _image = event_controller()
        state = {"pid": 123, "selected_window_id": 456, "frontmost_pid": 123,
                 "focused_window": {"window_id": 455, "title": ""},
                 "main_window": {"window_id": 456, "title": "fixture.txt — fixture.txt"}}
        with patch.object(smoke, "helper_json", return_value=state) as helper:
            self.assertEqual(capture.accessibility_state(), state)
        self.assertEqual(helper.call_args.args[1:], ("ax-state", 123, 456, "fixture.txt"))
        with patch.object(smoke, "helper_json", return_value=dict(state, selected_window_id=457)):
            with self.assertRaisesRegex(RuntimeError, "does not identify the active fixture window"):
                capture.accessibility_state()

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
        self.assertIn("state is unavailable", smoke.preflight_blocker(dict(ready, session_locked=None)))
        self.assertIn("state is unavailable", smoke.preflight_blocker(
            {key: value for key, value in ready.items() if key != "session_locked"}))
        self.assertIn("foreground", smoke.preflight_blocker(dict(ready, on_console=False)))
        self.assertIn("permissions", smoke.preflight_blocker(dict(ready, screen_recording=False)))

    def test_failed_activation_records_fresh_read_only_frontmost_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            binary = output / "mzed"
            binary.write_bytes(b"fixture binary")
            source = output / "source"
            source.mkdir()
            capture = smoke.MacSmoke(Path("helper"), output, "baseline", binary,
                                     source, False, 1)
            capture.window_id = 456
            fake_process = type("Process", (), {"pid": 123})()
            windows = [{"pid": 123, "window_id": 456, "title": "baseline-fixture.txt"}]
            with patch.object(smoke.subprocess, "Popen", return_value=fake_process), \
                    patch.object(capture, "wait_for_window"), \
                    patch.object(smoke, "helper_json", side_effect=[
                        {"activation_completed": False, "frontmost_pid": 10,
                         "activation_stage": "AXFrontmost"},
                        {"frontmost_pid": 10, "windows": windows},
                    ]) as helper:
                with self.assertRaisesRegex(RuntimeError, "fresh windows observation"):
                    capture.launch()

            self.assertEqual(helper.call_count, 2)
            self.assertEqual(helper.call_args_list[1].args[1:], ("windows", 123))
            self.assertEqual(capture.record["activation_fresh_observation"], {
                "frontmost_pid": 10, "windows": windows})


def event_controller():
    capture = object.__new__(smoke.MacSmoke)
    capture.helper = Path("injected-helper")
    capture.pid = 123
    capture.window_id = 456
    capture.fixture = Path("fixture.txt")
    capture.window_title = "fixture.txt — fixture.txt"
    capture.owned_input = None
    capture.record = {"input_cleanup": [], "window_event_checks": [], "input_event_acks": []}
    window = {"pid": 123, "window_id": 456, "title": capture.window_title,
              "x": 10.0, "y": 20.0, "width": 800.0, "height": 600.0,
              "layer": 0, "visible_on_screen": True}
    image = dict(window, scale_x=2.0, scale_y=2.0, pixel_width=1600, pixel_height=1200)
    return capture, window, image


def injected_native_helper(window, *, after_event=None):
    """Inject the native pre-event guard and separate PID-scoped cleanup emitter."""
    state = {"frontmost_pid": 123, "window_id": 456, "fixture_visible": True,
             "session_locked": False, "on_console": True}
    emitted = []
    cleanup_attempts = []

    def helper(_binary, *arguments, **_kwargs):
        command = arguments[0]
        if command == "windows":
            # Model the ordinary enumeration result racing with the native
            # per-event guard: it still reports the prior frontmost snapshot.
            return {"frontmost_pid": 123, "windows": [window]}
        if command in ("mouse-event", "key-event", "text-event"):
            if (state["session_locked"] or not state["on_console"]
                    or state["frontmost_pid"] != 123 or state["window_id"] != 456
                    or not state["fixture_visible"]):
                raise RuntimeError("injected native per-event guard refused the global event")
            action = arguments[4]
            payload = arguments[5:]
            emitted.append((command, action, *payload))
            if after_event is not None:
                after_event(state, command, action, payload)
            result = {"posted": True, "event_flags_before_post_raw": 0,
                      "event_flags_raw": 0, "event_flags_after_post_raw": 0}
            if command in ("key-event", "text-event"):
                result["sender_runloop_settle_requested_ms"] = 100
                result["sender_runloop_settle_ms"] = 100
            return result
        if command in ("cleanup-mouse", "cleanup-key", "cleanup-text"):
            cleanup_attempts.append(arguments)
            if state["session_locked"] or not state["on_console"]:
                raise RuntimeError("injected cleanup guard refused release while locked/off-console")
            result = {"posted_to_target_pid": True, "event_flags_before_post_raw": 0,
                      "event_flags_raw": 0, "event_flags_after_post_raw": 0}
            if command in ("cleanup-key", "cleanup-text"):
                result["sender_runloop_settle_requested_ms"] = 100
                result["sender_runloop_settle_ms"] = 100
            return result
        raise AssertionError(f"unexpected helper command: {arguments!r}")

    return state, emitted, cleanup_attempts, helper


class MacInputGuardTests(unittest.TestCase):
    def test_keyboard_sender_settle_requires_measured_minimum(self):
        smoke.require_keyboard_sender_settle({
            "sender_runloop_settle_requested_ms": 100,
            "sender_runloop_settle_ms": 90,
        }, "key-up")
        invalid = (
            {"sender_runloop_settle_requested_ms": 100},
            {"sender_runloop_settle_requested_ms": 100, "sender_runloop_settle_ms": 89},
            {"sender_runloop_settle_requested_ms": 100, "sender_runloop_settle_ms": True},
            {"sender_runloop_settle_requested_ms": 100, "sender_runloop_settle_ms": 90.0},
            {"sender_runloop_settle_requested_ms": 99, "sender_runloop_settle_ms": 100},
        )
        for result in invalid:
            with self.subTest(result=result), self.assertRaisesRegex(
                    RuntimeError, "lacks the required keyboard sender RunLoop settle"):
                smoke.require_keyboard_sender_settle(result, "key-up")

    def test_plain_input_ack_rejects_inherited_command_and_control_flags(self):
        smoke.require_unmodified_input_flags({"event_flags_before_post_raw": 0,
                                              "event_flags_after_post_raw": 0x20000000},
                                             "Unicode event")
        with self.assertRaisesRegex(RuntimeError, "retained synthetic modifier flags: 538181632"):
            smoke.require_unmodified_input_flags({"event_flags_before_post_raw": 0x20140000,
                                                  "event_flags_after_post_raw": 0x20140000},
                                                 "Unicode event")

    def test_post_annotation_does_not_fail_mouse_or_unicode_acknowledgements(self):
        capture, window, image = event_controller()
        _state, emitted, _cleanup_attempts, injected = injected_native_helper(window)

        def post_annotated(_binary, *arguments, **_kwargs):
            result = injected(_binary, *arguments)
            if arguments[0] in ("mouse-event", "text-event"):
                return {**result,
                        "event_flags_before_post_raw": 0,
                        "event_flags_raw": 0,
                        "event_flags_after_post_raw": 0x20000000}
            return result

        with patch.object(smoke, "helper_json", side_effect=post_annotated):
            capture.mouse("down", image, (5.0, 6.0))
            capture.mouse("up", image, (5.0, 6.0))
            capture.type_text("a")

        self.assertIsNone(capture.owned_input)
        self.assertEqual([event[:2] for event in emitted], [
            ("mouse-event", "down"), ("mouse-event", "up"),
            ("text-event", "down"), ("text-event", "up"),
        ])
        acknowledgements = capture.record["input_event_acks"]
        self.assertTrue(all(item["result"]["event_flags_before_post_raw"] == 0
                            and item["result"]["event_flags_after_post_raw"] == 0x20000000
                            for item in acknowledgements))

    def test_mouse_with_inherited_modifiers_is_released_and_fails_closed(self):
        capture, window, image = event_controller()
        _state, emitted, cleanup_attempts, injected = injected_native_helper(window)

        def contaminated_mouse(_binary, *arguments, **_kwargs):
            result = injected(_binary, *arguments)
            if arguments[0] == "mouse-event":
                return {**result, "event_flags_before_post_raw": 0x20140000,
                        "event_flags_raw": 0x20140000,
                        "event_flags_after_post_raw": 0x20140000}
            return result

        with patch.object(smoke, "helper_json", side_effect=contaminated_mouse):
            with self.assertRaisesRegex(RuntimeError, "plain mouse event retained synthetic modifier flags"):
                capture.mouse("down", image, (5.0, 6.0))

        self.assertEqual([event[:2] for event in emitted], [("mouse-event", "down")])
        self.assertEqual(cleanup_attempts[0][0], "cleanup-mouse")
        self.assertEqual(capture.record["input_cleanup"][0]["outcome"],
                         "release_posted_to_original_pid")
        self.assertIsNone(capture.owned_input)

    def test_unicode_with_inherited_modifiers_is_released_and_fails_closed(self):
        capture, window, _ = event_controller()
        _state, emitted, cleanup_attempts, injected = injected_native_helper(window)

        def contaminated_text(_binary, *arguments, **_kwargs):
            result = injected(_binary, *arguments)
            if arguments[0] == "text-event":
                return {**result, "event_flags_before_post_raw": 0x20140000,
                        "event_flags_raw": 0x20140000,
                        "event_flags_after_post_raw": 0x20140000}
            return result

        with patch.object(smoke, "helper_json", side_effect=contaminated_text):
            with self.assertRaisesRegex(RuntimeError, "plain Unicode event retained synthetic modifier flags"):
                capture.type_text("a")

        self.assertEqual(emitted, [("text-event", "down", "a")])
        self.assertEqual(cleanup_attempts[0][0], "cleanup-text")
        self.assertEqual(capture.record["input_cleanup"][0]["outcome"],
                         "release_posted_to_original_pid")
        self.assertIsNone(capture.owned_input)

    def test_mouse_down_ack_failure_still_attempts_targeted_release(self):
        capture, window, image = event_controller()

        def lose_ack_after_down(_state, command, action, _payload):
            if command == "mouse-event" and action == "down":
                raise RuntimeError("helper acknowledgement was lost after mouse down")

        _state, emitted, cleanup_attempts, injected = injected_native_helper(
            window, after_event=lose_ack_after_down)
        with patch.object(smoke, "helper_json", side_effect=injected):
            with self.assertRaisesRegex(RuntimeError, "acknowledgement was lost"):
                capture.mouse("down", image, (5.0, 6.0))

        self.assertEqual([event[:2] for event in emitted], [("mouse-event", "down")])
        self.assertEqual(cleanup_attempts[0][0], "cleanup-mouse")
        self.assertEqual(capture.record["input_cleanup"][0]["outcome"],
                         "release_posted_to_original_pid")
        self.assertEqual(capture.record["input_cleanup"][0]["ownership_state"],
                         "down_pending_ack")
        self.assertIsNone(capture.owned_input)

    def test_key_down_ack_failure_still_attempts_targeted_release(self):
        capture, window, _ = event_controller()

        def lose_ack_after_down(_state, command, action, _payload):
            if command == "key-event" and action == "down":
                raise RuntimeError("helper acknowledgement was lost after key down")

        _state, emitted, cleanup_attempts, injected = injected_native_helper(
            window, after_event=lose_ack_after_down)
        with patch.object(smoke, "helper_json", side_effect=injected):
            with self.assertRaisesRegex(RuntimeError, "acknowledgement was lost"):
                capture.key(1, "command")

        self.assertEqual([event[:2] for event in emitted], [("key-event", "down")])
        self.assertEqual(cleanup_attempts, [("cleanup-key", 123, 1, "command")])
        self.assertEqual(capture.record["input_cleanup"][0]["outcome"],
                         "release_posted_to_original_pid")
        self.assertEqual(capture.record["input_cleanup"][0]["ownership_state"],
                         "down_pending_ack")
        self.assertIsNone(capture.owned_input)

    def test_keyboard_ack_without_settle_fails_and_releases_pending_key(self):
        capture, window, _ = event_controller()
        _state, emitted, cleanup_attempts, injected = injected_native_helper(window)

        def lose_settle(_binary, *arguments, **kwargs):
            result = injected(_binary, *arguments, **kwargs)
            if arguments[0] == "key-event" and arguments[4] == "down":
                return {key: value for key, value in result.items()
                        if key != "sender_runloop_settle_ms"}
            return result

        with patch.object(smoke, "helper_json", side_effect=lose_settle):
            with self.assertRaisesRegex(RuntimeError, "lacks the required keyboard sender RunLoop settle"):
                capture.key(1)

        self.assertEqual(emitted, [("key-event", "down", 1, "none")])
        self.assertEqual(cleanup_attempts, [("cleanup-key", 123, 1, "none")])
        self.assertEqual(capture.record["input_cleanup"][0]["outcome"],
                         "release_posted_to_original_pid")
        self.assertIsNone(capture.owned_input)

    def test_targeted_keyboard_cleanup_requires_sender_settle(self):
        capture, window, _ = event_controller()
        _state, _emitted, cleanup_attempts, injected = injected_native_helper(window)
        capture.owned_input = {"kind": "key", "pid": 123, "window_id": 456,
                               "key_code": 1, "flags": "none",
                               "state": "down_pending_ack"}

        def lose_cleanup_settle(_binary, *arguments, **kwargs):
            result = injected(_binary, *arguments, **kwargs)
            if arguments[0] == "cleanup-key":
                return {key: value for key, value in result.items()
                        if key != "sender_runloop_settle_ms"}
            return result

        with patch.object(smoke, "helper_json", side_effect=lose_cleanup_settle):
            error = capture.release_owned_input("test missing cleanup settle")

        self.assertIn("lacks the required keyboard sender RunLoop settle", error)
        self.assertEqual(cleanup_attempts, [("cleanup-key", 123, 1, "none")])
        self.assertEqual(capture.record["input_cleanup"][0]["outcome"], "failed")
        self.assertIsNone(capture.owned_input)

    def test_key_can_bind_down_event_to_expected_ax_focus(self):
        capture, window, _ = event_controller()
        with patch.object(capture, "checked_window_identity", return_value=window), \
                patch.object(smoke, "helper_json", return_value={
                    "posted": True, "sender_runloop_settle_requested_ms": 100,
                    "sender_runloop_settle_ms": 100,
                }) as helper, \
                patch.object(smoke.time, "sleep"):
            capture.key(53, expected_focused_window_id=455)

        calls = [call.args[1:] for call in helper.call_args_list]
        self.assertEqual(calls[0], ("key-event", 123, 456, "fixture.txt", "down", 53, "none", 455))
        self.assertEqual(calls[1], ("key-event", 123, 456, "fixture.txt", "up", 53, "none"))

    def test_move_notice_ax_press_fails_closed_when_focus_is_not_its_sheet(self):
        capture, _window, _ = event_controller()
        startup_ocr = [{"text": "Move Zed to Applications?"}, {"text": "No"},
                       {"text": "Unrecognized Project"}]
        state = {"focused_window": {"window_id": 456, "role": "AXWindow"},
                 "focused_buttons_complete": True}
        with patch.object(capture, "capture", return_value={"path": "selected-window.png"}), \
                patch.object(capture, "accessibility_state", return_value=state), \
                patch.object(smoke, "helper_json", return_value={"observations": startup_ocr}) as helper:
            with self.assertRaisesRegex(RuntimeError, "not the focused same-process AX sheet"):
                capture.dismiss_move_to_applications_notice(startup_ocr)

        self.assertEqual(helper.call_count, 1)
        self.assertEqual(helper.call_args.args[1:], ("ocr", "selected-window.png"))
        self.assertNotIn("move_to_applications_notice", capture.record)

    def test_move_notice_ax_press_refuses_incomplete_button_tree(self):
        capture, _window, _ = event_controller()
        capture.record["auxiliary_windows"] = [{
            "window_id": 455, "title": "", "role": "untitled auxiliary process-owned window",
        }]
        startup_ocr = [{"text": "Move Zed to Applications?"}, {"text": "No"},
                       {"text": "Unrecognized Project"}]
        state = {
            "focused_window": {"window_id": 455, "role": "AXSheet"},
            "focused_buttons": [{"title": "No", "enabled": True}],
            "focused_buttons_complete": False,
        }
        observations = [{"text": "Move Zed to Applications?"}, {"text": "No"}]
        with patch.object(capture, "capture", return_value={"path": "selected-window.png"}), \
                patch.object(capture, "accessibility_state", return_value=state), \
                patch.object(capture, "current_windows", return_value=[{
                    "window_id": 455, "title": "",
                }]), \
                patch.object(smoke, "helper_json", return_value={"observations": observations}) as helper:
            with self.assertRaisesRegex(RuntimeError, "button tree is incomplete"):
                capture.dismiss_move_to_applications_notice(startup_ocr)

        self.assertEqual(helper.call_args.args[1:], ("ocr", "selected-window.png"))
        self.assertNotIn("move_to_applications_notice", capture.record)

    def test_text_down_ack_failure_still_attempts_targeted_release(self):
        capture, window, _ = event_controller()

        def lose_ack_after_down(_state, command, action, _payload):
            if command == "text-event" and action == "down":
                raise RuntimeError("helper acknowledgement was lost after text down")

        _state, emitted, cleanup_attempts, injected = injected_native_helper(
            window, after_event=lose_ack_after_down)
        with patch.object(smoke, "helper_json", side_effect=injected):
            with self.assertRaisesRegex(RuntimeError, "acknowledgement was lost"):
                capture.type_text("a")

        self.assertEqual(emitted, [("text-event", "down", "a")])
        self.assertEqual(cleanup_attempts, [("cleanup-text", 123, "a")])
        self.assertEqual(capture.record["input_cleanup"][0]["outcome"],
                         "release_posted_to_original_pid")
        self.assertEqual(capture.record["input_cleanup"][0]["ownership_state"],
                         "down_pending_ack")
        self.assertIsNone(capture.owned_input)

    def test_focus_loss_between_key_down_up_denies_global_up_and_targets_cleanup(self):
        capture, window, _ = event_controller()

        def lose_focus_after_down(state, command, action, _payload):
            if command == "key-event" and action == "down":
                state["frontmost_pid"] = 999

        _state, emitted, cleanup_attempts, injected = injected_native_helper(
            window, after_event=lose_focus_after_down)
        with patch.object(smoke, "helper_json", side_effect=injected):
            with self.assertRaisesRegex(RuntimeError, "native per-event guard refused"):
                capture.key(1, "command")

        self.assertEqual([event[:2] for event in emitted], [("key-event", "down")])
        self.assertEqual(cleanup_attempts, [("cleanup-key", 123, 1, "command")])
        self.assertEqual(capture.record["input_cleanup"][0]["outcome"],
                         "release_posted_to_original_pid")
        self.assertIsNone(capture.owned_input)

    def test_lock_between_mouse_down_up_blocks_global_up_and_records_cleanup_failure(self):
        capture, window, image = event_controller()

        def lock_after_down(state, command, action, _payload):
            if command == "mouse-event" and action == "down":
                state["session_locked"] = True

        _state, emitted, cleanup_attempts, injected = injected_native_helper(
            window, after_event=lock_after_down)
        with patch.object(smoke, "helper_json", side_effect=injected):
            capture.mouse("down", image, (5.0, 6.0))
            with self.assertRaisesRegex(RuntimeError, "targeted input release also failed"):
                capture.mouse("up", image, (5.0, 6.0))

        self.assertEqual([event[:2] for event in emitted], [("mouse-event", "down")])
        self.assertEqual(len(cleanup_attempts), 1)
        self.assertEqual(cleanup_attempts[0][0:1], ("cleanup-mouse",))
        self.assertEqual(capture.record["input_cleanup"][0]["outcome"], "failed")
        self.assertEqual(capture.record["input_cleanup"][0]["ownership_state"],
                         "down_acknowledged")
        self.assertIn("locked/off-console", capture.record["input_cleanup"][0]["error"])
        self.assertIsNone(capture.owned_input)

    def test_window_identity_loss_between_text_characters_blocks_later_global_text(self):
        capture, window, _ = event_controller()

        def lose_window_after_first_character(state, command, action, payload):
            if command == "text-event" and action == "up" and payload == ("a",):
                state["window_id"] = 999

        _state, emitted, cleanup_attempts, injected = injected_native_helper(
            window, after_event=lose_window_after_first_character)
        with patch.object(smoke, "helper_json", side_effect=injected):
            with self.assertRaisesRegex(RuntimeError, "native per-event guard refused"):
                capture.type_text("ab")

        self.assertEqual(emitted, [("text-event", "down", "a"),
                                   ("text-event", "up", "a")])
        self.assertEqual(cleanup_attempts, [("cleanup-text", 123, "b")])
        self.assertEqual(capture.record["input_cleanup"][0]["outcome"],
                         "release_posted_to_original_pid")
        self.assertEqual(capture.record["input_cleanup"][0]["ownership_state"],
                         "down_pending_ack")
        self.assertIsNone(capture.owned_input)


class MacEditSaveOrderingTests(unittest.TestCase):
    def test_editor_waits_for_typed_text_visibility_before_command_save(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.txt"
            fixture.write_text(smoke.FIXTURE)
            capture = object.__new__(smoke.MacSmoke)
            capture.fixture = fixture
            capture.window_id = 456
            capture.record = {"snapshots": [{"x": 10, "y": 20, "scale_x": 2,
                                             "scale_y": 2}], "operations": []}
            actions = []

            def wait_for_ocr(stage, predicate, timeout=45):
                actions.append(("wait", stage))
                if stage == "ready-editor":
                    observations = [{"text": "MZed pinned baseline fixture",
                                     "x": 12, "y": 24, "width": 240, "height": 20}]
                    observed_text = "mzed pinned baseline fixture"
                else:
                    observations = [{"text": smoke.ADDITION}]
                    observed_text = smoke.ADDITION.casefold()
                    self.assertFalse(predicate("mzed native edit save verifed"))
                if not predicate(observed_text):
                    raise AssertionError(f"unexpected OCR predicate for {stage}")
                return observations

            def key(key_code, flags, *, expected_focused_window_id=None):
                actions.append(("key", key_code, flags, expected_focused_window_id))
                if key_code == 1 and flags == "command":
                    fixture.write_text(smoke.EXPECTED_FILE)

            capture.wait_for_ocr = wait_for_ocr
            capture.click = lambda _capture, _point: actions.append(("click",))
            capture.key = key
            capture.type_text = lambda text: actions.append(("type", text))
            capture.capture = lambda stage: actions.append(("capture", stage))

            capture.edit_and_save()

            visible = actions.index(("wait", "typed-text-visible"))
            self.assertIn(("key", 125, "command", 456), actions)
            save = actions.index(("key", 1, "command", 456))
            self.assertLess(visible, save)
            self.assertEqual(fixture.read_text(), smoke.EXPECTED_FILE)
            self.assertEqual(capture.record["typed_text_visible"], [smoke.ADDITION])


if __name__ == "__main__":
    unittest.main()
