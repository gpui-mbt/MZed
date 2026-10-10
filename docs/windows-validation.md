# Windows build and smoke

The Windows lane preserves the same source contract as the Linux lane: Zed
v1.22.0 at `76659a55a8c10ed355a070f8764a0b1733e3c115`, reviewed gpui.mbt at
`bf965aebbeb1dfdfed26373d4a7a58bb51a5ad01`, MoonBit 0.10.14, and Rust 1.98.1.
Zed-derived source stays in this GPL-3.0-or-later repository, with upstream
license files and component notices retained.

`build_native.py` detects MSVC on Windows, compiles MoonBit's generated C and
the four required MoonBit runtime units with x64 `cl.exe`, then archives them
as `mzed_native.lib` using `lib.exe`. Set `MOON_HOME` to the matching MoonBit
toolchain. The script discovers VS 2022 through `vswhere` or `VSINSTALLDIR`; set
`MZED_VSDEVCMD` when Visual Studio is installed in a nonstandard location.
Windows sanitizer modes are unsupported by this MSVC lane.

From a fresh MZed checkout in PowerShell, point `MOON_HOME` at the pinned
MoonBit toolchain and acquire the exact sources first. Use a new disposable
directory for the Zed source on each attempt; acquisition pins the line-ending
policy before checkout, then verifies the release commit, clean tree and locked
file hashes. It refuses to overwrite or reset an existing source directory.
The downstream patch is checked out with LF endings even when the Windows Git
configuration requests CRLF, so `git apply --check` sees the same patch bytes
on both platforms.

```powershell
$env:MOON_HOME = 'C:\path\to\moonbit'
git clone https://github.com/gpui-mbt/gpui.mbt.git _build/gpui-windows
git -C _build/gpui-windows checkout --detach bf965aebbeb1dfdfed26373d4a7a58bb51a5ad01
python scripts/baseline.py source --source _build/zed-windows
```

Then run the native archive and same-process ABI checks:

```powershell
python -m unittest discover -s tests -v
python scripts/build_native.py --source _build/gpui-windows --output _build/native-windows
python scripts/test_native_windows.py --native _build/native-windows --output _build/native-windows-test
python scripts/baseline.py verify --source _build/zed-windows
python scripts/prepare_island.py --source _build/zed-windows
python scripts/build_island.py --source _build/zed-windows --native _build/native-windows --output _build/windows-island-build
python scripts/smoke_windows.py --binary _build/zed-windows/target/mzed-island/debug/zed.exe --source _build/zed-windows --output _build/windows-smoke
```

The ABI harness calls the actual static archive through the existing Rust C ABI
and covers initialization, create, copied snapshots, dispatch, resize, stale
identity rejection, destruction, remount, four-slot bounds, 1,000 lifecycle
cycles, and owner-thread enforcement. The editor smoke uses a fresh profile and
requires one visible process-owned top-level window. It captures that window,
checks the exact solid blue/pink scene pixels at the window's DPI scale, and
sends native Win32 `SendInput` pointer events to that same window. It exercises
unsupported right, modified-left, and wheel input; an accepted click after a
resize; outside-release and minimize cancellation; disable/remount; late
release rejection; and exact native dispatch/destroy log counts. For minimize
cancellation it waits for an app-side owned-press acknowledgment, then sends a
bare center release after restore before any new down and polls both scene pixels
and dispatch logs for stability. It then types
and saves a fixture through native keyboard input and compares the exact bytes.
`same_window_island` is `passed` only when the pixel, pointer, lifecycle, and
single-window checks all pass. Failed and unrun states remain explicit in
`smoke.json`.

The `Windows native island` GitHub Actions workflow runs these steps on a
Windows 2022 host and uploads JSON, logs, fixture bytes and screenshots. Read
the exact commit's artifact before reporting a pass. The workflow does not
publish an editor binary. This harness repository has no `moon.mod`, so MZed is
not itself a Mooncakes package and has no registry publish step.

## Local evidence

On 2026-10-10, the local x64 Windows host built the archive from the exact
gpui.mbt pin with MoonBit compiler/core `0.10.14+7d59c7ec9` and MSVC C toolset
`14.44.35207`. Rust `1.98.1` (`48a229cea`, 2026-09-01) linked the archive for
`x86_64-pc-windows-msvc`; both tests in `native/protocol.rs` passed, including
the 1,000 mount/dispatch/copy/destroy cycles. The build record is under
`_build/native-windows-final2/build.json`; Rust compile and runtime logs are
under `_build/native-windows-test-final2/` on the working Windows host. The
build and test JSON include the exact MZed harness commit and gpui.mbt source
pin, and the ABI test refuses archive evidence from another pin. The Python
harness suite passed 69 tests with 14 platform-specific tests skipped.

The full pinned Zed Windows build and editor window smoke are **UNRUN locally**.
Available C: space is below the build's 20 GiB preflight, so no full editor build
or runtime qualification was attempted locally. The workflow exposes that
longer editor build only through manual dispatch with
`run_editor_smoke` enabled. Read its exact commit's artifact before reporting a
Windows same-window island pass; the portable harness tests do not replace the
native pixel and pointer evidence.
