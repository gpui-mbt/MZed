#!/usr/bin/env bash
set -euo pipefail

WORK=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
: "${MOZC_SOURCE_ROOT:?set MOZC_SOURCE_ROOT to the verified, patched Debian Mozc source tree}"
: "${CXX:=c++}"
SOURCE="$(cd -- "$MOZC_SOURCE_ROOT" && pwd -P)"
TMP=$(mktemp -d)
trap 'rm -rf -- "$TMP"' EXIT

"$CXX" -std=c++17 \
  -Wall -Wextra -Werror -I "$SOURCE" \
  "$WORK/tests/peer_pid_log_gate_test.cc" -o "$TMP/peer-pid-gate-off"
"$TMP/peer-pid-gate-off"

"$CXX" -std=c++17 \
  -Wall -Wextra -Werror -DMOZC_TEST_AUTHENTICATED_PEER_PID -I "$SOURCE" \
  "$WORK/tests/peer_pid_log_gate_test.cc" -o "$TMP/peer-pid-gate-on"
"$TMP/peer-pid-gate-on"
