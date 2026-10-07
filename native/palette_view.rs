// SPDX-License-Identifier: GPL-3.0-or-later
//! Bounded Linux host for the shared MoonBit command palette.

use std::ops::Range;

use gpui::{
    Action, App, Bounds, ContentMask, Context, DismissEvent, ElementInputHandler, Entity,
    EntityInputHandler, EventEmitter, FocusHandle, Focusable, InteractiveElement, IntoElement,
    KeyUpEvent, ParentElement, Pixels, Point, Render, ShapedLine, SharedString, Styled,
    Subscription, UTF16Selection, WeakEntity, Window, WindowId, canvas, div, fill, point, px, rgb,
    size,
};
use ui::ActiveTheme;
use unicode_bidi::{BidiClass, bidi_class};
use unicode_segmentation::UnicodeSegmentation;
use workspace::{ModalView, MultiWorkspace, Workspace};

use super::{
    ToggleFullScreen,
    palette_protocol::{NativePalette, PaletteMetrics, PaletteSnapshot, SubmitResult},
};

const PALETTE_WIDTH: f32 = 640.0;
const PALETTE_HEIGHT: f32 = 276.0;
const FIELD_WIDTH: f32 = 608.0;
const FIELD_HEIGHT: f32 = 32.0;
const FIELD_X: f32 = 16.0;
const FIELD_Y: f32 = 12.0;
const ROW_TOP: f32 = 56.0;
const ROW_HEIGHT: f32 = 24.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum CloseKey {
    Enter,
    Escape,
}

impl CloseKey {
    fn tag(self) -> i32 {
        match self {
            Self::Enter => 1,
            Self::Escape => 2,
        }
    }

