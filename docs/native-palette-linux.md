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

## Command dispatch implementation update (2026-10-20)

The existing allowlisted dispatch path is now guarded by a consuming Rust
`DispatchTicket`. It compares the original GPUI window, workspace entity, and
active item entity at deferred close completion. Item entity identity is the
bounded document-lifetime contract here: closing/replacing the item cancels the
request; no path string, mutable title, or strong item reference revives it.
An empty workspace is admitted only while it is still empty. A switched workspace,
inactive window, replacement palette, failed close ACK, or stale native
selection snapshot cannot dispatch. The native generation, epoch, request, and
pending-operation state are checked before consuming the shared close.

`Open Settings File` invokes Zed's existing `open_settings_file` implementation
using the captured weak workspace and original window. Remote projects are
refused because the existing helper deliberately opens a new local workspace
for them; that behavior is outside this same-window profile. The global
`OpenSettingsFile` action otherwise resolves `with_active_or_new_workspace`,
which is not a suitable ownership boundary for a delayed modal selection.
The host logs the helper invocation with its palette entity and epoch; this is
a dispatch count, not proof by itself that the asynchronous file open completed.

The Linux 1x/2x same-window smoke retains the existing island and editor
edit/save checks and adds `--palette`: Escape must dispatch zero commands;
three Enter keydowns must leave the palette open without dispatch; matching
keyup must invoke settings once; an unmatched extra keyup must not invoke it
again. OCR must observe a unique marker in the actual settings buffer captured
from the original X11 window, followed by return to the original document.
The smoke retains PNGs, the settings image SHA256, OCR, window ID, dispatch
records, and final exact dispatch count in `smoke.json` plus `Zed.log`.
Missing, duplicate, malformed, or different-command dispatch evidence fails.

Validation at implementation time:

- `python3 -m unittest discover -s tests -q`: 146 tests, PASS (one skip).
  These include synthetic evidence rejection tests; they are not native runs.
- `python3 -m py_compile scripts/*.py`: PASS.
- `cc -std=gnu11 -Wall -Wextra -Werror -I native -fsyntax-only tests/native_palette_host_lifecycle.c`: PASS; syntax only.
- Rust dispatch-ticket execution, linked MoonBit ABI execution, derived Zed
  compile/link, and the new real X11 selection smoke: NOT RUN locally; no Rust
  compiler or built Zed runtime was available in this session. CI results must
  be attached separately for the new commit.
- Physical IME and exact native commit callback ordering: NOT RUN. The historical
  IME observations above remain historical and do not qualify this change.

Keep this PR draft. Selected-command runtime qualification remains pending
until the new smoke actually passes; adding this gate is not a claim of success.

## Selected-command dispatch qualification (2026-10-20 candidate)

The candidate below extends the consuming DispatchTicket above with an exact
terminal snapshot receipt and asynchronous completion evidence. Remote projects
remain refused. The selected ID is mapped through a host allowlist. `Open Settings File` calls
Zed's existing settings-file implementation with the captured workspace and
window, rather than posting the global action that resolves the active workspace
later. The Linux fullscreen command uses the captured window's native fullscreen
operation, equivalent to Zed's Linux action handler.

A receipt binds the terminal action to the native slot, generation, open epoch,
query document revision and final request. It is consumed once, before the close
ACK and host call. The deferred continuation also requires the original active
window, displayed workspace, original active editor item, restored workspace
focus and absence of any replacement modal. Any mismatch discards execution.
Native owner teardown remains mandatory even when execution is discarded.
An IME callback during a held Enter/Escape marks that entire physical sequence
as composition-related, even if composition begins and ends before key-up.

The required Linux CI now runs Rust receipt regressions, the existing linked
MoonBit palette ABI suites (including the actual settings command ID), and the
existing same-window scene/edit/save smoke followed by palette execution at
1x and 2x. The new X11 sequence cancels with Escape, filters `Settings`, holds
and repeats Enter Down with zero dispatches, releases Enter, observes the
settings file's known contents, and sends a duplicate release. `smoke.json`
requires exactly one dispatch and one completed settings-file open carrying
the same receipt and GPUI window. It also records the original X11 window,
raw screenshot hashes and the selected command. Missing/duplicate/misbound logs
fail the evidence oracle. A dispatch log alone is not a successful file open.

Local candidate verification: Python unittest discovery passed 149 tests (one
skip); Python compilation, C syntax check, four dispatch-owner Rust tests and
eight protocol/receipt Rust tests passed. The local Rust compiler was 1.99.1;
the required CI remains pinned to 1.98.1. Linked MoonBit and actual GUI tests
have not run locally.

Runtime status for this candidate is pending until the corresponding CI run
passes. Earlier observations above do not qualify this new execution path.
Physical IME commit callback order, Wayland command execution, macOS and Windows
palette execution remain unverified. The X11 synthetic key sequence is not a
physical IME test.
