#!/usr/bin/env bash
set -Eeuo pipefail

WORK=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
TEMP=$(mktemp -d)
trap 'rm -rf -- "$TEMP"' EXIT

RUNNER_CANDIDATE_DIR="$WORK/runner"
PREFIX=/private/prefix
LOG="$TEMP/log"
mkdir -p "$LOG"
source "$RUNNER_CANDIDATE_DIR/runner-fragment.sh"

MOZC_PID=321
MOZC_PARENT_PID=222
MOZC_START_TIME=987654
MOZC_SERVER=/private/prefix/usr/lib/mozc/mozc_server
FCITX_COMPILED_MOZC_SERVER="$MOZC_SERVER"
CHECK_CALLS=0
CHECK_MODE=accepted

verify_owned_mozc() {
  local output="$1"
  CHECK_CALLS=$((CHECK_CALLS + 1))
  case "$CHECK_MODE" in
    accepted)
      printf '%s\n' \
        '{"admitted":true,"reason":"owned-child-identity-matches","verification_scope":"single-known-owned-child","global_process_completeness_claimed":false,"owned_child_identity_verified":true}' \
        >> "$output"
      return 0
      ;;
    denied)
      printf '%s\n' \
        '{"admitted":false,"reason":"owned-process-disappeared","verification_scope":"single-known-owned-child","global_process_completeness_claimed":false,"owned_child_identity_verified":false}' \
        >> "$output"
      return 1
      ;;
    malformed)
      printf '%s\n' 'not-json' >> "$output"
      return 0
      ;;
    missing-record)
      return 1
      ;;
    *) return 2 ;;
  esac
}

verify_owned_mozc_startup
[[ "$CHECK_CALLS" == 1 ]]
python3 - "$LOG/mozc-owned-child-startup.json" <<'PY'
import json
import sys
from pathlib import Path

record = json.loads(Path(sys.argv[1]).read_text())
assert record["admitted"] is True
assert record["owned_child_identity_verified"] is True
assert record["verification_scope"] == "single-known-owned-child"
assert record["global_process_completeness_claimed"] is False
PY

# Each failure case models a fresh log root, so the prior success marker cannot
# be mistaken for the current invocation's result.
rm -- "$LOG/mozc-owned-child-startup.json"
for mode in denied malformed missing-record; do
  CHECK_MODE="$mode"
  prior_calls=$CHECK_CALLS
  if verify_owned_mozc_startup; then
    echo "startup gate unexpectedly admitted mode=$mode" >&2
    exit 1
  fi
  [[ "$CHECK_CALLS" == "$((prior_calls + 1))" ]]
  [[ ! -e "$LOG/mozc-owned-child-startup.json" ]]
  if [[ "$mode" == missing-record ]]; then
    [[ ! -s "$LOG/mozc-owned-child-checks-startup.jsonl" ]]
  fi
done

echo "owned-child startup gate: PASS (one identity check, three fail-closed cases)"
