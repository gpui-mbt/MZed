// SPDX-License-Identifier: GPL-3.0-or-later
// This integration is applied only to the pinned, derived Zed application.
mod protocol;

use gpui::{
    App, Bounds, ClickEvent, ContentMask, Context, DispatchPhase, FocusHandle, Hitbox,
    HitboxBehavior, HitboxId, InteractiveElement, IntoElement, MouseButton, MouseDownEvent,
    MouseMoveEvent, MouseUpEvent, ParentElement, Pixels, Render, ScrollWheelEvent,
    StatefulInteractiveElement, Styled, Subscription, WeakEntity, Window, canvas, div, fill, px,
    rgb, size,
};
use protocol::{Native, Press, Scene};
use workspace::{HideStatusItem, ItemHandle, StatusItemView};

pub struct NativeIsland {
    native: Option<Native>,
    reject_next_increment_for_probe: bool,
    press: Press,
    capture: Option<HitboxId>,
    editor_focus: Option<FocusHandle>,
    prior_focus: Option<FocusHandle>,
    press_focus_subscription: Option<Subscription>,
    _subscriptions: Vec<Subscription>,
}

struct Frame {
    bounds: Bounds<Pixels>,
    clipped: Bounds<Pixels>,
    hitbox: Hitbox,
    scene: Option<Scene>,
}

impl NativeIsland {
    pub fn new(window: &mut Window, cx: &mut Context<Self>) -> Self {
        let native = match Native::new(120, 18) {
            Ok(native) => Some(native),
            Err(error) => {
                log::error!("MZed island create failed: {error}");
                None
            }
        };
        let subscriptions = vec![
            cx.on_release_in(window, |this, window, _cx| this.cancel(window)),
            cx.observe_window_activation(window, |this, window, _cx| {
                if !window.is_window_active() {
                    this.cancel(window);
                    this.press.abandon();
                }
            }),
            cx.observe_window_visibility(window, |this, visibility, window, _cx| {
                if !visibility.is_visible() {
                    this.cancel(window);
                    this.press.abandon();
                }
            }),
        ];
        let reject_next_increment_for_probe = std::env::var("MZED_NATIVE_ISLAND_PROBE")
            .is_ok_and(|value| value == "reject-first-increment");
        log::info!("MZed island mounted: gpui.mbt bf965ae, copied scene v1");
        Self {
            native,
            reject_next_increment_for_probe,
            press: Press::default(),
            capture: None,
            editor_focus: None,
            prior_focus: None,
            press_focus_subscription: None,
            _subscriptions: subscriptions,
        }
    }

    fn release_capture(&mut self, window: &mut Window) {
        if self.capture.is_some() && window.captured_hitbox() == self.capture {
            window.release_pointer();
        }
        self.capture = None;
    }

    fn cancel(&mut self, window: &mut Window) {
        self.press.cancel();
        self.prior_focus = None;
        self.press_focus_subscription = None;
        self.release_capture(window);
    }

    fn restore_focus(&mut self, window: &mut Window, cx: &mut App) {
        if window.is_window_active() {
            if let Some(focus) = self
                .prior_focus
                .take()
                .or_else(|| self.editor_focus.clone())
            {
                focus.focus(window, cx);
            }
        }
    }

    fn toggle(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.cancel(window);
        if self.native.take().is_some() {
            log::info!("MZed island disabled and native instance destroyed");
        } else {
            match Native::new(120, 18) {
                Ok(native) => {
                    self.native = Some(native);
                    log::info!("MZed island remounted");
                }
                Err(error) => log::error!("MZed island remount failed: {error}"),
            }
        }
        self.restore_focus(window, cx);
        cx.notify();
    }