    fn matches(self, key: &str) -> bool {
        match self {
            Self::Enter => key == "enter",
            Self::Escape => key == "escape",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct PendingRelease {
    key: CloseKey,
    was_composing: bool,
    keydown_modifiers: i32,
}

struct KeyMap<'a> {
    tag: i32,
    shared_text: &'a str,
    insert_text: Option<&'a str>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct StyleStamp {
    font: String,
    font_size_bits: u32,
    line_height_bits: u32,
    scale_bits: u32,
}

#[derive(Clone)]
struct CaretStop {
    utf16: usize,
    x: f32,
}

#[derive(Clone)]
struct ShapeData {
    line: ShapedLine,
    metrics_ints: Vec<i32>,
    metrics_doubles: Vec<f64>,
    stops: Vec<CaretStop>,
    line_height: Pixels,
    font_size: Pixels,
    style: StyleStamp,
}

struct PaletteFrame {
    bounds: Bounds<Pixels>,
    field_bounds: Bounds<Pixels>,
    content_bounds: Bounds<Pixels>,
    snapshot: PaletteSnapshot,
    geometry: [f64; 19],
    query: ShapeData,
    rows: Vec<(ShapeData, bool, Bounds<Pixels>)>,
    input_ready: bool,
}

pub struct PaletteView {
    native: Option<NativePalette>,
    focus: FocusHandle,
    workspace: WeakEntity<Workspace>,
    workspace_id: Option<gpui::EntityId>,
    window_id: WindowId,
    view_entity_id: gpui::EntityId,
    style: StyleStamp,
    snapshot: Option<PaletteSnapshot>,
    query_shape: Option<ShapeData>,
    last_field_bounds: Option<Bounds<Pixels>>,
    pending_release: Option<PendingRelease>,
    terminal: bool,
    suspended: bool,
    _subscriptions: Vec<Subscription>,
}

impl EventEmitter<DismissEvent> for PaletteView {}
impl ModalView for PaletteView {}

impl Focusable for PaletteView {
    fn focus_handle(&self, _cx: &App) -> FocusHandle {
        self.focus.clone()
    }
}

impl PaletteView {
    pub fn new(
        workspace: WeakEntity<Workspace>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Self {
        let focus = cx.focus_handle();
        let initial_shape = shape_text("", window).ok();
        let native = initial_shape.as_ref().and_then(|shape| {
            NativePalette::new(
                PaletteMetrics {
                    ints: &shape.metrics_ints,
                    doubles: &shape.metrics_doubles,
                },
                f64::from(FIELD_WIDTH),
                f64::from(FIELD_HEIGHT),
                f64::from(shape.font_size),
            )
            .map_err(|error| log::error!("MZed palette owner open failed: {error}"))
            .ok()
        });
        let snapshot = native.as_ref().and_then(|owner| match owner.snapshot() {
            Ok(value) => Some(value),
            Err(error) => {
                log::error!("MZed palette initial snapshot failed: {error}");
                None
            }
        });
        let style = initial_shape
            .as_ref()
            .map(|shape| shape.style.clone())
            .unwrap_or_else(|| style_stamp(window));
        let window_id = window.window_handle().window_id();
        let workspace_id = workspace.upgrade().map(|workspace| workspace.entity_id());
        let view_entity_id = cx.entity_id();
        let weak = cx.weak_entity();
        let interceptor = cx.intercept_keystrokes(move |event, window, app| {
            if window.window_handle().window_id() != window_id {
                return;
            }
            let _ = weak.update(app, |this, cx| {
                this.intercept_key_down(&event.keystroke, window, cx);
            });
        });
        let blur_subscription = cx.on_blur(&focus, window, move |this, _window, cx| {
            if !this.terminal {
                this.terminal = true;
                this.pending_release = None;
                this.native.take();
                cx.emit(DismissEvent);
            }
        });
        let activation_subscription =
            cx.observe_window_activation(window, move |this, window, cx| {
                if !window.is_window_active() {
                    this.dismiss_without_action(window, cx);
                }
            });
        let visibility_subscription =
            cx.observe_window_visibility(window, move |this, visibility, window, cx| {
                if !visibility.is_visible() {
                    this.dismiss_without_action(window, cx);
                }
            });
        Self {
            native,
            focus,
            workspace,
            workspace_id,
            window_id,
            view_entity_id,
            style,
            snapshot,
            query_shape: initial_shape,
            last_field_bounds: None,
            pending_release: None,
            terminal: false,
            suspended: false,
            _subscriptions: vec![
                interceptor,
                blur_subscription,
                activation_subscription,
                visibility_subscription,
            ],
        }
    }

    fn intercept_key_down(
        &mut self,
        keystroke: &gpui::Keystroke,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if self.window_id != window.window_handle().window_id()
            || !self.focus.contains_focused(window, cx)
            || self.terminal
        {
            return;
        }
        // This is before keybinding resolution, so underlying editor bindings
        // and multi-stroke prefixes cannot observe modal keystrokes.
        cx.stop_propagation();
        window.prevent_default();
        if self.pending_release.is_some() {
            return;
        }
        if is_unqualified_clipboard_shortcut(keystroke) {
            log::debug!("MZed palette clipboard shortcut is outside this Linux profile");
            return;
        }
        let Some(mapped) = map_key(keystroke) else {
            return;
        };
        if self.suspended || self.native.is_none() {
            if mapped.tag == 2 {
                self.dismiss_without_action(window, cx);
            }
            return;
        }
        if let Some(key) = CloseKey::try_from_tag(mapped.tag) {
            let was_composing = self
                .snapshot
                .as_ref()
                .is_some_and(|snapshot| snapshot.composing);
            self.pending_release = Some(PendingRelease {
                key,
                was_composing,
                keydown_modifiers: modifier_bits(keystroke),
            });
            return;
        }
        let Some(owner) = self.native.as_mut() else {
            return;
        };
        let result = if let Some(text) = mapped.insert_text {
            owner.replace(None, text)
        } else {
            owner.input(
                1,
                mapped.tag,
                modifier_bits(keystroke),
                false,
                mapped.shared_text,
            )
        };
        match result {
            Ok(result) => {
                let snapshot = self.finish_or_measure(result, window);
                if let Some(snapshot) = snapshot {
                    self.snapshot = Some(snapshot.clone());
                    self.query_shape = None;
                    if snapshot.effect_kind == 2 && !snapshot.is_open {
                        // Shared Tab-to-blur is a terminal effect without a
                        // physical close-key fence. It has no action token.
                        self.defer_terminal_ack(snapshot.epoch, None, window, cx);
                        return;
                    }
                    cx.notify();
                }
            }
            Err(error) => log::debug!("MZed palette key was rejected: {error}"),
        }
    }

    fn finish_or_measure(
        &mut self,
        result: SubmitResult,
        window: &mut Window,
    ) -> Option<PaletteSnapshot> {
        if self.native.is_none() {
            return None;
        }
        match result {
            SubmitResult::Complete(snapshot) => Some(snapshot),
            SubmitResult::NeedLayout { pending, text } => {
                if text.len() > super::palette_protocol::PALETTE_MAX_QUERY_BYTES {
                    self.native.as_mut()?.abort_pending(pending).ok();
                    return None;
                }
                let shape = match shape_text(&text, window) {
                    Ok(value) if value.style == self.style => value,
                    Ok(_) | Err(_) => {
                        self.native.as_mut()?.abort_pending(pending).ok();
                        return None;
                    }
                };
                let continued = self.native.as_mut()?.continue_with_layout(
                    pending,
                    PaletteMetrics {
                        ints: &shape.metrics_ints,
                        doubles: &shape.metrics_doubles,
                    },
                );
                match continued {
                    Ok(SubmitResult::Complete(snapshot)) => {
                        self.query_shape = Some(shape);
                        Some(snapshot)
                    }
                    Ok(SubmitResult::NeedLayout { pending, .. }) => {
                        self.native.as_mut()?.abort_pending(pending).ok();
                        None
                    }
                    Err(error) => {
                        log::debug!("MZed palette layout continuation failed: {error}");
                        self.native.as_mut()?.abort_pending(pending).ok();
                        None
                    }
                }
            }
        }
    }

    fn on_key_up(&mut self, event: &KeyUpEvent, window: &mut Window, cx: &mut Context<Self>) {
        if self.window_id != window.window_handle().window_id() || self.pending_release.is_none() {
            return;
        }
        if self.terminal || !self.focus.contains_focused(window, cx) {
            // GPUI may have precomputed this capture path before an ancestor
            // moved focus. Never let an old modal's delayed close release
            // dispatch an action after its owner was retired.
            self.pending_release = None;
            return;
        }
        window.prevent_default();
        cx.stop_propagation();
        let Some(pending_release) = self.pending_release else {
            return;
        };
        let close_key = pending_release.key;
        if !close_key.matches(&event.keystroke.key) {
            return;
        }
        let Some(current_snapshot) = self.snapshot.as_ref() else {
            self.pending_release = None;
            return;
        };
        if self.native.is_none() {
            self.pending_release = None;
            return;
        }
        let current_composing = current_snapshot.composing;
        let press_repeat = delayed_press_is_repeat(pending_release, current_composing);
        let press_result = self.native.as_mut().map(|owner| {
            owner.input(
                1,
                close_key.tag(),
                pending_release.keydown_modifiers,
                press_repeat,
                "",
            )
        });
        let press_snapshot = match press_result
            .and_then(Result::ok)
            .and_then(|result| self.finish_or_measure(result, window))
        {
            Some(snapshot) => snapshot,
            None => {
                self.pending_release = None;
                self.dismiss_without_action(window, cx);
                return;
            }
        };
        let terminal_press = press_snapshot.effect_kind == 2 && !press_snapshot.is_open;
        let release_result = self.native.as_mut().map(|owner| {
            owner.input(
                2,
                close_key.tag(),
                modifier_bits_from_keyup(&event.keystroke),
                false,
                "",
            )
        });
        let release_snapshot = release_result
            .and_then(Result::ok)
            .and_then(|result| self.finish_or_measure(result, window));
        let release_succeeded = release_snapshot.is_some();
        let display_snapshot = release_snapshot.unwrap_or_else(|| press_snapshot.clone());
        self.snapshot = Some(display_snapshot);
        self.pending_release = None;
        if terminal_press {
            let action_id = if release_succeeded && !press_snapshot.composing {
                press_snapshot.action_id.clone()
            } else {
                None
            };
            self.defer_terminal_ack(press_snapshot.epoch, action_id, window, cx);
        } else {
            cx.notify();
        }
    }

    fn defer_terminal_ack(
        &mut self,
        epoch: i32,
        action_id: Option<String>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.terminal = true;
        let Some(mut owner) = self.native.take() else {
            cx.emit(DismissEvent);
            return;
        };
        let workspace = self.workspace.clone();
        let workspace_id = self.workspace_id;
        let view_entity_id = self.view_entity_id;
        // Retire the native input owner before emitting dismissal. Keep the FFI
        // guard strongly captured outside the view so dropping the modal cannot
        // skip its terminal ACK.
        window.retire_text_input_owner();
        cx.emit(DismissEvent);
        window.defer(cx, move |window, app| {
            let old_modal_is_active = workspace
                .update(app, |workspace, cx| {
                    workspace
                        .active_modal::<PaletteView>(cx)
                        .is_some_and(|modal| modal.entity_id() == view_entity_id)
                })
                .unwrap_or(false);
            if old_modal_is_active {
                drop(owner);
                return;
            }
            let captured_workspace_is_live = workspace.update(app, |_, _| ()).is_ok();
            let displayed_workspace_matches = workspace_id.is_some_and(|expected| {
                window
                    .root::<MultiWorkspace>()
                    .flatten()
                    .is_some_and(|multi_workspace| {
                        multi_workspace.read(app).workspace().entity_id() == expected
                    })
            });
            if let Err(error) = owner.finish_close(epoch) {
                log::error!("MZed palette close ACK rejected: {error}");
                drop(owner);
                return;
            }
            // finish_close consumes the shared terminal before the guard is
            // dropped and before an allowlisted host action can reenter GPUI.
            drop(owner);
            if !captured_workspace_is_live || !displayed_workspace_matches {
                return;
            }
            let action = match action_id.as_deref() {
                Some("mzed.open-settings-file") => {
                    Some(zed_actions::OpenSettingsFile.boxed_clone())
                }
                Some("mzed.toggle-full-screen") => Some(ToggleFullScreen.boxed_clone()),
                _ => None,
            };
            if let Some(action) = action {
                window.dispatch_action(action, app);
            }
        });
    }

    fn dismiss_without_action(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if self.terminal {
            return;
        }
        self.terminal = true;
        self.pending_release = None;
        window.retire_text_input_owner();
        self.native.take();
        cx.emit(DismissEvent);
    }

    fn prepare(&mut self, bounds: Bounds<Pixels>, window: &mut Window) -> Option<PaletteFrame> {
        let stamp = style_stamp(window);
        if stamp != self.style {
            self.suspended = true;
        }
        if self.suspended || self.terminal {
            return None;
        }
        let snapshot = self
            .snapshot
            .clone()
            .or_else(|| self.native.as_ref()?.snapshot().ok())?;
        let owner = self.native.as_ref()?;
        let geometry = owner.geometry().ok()?;
        let query = match self.query_shape.clone() {
            Some(shape)
                if shape.style == stamp && snapshot.displayed == shape.line.text.as_ref() =>
            {
                shape
            }
            _ => shape_text(&snapshot.displayed, window).ok()?,
        };
        let field_bounds = Bounds::new(
            bounds.origin + point(px(FIELD_X), px(FIELD_Y)),
            size(px(FIELD_WIDTH), px(FIELD_HEIGHT)),
        );
        let content_bounds = Bounds::new(
            field_bounds.origin + point(px(geometry[4] as f32), px(geometry[5] as f32)),
            size(px(geometry[6] as f32), px(geometry[7] as f32)),
        );
        self.last_field_bounds = Some(field_bounds);
        let mut rows = Vec::with_capacity(snapshot.rows.len());
        for (index, row) in snapshot.rows.iter().enumerate() {
            let shape = shape_row_label(&row.label, window).ok()?;
            let row_bounds = Bounds::new(
                bounds.origin + point(px(FIELD_X), px(ROW_TOP + ROW_HEIGHT * index as f32)),
                size(px(FIELD_WIDTH), px(ROW_HEIGHT)),
            );
            rows.push((shape, row.selected, row_bounds));
        }
        self.query_shape = Some(query.clone());
        Some(PaletteFrame {
            bounds,
            field_bounds,
            content_bounds,
            snapshot: snapshot.clone(),
            geometry,
            query,
            rows,
            input_ready: !self.suspended && !self.terminal && snapshot.is_open,
        })
    }

    fn paint(
        weak: WeakEntity<Self>,
        frame: PaletteFrame,
        window: &mut Window,
        cx: &mut App,
        focus: &FocusHandle,
        view: Entity<Self>,
    ) {
        let field = frame.field_bounds;
        let content = frame.content_bounds;
        let text_origin = field.origin + query_text_offset(&frame.geometry);
        let field_bg = Bounds::new(field.origin, field.size);
        let mut paint_ok = true;
        window.with_content_mask(
            Some(ContentMask {
                bounds: frame.bounds,
            }),
            |window| {
                window.paint_quad(fill(field_bg, rgb(0x1f2329)));
                window.with_content_mask(Some(ContentMask { bounds: content }), |window| {
                    if let (Some(start), Some(end)) = (
                        frame
                            .query
                            .stops
                            .iter()
                            .find(|stop| stop.utf16 == frame.snapshot.anchor_utf16 as usize),
                        frame
                            .query
                            .stops
                            .iter()
                            .find(|stop| stop.utf16 == frame.snapshot.head_utf16 as usize),
                    ) {
                        if start.utf16 != end.utf16 {
                            let selected = Bounds::new(
                                text_origin + point(px(start.x.min(end.x)), px(0.0)),
                                size(px((end.x - start.x).abs()), content.size.height),
                            );
                            window.paint_quad(fill(selected, rgb(0x365389)));
                        }
                    }
                    if let Some(marked) = frame.snapshot.marked.as_ref() {
                        if let (Some(start), Some(end)) = (
                            frame
                                .query
                                .stops
                                .iter()
                                .find(|stop| stop.utf16 == marked.start),
                            frame
                                .query
                                .stops
                                .iter()
                                .find(|stop| stop.utf16 == marked.end),
                        ) {
                            let underline = Bounds::new(
                                text_origin
                                    + point(px(start.x), px(content.size.height.as_f32() - 2.0)),
                                size(px((end.x - start.x).max(0.0)), px(1.0)),
                            );
                            window.paint_quad(fill(underline, rgb(0xe0b35a)));
                        }
                    }
                    if frame
                        .query
                        .line
                        .paint(
                            text_origin,
                            frame.query.line_height,
                            gpui::TextAlign::Left,
                            None,
                            window,
                            cx,
                        )
                        .is_err()
                    {
                        paint_ok = false;
                    }
                    let caret = Bounds::new(
                        field.origin
                            + point(px(frame.geometry[9] as f32), px(frame.geometry[10] as f32)),
                        size(px(frame.geometry[11] as f32), px(frame.geometry[12] as f32)),
                    );
                    if frame.input_ready && frame.snapshot.anchor_utf16 == frame.snapshot.head_utf16
                    {
                        window.paint_quad(fill(caret, rgb(0xd7dae0)));
                    }
                });
                for (shape, selected, bounds) in &frame.rows {
                    if *selected {
                        window.paint_quad(fill(*bounds, rgb(0x303943)));
                    }
                    let row_origin = bounds.origin + point(px(6.0), px(2.0));
                    if shape
                        .line
                        .paint(
                            row_origin,
                            shape.line_height,
                            gpui::TextAlign::Left,
                            None,
                            window,
                            cx,
                        )
                        .is_err()
                    {
                        paint_ok = false;
                    }
                }
            },
        );
        if !paint_ok {
            let _ = weak.update(cx, |this, _cx| this.suspended = true);
            window.retire_text_input_owner();
            return;
        }
        if frame.input_ready {
            window.handle_input(focus, ElementInputHandler::new(field, view), cx);
        }
    }

    fn current_snapshot(&self) -> Option<PaletteSnapshot> {
        self.snapshot.clone()
    }

    fn current_shape(&mut self, window: &Window) -> Option<ShapeData> {
        let snapshot = self.snapshot.as_ref()?.clone();
        if let Some(shape) = self.query_shape.as_ref()
            && shape.style == self.style
            && shape.line.text.as_ref() == snapshot.displayed
        {
            return Some(shape.clone());
        }
        let shape = shape_text(&snapshot.displayed, window).ok()?;
        if shape.style != self.style {
            self.suspended = true;
            return None;
        }
        self.query_shape = Some(shape.clone());
        Some(shape)
    }
}

impl EntityInputHandler for PaletteView {
    fn text_for_range(
        &mut self,
        range: Range<usize>,
        adjusted_range: &mut Option<Range<usize>>,
        _window: &mut Window,
        _cx: &mut Context<Self>,
    ) -> Option<String> {
        let snapshot = self.current_snapshot()?;
        let bytes = utf16_range_to_bytes(&snapshot.displayed, range)?;
        if let Some(text) = snapshot.displayed.get(bytes) {
            adjusted_range.take();
            Some(text.to_owned())
        } else {
            None
        }
    }

    fn selected_text_range(
        &mut self,
        _ignore_disabled_input: bool,
        _window: &mut Window,
        _cx: &mut Context<Self>,
    ) -> Option<UTF16Selection> {
        let snapshot = self.current_snapshot()?;
        Some(UTF16Selection {
            range: snapshot.anchor_utf16.min(snapshot.head_utf16) as usize
                ..snapshot.anchor_utf16.max(snapshot.head_utf16) as usize,
            reversed: snapshot.anchor_utf16 > snapshot.head_utf16,
        })
    }

    fn marked_text_range(
        &self,
        _window: &mut Window,
        _cx: &mut Context<Self>,
    ) -> Option<Range<usize>> {
        self.snapshot.as_ref()?.marked.clone()
    }

    fn unmark_text(&mut self, window: &mut Window, _cx: &mut Context<Self>) {
        if !self.accepting_input() {
            return;
        }
        let result = self.native.as_mut().map(NativePalette::unmark_composition);
        match result {
            Some(Ok(result)) => {
                if let Some(snapshot) = self.finish_or_measure(result, window) {
                    self.snapshot = Some(snapshot);
                    window.refresh();
                }
            }
            Some(Err(error)) => log::debug!("MZed palette unmark rejected: {error}"),
            None => (),
        }
    }

    fn paste(&mut self, _item: gpui::ClipboardItem, _window: &mut Window, _cx: &mut Context<Self>) {
        // This ABI has no clipboard success receipt, so paste remains closed
        // until the host can preserve the shared Cut/Paste transaction rules.
    }

    fn replace_text_in_range(
        &mut self,
        range: Option<Range<usize>>,
        text: &str,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if !self.accepting_input() {
            return;
        }
        let result = self.native.as_mut().map(|owner| owner.replace(range, text));
        match result {
            Some(Ok(result)) => {
                if let Some(snapshot) = self.finish_or_measure(result, window) {
                    self.snapshot = Some(snapshot);
                    cx.notify();
                }
            }
            Some(Err(error)) => log::debug!("MZed palette text replacement rejected: {error}"),
            None => (),
        }
    }

    fn cancel_text_composition(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if !self.accepting_input() {
            return;
        }
        let result = self.native.as_mut().map(NativePalette::cancel_composition);
        match result {
            Some(Ok(result)) => {
                if let Some(snapshot) = self.finish_or_measure(result, window) {
                    self.snapshot = Some(snapshot);
                    cx.notify();
                }
            }
            Some(Err(error)) => log::debug!("MZed palette composition cancel rejected: {error}"),
            None => (),
        }
    }

    fn replace_and_mark_text_in_range(
        &mut self,
        range: Option<Range<usize>>,
        new_text: &str,
        selection: Option<Range<usize>>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if !self.accepting_input() {
            return;
        }
        let result = self
            .native
            .as_mut()
            .map(|owner| owner.mark(range, new_text, selection));
        match result {
            Some(Ok(result)) => {
                if let Some(snapshot) = self.finish_or_measure(result, window) {
                    self.snapshot = Some(snapshot);
                    cx.notify();
                }
            }
            Some(Err(error)) => log::debug!("MZed palette composition update rejected: {error}"),
            None => (),
        }
    }

    fn bounds_for_range(
        &mut self,
        range_utf16: Range<usize>,
        element_bounds: Bounds<Pixels>,
        window: &mut Window,
        _cx: &mut Context<Self>,
    ) -> Option<Bounds<Pixels>> {
        let shape = self.current_shape(window)?;
        let start = shape
            .stops
            .iter()
            .find(|stop| stop.utf16 == range_utf16.start)?;
        let end = shape
            .stops
            .iter()
            .find(|stop| stop.utf16 == range_utf16.end)?;
        let geometry = self.native.as_ref()?.geometry().ok()?;
        let content_x = query_x_offset(&geometry);
        let caret_y = geometry[10] as f32;
        let caret_height = geometry[12] as f32;
        Some(Bounds::new(
            element_bounds.origin + point(px(content_x + start.x), px(caret_y)),
            size(px((end.x - start.x).max(0.0)), px(caret_height)),
        ))
    }

    fn character_index_for_point(
        &mut self,
        point: Point<Pixels>,
        window: &mut Window,
        _cx: &mut Context<Self>,
    ) -> Option<usize> {
        let shape = self.current_shape(window)?;
        let field_bounds = self.last_field_bounds?;
        if !field_bounds.contains(&point) {
            return None;
        }
        let geometry = self.native.as_ref()?.geometry().ok()?;
        let content_x = query_x_offset(&geometry);
        let local_x = point.x - field_bounds.origin.x - px(content_x);
        shape
            .stops
            .iter()
            .min_by(|a, b| {
                (a.x - local_x.as_f32())
                    .abs()
                    .total_cmp(&(b.x - local_x.as_f32()).abs())
            })
            .map(|stop| stop.utf16)
    }

    fn text_length_utf16(
        &mut self,
        _window: &mut Window,
        _cx: &mut Context<Self>,
    ) -> Option<usize> {
        Some(self.snapshot.as_ref()?.displayed.encode_utf16().count())
    }

    fn accepts_text_input(&self, _window: &mut Window, _cx: &mut Context<Self>) -> bool {
        self.accepting_input()
    }

    fn prefers_direct_ime_commit_as_text(
        &self,
        _window: &mut Window,
        _cx: &mut Context<Self>,
    ) -> bool {
        self.accepting_input()
    }
}

impl PaletteView {
    fn accepting_input(&self) -> bool {
        !self.terminal
            && !self.suspended
            && self.native.is_some()
            && self
                .snapshot
                .as_ref()
                .is_some_and(|snapshot| snapshot.is_open)
    }
}

impl Render for PaletteView {
    fn render(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let weak_prepare = cx.weak_entity();
        let weak_paint = cx.weak_entity();
        let focus = self.focus.clone();
        let entity = cx.entity();
        let key_up = cx.listener(Self::on_key_up);
        let theme = cx.theme();
        let mut root = div()
            .track_focus(&self.focus)
            .capture_key_up(move |event, window, cx| key_up(event, window, cx))
            .on_mouse_down(
                gpui::MouseButton::Left,
                cx.listener(|this, _, window, cx| {
                    window.focus(&this.focus, cx);
                }),
            )
            .w(px(PALETTE_WIDTH))
            .h(px(PALETTE_HEIGHT))
            .bg(theme.colors().elevated_surface_background)
            .border_1()
            .border_color(theme.colors().border_variant)
            .rounded_md();
        if self.suspended || self.native.is_none() {
            root = root.child(
                div()
                    .p_4()
                    .child("Palette input is suspended. Press Escape to close."),
            );
        } else {
            root = root.child(
                canvas(
                    move |bounds, window, cx| {
                        weak_prepare
                            .update(cx, |this, _cx| this.prepare(bounds, window))
                            .ok()
                            .flatten()
                    },
                    move |_, frame, window, cx| {
                        if let Some(frame) = frame {
                            Self::paint(weak_paint, frame, window, cx, &focus, entity);
                        }
                    },
                )
                .w(px(PALETTE_WIDTH))
                .h(px(PALETTE_HEIGHT)),
            );
        }
        root
    }
}

fn style_stamp(window: &Window) -> StyleStamp {
    let text_style = window.text_style();
    let font_size = text_style.font_size.to_pixels(window.rem_size());
    let line_height = window.pixel_snap(
        text_style
            .line_height
            .to_pixels(font_size.into(), window.rem_size()),
    );
    StyleStamp {
        font: format!("{:?}", text_style.font()),
        font_size_bits: font_size.as_f32().to_bits(),
        line_height_bits: line_height.as_f32().to_bits(),
        scale_bits: window.scale_factor().to_bits(),
    }
}

fn shape_text(text: &str, window: &Window) -> Result<ShapeData, &'static str> {
    if text.len() > super::palette_protocol::PALETTE_MAX_QUERY_BYTES
        || text.contains('\n')
        || text.contains('\r')
    {
        return Err("text exceeds the admitted single-line profile");
    }
    for grapheme in text.graphemes(true) {
        if grapheme.chars().count() != 1 {
            return Err("multi-scalar grapheme geometry is not admitted");
        }
    }
    for scalar in text.chars() {
        if matches!(
            bidi_class(scalar),
            BidiClass::R
                | BidiClass::AL
                | BidiClass::AN
                | BidiClass::RLE
                | BidiClass::RLO
                | BidiClass::RLI
                | BidiClass::LRE
                | BidiClass::LRO
                | BidiClass::LRI
                | BidiClass::FSI
                | BidiClass::PDI
                | BidiClass::PDF
        ) {
            return Err("bidirectional text is not admitted");
        }
    }
    let stamp = style_stamp(window);
    let text_style = window.text_style();
    let font_size = text_style.font_size.to_pixels(window.rem_size());
    let line_height = window.pixel_snap(
        text_style
            .line_height
            .to_pixels(font_size.into(), window.rem_size()),
    );
    if font_size.as_f32() <= 0.0 || line_height.as_f32() <= 0.0 {
        return Err("invalid text style");
    }
    let shared: SharedString = text.to_owned().into();
    let run = text_style.to_run(text.len());
    let line = window
        .text_system()
        .shape_line(shared, font_size, &[run], None);
    let mut glyphs = line
        .runs
        .iter()
        .flat_map(|run| run.glyphs.iter())
        .collect::<Vec<_>>();
    glyphs.sort_by_key(|glyph| glyph.index);
    let scalars = text.char_indices().collect::<Vec<_>>();
    if glyphs.len() != scalars.len() {
        return Err("shape did not produce exactly one glyph per scalar");
    }
    let mut stops = Vec::with_capacity(scalars.len() + 1);
    let mut utf16_offset = 0_usize;
    let mut previous_x = -1.0_f32;
    let mut ints = vec![1, 0, checked_i32(scalars.len() + 1)?];
    for (index, (byte_offset, scalar)) in scalars.iter().enumerate() {
        let glyph = glyphs[index];
        let x = glyph.position.x.as_f32();
        if glyph.index != *byte_offset || glyph.id.0 == 0 || !x.is_finite() || x <= previous_x {
            return Err("shape contains a merged, missing, or non-monotone glyph stop");
        }
        stops.push(CaretStop {
            utf16: utf16_offset,
            x,
        });
        ints.push(checked_i32(utf16_offset)?);
        ints.push(1);
        previous_x = x;
        utf16_offset += scalar.len_utf16();
    }
    let end_x = line.width().as_f32();
    if !end_x.is_finite() || end_x < previous_x {
        return Err("shape produced an invalid trailing caret");
    }
    stops.push(CaretStop {
        utf16: utf16_offset,
        x: end_x,
    });
    ints.push(checked_i32(utf16_offset)?);
    ints.push(1);
    let line_height_f32 = line_height.as_f32();
    let ascent = line.ascent.as_f32();
    let descent = line.descent.as_f32();
    let baseline = (line_height_f32 - ascent - descent) / 2.0 + ascent;
    if !baseline.is_finite() || baseline < 0.0 || baseline > line_height_f32 {
        return Err("shape produced an invalid baseline");
    }
    let width = end_x.max(0.0);
    let mut doubles = vec![
        0.0,
        0.0,
        f64::from(width),
        f64::from(line_height),
        // This profile admits host shaping and rejects paint errors. It does
        // not claim exact CPU ink bounds or Pango-equivalent bearings.
        0.0,
        0.0,
        f64::from(width),
        f64::from(line_height),
        f64::from(baseline),
    ];
    for stop in &stops {
        doubles.extend_from_slice(&[
            f64::from(stop.x),
            0.0,
            0.0,
            f64::from(line_height),
            f64::from(stop.x),
            0.0,
            0.0,
            f64::from(line_height),
        ]);
    }
    Ok(ShapeData {
        line,
        metrics_ints: ints,
        metrics_doubles: doubles,
        stops,
        line_height,
        font_size,
        style: stamp,
    })
}

fn shape_row_label(text: &str, window: &Window) -> Result<ShapeData, &'static str> {
    let style = style_stamp(window);
    let text_style = window.text_style();
    let font_size = text_style.font_size.to_pixels(window.rem_size());
    let line_height = window.pixel_snap(
        text_style
            .line_height
            .to_pixels(font_size.into(), window.rem_size()),
    );
    if font_size.as_f32() <= 0.0 || line_height.as_f32() <= 0.0 {
        return Err("invalid row text style");
    }
    let shared: SharedString = text.to_owned().into();
    let run = text_style.to_run(text.len());
    let line = window
        .text_system()
        .shape_line(shared, font_size, &[run], None);
    Ok(ShapeData {
        line,
        metrics_ints: Vec::new(),
        metrics_doubles: Vec::new(),
        stops: Vec::new(),
        line_height,
        font_size,
        style,
    })
}

fn checked_i32(value: usize) -> Result<i32, &'static str> {
    i32::try_from(value).map_err(|_| "text offset overflow")
}

