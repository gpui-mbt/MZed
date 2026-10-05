# Pinned Linux baseline

## Selection and provenance

Selected release: **Zed v1.22.0**, immutable stable release published 2026-09-30.
Source: `76659a55a8c10ed355a070f8764a0b1733e3c115`.

This is the current stable baseline rather than a moving branch or an arbitrary
older port. Its upstream Linux procedure supports X11 and Wayland. Its own
`rust-toolchain.toml` requires **Rust 1.98.1**. The qualification lane uses Ubuntu
24.04 and its native package APIs. This selection is a candidate until the
pinned build and editor smoke pass; it is not a claim of binary compatibility
with gpui.mbt.

The gpui.mbt behavioral comparison pin remains
`d9afb21688e04f89d9e94d96d33eb530aef90886`, separately from both the Zed source pin
and roadmap commit `bf965aebbeb1dfdfed26373d4a7a58bb51a5ad01`.

Read-only sources:

- [Release](https://github.com/zed-industries/zed/releases/tag/v1.22.0)
- [Linux build instructions](https://github.com/zed-industries/zed/blob/76659a55a8c10ed355a070f8764a0b1733e3c115/docs/src/development/linux.md)
- [Toolchain](https://github.com/zed-industries/zed/blob/76659a55a8c10ed355a070f8764a0b1733e3c115/rust-toolchain.toml)
- [Licensing declaration](https://github.com/zed-industries/zed/blob/76659a55a8c10ed355a070f8764a0b1733e3c115/README.md#licensing)

The app is GPL-3.0-or-later; Apache-2.0 applies only where marked. The harness
clones the whole source tree and preserves its license files and notices.
`upstream.lock.json` records SHA-256 for Cargo.lock, the toolchain file, the two
license files and the Linux dependency script. We do not import Zed code into
gpui.mbt or relicense it. No binary release or trademark approval is implied.
Future modified source must retain attribution and document modifications.

## Reproduce

Use a fresh Linux builder with Git, Python 3, rustup, Rust 1.98.1 and the pinned
upstream Linux dependencies. The CI workflow spells out Ubuntu packages.
Installing prerequisites is explicit; the harness does not change the system.

```sh
python3 scripts/baseline.py source
python3 scripts/baseline.py inspect
python3 scripts/baseline.py build --jobs 2 --timeout-seconds 5400
```

Acquisition verifies the full commit and tracked provenance, rejects dirty or
untracked source, and disables the checkout's push URL. It does not delete,
reset, stash, or clean existing work. An existing evidence directory is refused;
choose a new `--output` path and fresh source checkout for each build attempt.
The target directory stays beneath the Zed checkout because its dev asset loader
resolves the first `.git` ancestor of the executable. An existing target is
refused rather than cleaned or silently reused. The build uses the upstream Cargo
lock, no source patch, dev profile with debug info and incremental compilation
disabled to limit disk consumption. This is not a release/performance profile.

Declared bounds before running: two Cargo jobs, 90 minutes, minimum 20 GiB
initial free disk, stop below 2 GiB free. Timeout or disk exhaustion terminates
the build process group and preserves logs/partial outputs. RAM is recorded;
there is no claim that these bounds guarantee a full Zed build on every machine.

For native smoke, install the workflow's X11/Vulkan tools, then:

```sh
export LIBGL_ALWAYS_SOFTWARE=1 GPUI_X11_SCALE_FACTOR=1
export XDG_RUNTIME_DIR="$(mktemp -d)"
chmod 700 "$XDG_RUNTIME_DIR"
xvfb-run -a -s '-screen 0 1280x800x24' dbus-run-session -- \
  python3 scripts/smoke_x11.py --binary _build/zed/target/mzed-baseline/debug/zed \
  --output _build/smoke
```

The smoke starts a fresh app data/config area, disables telemetry/AI/updates,
opens a synthetic plain-text fixture, identifies exactly one visible window
owned by that process, types one sentinel, saves through native key events,
and checks exact bytes. It records screenshot, XRandR, Vulkan information,
logs and binary hash. No account login, real document, extension, or external
message is involved. A failed or ambiguous target fails closed.

## Evidence and limits

Local 2026-10-05 inspection verified clean source SHA and all recorded hashes,
and installed/verified Rust 1.98.1. The local preflight was **blocked before
compilation** by missing clang, cmake and native development headers. A live
cloud Linux desktop was observed separately; absent shell DISPLAY alone was
not interpreted as a missing desktop. CI is the reproducible native builder.

`build.json` and `smoke.json` deliberately separate build, editor smoke and
island status. Read the exact-commit workflow and artifact before reporting a
pass. CI software rendering, even when successful, does not qualify general
Linux hardware performance. This first smoke covers one native open/edit/save
sequence at scale 1; lifecycle, IME, multi-scale, input ownership, island churn,
and failure recovery remain open.

### Initial hosted run

[Run 37249353561](https://github.com/gpui-mbt/MZed/actions/runs/37249353561)
on PR head `d193820393eededa5c03696c4aed94e2c9e106d7` built the unchanged
Zed source successfully in 1,054.12 seconds on Ubuntu 24.04.5, Rust 1.98.1,
clang 18.1.3. The runner exposed ~16 GiB RAM and 91 GB initial free disk.
Binary SHA-256: `6d784316fbb395c7adaa37f7d8e424cbc750af897de5b7c447b08c241c1dab8a`.
The PR workflow checked GitHub's merge-test ref `3f588326dd65baf159e4bce485c71a839d8f6d90`.

Native smoke failed at launch with `settings/default.json` missing. This exposed
a harness layout error: the external Cargo target made upstream `dev_repo_root`
resolve the harness repository before the source checkout. The updated harness
places its fresh Cargo target under Zed's own source tree without patching Zed.
A new full build and smoke run must verify the fix. The failed artifact and
logs remain evidence; the first run does not establish working editor smoke.
