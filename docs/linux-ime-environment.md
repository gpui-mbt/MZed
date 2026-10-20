# Linux IME test environment IaC

This recipe recreates the optional, private Linux IME profile for the command
palette. The supported MZed/Zed build remains the existing pinned lane in
[`linux-baseline.md`](linux-baseline.md) and the existing `baseline.py`,
`prepare_island.py`, `build_native.py`, and `build_island.py` scripts. This
profile does not change that build or the native island ABI.

The profile is test-only. It extracts pinned Debian 13 packages into caller
owned prefixes without `apt install` or package maintainer scripts, builds the
Debian Mozc source with an absolute private `--server_dir`, and prepares fresh
XDG and Zed data directories. It runs nested labwc over the owner's existing
X11 desktop with Pixman software rendering. The optional diagnostic build
applies a separate compile-gated patch that emits only the kernel peer PID
from an authenticated Fcitx client connection. Existing UID and expected
executable checks stay enabled. The hook logs no input text, is disabled by
default, and makes no global process uniqueness claim.

The host prerequisite is Debian 13 on amd64. The extracted Debian executables
use the host's ELF loader and Perl, so `doctor.py host` checks that contract
before profile preparation.

## Pinned inputs

[`scripts/linux-ime-profile/manifest.lock.json`](../scripts/linux-ime-profile/manifest.lock.json)
records the exact Debian source and package versions, SHA-256 values, signed
index digests, and accepted signer fingerprints. It pins the Debian 13 amd64
profile closure (219 package records) and the separate Mozc build closure (321
package records), plus Mozc 2.29.5160.102+dfsg-1.4 source files. The runtime
profile pins labwc 0.8.3-1, Fcitx5 5.1.12-2, Pixman 0.44.0-3, and Wayland
1.23.1-3.

The bootstrap downloads only HTTPS files from `deb.debian.org` and checks each
file's locked size and SHA-256 before use. It does not re-fetch or re-verify
the full signed Debian `InRelease`, `Packages`, or `Sources` indexes on each
run; the lock preserves their digests and signer fingerprints from the
verified preparation. The source `.dsc` uploader signature was not
independently verified; its hash is linked through the signed `Sources` index
to the pinned source archives. A new signed-index validation step remains a
follow-up before treating this recipe as a fresh Debian signature-chain
verification.

`source-lock.json` records the current profile source and test files. After
editing this profile, regenerate and check it before assembly:

```sh
PROFILE_IAC=scripts/linux-ime-profile
python3 "$PROFILE_IAC/update_source_lock.py"
python3 "$PROFILE_IAC/update_source_lock.py" --check
```

Assembly verifies the whole profile source tree against this lock, including
the runtime files it copies into a fresh run directory. Python cache files are
excluded; symlinked inputs are rejected.

## Source-only checks

These checks exercise the lock, profile assembly with mock binaries, runner
guards, and the peer-hook's compile-time OFF/ON contract. They do not build
Mozc or start the desktop profile. Set `MZED_IME_MOZC_BUILD_ROOT` to the
already prepared Mozc work root before running the peer-hook compile check.

```bash
PROFILE_IAC=scripts/linux-ime-profile
python3 "$PROFILE_IAC/update_source_lock.py" --check
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PROFILE_IAC" \
  python3 -m unittest discover -s "$PROFILE_IAC/tests" -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PROFILE_IAC/runner:$PROFILE_IAC" \
  python3 -m unittest discover -s "$PROFILE_IAC/runner" -p 'test_*.py' -v
for test in "$PROFILE_IAC"/runner/test_*.sh; do
  if [[ "$(basename "$test")" == test_peer_pid_log_gate.sh ]]; then
    MOZC_SOURCE_ROOT="$MZED_IME_MOZC_BUILD_ROOT/source/src" bash "$test"
  else
    bash "$test"
  fi
done
```

The peer-hook compile check needs the patched source tree created by the
explicit Mozc build step. Its two temporary programs exercise the diagnostic
header with the compile gate off and on; they are not the Mozc binaries.

## Prepare the private prefixes

Choose fresh directories owned by your user, outside system directories. The
commands below only inspect the lock at first. `fetch` uses network access when
you choose to run it; `extract` writes only into the two selected prefixes.

```sh
PROFILE_IAC=scripts/linux-ime-profile
python3 "$PROFILE_IAC/bootstrap.py" plan
python3 "$PROFILE_IAC/bootstrap.py" fetch --cache-root "$MZED_IME_CACHE_ROOT"
python3 "$PROFILE_IAC/bootstrap.py" verify --cache-root "$MZED_IME_CACHE_ROOT"
python3 "$PROFILE_IAC/bootstrap.py" extract \
  --cache-root "$MZED_IME_CACHE_ROOT" \
  --build-prefix "$MZED_IME_BUILD_PREFIX" \
  --runtime-prefix "$MZED_IME_RUNTIME_PREFIX"
```