fn query_x_offset(geometry: &[f64; 19]) -> f32 {
    geometry[4] as f32 - geometry[8] as f32 + geometry[13] as f32
}

fn query_global_x(field_origin: Pixels, geometry: &[f64; 19]) -> Pixels {
    field_origin + px(query_x_offset(geometry))
}

fn query_text_offset(geometry: &[f64; 19]) -> Point<Pixels> {
    point(
        px(query_x_offset(geometry)),
        px(geometry[5] as f32 + geometry[14] as f32),
    )
}

fn modifier_bits(keystroke: &gpui::Keystroke) -> i32 {
    (keystroke.modifiers.shift as i32)
        | ((keystroke.modifiers.control as i32) << 1)
        | ((keystroke.modifiers.alt as i32) << 2)
        | ((keystroke.modifiers.platform as i32) << 3)
}

fn delayed_press_is_repeat(pending: PendingRelease, currently_composing: bool) -> bool {
    match pending.key {
        CloseKey::Enter => pending.was_composing,
        CloseKey::Escape => pending.was_composing && !currently_composing,
    }
}

fn modifier_bits_from_keyup(keystroke: &gpui::Keystroke) -> i32 {
    modifier_bits(keystroke)
}

fn is_unqualified_clipboard_shortcut(keystroke: &gpui::Keystroke) -> bool {
    (keystroke.modifiers.control || keystroke.modifiers.platform)
        && matches!(keystroke.key.to_ascii_lowercase().as_str(), "c" | "v" | "x")
}

