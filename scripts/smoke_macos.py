#!/usr/bin/env python3
"""Local macOS same-window editor/island qualification; hosted CI stays ABI-only."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

BLUE = (40, 96, 160)
PINK = (200, 96, 160)
SCENE_LOGICAL_SIZE = (120, 18)
FIXTURE = "MZed pinned baseline fixture\n"
ADDITION = "MZed native edit save verified"
EXPECTED_FILE = FIXTURE + ADDITION + "\n"
COUNTER_RE = re.compile(r"MZed island counter=(\d+)")
FAILURE_MARKERS = (
    "MZed island create failed",
    "MZed island remount failed",
    "MZed island scene failed",
    "MZed island dispatch failed",
)
EDITOR_MINIMUM_SIZE = (700, 500)
WINDOW_SIZE_TOLERANCE = 2


def run(argv: list[str], *, timeout: float = 30, cwd: Path | None = None,
        env: dict[str, str] | None = None) -> str:
    result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True,
                            timeout=timeout)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"command failed ({result.returncode}): {argv!r}: {detail[-2000:]}")
    return result.stdout.strip()


def helper_json(binary: Path, *arguments: object, timeout: float = 30) -> dict:
    output = run([str(binary), *(str(value) for value in arguments)], timeout=timeout)
    try:
        result = json.loads(output)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"macOS helper returned invalid JSON for {arguments!r}: {output[:1000]}") from error
    if not isinstance(result, dict):
        raise RuntimeError(f"macOS helper returned a non-object for {arguments!r}")
    return result


def color_components(binary: Path, image: Path, color: tuple[int, int, int]) -> list[dict]:
    result = helper_json(binary, "colors", image, *color)
    components = result.get("components")
    if not isinstance(components, list) or any(not isinstance(item, dict) for item in components):
        raise RuntimeError(f"invalid color component evidence: {result!r}")
    return components


def scene_state(snapshot: dict) -> tuple[str, tuple[int, int, int, int] | None]:
    blue = snapshot["colors"]["blue"]
    pink = snapshot["colors"]["pink"]
    if blue and pink:
        raise RuntimeError("both native scene colors appear in one window capture")
    if not blue and not pink:
        return "off", None
    color, components = ("blue", blue) if blue else ("pink", pink)
    if len(components) != 1:
        raise RuntimeError(f"expected one {color} scene component, got {components!r}")
    item = components[0]
    rect = (item["x"], item["y"], item["width"], item["height"])
    if item["pixels"] != item["width"] * item["height"]:
        raise RuntimeError(f"{color} pixels are not one solid bounded rectangle: {item!r}")
    return color, rect


def validate_scene_size(rect: tuple[int, int, int, int], scale_x: float,
                        scale_y: float | None = None) -> tuple[int, int]:
    if scale_y is None:
        scale_y = scale_x
    if scale_x <= 0 or scale_y <= 0 or abs(scale_x - scale_y) > 0.02:
        raise RuntimeError(f"window screenshot has inconsistent or invalid scale: {scale_x}x{scale_y}")
    _, _, width, height = rect
    logical = (round(width / scale_x), round(height / scale_y))
    if logical != SCENE_LOGICAL_SIZE or abs(width - logical[0] * scale_x) > 1.1 or abs(height - logical[1] * scale_y) > 1.1:
        raise RuntimeError(
            f"scene was not scaled exactly once to 120x18 logical points: {rect}, scale={scale_x}x{scale_y}")
    return logical


def screen_point(capture: dict, point: tuple[float, float]) -> tuple[float, float]:
    return (capture["x"] + point[0] / capture["scale_x"],
            capture["y"] + point[1] / capture["scale_y"])


def _frame_values(frame: dict, label: str) -> tuple[float, float, float, float]:
    try:
        raw = tuple(frame[key] for key in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"{label} does not contain a numeric display frame: {frame!r}") from error
    if any(type(value) not in (int, float) for value in raw):
        raise RuntimeError(f"{label} does not contain a numeric display frame: {frame!r}")
    values = tuple(float(value) for value in raw)
    if not all(math.isfinite(value) for value in values) or values[2] <= 0 or values[3] <= 0:
        raise RuntimeError(f"{label} has invalid display geometry: {frame!r}")
    return values


def resize_size_matches(actual: tuple[float, float], expected: tuple[float, float],
                        *, tolerance: float = WINDOW_SIZE_TOLERANCE) -> bool:
    return abs(actual[0] - expected[0]) <= tolerance and abs(actual[1] - expected[1]) <= tolerance


def require_observed_resize(previous: dict, actual: dict, label: str) -> tuple[float, float]:
    previous_size = (float(previous["width"]), float(previous["height"]))
    actual_size = (float(actual["width"]), float(actual["height"]))
    if resize_size_matches(actual_size, previous_size):
        raise RuntimeError(
            f"{label} did not change the observed window size beyond the "
            f"{WINDOW_SIZE_TOLERANCE}-point tolerance: {previous_size!r} -> {actual_size!r}")
    return actual_size


def frame_fits_display(frame: dict, display: dict, *, tolerance: float = 1.0) -> bool:
    x, y, width, height = _frame_values(frame, "frame")
    dx, dy, display_width, display_height = _frame_values(display, "display")
    return (x >= dx - tolerance and y >= dy - tolerance
            and x + width <= dx + display_width + tolerance
            and y + height <= dy + display_height + tolerance)


def point_inside_frame(point: tuple[float, float], frame: dict, *, tolerance: float = 1.0) -> bool:
    x, y = point
    fx, fy, width, height = _frame_values(frame, "frame")
    return (math.isfinite(x) and math.isfinite(y)
            and fx - tolerance <= x <= fx + width + tolerance
            and fy - tolerance <= y <= fy + height + tolerance)


def display_for_window_origin(window: dict, displays: list[dict]) -> dict:
    x, y, _width, _height = _frame_values(window, "window")
    matches = []
    seen_bounds = set()
    for display in displays:
        dx, dy, width, height = _frame_values(display, "display")
        if dx <= x < dx + width and dy <= y < dy + height:
            bounds = (dx, dy, width, height)
            # Mirrored displays can share a frame. Treat identical frames as
            # one usable geometry, while refusing ambiguous overlapping layouts.
            if bounds not in seen_bounds:
                matches.append(display)
                seen_bounds.add(bounds)
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one active display containing the fixture window origin; found {matches!r}")
    return matches[0]


def fit_resize_targets(window: dict, displays: list[dict], *,
                       margin: int = 24, gap: tuple[int, int] = (64, 40)) -> tuple[tuple[int, int], tuple[int, int], dict]:
    """Choose two distinct AX sizes that remain inside the origin display."""
    x, y, width, height = _frame_values(window, "window")
    display = display_for_window_origin(window, displays)
    dx, dy, display_width, display_height = _frame_values(display, "display")
    # Keep a few points above the helper's strict >=700x500 fixture identity
    # guard because AX/window-server geometry may settle within the existing
    # two-point resize tolerance.
    minimum_width, minimum_height = (EDITOR_MINIMUM_SIZE[0] + 4,
                                    EDITOR_MINIMUM_SIZE[1] + 4)
    max_width = math.floor(dx + display_width - x - margin)
    max_height = math.floor(dy + display_height - y - margin)
    if max_width < minimum_width or max_height < minimum_height:
        raise RuntimeError(
            f"active display has insufficient room for the minimum fixture editor size: "
            f"available={max_width}x{max_height}, minimum={EDITOR_MINIMUM_SIZE}, display={display!r}")

    # Keep a safety margin from the screen edge. The second size approaches
    # that safe maximum; the first is smaller on both axes for a real second
    # AX resize even when the starting window already fills the display.
    second = (max_width, max_height)
    current = (round(width), round(height))
    if second == current:
        # If the starting window already occupies the safe maximum, reserve a
        # smaller second target instead of issuing a no-op AX resize.
        shrink_axes = sorted((second[0] - minimum_width, second[1] - minimum_height),
                             reverse=True)
        if shrink_axes[0] >= 2:
            if second[0] - minimum_width >= second[1] - minimum_height:
                second = (second[0] - min(16, second[0] - minimum_width), second[1])
            else:
                second = (second[0], second[1] - min(16, second[1] - minimum_height))
    first = (max(minimum_width, second[0] - gap[0]),
             max(minimum_height, second[1] - gap[1]))
    if first == second:
        if second[0] > minimum_width:
            first = (second[0] - 1, second[1])
        elif second[1] > minimum_height:
            first = (second[0], second[1] - 1)
    if first == current:
        if first[0] > minimum_width:
            first = (first[0] - 1, first[1])
        elif first[1] > minimum_height:
            first = (first[0], first[1] - 1)
    if (first == second or first == current or second == current
            or resize_size_matches(first, current)
            or resize_size_matches(second, first)):
        raise RuntimeError(
            f"active display cannot provide two observable distinct resize sizes outside the "
            f"{WINDOW_SIZE_TOLERANCE}-point waiter tolerance: "
            f"current={current}, first={first}, second={second}, "
            f"minimum={(minimum_width, minimum_height)}")

    for target in (first, second):
        target_frame = {"x": x, "y": y, "width": target[0], "height": target[1]}
        if not frame_fits_display(target_frame, display, tolerance=0):
            raise RuntimeError(f"planned AX resize would leave its active display: {target_frame!r}")
    return first, second, display


def scene_screen_frame(snapshot: dict, rect: tuple[int, int, int, int]) -> dict:
    scale_x = float(snapshot["scale_x"])
    scale_y = float(snapshot["scale_y"])
    if scale_x <= 0 or scale_y <= 0 or not math.isfinite(scale_x + scale_y):
        raise RuntimeError(f"capture has invalid scale for scene placement: {snapshot!r}")
    x, y, width, height = rect
    return {
        "x": float(snapshot["x"]) + x / scale_x,
        "y": float(snapshot["y"]) + y / scale_y,
        "width": width / scale_x,
        "height": height / scale_y,
    }


def require_scene_inside_display(snapshot: dict, rect: tuple[int, int, int, int],
                                 window: dict, display: dict) -> dict:
    scene = scene_screen_frame(snapshot, rect)
    if not frame_fits_display(scene, display):
        raise RuntimeError(f"captured native scene extends outside its active display: {scene!r}, {display!r}")
    if not frame_fits_display(scene, window):
        raise RuntimeError(f"captured native scene extends outside the actual resized window: {scene!r}, {window!r}")
    point = screen_point(snapshot, center(rect))
    if not point_inside_frame(point, display) or not point_inside_frame(point, window):
        raise RuntimeError(f"native scene center is outside its resized window/display: {point!r}")
    return {"bounds": scene, "center": [point[0], point[1]],
            "display_id": display.get("display_id")}


def center(rect: tuple[int, int, int, int]) -> tuple[float, float]:
    x, y, width, height = rect
    return (x + (width - 1) / 2, y + (height - 1) / 2)


def counters(log: str) -> list[int]:
    return [int(value) for value in COUNTER_RE.findall(log)]


def is_fixture_editor(window: dict, fixture_name: str) -> bool:
    title = str(window.get("title", ""))
    return (fixture_name.casefold() in title.casefold()
            and float(window.get("width", 0)) >= EDITOR_MINIMUM_SIZE[0]
            and float(window.get("height", 0)) >= EDITOR_MINIMUM_SIZE[1]
            and window.get("visible_on_screen", True) is True
            and int(window.get("layer", 0)) == 0)


def select_fixture_editor(windows: list[dict], fixture_name: str) -> dict | None:
    matches = [window for window in windows if is_fixture_editor(window, fixture_name)]
    if len(matches) > 1:
        raise RuntimeError(f"ambiguous visible fixture editor windows for {fixture_name!r}: {matches!r}")
    return matches[0] if matches else None


def preflight_blocker(permissions: dict) -> str | None:
    if permissions.get("session_locked") is not False:
        return "the active macOS console session is locked or its state is unavailable"
    if permissions.get("on_console") is not True:
        return "the invoking process is not in an unlocked foreground macOS console session"
    if permissions.get("accessibility") is not True or permissions.get("screen_recording") is not True:
        return "Accessibility and Screen Recording permissions must already be granted to the invoking terminal"
    return None


def require_unmodified_input_flags(result: dict, action: str) -> None:
    # Core Graphics may annotate its event object during post(). Validate the
    # captured pre-post value; `event_flags_after_post_raw` is diagnostic only.
    flags = result.get("event_flags_before_post_raw")
    if flags != 0:
        raise RuntimeError(f"plain {action} retained synthetic modifier flags: {flags!r}")


def require_guarded_mouse_cleanup_ack(result: dict, pid: int, window_id: int,
                                     fixture_name: str, point: tuple[float, float]) -> None:
    if (result.get("cleanup") != "guarded-hid-mouse-up"
            or result.get("delivery_route") != "guarded_cghid_event_tap"
            or result.get("guarded_hid_enqueue") is not True):
        raise RuntimeError(f"mouse cleanup was not acknowledged as a guarded HID enqueue: {result!r}")
    if result.get("posted_to_target_pid") is not None:
        raise RuntimeError("mouse cleanup must not claim PID-targeted delivery")
    guard = result.get("guard_evidence")
    if not isinstance(guard, dict):
        raise RuntimeError(f"mouse cleanup lacks its native guard evidence: {result!r}")
    expected_guard_fields = {
        "pid", "window_id", "fixture", "selected_window_id", "accessibility_trusted",
        "screen_capture_trusted", "session_locked", "on_console", "target_process_live",
        "frontmost_pid", "focused_window_id", "point", "current_window_frame",
        "point_owner", "point_owner_refusal", "focused_window_refusal",
        "cleanup_ax_budget_available",
    }
    if set(guard) != expected_guard_fields:
        raise RuntimeError(f"mouse cleanup guard evidence has an unexpected shape: {sorted(guard)}")
    for field, expected in (("pid", pid), ("window_id", window_id),
                            ("fixture", fixture_name), ("frontmost_pid", pid),
                            ("focused_window_id", window_id), ("selected_window_id", window_id),
                            ("accessibility_trusted", True),
                            ("screen_capture_trusted", True),
                            ("session_locked", False), ("on_console", True),
                            ("target_process_live", True)):
        observed = guard.get(field)
        if type(observed) is not type(expected) or observed != expected:
            raise RuntimeError(
                f"mouse cleanup guard evidence changed {field}: expected={expected!r}, "
                f"observed={observed!r}")
    for field, expected in (("pid", pid), ("window_id", window_id), ("fixture", fixture_name)):
        observed = result.get(field)
        if type(observed) is not type(expected) or observed != expected:
            raise RuntimeError(
                f"mouse cleanup acknowledgement changed {field}: expected={expected!r}, "
                f"observed={observed!r}")
    observed_point = guard.get("point")
    if (not isinstance(observed_point, dict) or set(observed_point) != {"x", "y"}
            or any(type(observed_point.get(axis)) not in (int, float)
                   or not math.isfinite(float(observed_point[axis]))
                   or not math.isclose(float(observed_point[axis]), value, abs_tol=1e-6)
                   for axis, value in zip(("x", "y"), point))):
        raise RuntimeError(f"mouse cleanup guard evidence changed its owned point: {observed_point!r}")
    frame = guard.get("current_window_frame")
    if not isinstance(frame, dict) or not point_inside_frame(point, frame, tolerance=0):
        raise RuntimeError(f"mouse cleanup point is outside the current selected-window frame: {frame!r}")
    owner = guard.get("point_owner")
    owner_fields = {
        "method", "hit_pid", "hit_role", "resolution_origin", "parent_depth",
        "parent_resolution_complete", "candidate_pid", "candidate_window_id",
        "candidate_role", "candidate_geometry", "matching_window_ids", "global_point",
    }
    if not isinstance(owner, dict) or set(owner) != owner_fields:
        raise RuntimeError(f"mouse cleanup lacks exact AX point-owner evidence: {owner!r}")
    allowed_hit_roles = {
        "AXWindow", "AXApplication", "AXGroup", "AXButton", "AXCheckBox",
        "AXImage", "AXLink", "AXProgressIndicator", "AXRadioButton", "AXScrollArea",
        "AXSlider", "AXStaticText", "AXTextArea", "AXTextField", "AXUnknown", "AXOther",
    }
    resolution_origin = owner.get("resolution_origin")
    if (owner.get("method") != "accessibility_point_hit"
            or type(owner.get("hit_pid")) is not int or owner["hit_pid"] != pid
            or not isinstance(owner.get("hit_role"), str)
            or owner["hit_role"] not in allowed_hit_roles
            or not isinstance(resolution_origin, str)
            or resolution_origin not in {"hit_is_window", "window_attribute", "parent"}
            or type(owner.get("parent_depth")) is not int
            or not 0 <= owner["parent_depth"] <= 8
            or type(owner.get("parent_resolution_complete")) is not bool
            or owner["parent_resolution_complete"] is not True
            or type(owner.get("candidate_pid")) is not int or owner["candidate_pid"] != pid
            or type(owner.get("candidate_window_id")) is not int
            or owner["candidate_window_id"] != window_id
            or owner.get("candidate_role") != "AXWindow"
            or owner.get("matching_window_ids") != [window_id]
            or type(owner["matching_window_ids"][0]) is not int):
        raise RuntimeError(f"mouse cleanup AX point owner does not map uniquely to the original window: {owner!r}")
    origin = resolution_origin
    if ((origin in {"hit_is_window", "window_attribute"} and owner["parent_depth"] != 0)
            or (origin == "parent" and owner["parent_depth"] < 1)):
        raise RuntimeError(f"mouse cleanup AX resolution origin/depth is inconsistent: {owner!r}")
    if ((origin == "hit_is_window" and owner["hit_role"] != "AXWindow")
            or (origin in {"window_attribute", "parent"}
                and owner["hit_role"] == "AXWindow")):
        raise RuntimeError(f"mouse cleanup AX hit role contradicts its resolution origin: {owner!r}")

    def finite_number(value: object) -> bool:
        return type(value) in (int, float) and math.isfinite(float(value))

    geometry = owner.get("candidate_geometry")
    if (not isinstance(geometry, dict) or set(geometry) != {"x", "y", "width", "height"}
            or not all(finite_number(geometry.get(axis)) for axis in ("x", "y", "width", "height"))
            or geometry["width"] <= 0 or geometry["height"] <= 0):
        raise RuntimeError(f"mouse cleanup AX point-owner geometry is malformed: {geometry!r}")
    if not isinstance(frame, dict) or set(frame) != {"x", "y", "width", "height"}:
        raise RuntimeError(f"mouse cleanup selected frame is malformed: {frame!r}")
    if (not all(finite_number(frame.get(axis)) for axis in ("x", "y", "width", "height"))
            or frame["width"] <= 0 or frame["height"] <= 0
            or any(abs(float(geometry[axis]) - float(frame[axis])) > 2
                   for axis in ("x", "y", "width", "height"))):
        raise RuntimeError(f"mouse cleanup AX geometry does not match the selected frame: {geometry!r} vs {frame!r}")
    owner_point = owner.get("global_point")
    if (not isinstance(owner_point, dict) or set(owner_point) != {"x", "y"}
            or any(not finite_number(owner_point.get(axis)) for axis in ("x", "y"))
            or any(not math.isclose(float(owner_point[axis]), value, abs_tol=1e-6)
                   for axis, value in zip(("x", "y"), point))):
        raise RuntimeError(f"mouse cleanup AX point owner changed the global hit point: {owner_point!r}")
    if not point_inside_frame(point, geometry, tolerance=0):
        raise RuntimeError(f"mouse cleanup hit point is outside its AX-owned window geometry: {geometry!r}")
    if (guard.get("point_owner_refusal") is not None
            or guard.get("focused_window_refusal") is not None
            or guard.get("cleanup_ax_budget_available") is not True):
        raise RuntimeError("mouse cleanup success ACK contains a refusal or expired AX budget")


def require_input_sender_settle(result: dict, action: str) -> None:
    requested = result.get("sender_runloop_settle_requested_ms")
    elapsed = result.get("sender_runloop_settle_ms")
    if requested != 100 or type(elapsed) is not int or elapsed < 90:
        raise RuntimeError(
            f"{action} helper acknowledgement lacks the required input sender RunLoop settle: "
            f"requested={requested!r}ms, observed={elapsed!r}ms")


def assert_scene_unchanged(snapshot: dict, current: dict, color: str,
                           expected_counts: list[int], expected_log_counts: list[int]) -> None:
    actual_color, actual_rect = scene_state(current)
    previous_color, previous_rect = scene_state(snapshot)
    if actual_color != color or previous_color != color or actual_rect != previous_rect:
        raise RuntimeError(f"scene changed during a canceled/stale press: {previous_color}/{previous_rect} -> {actual_color}/{actual_rect}")
    if counters(current["log"]) != expected_counts or counters(snapshot["log"]) != expected_log_counts:
        raise RuntimeError("native dispatch log changed during a canceled/stale press")


class MacSmoke:
    def __init__(self, helper: Path, output: Path, label: str, binary: Path, source: Path,
                 island: bool, timeout: float):
        self.helper = helper
        self.output = output
        self.label = label
        self.binary = binary.resolve()
        self.source = source.resolve()
        self.island = island
        self.timeout = timeout
        self.fixture = output / f"{label}-fixture.txt"
        self.data = output / "data"
        self.home = output / "home"
        self.process: subprocess.Popen | None = None
        self.window_id: int | None = None
        self.window_title: str | None = None
        self.pid: int | None = None
        self.owned_input: dict | None = None
        self.record = {
            "schema": 1,
            "label": label,
            "editor_smoke": "failed",
            "same_window_island": "failed" if island else "not_applicable",
            "binary": str(self.binary),
            "binary_sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest(),
            "operations": [],
            "snapshots": [],
            "dispatch_log_copies": [],
            "input_cleanup": [],
            "input_event_acks": [],
        }

    def prepare(self) -> tuple[dict[str, str], Path]:
        self.fixture.write_text(FIXTURE)
        (self.data / "config").mkdir(parents=True)
        self.home.mkdir()
        (self.data / "config/settings.json").write_text(json.dumps({
            "telemetry": {"diagnostics": False, "metrics": False},
            "disable_ai": True,
            "auto_update": False,
            "auto_install_extensions": {"html": False},
            "ensure_final_newline_on_save": True,
            "languages": {"Plain Text": {"enable_language_server": False}},
        }, indent=2) + "\n")
        env = dict(os.environ)
        # The smoke needs MZed's INFO-level island counter as an oracle.
        # Pinned Zed gives ZED_LOG precedence over inherited RUST_LOG, so set
        # this only in the isolated child environment rather than altering the
        # caller's shell or logging preferences.
        env["ZED_LOG"] = "info"
        self.record["logging_contract"] = {
            "ZED_LOG": "info",
            "counter_oracle": "INFO-level MZed island counter records",
            "precedence": "task-local ZED_LOG overrides inherited RUST_LOG for the launched editor",
        }
        env.update(HOME=str(self.home), XDG_CONFIG_HOME=str(self.output / "config"),
                   XDG_DATA_HOME=str(self.output / "xdg-data"),
                   XDG_CACHE_HOME=str(self.output / "cache"))
        env.pop("MZED_NATIVE_ISLAND", None)
        if self.island:
            env["MZED_NATIVE_ISLAND"] = "1"
        else:
            env["MZED_NATIVE_ISLAND"] = "0"
        return env, self.fixture

    def launch(self) -> None:
        if not self.binary.is_file():
            raise RuntimeError(f"editor binary does not exist: {self.binary}")
        env, fixture = self.prepare()
        with (self.output / "editor.log").open("w") as log:
            self.process = subprocess.Popen(
                [str(self.binary), "--user-data-dir", str(self.data), str(fixture)],
                cwd=self.source, env=env, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True)
        self.pid = self.process.pid
        self.record["pid"] = self.pid
        self.wait_for_window()
        active = helper_json(self.helper, "activate", self.pid, self.window_id,
                             self.fixture.name)
        self.record["activation"] = active
        if active.get("activation_completed") is not True or active.get("frontmost_pid") != self.pid:
            self.record["activation_fresh_observation"] = self.current_window_snapshot()
            raise RuntimeError(
                f"target app did not become frontmost: activation={active!r}, "
                f"fresh windows observation={self.record['activation_fresh_observation']!r}")
        self.checked_window_identity()
        ax_state = self.accessibility_state()
        self.record["ax_state_after_activation"] = ax_state
        self.record["operations"].append(
            "opened the uniquely fixture-titled visible editor window and recorded auxiliary process windows")

    def current_displays(self) -> list[dict]:
        assert self.pid is not None and self.window_id is not None
        result = helper_json(self.helper, "displays", self.pid, self.window_id,
                             self.fixture.name)
        displays = result.get("displays")
        if not isinstance(displays, list) or not displays or any(not isinstance(item, dict) for item in displays):
            raise RuntimeError(f"invalid guarded active-display evidence: {result!r}")
        return displays

    def checked_resize_display(self, window: dict, expected_display: dict) -> dict:
        current = display_for_window_origin(window, self.current_displays())
        expected_bounds = _frame_values(expected_display, "planned display")
        current_bounds = _frame_values(current, "current display")
        if any(abs(before - after) > 1 for before, after in zip(expected_bounds, current_bounds)):
            raise RuntimeError(
                f"active display geometry changed during resize: {expected_display!r} -> {current!r}")
        if not frame_fits_display(window, current):
            raise RuntimeError(
                f"actual resized fixture window is outside its active display: {window!r}, {current!r}")
        return current

    def current_window_snapshot(self) -> dict:
        assert self.pid is not None
        result = helper_json(self.helper, "windows", self.pid)
        windows = result.get("windows")
        if not isinstance(windows, list):
            raise RuntimeError(f"invalid process window evidence: {result!r}")
        return result

    def accessibility_state(self) -> dict:
        assert self.pid is not None and self.window_id is not None
        state = helper_json(self.helper, "ax-state", self.pid, self.window_id,
                            self.fixture.name)
        if (state.get("pid") != self.pid or state.get("selected_window_id") != self.window_id
                or state.get("frontmost_pid") != self.pid):
            raise RuntimeError(f"Accessibility state does not identify the active fixture window: {state!r}")
        return state

    def current_windows(self, *, require_frontmost: bool = True) -> list[dict]:
        result = self.current_window_snapshot()
        if require_frontmost and result.get("frontmost_pid") != self.pid:
            raise RuntimeError(f"editor lost foreground ownership: {result!r}")
        return result["windows"]

    def wait_for_window(self) -> None:
        assert self.process is not None and self.pid is not None
        deadline = time.monotonic() + self.timeout
        last = []
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"editor exited before a window appeared: {self.process.returncode}")
            # Window enumeration is allowed before activation; interaction and
            # capture methods require the app to be frontmost.
            last = self.current_windows(require_frontmost=False)
            selected = select_fixture_editor(last, self.fixture.name)
            if selected is not None:
                self.window_id = int(selected["window_id"])
                self.window_title = str(selected.get("title", ""))
                self.record["window_id"] = self.window_id
                self.record["window_title"] = self.window_title
                self.record["initial_window"] = selected
                self.record["initial_process_windows"] = last
                self.record["auxiliary_windows"] = [
                    dict(window, role=("untitled auxiliary process-owned window"
                                       if not str(window.get("title", ""))
                                       else "additional process-owned window"))
                    for window in last if int(window.get("window_id", -1)) != self.window_id
                ]
                return
            time.sleep(0.25)
        raise RuntimeError(f"no unique visible window for fixture {self.fixture.name!r}; last windows={last!r}")

    def assert_window_identity(self) -> dict:
        assert self.pid is not None and self.window_id is not None
        windows = self.current_windows()
        selected = select_fixture_editor(windows, self.fixture.name)
        if selected is None:
            raise RuntimeError(f"fixture editor is no longer a visible level-zero window: {windows!r}")
        if int(selected.get("window_id", -1)) != self.window_id:
            raise RuntimeError(
                f"selected PID/window identity changed: PID={self.pid}, window={self.window_id}, now={selected!r}")
        if self.window_title and self.fixture.name.casefold() not in str(selected.get("title", "")).casefold():
            raise RuntimeError(f"selected window no longer identifies the isolated fixture: {selected!r}")
        return selected

    def release_owned_input(self, reason: str) -> str | None:
        owned = self.owned_input
        if owned is None:
            return None
        self.owned_input = None
        kind = owned["kind"]
        if kind == "mouse":
            command = ("cleanup-mouse", owned["pid"], owned["window_id"],
                       self.fixture.name, owned["x"], owned["y"])
        elif kind == "key":
            command = ("cleanup-key", owned["pid"], owned["key_code"], owned["flags"])
        elif kind == "text":
            command = ("cleanup-text", owned["pid"], owned["text"])
        else:
            raise RuntimeError(f"unknown owned input type: {kind!r}")
        attempt = {"reason": reason, "kind": kind, "pid": owned["pid"],
                   "window_id": owned["window_id"], "command": command[0],
                   "ownership_state": owned.get("state", "down_acknowledged")}
        try:
            result = helper_json(self.helper, *command, timeout=8)
            if kind == "mouse":
                require_guarded_mouse_cleanup_ack(
                    result, owned["pid"], owned["window_id"], self.fixture.name,
                    (owned["x"], owned["y"]))
            elif result.get("posted_to_target_pid") is not True:
                raise RuntimeError(f"PID-targeted keyboard release was not confirmed: {result!r}")
            if kind in ("mouse", "text"):
                require_unmodified_input_flags(result, f"{kind} cleanup")
            require_input_sender_settle(result, f"{kind} cleanup")
            attempt["outcome"] = ("guarded_hid_release_enqueued" if kind == "mouse"
                                   else "release_posted_to_original_pid")
            attempt["result"] = result
            self.record["input_cleanup"].append(attempt)
            return None
        except Exception as error:
            attempt["outcome"] = "failed"
            attempt["error"] = str(error)
            self.record["input_cleanup"].append(attempt)
            return str(error)

    def checked_window_identity(self) -> dict:
        try:
            return self.assert_window_identity()
        except Exception as error:
            cleanup_error = self.release_owned_input("window identity or foreground check failed")
            if cleanup_error:
                raise RuntimeError(f"{error}; targeted input release also failed: {cleanup_error}") from error
            raise

    def checked_capture_identity(self, capture: dict) -> dict:
        window = self.checked_window_identity()
        expected = {
            "pid": self.pid,
            "window_id": self.window_id,
            "title": window.get("title"),
        }
        for key, value in expected.items():
            if capture.get(key) != value:
                cleanup_error = self.release_owned_input("capture no longer identifies the selected window")
                error = f"capture {key} differs from the selected visible window: {capture!r} versus {window!r}"
                if cleanup_error:
                    error += f"; targeted input release also failed: {cleanup_error}"
                raise RuntimeError(error)
        for key in ("x", "y", "width", "height"):
            if abs(float(capture[key]) - float(window[key])) > 1:
                cleanup_error = self.release_owned_input("captured window geometry changed before input")
                error = f"captured window geometry changed before input: {capture!r} versus {window!r}"
                if cleanup_error:
                    error += f"; targeted input release also failed: {cleanup_error}"
                raise RuntimeError(error)
        return window

    def capture(self, stage: str) -> dict:
        window = self.checked_window_identity()
        assert self.pid is not None and self.window_id is not None
        image = self.output / f"{stage}.png"
        try:
            capture = helper_json(self.helper, "capture", self.pid, self.window_id,
                                  self.fixture.name, image)
        except Exception as error:
            cleanup_error = self.release_owned_input("native window capture failed")
            if cleanup_error:
                raise RuntimeError(f"{error}; targeted input release also failed: {cleanup_error}") from error
            raise
        if capture.get("pid") != self.pid or capture.get("window_id") != self.window_id:
            raise RuntimeError(f"capture identity does not match selected app window: {capture!r}")
        if capture.get("title") != window.get("title"):
            raise RuntimeError("window title changed during capture")
        scale_x, scale_y = float(capture["scale_x"]), float(capture["scale_y"])
        if not 0.9 <= scale_x <= 4.0 or abs(scale_x - scale_y) > 0.02:
            raise RuntimeError(f"unsupported or inconsistent window screenshot scale: {capture!r}")
        if abs(int(capture["pixel_width"]) / float(capture["width"]) - scale_x) > 0.01:
            raise RuntimeError("window screenshot width was scaled inconsistently")
        if abs(int(capture["pixel_height"]) / float(capture["height"]) - scale_y) > 0.01:
            raise RuntimeError("window screenshot height was scaled inconsistently")
        entry = dict(capture, stage=stage, image=str(image))
        self.record["snapshots"].append(entry)
        return entry

    def snapshot(self, stage: str, *, expected: str | None = None,
                 timeout: float = 8) -> dict:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            capture = self.capture(stage)
            colors = {
                "blue": color_components(self.helper, Path(capture["path"]), BLUE),
                "pink": color_components(self.helper, Path(capture["path"]), PINK),
            }
            last = dict(capture, colors=colors)
            color, rect = scene_state(last)
            if color != "off" and rect is not None:
                validate_scene_size(rect, float(capture["scale_x"]), float(capture["scale_y"]))
            if expected is None or color == expected:
                last["scene"] = color
                if rect is not None:
                    last["scene_rect"] = list(rect)
                return last
            time.sleep(0.12)
        raise RuntimeError(f"{stage}: expected scene={expected}, last evidence={last!r}")

    def wait_for_ocr(self, stage: str, predicate, timeout: float = 45) -> list[dict]:
        deadline = time.monotonic() + timeout
        last: list[dict] = []
        while time.monotonic() < deadline:
            capture = self.capture(stage)
            ocr = helper_json(self.helper, "ocr", capture["path"]).get("observations", [])
            last = ocr
            (self.output / f"{stage}.ocr.json").write_text(json.dumps(ocr, indent=2) + "\n")
            text = " ".join(str(item.get("text", "")) for item in ocr).casefold()
            if predicate(text):
                return ocr
            time.sleep(0.25)
        raise RuntimeError(f"{stage}: expected OCR content not observed; last observations={last!r}")

    @staticmethod
    def ocr_text(observations: list[dict]) -> str:
        return " ".join(str(item.get("text", "")) for item in observations).casefold()

    def require_focused_main_window(self, stage: str) -> dict:
        state = self.accessibility_state()
        focused = state.get("focused_window")
        if (not isinstance(focused, dict) or focused.get("window_id") != self.window_id
                or focused.get("role") != "AXWindow"):
            raise RuntimeError(f"{stage}: focused Accessibility window is not the selected editor main window: {state!r}")
        return state

    def dismiss_move_to_applications_notice(self, startup_ocr: list[dict]) -> None:
        text = self.ocr_text(startup_ocr)
        if "move zed to applications?" not in text:
            self.record["move_to_applications_notice"] = {"status": "not_present"}
            return
        if "unrecognized project" not in text or "no" not in text:
            raise RuntimeError("launch notice appeared without the expected Restricted Mode prompt and No action")
        assert self.pid is not None and self.window_id is not None

        capture = self.capture("move-notice-before-press")
        observations = helper_json(self.helper, "ocr", capture["path"]).get("observations", [])
        text = self.ocr_text(observations)
        if "move zed to applications?" not in text or "no" not in text:
            raise RuntimeError("fresh selected-window capture no longer shows the exact cancelable launch notice")

        state = self.accessibility_state()
        focused = state.get("focused_window")
        if (not isinstance(focused, dict) or focused.get("role") != "AXSheet"
                or not isinstance(focused.get("window_id"), int)
                or focused.get("window_id") == self.window_id):
            raise RuntimeError(f"launch notice is not the focused same-process AX sheet: {state!r}")
        focused_id = int(focused["window_id"])
        original_auxiliary = [
            window for window in self.record.get("auxiliary_windows", [])
            if int(window.get("window_id", -1)) == focused_id
            and not str(window.get("title", ""))
            and window.get("role") == "untitled auxiliary process-owned window"
        ]
        current_auxiliary = [
            window for window in self.current_windows()
            if int(window.get("window_id", -1)) == focused_id
            and not str(window.get("title", ""))
        ]
        if len(original_auxiliary) != 1 or len(current_auxiliary) != 1:
            raise RuntimeError(
                f"focused launch sheet does not map to one stable untitled same-PID auxiliary window: "
                f"initial={original_auxiliary!r}, current={current_auxiliary!r}")
        controls = state.get("focused_buttons")
        if state.get("focused_buttons_complete") is not True:
            raise RuntimeError(f"focused same-process AX button tree is incomplete: {state!r}")
        no_buttons = [item for item in controls if isinstance(item, dict)
                      and item.get("title") == "No" and item.get("enabled") is True] \
            if isinstance(controls, list) else []
        if len(no_buttons) != 1:
            raise RuntimeError(f"focused launch sheet does not expose exactly one enabled No button: {state!r}")

        result = helper_json(self.helper, "ax-press", self.pid, self.window_id,
                             self.fixture.name, focused_id, "No")
        if result.get("ax_press_error") != 0 or result.get("focused_window_id") != focused_id:
            raise RuntimeError(f"Accessibility did not confirm the exact No button action: {result!r}")
        evidence = {
            "status": "pressed_exact_no_button",
            "pid": self.pid,
            "selected_window_id": self.window_id,
            "focused_sheet_window_id": focused_id,
            "focused_sheet_role": focused.get("role"),
            "button": no_buttons[0],
            "before_capture": capture,
            "before_ocr": observations,
            "ax_press": result,
        }
        deadline = time.monotonic() + 12
        last = {}
        while time.monotonic() < deadline:
            after_capture = self.capture("move-notice-after-press")
            after_ocr = helper_json(self.helper, "ocr", after_capture["path"]).get("observations", [])
            after_state = self.accessibility_state()
            remaining = [window for window in self.current_windows()
                         if int(window.get("window_id", -1)) == focused_id]
            after_text = self.ocr_text(after_ocr)
            after_focus = after_state.get("focused_window")
            last = {"capture": after_capture, "ocr": after_ocr,
                    "ax_state": after_state, "old_sheet_windows": remaining}
            if ("move zed to applications?" not in after_text
                    and "unrecognized project" in after_text
                    and isinstance(after_focus, dict)
                    and after_focus.get("window_id") == self.window_id
                    and after_focus.get("role") == "AXWindow"
                    and not remaining):
                evidence["after"] = last
                self.record["move_to_applications_notice"] = evidence
                self.record["operations"].append(
                    "dismissed only the isolated editor's focused Move Zed notice through its exact AX No button")
                return
            time.sleep(0.2)
        evidence["last_after"] = last
        self.record["move_to_applications_notice"] = evidence
        raise RuntimeError(f"launch notice dismissal was not verified by capture and AX identity: {last!r}")

    def verify_ready_editor(self) -> None:
        capture = self.capture("ready-editor-confirmation")
        observations = helper_json(self.helper, "ocr", capture["path"]).get("observations", [])
        text = self.ocr_text(observations)
        if ("mzed pinned baseline fixture" not in text
                or "unrecognized project" in text
                or "move zed to applications?" in text):
            raise RuntimeError(f"editor did not reach a clean ready state: {observations!r}")
        state = self.require_focused_main_window("ready editor verification")
        self.record["ready_editor_verification"] = {
            "capture": capture,
            "ocr": observations,
            "ax_state": state,
            "ready": True,
        }

    def click(self, capture: dict, point: tuple[float, float]) -> None:
        self.mouse("down", capture, point)
        time.sleep(0.06)
        self.mouse("up", capture, point)
        time.sleep(0.14)

    def mouse(self, action: str, capture: dict, point: tuple[float, float]) -> None:
        if self.owned_input is not None and self.owned_input["kind"] != "mouse":
            raise RuntimeError(f"cannot send pointer input while another synthetic input is owned: {self.owned_input!r}")
        window = self.checked_capture_identity(capture)
        assert self.pid is not None and self.window_id is not None
        x, y = screen_point(capture, point)
        owned = self.owned_input
        if action == "down" and owned is None:
            pending = {"kind": "mouse", "pid": self.pid, "window_id": self.window_id,
                       "x": x, "y": y, "state": "down_pending_ack"}
        else:
            pending = owned
        if action == "down" and pending is not None:
            # The helper posts the event before returning its JSON acknowledgement.
            # Track ownership first so a timeout or output failure after the post
            # still triggers a confined release attempt.
            self.owned_input = pending
        try:
            result = helper_json(self.helper, "mouse-event", self.pid, self.window_id,
                                 self.fixture.name, action, x, y)
            if result.get("posted") is not True:
                raise RuntimeError(f"native mouse event was not confirmed: {result!r}")
            require_unmodified_input_flags(result, "mouse event")
            require_input_sender_settle(result, f"mouse {action}")
        except Exception as error:
            cleanup_error = self.release_owned_input(f"native mouse {action} event failed")
            if cleanup_error:
                raise RuntimeError(f"{error}; targeted input release also failed: {cleanup_error}") from error
            raise
        if action == "down" and pending is not None:
            pending["state"] = "down_acknowledged"
            self.owned_input = pending
        elif action == "up" and self.owned_input is not None and self.owned_input["kind"] == "mouse":
            self.owned_input = None
        self.record.setdefault("window_event_checks", []).append({
            "event": action,
            "pid": self.pid,
            "window_id": self.window_id,
            "fixture": self.fixture.name,
            "visible_window": True,
            "frontmost": True,
            "bounds": {key: window[key] for key in ("x", "y", "width", "height")},
            "acknowledgement": result,
        })
        self.record.setdefault("input_event_acks", []).append({
            "kind": "mouse", "action": action, "pid": self.pid,
            "window_id": self.window_id, "result": result,
        })

    def key(self, key_code: int, flags: str = "none", *,
            expected_focused_window_id: int | None = None) -> None:
        if self.owned_input is not None:
            raise RuntimeError(f"cannot start keyboard input while another synthetic input is owned: {self.owned_input!r}")
        assert self.pid is not None and self.window_id is not None
        self.checked_window_identity()
        try:
            # A failed subprocess response does not prove that the native helper
            # failed before posting the down event.
            self.owned_input = {"kind": "key", "pid": self.pid, "window_id": self.window_id,
                                "key_code": key_code, "flags": flags,
                                "state": "down_pending_ack"}
            down_event: list[object] = ["key-event", self.pid, self.window_id,
                                        self.fixture.name, "down", key_code, flags]
            if expected_focused_window_id is not None:
                down_event.append(expected_focused_window_id)
            down_result = helper_json(self.helper, *down_event)
            if down_result.get("posted") is not True:
                raise RuntimeError(f"native key-down was not confirmed: {down_result!r}")
            require_input_sender_settle(down_result, "key-down")
            self.record.setdefault("input_event_acks", []).append({
                "kind": "key", "action": "down", "pid": self.pid,
                "window_id": self.window_id, "key_code": key_code,
                "modifier_flags": flags, "result": down_result,
            })
            self.owned_input["state"] = "down_acknowledged"
            self.checked_window_identity()
            up_result = helper_json(self.helper, "key-event", self.pid, self.window_id,
                                    self.fixture.name, "up", key_code, flags)
            if up_result.get("posted") is not True:
                raise RuntimeError(f"native key-up was not confirmed: {up_result!r}")
            require_input_sender_settle(up_result, "key-up")
            self.record.setdefault("input_event_acks", []).append({
                "kind": "key", "action": "up", "pid": self.pid,
                "window_id": self.window_id, "key_code": key_code,
                "modifier_flags": flags, "result": up_result,
            })
            self.owned_input = None
        except Exception as error:
            cleanup_error = self.release_owned_input(f"native key {key_code} sequence failed")
            if cleanup_error:
                raise RuntimeError(f"{error}; targeted input release also failed: {cleanup_error}") from error
            raise
        time.sleep(0.12)

    def type_text(self, text: str) -> None:
        if self.owned_input is not None:
            raise RuntimeError(f"cannot start text input while another synthetic input is owned: {self.owned_input!r}")
        assert self.pid is not None and self.window_id is not None
        for character in text:
            try:
                self.checked_window_identity()
                # The native helper may post the down and then fail while
                # writing its acknowledgement; conservatively own it first.
                self.owned_input = {"kind": "text", "pid": self.pid,
                                    "window_id": self.window_id, "text": character,
                                    "state": "down_pending_ack"}
                down_result = helper_json(self.helper, "text-event", self.pid, self.window_id,
                                          self.fixture.name, "down", character)
                if down_result.get("posted") is not True:
                    raise RuntimeError(f"native text-down was not confirmed: {down_result!r}")
                require_input_sender_settle(down_result, "text-down")
                require_unmodified_input_flags(down_result, "Unicode event")
                self.record.setdefault("input_event_acks", []).append({
                    "kind": "text", "action": "down", "pid": self.pid,
                    "window_id": self.window_id, "text": character,
                    "result": down_result,
                })
                self.owned_input["state"] = "down_acknowledged"
                self.checked_window_identity()
                up_result = helper_json(self.helper, "text-event", self.pid, self.window_id,
                                        self.fixture.name, "up", character)
                if up_result.get("posted") is not True:
                    raise RuntimeError(f"native text-up was not confirmed: {up_result!r}")
                require_input_sender_settle(up_result, "text-up")
                require_unmodified_input_flags(up_result, "Unicode event")
                self.record.setdefault("input_event_acks", []).append({
                    "kind": "text", "action": "up", "pid": self.pid,
                    "window_id": self.window_id, "text": character,
                    "result": up_result,
                })
                self.owned_input = None
            except Exception as error:
                cleanup_error = self.release_owned_input("native text event sequence failed")
                if cleanup_error:
                    raise RuntimeError(f"{error}; targeted input release also failed: {cleanup_error}") from error
                raise
        time.sleep(0.12)

    def resize(self, width: float, height: float) -> dict:
        self.checked_window_identity()
        assert self.pid is not None and self.window_id is not None
        try:
            return helper_json(self.helper, "resize", self.pid, self.window_id,
                               self.fixture.name, width, height)
        except Exception as error:
            cleanup_error = self.release_owned_input("native resize failed")
            if cleanup_error:
                raise RuntimeError(f"{error}; targeted input release also failed: {cleanup_error}") from error
            raise

    def log_path(self) -> Path:
        candidates = list(self.output.rglob("Zed.log"))
        if len(candidates) == 1:
            return candidates[0]
        likely = [self.data / "logs/Zed.log", self.home / "Library/Logs/Zed/Zed.log"]
        existing = [path for path in likely if path.is_file()]
        if len(existing) == 1:
            return existing[0]
        raise RuntimeError(f"expected one isolated Zed.log below {self.output}; found {candidates!r}")

    def read_log(self) -> str:
        return self.log_path().read_text(errors="replace")

    def save_dispatch_log(self, stage: str, log: str | None = None) -> str:
        log = self.read_log() if log is None else log
        markers = (*FAILURE_MARKERS, "MZed island counter=",
                   "MZed island disabled and native instance destroyed",
                   "MZed island remounted")
        selected = [line for line in log.splitlines() if any(marker in line for marker in markers)]
        destination = self.output / "dispatch-logs" / f"{stage}.log"
        destination.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(selected) + ("\n" if selected else "")
        destination.write_text(text)
        self.record["dispatch_log_copies"].append({
            "stage": stage,
            "path": str(destination),
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "counters": counters(log),
        })
        return log

    def wait_for_counters(self, expected: list[int], timeout: float = 8) -> str:
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            last = self.read_log()
            actual = counters(last)
            if actual == expected:
                return last
            if len(actual) > len(expected) or actual != expected[:len(actual)]:
                raise RuntimeError(f"unexpected native dispatch sequence: expected {expected}, got {actual}")
            time.sleep(0.1)
        raise RuntimeError(f"native dispatch log did not reach {expected}; got {counters(last)}")

    def wait_for_window_size(self, expected: tuple[float, float], timeout: float = 8) -> dict:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = self.checked_window_identity()
            if resize_size_matches((float(last["width"]), float(last["height"])), expected):
                return last
            time.sleep(0.15)
        raise RuntimeError(f"Accessibility resize did not reach requested size {expected}: {last!r}")

    def wait_log_phrase_count(self, phrase: str, expected: int, timeout: float = 8) -> str:
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            last = self.read_log()
            if last.count(phrase) == expected:
                return last
            if last.count(phrase) > expected:
                raise RuntimeError(f"expected {expected} {phrase!r} log entries; got {last.count(phrase)}")
            time.sleep(0.1)
        raise RuntimeError(f"expected {expected} {phrase!r} log entries; got {last.count(phrase)}")

    def find_scene(self, snapshot: dict) -> tuple[int, int, int, int]:
        color, rect = scene_state(snapshot)
        if color == "off" or rect is None:
            raise RuntimeError("native scene pixels are absent")
        validate_scene_size(rect, float(snapshot["scale_x"]), float(snapshot["scale_y"]))
        return rect

    def edit_and_save(self) -> None:
        observations = self.wait_for_ocr(
            "ready-editor", lambda text: "mzed pinned baseline fixture" in text and "unrecognized project" not in text)
        line = next((item for item in observations
                     if "mzed pinned baseline fixture" in str(item.get("text", "")).casefold()), None)
        if line is None:
            raise RuntimeError("OCR saw the ready fixture text but did not return its click target")
        center_point = (line["x"] + line["width"] / 2, line["y"] + line["height"] / 2)
        capture = self.record["snapshots"][-1]
        self.click(capture, center_point)
        # On macOS Command-Down moves the insertion point to the document end.
        self.key(125, "command", expected_focused_window_id=self.window_id)
        self.type_text(ADDITION)
        visible_text = self.wait_for_ocr(
            "typed-text-visible", lambda text: ADDITION.casefold() in text
            and "unrecognized project" not in text)
        self.record["typed_text_visible"] = [item.get("text", "") for item in visible_text]
        # Wait for the rendered text before saving; native events can be posted
        # faster than the editor processes the final queued characters.
        self.key(1, "command", expected_focused_window_id=self.window_id)  # Command-S.
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and self.fixture.read_text() != EXPECTED_FILE:
            time.sleep(0.15)
        actual = self.fixture.read_text()
        self.record["fixture_content"] = actual
        if actual != EXPECTED_FILE:
            raise RuntimeError(f"native edit/save bytes differ from expectation: {actual!r}")
        self.record["operations"].append("typed and saved the exact expected fixture bytes through native keyboard input")
        self.capture("after-edit-save")
        self.record["editor_smoke"] = "passed"

    def run_editor_smoke(self) -> None:
        self.launch()
        modal = self.wait_for_ocr(
            "startup-modal", lambda text: "unrecognized project" in text and "unsupported gpu" not in text)
        self.record["startup_modal_text"] = [item.get("text", "") for item in modal]
        self.dismiss_move_to_applications_notice(modal)
        modal = self.wait_for_ocr(
            "restricted-mode-modal",
            lambda text: "unrecognized project" in text and "move zed to applications?" not in text)
        self.record["restricted_mode_modal_text"] = [item.get("text", "") for item in modal]
        self.require_focused_main_window("Restricted Mode prompt before action")
        # Pinned MZed macOS shortcut to stay in Restricted Mode: Control-Command-S.
        assert self.window_id is not None
        self.key(1, "command,control", expected_focused_window_id=self.window_id)
        self.record["operations"].append("dismissed the untrusted-project prompt into Restricted Mode")
        self.wait_for_ocr("ready-editor", lambda text: "mzed pinned baseline fixture" in text
                          and "unrecognized project" not in text
                          and "move zed to applications?" not in text)
        self.verify_ready_editor()
        if self.island:
            self.exercise_island()
        self.edit_and_save()
        self.record["log_path"] = str(self.log_path())
        self.record["editor_smoke"] = "passed"
        if self.island:
            self.record["same_window_island"] = "passed"

    def exercise_island(self) -> None:
        initial = self.snapshot("island-initial", expected="blue")
        self.record["initial_scene_rect"] = list(self.find_scene(initial))
        initial_log = self.wait_for_counters([])
        self.save_dispatch_log("initial", initial_log)
        initial["log"] = initial_log

        # A normal click paints pink/count 1; press ownership survives redraw and
        # an accessibility-driven resize without a dispatch until the matching up.
        self.click(initial, center(self.find_scene(initial)))
        pink = self.snapshot("island-first-click", expected="pink")
        pink_log = self.wait_for_counters([1])
        self.save_dispatch_log("first-click", pink_log)
        pink["log"] = pink_log
        old_window = self.checked_window_identity()
        first_target, second_target, resize_display = fit_resize_targets(
            old_window, self.current_displays())
        self.mouse("down", pink, center(self.find_scene(pink)))
        before_resize_log = self.read_log()
        self.resize(*first_target)
        first_resized = self.wait_for_window_size(first_target)
        first_size = require_observed_resize(old_window, first_resized, "first AX resize")
        first_display = self.checked_resize_display(first_resized, resize_display)
        first_held = self.snapshot("island-held-after-first-resize", expected="pink")
        first_scene_visible = require_scene_inside_display(
            first_held, self.find_scene(first_held), first_resized, first_display)
        first_log = self.read_log()
        self.save_dispatch_log("held-after-first-resize", first_log)
        first_held["log"] = first_log

        self.resize(*second_target)
        second_resized = self.wait_for_window_size(second_target)
        second_size = require_observed_resize(first_resized, second_resized, "second AX resize")
        second_display = self.checked_resize_display(second_resized, resize_display)
        held = self.snapshot("island-held-after-second-resize", expected="pink")
        second_scene_visible = require_scene_inside_display(
            held, self.find_scene(held), second_resized, second_display)
        held_log = self.read_log()
        self.save_dispatch_log("held-after-second-resize", held_log)
        held["log"] = held_log
        before_logical = validate_scene_size(
            tuple(pink["scene_rect"]), float(pink["scale_x"]), float(pink["scale_y"]))
        first_logical = validate_scene_size(
            tuple(first_held["scene_rect"]), float(first_held["scale_x"]), float(first_held["scale_y"]))
        after_logical = validate_scene_size(
            tuple(held["scene_rect"]), float(held["scale_x"]), float(held["scale_y"]))
        if counters(first_log) != [1] or counters(held_log) != [1] or counters(before_resize_log) != [1]:
            raise RuntimeError("window resize changed native dispatches while the press was held")
        if first_logical != before_logical or abs(float(pink["scale_x"]) - float(first_held["scale_x"])) > 0.01:
            raise RuntimeError("first window resize changed the scene's logical bounds or screenshot scale")
        if before_logical != after_logical or abs(float(pink["scale_x"]) - float(held["scale_x"])) > 0.01:
            raise RuntimeError("second window resize changed the scene's logical bounds or screenshot scale")
        self.record["resize"] = {
            "before": {key: old_window[key] for key in ("x", "y", "width", "height")},
            "first_after": {key: first_resized[key] for key in ("x", "y", "width", "height")},
            "second_after": {key: second_resized[key] for key in ("x", "y", "width", "height")},
            "scene_rect_before": pink["scene_rect"],
            "scene_rect_after_first": first_held["scene_rect"],
            "scene_rect_after_second": held["scene_rect"],
            "logical_scene_size_before": list(before_logical),
            "logical_scene_size_after_first": list(first_logical),
            "logical_scene_size_after": list(after_logical),
            "display_bounds": {key: resize_display[key] for key in ("display_id", "x", "y", "width", "height")},
            "target_first": list(first_target),
            "target_second": list(second_target),
            "observed_first": list(first_size),
            "observed_second": list(second_size),
            "scene_visible_after_first": first_scene_visible,
            "scene_visible_after_second": second_scene_visible,
            "dispatches_unchanged_across_both_resizes": True,
        }
        self.record["operations"].append("held scene press across two same-window AX resizes; pixels and dispatch count stayed stable")
        self.mouse("move", held, center(self.find_scene(held)))
        self.mouse("up", held, center(self.find_scene(held)))
        blue = self.snapshot("island-resize-release", expected="blue")
        blue_log = self.wait_for_counters([1, 2])
        self.save_dispatch_log("resize-release", blue_log)
        blue["log"] = blue_log
        self.record["operations"].append("released owned press at the resized scene and verified one dispatch")

        # An owned release outside the scene is canceled by the editor contract.
        self.mouse("down", blue, center(self.find_scene(blue)))
        outside = (float(blue["pixel_width"]) / 2, float(blue["pixel_height"]) / 2)
        rect = self.find_scene(blue)
        sx, sy = outside
        if rect[0] <= sx < rect[0] + rect[2] and rect[1] <= sy < rect[1] + rect[3]:
            outside = (float(blue["pixel_width"]) / 2, max(0, rect[1] - 2))
        self.mouse("move", blue, outside)
        self.mouse("up", blue, outside)
        after_outside = self.snapshot("island-outside-release", expected="blue")
        current_log = self.read_log()
        self.save_dispatch_log("outside-release", current_log)
        after_outside["log"] = current_log
        assert_scene_unchanged(blue, after_outside, "blue", [1, 2], [1, 2])
        if counters(current_log) != [1, 2]:
            raise RuntimeError(f"outside release dispatched unexpectedly: {counters(current_log)}")
        self.record["operations"].append("owned release outside the scene preserved blue pixels and dispatches")

        # Disable while a scene press is owned, release late, remount, then send
        # a second bare release: neither stale up may affect the fresh instance.
        self.mouse("down", after_outside, center(self.find_scene(after_outside)))
        toggle = (self.find_scene(after_outside)[0] - 18 * float(after_outside["scale_x"]),
                  center(self.find_scene(after_outside))[1])
        self.click(after_outside, toggle)
        disabled = self.snapshot("island-disabled", expected="off")
        log = self.wait_log_phrase_count("MZed island disabled and native instance destroyed", 1)
        self.save_dispatch_log("disabled", log)
        if counters(log) != [1, 2]:
            raise RuntimeError("disabling the island changed the native dispatch sequence")
        self.mouse("up", after_outside, center(self.find_scene(after_outside)))
        still_disabled = self.snapshot("island-disabled-late-release", expected="off")
        log = self.read_log()
        self.save_dispatch_log("disabled-late-release", log)
        if counters(log) != [1, 2]:
            raise RuntimeError("bare release after disable dispatched into native state")

        self.click(disabled, toggle)
        remounted = self.snapshot("island-remounted", expected="blue")
        log = self.wait_log_phrase_count("MZed island remounted", 1)
        self.save_dispatch_log("remounted", log)
        if counters(log) != [1, 2]:
            raise RuntimeError("remount began with an unexpected dispatch")
        self.mouse("up", remounted, center(self.find_scene(remounted)))
        after_remount_release = self.snapshot("island-remount-late-release", expected="blue")
        log = self.read_log()
        self.save_dispatch_log("remount-late-release", log)
        if counters(log) != [1, 2]:
            raise RuntimeError("bare release after remount dispatched into fresh native state")

        self.click(after_remount_release, center(self.find_scene(after_remount_release)))
        last_click = self.snapshot("island-fresh-click", expected="pink")
        final_click_log = self.wait_for_counters([1, 2, 1])
        self.save_dispatch_log("fresh-click", final_click_log)
        self.click(last_click, toggle)
        final_off = self.snapshot("island-final-disabled", expected="off")
        final_log = self.wait_log_phrase_count("MZed island disabled and native instance destroyed", 2)
        self.save_dispatch_log("final-disabled", final_log)
        self.wait_log_phrase_count("MZed island remounted", 1)
        if counters(final_log) != [1, 2, 1]:
            raise RuntimeError(f"final native counter sequence is not [1, 2, 1]: {counters(final_log)}")
        for marker in FAILURE_MARKERS:
            if marker in final_log:
                raise RuntimeError(f"native island log contains failure marker {marker!r}")
        self.record["dispatch_counters"] = counters(final_log)
        self.record["disable_count"] = final_log.count("MZed island disabled and native instance destroyed")
        self.record["remount_count"] = final_log.count("MZed island remounted")
        self.record["final_scene"] = scene_state(final_off)[0]
        self.record["operations"].append("disabled, rejected late releases before and after remount, verified fresh counter 1, disabled again")
        self.assert_window_identity()

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()


def run_one(helper: Path, output: Path, label: str, binary: Path, source: Path,
            island: bool, timeout: float) -> dict:
    root = output / label
    root.mkdir(parents=False, exist_ok=False)
    smoke = MacSmoke(helper, root, label, binary, source, island, timeout)
    try:
        smoke.run_editor_smoke()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        smoke.record["error"] = str(error)
    finally:
        if smoke.owned_input is not None:
            owned = dict(smoke.owned_input)
            cleanup_error = smoke.release_owned_input("smoke run ended before matching release")
            message = f"smoke run ended with an unmatched synthetic {owned['kind']} input"
            if cleanup_error:
                message += f"; targeted release failed: {cleanup_error}"
            smoke.record["error"] = "; ".join(
                item for item in (smoke.record.get("error"), message) if item)
            smoke.record["editor_smoke"] = "failed"
            if island:
                smoke.record["same_window_island"] = "failed"
        try:
            smoke.record["log_path"] = str(smoke.log_path())
            log = smoke.read_log()
            smoke.record["native_log_sha256"] = hashlib.sha256(log.encode()).hexdigest()
        except (OSError, RuntimeError):
            pass
        smoke.stop()
        (root / "smoke.json").write_text(json.dumps(smoke.record, indent=2) + "\n")
    return smoke.record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--derived", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=90,
                        help="seconds to wait for a visible fixture window")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("the macOS UI qualification requires a local macOS desktop")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    helper = output / "macos-ui-helper"
    records: dict = {"schema": 1, "status": "failed", "host": sys.platform, "runs": {}}
    try:
        run(["xcrun", "--sdk", "macosx", "swiftc", "-warnings-as-errors", "-O",
             "-framework", "AppKit", "-framework", "ApplicationServices",
             "-framework", "Vision", "-framework", "ImageIO",
             str(Path(__file__).with_name("macos_ui.swift")), "-o", str(helper)], timeout=180)
        records["guard_fixtures"] = helper_json(helper, "guard-fixtures")
        if (records["guard_fixtures"].get("session_fixtures") != "passed"
                or records["guard_fixtures"].get("frontmost_guard_fixtures") != "passed"
                or records["guard_fixtures"].get("focused_window_guard_fixtures") != "passed"
                or records["guard_fixtures"].get("ax_press_guard_fixtures") != "passed"
                or records["guard_fixtures"].get("ax_traversal_fixtures") != "passed"
                or records["guard_fixtures"].get("plain_input_flags_fixtures") != "passed"
                or records["guard_fixtures"].get("mouse_release_ownership_fixtures") != "passed"
                or records["guard_fixtures"].get("mouse_release_ownership_fixture_count") != 18
                or records["guard_fixtures"].get("mouse_release_refusal_fixtures") != "passed"
                or records["guard_fixtures"].get("mouse_release_refusal_fixture_count") != 18
                or records["guard_fixtures"].get("ax_point_ownership_fixtures") != "passed"
                or records["guard_fixtures"].get("ax_point_ownership_fixture_count") != 38
                or records["guard_fixtures"].get("input_sender_settle_fixtures") != "passed"):
            raise RuntimeError("macOS UI guard fixtures did not pass")
        require_input_sender_settle({
            "sender_runloop_settle_requested_ms": records["guard_fixtures"].get(
                "input_sender_settle_requested_ms"),
            "sender_runloop_settle_ms": records["guard_fixtures"].get("input_sender_settle_ms"),
        }, "no-source RunLoop fixture")
        permissions = helper_json(helper, "permissions")
        records["permissions"] = permissions
        blocker = preflight_blocker(permissions)
        if blocker:
            records.update(status="blocked", error=(
                f"{blocker}; the harness does not unlock the session, open permission prompts, or bypass TCC"))
            raise RuntimeError(records["error"])
        for label, binary, island in (
            ("baseline", args.baseline, False),
            ("derived", args.derived, True),
        ):
            records["runs"][label] = run_one(helper, output, label, binary, args.source, island, args.timeout)
            if records["runs"][label]["editor_smoke"] != "passed":
                raise RuntimeError(f"{label} editor smoke failed: {records['runs'][label].get('error')}")
            if island and records["runs"][label]["same_window_island"] != "passed":
                raise RuntimeError(f"{label} same-window island smoke failed: {records['runs'][label].get('error')}")
        records["status"] = "passed"
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        records.setdefault("error", str(error))
    (output / "qualification.json").write_text(json.dumps(records, indent=2) + "\n")
    print(json.dumps(records, indent=2))
    return 0 if records["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
