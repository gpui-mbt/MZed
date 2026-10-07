# Linux command palette profile

This document describes the bounded MZed integration work. The pinned derived
Zed package passed a serial compile/link check. In a dedicated X11 profile, the
MZed palette opened in the existing Zed window, rendered its field and selected
command row legibly, accepted a keyboard query, filtered to `Open Settings File`,
and closed with exit code 0. The query `Settings` matched; lowercase `settings`
did not, because the pinned shared picker currently filters case-sensitively.
The shutdown log also contains an inotify-watch removal warning and a GPUI
`window not found` error. This smoke covers the X11 window, palette, query, and
close path. Wayland and IME input have not been qualified.

The Linux-only palette is mounted as a modal in the existing Zed window when
`MZED_NATIVE_PALETTE=1` and opened with `Ctrl+Alt+Shift+P`. The Rust view owns
GPUI window, focus, input registration, shaping, painting, and platform-resource
lifetime. The shared MoonBit command palette owns the query field, composition
transaction, selection, command filtering, navigation, and close/action state.
The FFI carries bounded copied values and does not expose runtime objects or
callbacks.

The intended keyboard profile covers printable query input, shared selection
and undo/redo keys, arrow navigation, and Enter/Escape handling. Enter and
Escape are completed on their matching physical key release, so repeats and
intervening keystrokes remain attached to the original sequence. IME edits use
the shared field's marked-text transaction. The palette explicitly opts into
receiving one-byte Wayland text-input-v3 commits as text while a physical
close-key release is pending; all other handlers retain the existing key-event
routing by default. The host adds the modal's window-local origin once when
reporting field geometry.

The following paths remain outside this profile:

- Pointer input can focus the modal, but it does not place the caret, select
  query text, or activate command rows.
- Clipboard shortcuts C/V/X are consumed without editing. The ABI has no
  clipboard payload or success acknowledgement.
- Arbitrary system selection changes and partial replacement ranges are not
  supported. The adapter admits only exact UTF-16 scalar boundaries and the
  current visible marked span or selection.
- Surrounding-text deletion and IME reconversion are unqualified. The pinned
  Wayland text-input-v3 path does not provide a demonstrated byte-range to
  shared UTF-16 transaction mapping for those operations.
- Geometry admission fails closed for bidi text, combining or multi-scalar
  grapheme clusters, merged glyphs, missing glyphs, and non-monotone caret
  stops. The host uses GPUI shaping; it makes no Pango-equivalence or complete
  native raster-admission claim.
- The observed runtime smoke used X11. Wayland text-input-v3, other
  compositors, seat hot-unplug, macOS, Windows, and accessibility behavior
  remain unqualified.

The linked C tests exercise the copied MoonBit ABI, Unicode boundaries,
composition transactions, and delayed Enter/Escape state transitions. The
derived Zed package compile/link check confirms the Rust host graph builds, but
the linked tests and compile do not execute the Rust ModalLayer, GPUI keybinding
interceptor, Wayland client, or an IME. A dedicated Linux X11 profile/window
smoke exercised the mounted palette, visible keyboard query/filter result, and
close path with exit code 0. It does not qualify Wayland text-input-v3 or real
IBus/Mozc input; those still need separate checks before claiming Wayland
runtime or IME qualification.