fn map_key(keystroke: &gpui::Keystroke) -> Option<KeyMap<'_>> {
    let key = keystroke.key.as_str();
    let modifiers = keystroke.modifiers;
    let no_text_modifier = !modifiers.control && !modifiers.platform;
    Some(match key {
        "enter" => KeyMap {
            tag: 1,
            shared_text: "",
            insert_text: None,
        },
        "escape" => KeyMap {
            tag: 2,
            shared_text: "",
            insert_text: None,
        },
        "backspace" => KeyMap {
            tag: 3,
            shared_text: "",
            insert_text: None,
        },
        "delete" => KeyMap {
            tag: 4,
            shared_text: "",
            insert_text: None,
        },
        "tab" => KeyMap {
            tag: 5,
            shared_text: "",
            insert_text: None,
        },
        "left" => KeyMap {
            tag: 6,
            shared_text: "",
            insert_text: None,
        },
        "right" => KeyMap {
            tag: 7,
            shared_text: "",
            insert_text: None,
        },
        "up" => KeyMap {
            tag: 8,
            shared_text: "",
            insert_text: None,
        },
        "down" => KeyMap {
            tag: 9,
            shared_text: "",
            insert_text: None,
        },
        "home" => KeyMap {
            tag: 10,
            shared_text: "",
            insert_text: None,
        },
        "end" => KeyMap {
            tag: 11,
            shared_text: "",
            insert_text: None,
        },
        "pageup" => KeyMap {
            tag: 12,
            shared_text: "",
            insert_text: None,
        },
        "pagedown" => KeyMap {
            tag: 13,
            shared_text: "",
            insert_text: None,
        },
        "space" if no_text_modifier => KeyMap {
            tag: 14,
            shared_text: " ",
            insert_text: Some(keystroke.key_char.as_deref().unwrap_or(" ")),
        },
        "space" => KeyMap {
            tag: 14,
            shared_text: " ",
            insert_text: None,
        },
        _ => {
            let shared_text = if no_text_modifier {
                keystroke.key_char.as_deref().unwrap_or(key)
            } else {
                key
            };
            if shared_text.chars().count() != 1 {
                return None;
            }
            KeyMap {
                tag: 15,
                shared_text,
                insert_text: if no_text_modifier {
                    keystroke
                        .key_char
                        .as_deref()
                        .filter(|text| !text.is_empty())
                } else {
                    None
                },
            }
        }
    })
}

