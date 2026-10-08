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

An earlier, separate nested Wayland IME run used the pinned `ZED_STATELESS=1`
diagnostic path. It showed `にほんご` preedit and candidate choices, Escape
cancellation while the palette stayed open, and a visible unmarked `日本語`
state after Return followed by palette dismissal on Escape. Those screenshots
and observations belong to that run only. They did not establish the exact
commit callback or native event order, and they did not independently identify
the active Fcitx engine or the connected Mozc process.

A later run on 2026-10-08 used a fresh private profile and the same
`ZED_STATELESS=1` diagnostic path, which uses in-memory databases and skips the
Linux single-instance listener. The live nested compositor passed the
text-input-v3 and input-method-v2 interface checks. Fcitx switched from its
keyboard input method to Mozc, and the active context read back as Mozc. The
actual Fcitx client connection reported a kernel peer identity that matched
the retained, supervised Mozc child across 40 observed records. The existing
UID and executable checks remained enabled. This verifies the observed
connection peer for this run; it does not claim that no other Mozc process
existed system-wide.

The visible sequence was: `にほんご` preedit and candidates; Escape canceled
composition and left the palette open; Space showed an underlined `日本語`
conversion preview; Return left unmarked `日本語` visible with the palette
still open; Escape then removed the palette. The shared filter behavior is
consistent with Return committing the query, but the commit callback and exact
native event order were not traced. No command was selected or dispatched.
Only the preedit and cancellation screenshots from this run were retained.
The Space, Return, Escape-dismissal, and post-exit images were observed during
the run but were not saved as raw images, so they have no retained image hashes.
The earlier run's screenshots are separate evidence and do not fill this gap.

The later run ended through Ctrl+Q with Zed exit code 0 and a completed wait
on its supervised child. The Fcitx monitor and both process supervisors also
exited successfully. The owned Mozc child was stopped and reaped by its
supervisor; a cleanup readback found no remaining tracked run processes or
private sockets. This is a bounded normal-exit result for the isolated run,
not a claim of error-free shutdown on every profile.

The stateless runs do not resolve persistent-profile startup. An earlier
normal-profile launch reported `zed is already running`, but its log did not
expose the underlying bind errno. A separate socket diagnostic was denied by
the environment with `EPERM`; that probe did not reproduce the Zed launch and
does not identify its cause. No workaround was applied. `ZED_STATELESS=1` is
limited to the isolated diagnostic profile: it does not disable all settings,
logs, caches, or other files.

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
- Runtime tests showed a filtered command row but did not activate a command.
  `Open Settings File` execution and exactly-once action dispatch remain
  unqualified.
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
stateless nested IME run adds observed Japanese preedit, cancellation,
conversion display, an unmarked post-Return query, authenticated
Fcitx-to-Mozc peer identity, and normal supervised exit for that run.
Persistent-profile startup, exact commit callback/event ordering,
selected-command execution, arbitrary system selection changes, and other
compositor or hardware-renderer profiles remain unqualified.
