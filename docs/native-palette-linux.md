# Linux command palette profile

This document describes the bounded MZed integration work. The pinned derived
Zed package passed a serial compile/link check. In a dedicated X11 profile, the
MZed palette opened in the existing Zed window, rendered its field and selected
command row legibly, accepted a keyboard query, filtered to `Open Settings File`,
and closed with exit code 0. The query `Settings` matched; lowercase `settings`
did not, because the pinned shared picker currently filters case-sensitively.
The shutdown log also contains an inotify-watch removal warning and a GPUI
`window not found` error.

A dedicated nested labwc/Pixman Wayland run passed the live global-interface
gate for `zwp_text_input_manager_v3`, `zwp_input_method_manager_v2`, and
`zwp_virtual_keyboard_manager_v1`. In that session, the new
`Ctrl+Alt+Shift+Z` chord opened the palette, `Settings` filtered to
`Open Settings File`, Escape dismissed it, and `Ctrl+Q` quit Zed with launcher
exit code 0. The profile used llvmpipe OpenGL with software rendering. Its Zed
log records unavailable Vulkan drivers, network failures for extension and ACP
registries, missing DBus, and a `timed out waiting on app_will_quit` error at
shutdown. The smoke covers normal keyboard events and palette open/query/dismiss
on the nested Wayland session; it did not exercise IME behavior.

A separate nested Wayland IME smoke used the pinned `ZED_STATELESS=1` diagnostic
path, which uses in-memory databases and skips the Linux single-instance
listener. With nested Fcitx5 running and its Mozc addon loaded, the palette
displayed `にほんご` preedit and candidate choices; Escape canceled composition
while keeping the palette open; Space and Return committed `日本語`; a fresh
Escape dismissed the palette.
This demonstrates visible Japanese preedit, cancel, and commit behavior in the
tested nested profile. The selected Fcitx engine was not independently
confirmed by the saved status command, and a direct Fcitx-to-Mozc IPC identity
trace was not captured. The Mozc server PID and executable were not captured
while input was active.

The stateless run does not resolve the persistent-profile startup issue: the
earlier normal run still reported `zed is already running`, and no bind errno
was available in its log. The diagnostic is limited to its isolated profile
and does not imply that all settings, logs, caches, or other files are
disabled. Zed and the runner both exited with code 0, while Zed logged a
`timed out waiting on app_will_quit` diagnostic. Fcitx logged a SIGTERM
shutdown trace. After shutdown, a Mozc server was observed as a zombie with
parent PID 1; a later check no longer found its process entry, but the
reaping time and cause were not observed. This is not an error-free or
complete-cleanup claim.

The Linux-only palette is mounted as a modal in the existing Zed window when
`MZED_NATIVE_PALETTE=1` and opened with `Ctrl+Alt+Shift+Z`. This avoids the
pinned Linux keymap’s existing `Ctrl+Alt+Shift+P` FPS-overlay binding. The
binding is re-applied after a default keymap reload, before user key bindings, so
a user keymap can still take precedence. The Rust view owns
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
- Runtime coverage is limited to the pinned X11 profile and nested
  labwc/Pixman keyboard and Japanese IME smoke. Other compositors, hardware
  renderers, seat hot-unplug, macOS, Windows, and accessibility behavior
  remain unqualified.

The linked C tests exercise the copied MoonBit ABI, Unicode boundaries,
composition transactions, and delayed Enter/Escape state transitions. The
derived Zed package compile/link check confirms the Rust host graph builds, but
the linked tests and compile do not execute the Rust ModalLayer, GPUI keybinding
interceptor, Wayland client, or an IME. The X11 and nested Wayland runs exercised
the mounted palette, visible keyboard query/filter result, and dismissal. The
separate stateless Wayland run also showed Japanese preedit, cancellation, and
commit using Fcitx5/Mozc in the nested profile. Persistent-profile startup,
direct Fcitx-to-Mozc IPC identity, selected-engine machine verification, and
complete process reaping remain unqualified or incomplete as recorded above.
