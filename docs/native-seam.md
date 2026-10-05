# Native island seam investigation

This records the baseline-stage source inspection, not runtime acceptance.
The subsequent opt-in implementation and its bounded qualification are described
in [native-island.md](native-island.md).

## Candidate: host-composited copied quads

Keep Rust Zed/GPUI authoritative for the UI thread, app/event loop, window, GPU,
frame scheduling and editor focus. A persistent controller owns one native
MoonBit instance. MoonBit owns the bounded island's state, layout and copied
quad output. A frame-local Rust custom Element or canvas paints those quads in
the existing editor window; dropping a rendered element must not destroy the
persistent MoonBit instance.

Verified at Zed `76659a55a8c10ed355a070f8764a0b1733e3c115`:

- `crates/gpui/src/elements/canvas.rs`: canvas supplies bounds and painting access.
- `crates/gpui/src/element.rs`: request_layout / prepaint / paint lifecycle.
- `crates/gpui/src/window.rs`: paint_quad, with_content_mask, insert_hitbox,
  on_mouse_event, on_key_event, focused/focus, capture_pointer/release_pointer.
- `crates/workspace/src/status_bar.rs`: a StatusItemView can be mounted and
  removed inside an existing workspace; it is one possible bounded host region.

The gpui.mbt portable Element lifecycle, SceneSnapshot and quad-only SceneItem
provide an appropriate starting vocabulary. Its App::run_ready limits callback
count, not individual callback wall time. Native handlers must initially be
small, nonblocking and total: an in-process timeout cannot safely preempt an
arbitrary native function.

The inspected gpui.mbt tree was `f8b9006476e1f932e3b89a1a9c64f6e3fad14ff6`;
this inspection does not update its independent behavioral comparison pin.

## Rejected shortcuts

The current gpui.mbt Ubuntu backend calls wl_display_connect and creates its
own wl_surface / xdg_surface / xdg_toplevel and GPU state. Its platform Host
creates owned windows; it is not a host-supplied surface API. Launching that
backend alongside Zed gives another toplevel and cannot pass same-window proof.

Zed's Window::paint_surface is macOS-only and uses CVPixelBuffer at this pin;
it is not an available Linux shared-surface bridge. Wayland subsurface or
shared-texture embedding would add lifetime/synchronization/backend work.
Do not assume a stable Rust ABI or pass raw OS/GPU handles between runtimes.

## Next measured checkpoints

1. Qualify the unchanged baseline with native file-open/edit/save evidence.
2. Prove Rust → native MoonBit initialization, create/dispatch/copy/destroy in
   the same process, with a versioned C-compatible bounded envelope. No current
   host-callable MoonBit island ABI was found. This checkpoint alone is logic
   linkage, not a visual island.
3. Paint a small opaque rectangle subset in the same editor window from
   MoonBit-owned state. Reject unsupported resources and rotation/shear. Use
   logical coordinates, host scaling exactly once, and intersect host clipping.
4. Give one runtime ownership of an entire input sequence. Gate on clipped
   visible bounds, use one dispatch phase, carry sequence ownership across
   redraws and renew changing hitboxes. Restore editor focus explicitly.
   Specify modifiers/wheel behavior; do not install a MoonBit text-input handler
   in the first proof or steal an existing editor IME composition.
5. Use integer instance/generation/request/sequence identity, caller-owned
   bounded buffers and typed errors. Reject stale/duplicate/out-of-order replies.
   Serialize on the host UI thread, never share mutable runtime entity graphs.
6. Test toggle-off fallback, hidden/zero-sized/clipped regions, resize/scales,
   repeated destruction/remount, late results, failure injection, original
   editing afterward, and predeclared resource/frame/input churn bounds.

Do not claim issue 0019 acceptance until actual native runs pass every required
gate. Do not substitute a second window, logic-only model, Electron/Tauri, or a
replacement editor. New Windows work remains deferred; preserve existing checks.