fn utf16_range_to_bytes(text: &str, range: Range<usize>) -> Option<Range<usize>> {
    if range.start > range.end {
        return None;
    }
    fn byte_for_offset(text: &str, target: usize) -> Option<usize> {
        let mut utf16 = 0;
        for (byte, scalar) in text.char_indices() {
            if utf16 == target {
                return Some(byte);
            }
            utf16 += scalar.len_utf16();
            if utf16 > target {
                return None;
            }
        }
        (utf16 == target).then_some(text.len())
    }
    Some(byte_for_offset(text, range.start)?..byte_for_offset(text, range.end)?)
}

impl CloseKey {
    fn try_from_tag(tag: i32) -> Option<Self> {
        match tag {
            1 => Some(Self::Enter),
            2 => Some(Self::Escape),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn range_adapter_rejects_utf16_surrogate_interiors() {
        assert_eq!(utf16_range_to_bytes("a😀b", 1..3), Some(1..5));
        assert_eq!(utf16_range_to_bytes("a😀b", 2..3), None);
        assert_eq!(utf16_range_to_bytes("a😀b", 1..2), None);
    }

    #[test]
    fn close_key_release_matches_only_stable_terminal_keys() {
        assert!(CloseKey::Enter.matches("enter"));
        assert!(!CloseKey::Enter.matches("?"));
        assert!(CloseKey::Escape.matches("escape"));
    }

    #[test]
    fn composing_enter_release_survives_commit_before_key_up() {
        let first = PendingRelease {
            key: CloseKey::Enter,
            was_composing: true,
            keydown_modifiers: 0,
        };
        assert!(delayed_press_is_repeat(first, false));
        let fresh = PendingRelease {
            was_composing: false,
            ..first
        };
        assert!(!delayed_press_is_repeat(fresh, false));
    }

    #[test]
    fn composing_escape_cancels_once_then_a_fresh_escape_can_dismiss() {
        let first = PendingRelease {
            key: CloseKey::Escape,
            was_composing: true,
            keydown_modifiers: 0,
        };
        assert!(!delayed_press_is_repeat(first, true));
        assert!(delayed_press_is_repeat(first, false));
        let fresh = PendingRelease {
            was_composing: false,
            ..first
        };
        assert!(!delayed_press_is_repeat(fresh, false));
    }

    #[test]
    fn control_selection_and_undo_keys_have_logical_characters_without_text_insert() {
        for key in ["a", "z", "y"] {
            let event = gpui::Keystroke {
                modifiers: gpui::Modifiers {
                    control: true,
                    ..Default::default()
                },
                key: key.to_owned(),
                key_char: None,
            };
            let mapped = map_key(&event).unwrap();
            assert_eq!(mapped.tag, 15);
            assert_eq!(mapped.shared_text, key);
            assert!(mapped.insert_text.is_none());
        }
    }

    #[test]
    fn printable_and_clipboard_shortcuts_have_separate_policies() {
        let letter = gpui::Keystroke {
            modifiers: Default::default(),
            key: "a".to_owned(),
            key_char: Some("a".to_owned()),
        };
        let mapped = map_key(&letter).unwrap();
        assert_eq!(mapped.insert_text, Some("a"));
        let cut = gpui::Keystroke {
            modifiers: gpui::Modifiers {
                control: true,
                ..Default::default()
            },
            key: "x".to_owned(),
            key_char: None,
        };
        assert!(is_unqualified_clipboard_shortcut(&cut));
        assert_eq!(map_key(&cut).unwrap().shared_text, "x");
    }

    #[test]
    fn query_geometry_applies_field_origin_padding_run_origin_and_scroll_once() {
        let mut geometry = [0.0; 19];
        geometry[4] = 4.0;
        geometry[5] = 4.0;
        geometry[8] = 11.0;
        geometry[13] = 2.0;
        geometry[14] = 1.0;
        assert_eq!(query_x_offset(&geometry), -5.0);
        assert_eq!(query_global_x(px(500.0), &geometry), px(495.0));
    }

    #[test]
    fn delayed_close_press_keeps_keydown_modifiers_until_release() {
        let down = gpui::Keystroke {
            modifiers: gpui::Modifiers {
                control: true,
                ..Default::default()
            },
            key: "enter".to_owned(),
            key_char: None,
        };
        let up = gpui::Keystroke {
            modifiers: Default::default(),
            key: "enter".to_owned(),
            key_char: None,
        };
        let pending = PendingRelease {
            key: CloseKey::Enter,
            was_composing: false,
            keydown_modifiers: modifier_bits(&down),
        };
        assert_eq!(pending.keydown_modifiers, 2);
        assert_eq!(modifier_bits_from_keyup(&up), 0);
    }
}