    fn prepare(&mut self, bounds: Bounds<Pixels>, window: &mut Window) -> Frame {
        let clipped = bounds.intersect(&window.content_mask().bounds);
        let hitbox = window.insert_hitbox(bounds, HitboxBehavior::Normal);
        if self.press.owned() {
            if self.capture.is_some() && window.captured_hitbox() == self.capture {
                window.capture_pointer(hitbox.id);
                self.capture = Some(hitbox.id);
            } else {
                self.press.cancel();
            }
        }
        let width = f32::from(bounds.size.width).floor() as i32;
        let height = f32::from(bounds.size.height).floor() as i32;
        let visible = clipped.size.width > px(0.)
            && clipped.size.height > px(0.)
            && (1..=4096).contains(&width)
            && (1..=4096).contains(&height);
        let scene = if visible {
            if let Some(native) = self.native.as_mut() {
                match native.resize(width, height) {
                    Ok(()) => Some(native.scene()),
                    Err(error) => {
                        log::error!("MZed island scene failed: {error}; disabling");
                        self.native = None;
                        self.cancel(window);
                        None
                    }
                }
            } else {
                None
            }
        } else {
            self.cancel(window);
            None
        };
        Frame {
            bounds,
            clipped,
            hitbox,
            scene,
        }
    }

    fn with_view(
        weak: &WeakEntity<Self>,
        cx: &mut App,
        update: impl FnOnce(&mut Self, &mut Context<Self>),
    ) {
        if let Err(error) = weak.update(cx, update) {
            log::debug!("MZed island callback after view release: {error}");
        }
    }

    fn paint(frame: Frame, weak: WeakEntity<Self>, window: &mut Window, _cx: &mut App) {
        if let Some(scene) = frame.scene {
            window.with_content_mask(
                Some(ContentMask {
                    bounds: frame.bounds,
                }),
                |window| {
                    window.paint_quad(fill(
                        Bounds::new(
                            frame.bounds.origin,
                            size(px(scene.width as f32), px(scene.height as f32)),
                        ),
                        rgb(scene.rgb),
                    ));
                },
            );
        }
        let Frame {
            clipped, hitbox, ..
        } = frame;
        let scroll_hitbox = hitbox.clone();
        window.on_mouse_event(move |event: &ScrollWheelEvent, phase, window, cx| {
            if phase == DispatchPhase::Bubble
                && scroll_hitbox.should_handle_scroll(window)
                && clipped.size.width > px(0.)
                && clipped.size.height > px(0.)
                && clipped.contains(&event.position)
            {
                window.prevent_default();
                cx.stop_propagation();
            }
        });
        let down_view = weak.clone();
        window.on_mouse_event(move |event: &MouseDownEvent, phase, window, cx| {
            if phase == DispatchPhase::Capture {
                Self::with_view(&down_view, cx, |this, _cx| {
                    if this.press.owned() {
                        this.cancel(window);
                        this.press.abandon();
                    }
                });
                return;
            }
            if phase != DispatchPhase::Bubble
                || !hitbox.is_hovered(window)
                || clipped.size.width <= px(0.)
                || clipped.size.height <= px(0.)
                || !clipped.contains(&event.position)
            {
                return;
            }
            window.prevent_default();
            cx.stop_propagation();
            if event.button != MouseButton::Left || event.modifiers.modified() {
                return;
            }
            Self::with_view(&down_view, cx, |this, cx| {
                if let Some(native) = &this.native {
                    if this.press.begin(native.generation) {
                        this.prior_focus = window.focused(cx);
                        this.press_focus_subscription = this.prior_focus.as_ref().map(|focus| {
                            cx.on_blur(focus, window, |this, window, _cx| this.cancel(window))
                        });
                        window.capture_pointer(hitbox.id);
                        this.capture = Some(hitbox.id);
                        window.prevent_default();
                        cx.stop_propagation();
                        // Force a redraw while down so ownership cannot rely on a frame-local hitbox.
                        cx.notify();
                    }
                }
            });
        });
        let move_view = weak.clone();
        window.on_mouse_event(move |event: &MouseMoveEvent, phase, window, cx| {
            if phase != DispatchPhase::Capture {
                return;
            }
            Self::with_view(&move_view, cx, |this, cx| {
                if this.press.owned() {
                    if window.captured_hitbox() != this.capture {
                        this.cancel(window);
                        this.press.abandon();
                        return;
                    }
                    if event.pressed_button != Some(MouseButton::Left) {
                        this.cancel(window);
                        this.press.abandon();
                        return;
                    }
                    if event.modifiers.modified() {
                        this.press.cancel();
                    }
                    window.prevent_default();
                    cx.stop_propagation();
                }
            });
        });
        window.on_mouse_event(move |event: &MouseUpEvent, phase, window, cx| {
            if phase != DispatchPhase::Capture {
                return;
            }
            Self::with_view(&weak, cx, |this, cx| {
                if !this.press.owned() {
                    return;
                }
                if window.captured_hitbox() != this.capture {
                    this.cancel(window);
                    this.press.abandon();
                    return;
                }
                let generation = this.native.as_ref().map(|native| native.generation);
                let accept = this.press.finish(
                    generation,
                    this.capture.is_some()
                        && event.button == MouseButton::Left
                        && !event.modifiers.modified()
                        && window.is_window_active()
                        && clipped.size.width > px(0.)
                        && clipped.size.height > px(0.)
                        && clipped.contains(&event.position),
                );
                this.release_capture(window);
                window.prevent_default();
                cx.stop_propagation();
                if accept {
                    if let Some(native) = this.native.as_mut() {
                        let result = if std::mem::take(&mut this.reject_next_increment_for_probe) {
                            log::info!("MZed island dispatch rejection probe");
                            native.reject_increment_for_probe()
                        } else {
                            native.increment()
                        };
                        match result {
                            Ok(()) => log::info!("MZed island counter={}", native.scene().counter),
                            Err(error) => {
                                log::error!("MZed island dispatch failed: {error}; disabling");
                                this.native = None;
                            }
                        }
                    }
                }
                this.press_focus_subscription = None;
                if let Some(focus) = this.prior_focus.take() {
                    if window.is_window_active() {
                        focus.focus(window, cx);
                    }
                }
                cx.notify();
            });
        });
    }
}

