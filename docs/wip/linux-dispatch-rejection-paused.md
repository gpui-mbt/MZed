# WIP: Linux dispatch-rejection fallback gate

Status: paused by a strategy change. This is unfinished work, not an acceptance
result, and it has not been merged.

The implementation is based on MZed main `59a4a2b6daa48967c79de114a9de2ed119115c7b`.
Dependency-free validation passed: 49 unit tests and `python3 -m py_compile scripts/*.py`.

Native ABI tests, UBSan/ASan runs, an editor build, real Linux UI smoke, and CI
have not been run for this candidate. There is no same-window rejection or
fallback acceptance claim, and no merge or release.
