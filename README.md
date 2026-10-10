> [!IMPORTANT]
> Remove this line to confirm you've reviewed this PR before submitting.

# MZed

Keep a working Rust Zed editor while replacing small, optional regions with
MoonBit. The Linux baseline and island have native qualification workflows. A
separate Windows lane now builds the pinned native ABI with MSVC and records a
Windows editor open/edit/save smoke; it does not yet qualify island pixels or
pointer interactions on Windows.

The baseline editor and the opt-in MoonBit island remain separate lanes. This
repository pins Zed v1.22.0, verifies source and license provenance, and
exercises native file-open/edit/save. Inspect each lane's exact CI evidence
before calling it qualified.

- [Source pin and build procedure](docs/linux-baseline.md)
- [Native seam investigation and remaining gates](docs/native-seam.md)
- [Windows build and smoke procedure](docs/windows-validation.md)
- [Machine-readable source lock](upstream.lock.json)

Run the dependency-free harness tests:

```sh
python3 -m unittest discover -s tests -v
```

Zed upstream is strictly read-only. Never open issues, PRs, comments, or push
there. Zed-derived changes belong in this repository with original licenses and
attribution retained; independently authored generic framework changes belong
in their own repositories. Nothing here publishes an editor release.

## Opt-in native island experiment

The separate derived-source lane hosts one MoonBit/gpui.mbt copied-scene region
inside the original editor status bar. See [the bounded contract and reproduction
steps](docs/native-island.md). The unchanged baseline lane remains separate.
Same-window acceptance requires the exact commit's native smoke evidence; ABI
linkage alone is not sufficient. No upstream Zed writes or editor release.