impl Render for NativeIsland {
    fn render(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let prepare_view = cx.weak_entity();
        let paint_view = cx.weak_entity();
        let label = self
            .native
            .as_ref()
            .map(|native| format!("MB {}", native.scene().counter))
            .unwrap_or_else(|| "MB off".to_owned());
        div()
            .flex()
            .items_center()
            .flex_shrink_0()
            .child(
                div()
                    .id("mzed-island-toggle")
                    .w(px(36.))
                    .h(px(18.))
                    .child("M")
                    .on_any_mouse_down(|_, window, cx| {
                        window.prevent_default();
                        cx.stop_propagation();
                    })
                    .on_click(cx.listener(|this, event, window, cx| {
                        if let ClickEvent::Mouse(event) = event {
                            if event.down.button == MouseButton::Left
                                && event.up.button == MouseButton::Left
                                && !event.down.modifiers.modified()
                                && !event.up.modifiers.modified()
                            {
                                this.toggle(window, cx);
                                cx.stop_propagation();
                            }
                        }
                    })),
            )
            .child(
                canvas(
                    move |bounds, window, cx| match prepare_view
                        .update(cx, |this, _cx| this.prepare(bounds, window))
                    {
                        Ok(frame) => Some(frame),
                        Err(error) => {
                            log::debug!("MZed island prepaint after release: {error}");
                            None
                        }
                    },
                    move |_, frame, window, cx| {
                        if let Some(frame) = frame {
                            Self::paint(frame, paint_view, window, cx);
                        }
                    },
                )
                .w(px(120.))
                .h(px(18.)),
            )
            .child(div().w(px(56.)).child(label))
    }
}

impl StatusItemView for NativeIsland {
    fn set_active_pane_item(
        &mut self,
        active_pane_item: Option<&dyn ItemHandle>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.cancel(window);
        self.editor_focus = active_pane_item.map(|item| item.item_focus_handle(cx));
        self.prior_focus = None;
    }
    fn hide_setting(&self, _cx: &App) -> Option<HideStatusItem> {
        None
    }
}
