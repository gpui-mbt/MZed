# First bounded same-window island

This experiment adds one opt-in status-bar region to the pinned original Rust
editor. It does not replace the editor, start gpui.mbt's platform Host, create
another window, or change upstream Zed. It is not full roadmap issue 0019 acceptance.

## Sources and provenance

- Zed v1.22.0: `76659a55a8c10ed355a070f8764a0b1733e3c115`.
- Reviewed gpui.mbt: `bf965aebbeb1dfdfed26373d4a7a58bb51a5ad01`.
  Parked async work from issue 0016 is not included.
- MoonBit compiler and core: `0.10.14+7d59c7ec9`; Rust: `1.98.1`.
- Zed-derived patch and application integration remain in this repository under
  GPL-3.0-or-later. The derived checkout retains upstream LICENSE-GPL,
  LICENSE-APACHE and component notices. No GPL application code is copied into
  the independent Apache gpui.mbt framework. `prepare_island.py` records source
  and patch hashes.

## Ownership and bounded contract

Rust owns the event loop, existing editor window, renderer, scaling, clipping,
focus and native resource lifetime. A persistent status-bar entity owns the
native token; frame-local canvas values only hold copied scene data and weak
entity handles. MoonBit owns counter state and produces a real portable gpui.mbt
Element → request_layout → prepaint → paint_snapshot(1.0) scene.

The C ABI only accepts/returns copied i32 values. One process-global MoonBit
image is serialized on its first Rust owner thread; the Rust façade is !Send
and !Sync. No runtime objects, pointers, strings, callbacks, OS/GPU handles, or
Rust ABI values cross the boundary. There are four slots, fourteen scene fields,
nonwrapping generation/request limits of 1,000,000, counter limit 65,535,
and dimensions 1..4096. Invalid requests do not mutate state. Snapshots reject
stale identities. Destroy retains a generation tombstone; remount starts a
fresh counter. Snapshot copies outlive the native instance.

Only one opaque axis-aligned identity-transform quad is accepted; resources,
clip chains, opacity and unsupported scene shapes fail closed. Rust paints in
logical Pixels, allowing its existing paint_quad path to scale exactly once.
The canvas intersects ancestor clipping, and empty/invalid bounds neither draw
nor accept input. There is no independently owned platform backend or surface.

Only plain left-button sequences activate. Persistent press ownership survives
redraws and renews the changing GPUI hitbox capture ID. Event-time capture
validation prevents taking another control's sequence. Outside/modified up,
focus loss, activation/visibility loss, capture transfer and native destruction
cancel. Fresh down/no-button motion clears an interrupted trailing-up tombstone.
Unsupported buttons/modifiers/wheel do not activate or focus the status bar.
There is no keyboard focus target, text-input or IME handler. The `M` control
turns the native instance off/on without replacing the editor.

## Reproduction and evidence

The existing `Pinned Linux baseline` workflow is unchanged. The separate
`Same-window native island` workflow checks out a fresh derived source,
links real native MoonBit, runs contract tests normally and with C/runtime
UBSan and ASan+UBSan, then builds the editor. Rust is not sanitizer-instrumented;
LeakSanitizer is disabled and no leak qualification is claimed.

The separate [macOS ABI workflow](../.github/workflows/macos-native-island.yml)
builds the same copied-value archive and runs its Rust protocol tests on Apple
Silicon. It does not launch Zed or qualify the Mac UI. Follow the [macOS
qualification path](macos.md) for the pinned editor build and same-window
status-bar smoke; the Linux X11 lane remains unchanged.

The `Windows native island` workflow builds the same copied-scene archive with
MSVC, links and runs `native/protocol.rs` in one Rust process, builds the pinned
editor with the opt-in island, and records an isolated native-window
open/edit/save smoke. That Windows smoke does not inspect island pixels or
pointer ownership; Windows same-window UI acceptance remains unqualified.

The declared local churn budget is 1,000 create/dispatch/copy/destroy cycles,
maximum four live slots and one quad per snapshot. This is a bounded regression,
not proof of an allocation ceiling or arbitrary callback execution budget.
Runtime roots live until process exit; runtime unloading and panic containment
are not qualified.

Native X11 smoke runs use fresh isolated profiles, restricted worktree mode,
Xvfb/Openbox/xcompmgr and requested software rendering. They require a unique
process-owned toplevel before/after, screenshots of the MoonBit scene inside the
fixture editor, real targeted pointer input, redraw/resize ownership, unsupported
input and outside-release cancellation, toggle/destroy/remount/late-up checks,
then exact native editor edit/save after disabling the island. The 1x/2x runs
check rectangle pixels against logical dimensions rather than assuming scale.

A compiled binary or ABI-only pass is not a same-window pass. Consult the exact
commit's workflow and uploaded build/smoke JSON, logs and screenshots. Hardware,
Wayland, macOS application UI, Windows island pixel and pointer acceptance,
accessibility, IME, modal-during-drag, full-frame performance and all
hidden/clipped lifecycle paths remain outside this first qualification unless
separately demonstrated.

## Faster harness iteration, separate final qualification

The Linux lane keeps an exact-input repository build cache. It is not a private
or confidential store: it contains only this public-source executable, matching
source/license metadata and integrity information. No user profile, credential,
editor binary artifact or release is uploaded. The key includes source/patch and
build-script contents, actual native archive/runtime/compiler inputs, Rust,
normalized build environment and the installed system package set. Unsupported
external Cargo configuration and compiler/loader overrides fail closed.

An exact hit must match the source fingerprint, successful build record,
executable size/SHA256, permissions and system-library hashes before it is copied
under the same derived source tree. There are no restore-prefix keys; an inexact
match is rejected. A miss builds once and saves before smoke, so a subsequent
harness-only fix can reuse that verified executable. Cache round-trip extraction
uses a fresh directory and is verified. Payload is capped at 2 GiB and requires
explicit free disk headroom. These hashes establish integrity, not authenticated
provenance against a malicious cache writer.

Cached smoke is provisional. For final qualification, rerun the workflow (attempt
2 or later) or select the uncached manual input: both bypass cache restore/save.
A first attempt with a cache miss also records a genuine clean build. Build JSON
states cache reuse and the executable's original source commit explicitly.
The unchanged baseline lane remains available by manual dispatch and runs when
its own inputs change; fast island harness tests gate the expensive island job.

## Local commands

Set MOON_HOME to the matching toolchain, then acquire gpui.mbt at the exact pin
in a clean checkout. Every output/source attempt must be fresh; nothing resets,
cleans or stashes existing work.

```sh
python3 scripts/build_native.py --source /path/to/pinned/gpui --output _build/native-normal
bash scripts/test_native.sh _build/native-normal
python3 scripts/baseline.py source --source _build/zed-island
python3 scripts/prepare_island.py --source _build/zed-island
python3 scripts/build_island.py --source _build/zed-island --native _build/native-normal --output _build/island-build
```

The application mount is opt-in through `MZED_NATIVE_ISLAND=1`; the unchanged
baseline build does not include the patch. No editor binary is published.
