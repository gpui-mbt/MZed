#!/usr/bin/env bash
set -euo pipefail

MODE=prepare
case "${1:-}" in
  --run) MODE=run-fresh; shift ;;
  --run-prepared) MODE=run-prepared; shift ;;
esac
if [[ $# -ne 0 ]]; then
  echo "usage: reproduce.sh [--run|--run-prepared]" >&2
  exit 2
fi

PROFILE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
if [[ "$MODE" == "run-prepared" ]]; then
  for variable in MZED_IME_RUNTIME_PREFIX MZED_IME_RUN_ROOT MZED_IME_ZED_BINARY; do
    [[ -n "${!variable:-}" ]] || { printf 'required variable is unset: %s\n' "$variable" >&2; exit 2; }
  done
  export MZED_RUN_ROOT="$MZED_IME_RUN_ROOT"
  export MZED_RUNTIME_PREFIX="$MZED_IME_RUNTIME_PREFIX"
  export MZED_ZED_BINARY="$MZED_IME_ZED_BINARY"
  python3 "$PROFILE_DIR/update_source_lock.py" --check
  python3 "$PROFILE_DIR/doctor.py" profile \
    --run-root "$MZED_IME_RUN_ROOT" \
    --runtime-prefix "$MZED_IME_RUNTIME_PREFIX"
  : "${DISPLAY:?run mode requires the owner-provided desktop DISPLAY}"
  [[ -n "${XAUTHORITY:-}" && -r "$XAUTHORITY" ]] || {
    echo "run mode requires the owner-provided readable XAUTHORITY path" >&2
    exit 2
  }
  exec "$MZED_IME_RUN_ROOT/launch-w3moz"
fi

for variable in MZED_IME_CACHE_ROOT MZED_IME_BUILD_PREFIX MZED_IME_RUNTIME_PREFIX \
  MZED_IME_RUN_ROOT MZED_IME_ZED_BINARY MZED_IME_ZED_BUILD_RECEIPT \
  MZED_IME_MOZC_BUILD_IDENTITY; do
  [[ -n "${!variable:-}" ]] || { printf 'required variable is unset: %s\n' "$variable" >&2; exit 2; }
done
export MZED_RUN_ROOT="$MZED_IME_RUN_ROOT"
export MZED_RUNTIME_PREFIX="$MZED_IME_RUNTIME_PREFIX"
export MZED_ZED_BINARY="$MZED_IME_ZED_BINARY"

python3 "$PROFILE_DIR/update_source_lock.py" --check
python3 "$PROFILE_DIR/bootstrap.py" verify --cache-root "$MZED_IME_CACHE_ROOT"
python3 "$PROFILE_DIR/doctor.py" host
python3 "$PROFILE_DIR/doctor.py" runtime \
  --runtime-prefix "$MZED_IME_RUNTIME_PREFIX" \
  --build-prefix "$MZED_IME_BUILD_PREFIX" \
  --mozc-build-identity "$MZED_IME_MOZC_BUILD_IDENTITY" \
  --diagnostic-profile \
  --zed-binary "$MZED_IME_ZED_BINARY" \
  --zed-build-receipt "$MZED_IME_ZED_BUILD_RECEIPT"
python3 "$PROFILE_DIR/prepare_profile.py" \
  --run-root "$MZED_IME_RUN_ROOT" \
  --runtime-prefix "$MZED_IME_RUNTIME_PREFIX"
python3 "$PROFILE_DIR/assemble_run.py" \
  --run-root "$MZED_IME_RUN_ROOT" \
  --runtime-prefix "$MZED_IME_RUNTIME_PREFIX" \
  --zed-binary "$MZED_IME_ZED_BINARY" \
  --zed-build-receipt "$MZED_IME_ZED_BUILD_RECEIPT" \
  --mozc-build-identity "$MZED_IME_MOZC_BUILD_IDENTITY"
python3 "$PROFILE_DIR/doctor.py" profile \
  --run-root "$MZED_IME_RUN_ROOT" \
  --runtime-prefix "$MZED_IME_RUNTIME_PREFIX"

if [[ "$MODE" == "run-fresh" ]]; then
  : "${DISPLAY:?run mode requires the owner-provided desktop DISPLAY}"
  [[ -n "${XAUTHORITY:-}" && -r "$XAUTHORITY" ]] || {
    echo "run mode requires the owner-provided readable XAUTHORITY path" >&2
    exit 2
  }
  exec "$MZED_IME_RUN_ROOT/launch-w3moz"
fi

echo "Profile assembled and checked; no GUI/runtime process was started. Use --run for the explicit diagnostic run."
