# macOS native island qualification

## Scope

The macOS workflow builds the pinned MoonBit island and links the copied-value
C ABI into a Rust contract-test executable on an Apple Silicon runner. It
exercises create, dispatch, copied snapshots, destroy, slot reuse and input
sequence ownership. It does not build or launch Zed, so a green workflow is ABI
evidence only. The Linux baseline and same-window island jobs keep their own
existing X11 qualification and remain unchanged.

The full macOS editor check is a local qualification on a Mac with Xcode,
Command Line Tools, the Metal Toolchain component, CMake, Rust 1.98.1, MoonBit
`0.10.14+7d59c7ec9`, and at least 20 GiB free on the volume holding the Zed
checkout and Cargo target. Run `python3 scripts/baseline.py inspect` first. It
checks that the Metal compiler executes and, when Xcode exposes an installed
component identifier, selects that identifier through `TOOLCHAINS` for both
baseline and derived editor builds. A toolchain entry in Xcode's component list
alone does not pass this check. If the component is absent or the compiler
cannot run, install/select it through Xcode before starting the editor build.
The local qualification recorded for this change used macOS 26.5.2 (build
25F84), arm64, with Metal Toolchain build 17F109
(`com.apple.dt.toolchain.Metal.32023.883`). Its default `xcrun metal` shim did
not find the installed component, while selecting it explicitly did; the
harness performs that selection automatically. The qualification uses the
same pinned, read-only Zed source and opt-in status-bar island. Rust remains
responsible for the existing editor window, renderer, focus and event loop; the
MoonBit island does not create a second window.

## Reproduce the native ABI lane

The workflow is [macOS native island ABI](../.github/workflows/macos-native-island.yml).
For a local run, put generated files and Rust downloads on a volume with enough
space. If this worktree has no `_build` directory, link it to that volume before
running these commands. Keep the installed Rustup home when the pinned compiler
is already available; the `CARGO_HOME` below moves registry downloads and build
scratch away from the system volume.

```sh
mkdir -p _build/tmp _build/cargo-home
export TMPDIR="$PWD/_build/tmp"
export CARGO_HOME="$PWD/_build/cargo-home"
python3 scripts/baseline.py inspect
if ! rustc +1.98.1 --version; then
  # Keep any new Rust toolchain files on the volume with the build artifacts.
  export RUSTUP_HOME="$PWD/_build/rustup-home"
  rustup toolchain install 1.98.1 --profile minimal
fi
git clone --no-checkout https://github.com/gpui-mbt/gpui.mbt.git _build/gpui
git -C _build/gpui fetch --depth=1 origin bf965aebbeb1dfdfed26373d4a7a58bb51a5ad01
git -C _build/gpui checkout --detach bf965aebbeb1dfdfed26373d4a7a58bb51a5ad01
python3 scripts/build_native.py --source _build/gpui --output _build/native-macos
bash scripts/test_native.sh _build/native-macos
```

The macOS ABI lane uses the normal compiler and runtime. The existing UBSan and
ASan+UBSan lanes remain Linux-only; the Linux sanitizer setup and workflow are
unchanged.

## Build the unchanged baseline and same-window editor island

Use a clean source checkout. `baseline.py source` verifies the exact Zed commit
and provenance, then disables the checkout's push URL. Build and exercise the
unchanged editor before applying the opt-in patch:

```sh
python3 scripts/baseline.py source --source _build/zed-island
python3 scripts/baseline.py build \
  --source _build/zed-island \
  --output _build/baseline-macos \
  --jobs 2 \
  --timeout-seconds 5400
```

On success, the harness copies the unchanged executable to
`_build/zed-island/target/mzed-baseline-preserved/debug/zed` and records both
that runnable path and its Cargo target path in the baseline evidence. The
copy stays under the pinned Zed checkout, outside the Cargo target that the
derived build reuses, so executable-relative asset lookup still reaches the
correct `.git` root even when `_build` points to external storage. Open a
fixture with the baseline binary and verify native edit/save before preparing
the island:

```sh
mkdir -p _build/baseline-smoke/data _build/baseline-smoke/home
printf 'MZed baseline fixture\n' > _build/baseline-smoke/fixture.txt
HOME="$PWD/_build/baseline-smoke/home" \
  "$PWD/_build/zed-island/target/mzed-baseline-preserved/debug/zed" \
  --user-data-dir "$PWD/_build/baseline-smoke/data" \
  "$PWD/_build/baseline-smoke/fixture.txt"
```

If a build is interrupted or times out, keep its target and evidence. Start a
new attempt with a fresh output path and `--resume-target`; the script records
that it reused the target and applies the selected Metal toolchain again.

After preserving the baseline executable, patch only this derived checkout and
reuse its exact Cargo target so unchanged crates do not need a second full
compile:

```sh
python3 scripts/prepare_island.py --source _build/zed-island
```

Build the derived editor with the same pinned compiler and native ABI:

```sh
python3 scripts/build_island.py \
  --source _build/zed-island \
  --native _build/native-macos \
  --output _build/island-build-macos \
  --reuse-target _build/zed-island/target/mzed-baseline
```

### Run the local same-window qualification

Run the executable harness against the preserved baseline and derived binaries
from a fresh output directory:

```sh
python3 scripts/smoke_macos.py \
  --baseline _build/zed-island/target/mzed-baseline-preserved/debug/zed \
  --derived _build/zed-island/target/mzed-baseline/debug/zed \
  --source _build/zed-island \
  --output _build/macos-ui-round1
```

The harness first checks that the invoking process is on an unlocked console
session and already has Accessibility and Screen Recording permission. If the
console is locked or either permission is absent, it stops before launching an
editor, sending input or capturing a window. Unlock the Mac or grant permission
through macOS System Settings and rerun. The harness does not unlock the session,
open permission prompts or bypass TCC. It captures only the selected editor
window by its WindowServer ID, not the whole display. Both binaries receive
fresh isolated profiles and the same untrusted fixture, then the harness
exercises native keyboard input and saves the file through the editor. The saved
bytes must be exactly:

```text
MZed pinned baseline fixture
MZed native edit save verified
```

The harness selects the unique visible level-zero process-owned window whose
title contains the isolated fixture name and whose size is at least 700×500
logical points. It records other process-owned windows separately, including
the small untitled auxiliary window used by this app. It rejects ambiguous
fixture windows, a missing or changed selected WindowServer ID, and any loss of
frontmost ownership before each capture or input. For the derived run, it also
checks solid blue/pink pixels, 120×18 logical scene bounds scaled once, a held
press across two repeated same-window resizes with unchanged dispatches,
outside-release cancellation, and late bare releases after disable and after
remount. It requires fresh remount state and native dispatch counters `[1, 2,
1]`, one remount, two disables, no native failure, and the same exact edited
file bytes. A pass writes `qualification.json`, one `smoke.json` per binary,
the isolated editor logs, OCR records, fixtures and window-only PNGs under the
chosen output directory. That directory must not already exist; use a new path
for each attempt so earlier evidence stays intact.

This local UI procedure proves one native open/edit/save sequence and the
listed status-bar interaction gates on the tested macOS machine. Hosted macOS CI
remains ABI-only and must not be described as GUI evidence. The local smoke
does not qualify focus-loss/minimize cancellation, multiple editor windows or displays,
general accessibility support, IME behavior, hardware performance or full-frame
rendering.