The runtime extraction omits Debian's `mozc-server` and `fcitx5-mozc`
packages because both binaries are built from the same pinned Debian source.
The build copies the two Fcitx addon/input-method descriptors from that same
source tree and records their hashes. Other Fcitx5 and runtime dependencies
come from the locked package closure. Existing directories with files are
refused and left untouched. The build prefix gets only the two required
relative `lib -> usr/lib` and `lib64 -> usr/lib64` links; they stay inside that
private prefix and satisfy the Debian toolchain's sysroot layout. The build
doctor allows Debian command symlinks such as `gcc` and `python3` only when
their resolved executable remains inside the selected private prefix.

## Build Mozc and MZed

Build Mozc into the chosen runtime prefix. The peer-PID hook is opt-in and
must only be used for the isolated diagnostic profile:

```sh
python3 "$PROFILE_IAC/build_mozc.py" \
  --cache-root "$MZED_IME_CACHE_ROOT" \
  --build-prefix "$MZED_IME_BUILD_PREFIX" \
  --runtime-prefix "$MZED_IME_RUNTIME_PREFIX" \
  --work-root "$MZED_IME_MOZC_BUILD_ROOT" \
  --diagnostic-peer-pid
```

The build runs GYP and Ninja serially, configures `--server_dir` to the exact
runtime prefix path, uses a fixed deadline and stops below the 3 GiB free-disk
floor. Before compiling, it inspects Ninja's expanded target commands for the
server path and the diagnostic gate, then hashes the included target graph.
The output identity records the configured absolute server directory,
relative artifact paths, descriptor hashes, and graph hashes. The absolute
directory is compiled into the binaries, so moving the runtime prefix requires
rebuilding Mozc. Its separate build receipt remains in the selected private
work root.

Build Zed/MZed using the existing pinned source and native-island steps from
[`linux-baseline.md`](linux-baseline.md). `build_island.py` writes a receipt
which the profile doctor checks against the selected Zed binary. The profile
does not rebuild Zed or copy a binary into the repository.

## Check and run the profile

`doctor.py` is read-only. `reproduce.sh` verifies the prepared packages and
build identities, creates one fresh profile, checks Unix socket byte limits,
and assembles exact runner sources. By default it stops before launching any
process. The explicit `--run` form does the same preparation and then starts
the private D-Bus, nested labwc, Fcitx, owned Mozc child, and Zed process; it
requires the owner-provided `DISPLAY` and `XAUTHORITY`.

Set these variables to fresh user-owned locations and the existing build
outputs:

```sh
export MZED_IME_CACHE_ROOT=/path/to/private-cache
export MZED_IME_BUILD_PREFIX=/path/to/private-build-prefix
export MZED_IME_RUNTIME_PREFIX=/path/to/private-runtime-prefix
export MZED_IME_MOZC_BUILD_ROOT=/path/to/new-private-mozc-build
export MZED_IME_RUN_ROOT=/path/to/new-private-run
export MZED_IME_MOZC_BUILD_IDENTITY=/path/to/private-mozc-build/build-identity.json
export MZED_IME_ZED_BINARY=/path/to/derived/zed
export MZED_IME_ZED_BUILD_RECEIPT=/path/to/derived/build.json
scripts/linux-ime-profile/reproduce.sh
```

For review before launch, run the default preparation command, inspect the
profile and `doctor.py profile` result, then launch that exact prepared run
with `--run-prepared`. It rechecks the profile and the owner-provided desktop
environment before starting the processes:

```sh
scripts/linux-ime-profile/reproduce.sh --run-prepared
```

`reproduce.sh` calls `doctor.py host`, `doctor.py runtime`, `prepare_profile.py`,
`assemble_run.py`, and `doctor.py profile`. The runner refuses an existing run
root, stale D-Bus or Wayland sockets, source-hash mismatches, a server/client
compiled-path mismatch, unexpected Fcitx engine selection, or an invalid
owned-child or peer record. It waits for the exact child through its retained
process handle and does not enumerate the global process table.

## Qualification limits

The 2026-10-08 run exercised a fresh `ZED_STATELESS=1` profile and nested
software-rendered Wayland. It observed Japanese preedit and cancellation, an
underlined conversion preview, unmarked Japanese text after Return while the
palette remained open, and palette absence after Escape. The exact native
commit callback and event order were not traced. No palette command was
selected or dispatched. Only the preedit and cancellation JPEGs were saved in
that run; the later frames were seen in the live desktop but were not retained
as raw images.

Persistent-profile Zed startup remains unresolved. The normal-profile attempt
reported `zed is already running` without exposing the underlying bind errno.
A separate socket probe was denied by the environment and did not identify
Zed's cause. `ZED_STATELESS=1` skips the single-instance listener and uses an
in-memory database for this isolated diagnostic; it does not disable all
settings, logs, caches, or other files.

The checks run while preparing this IaC change are source-level and mocked
profile checks only. No Debian packages were downloaded or extracted, no Mozc
or Zed build was run, and no GUI, D-Bus, Fcitx, Mozc, or Zed process was
started. This commit makes the setup reproducible in source; it does not claim
a clean-machine replay or a new IME runtime pass.
