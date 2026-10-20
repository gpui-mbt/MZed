#!/usr/bin/env bash
set -Eeuo pipefail

TEMP=$(mktemp -d)
trap 'rm -rf -- "$TEMP"' EXIT

start_exec_preserving_emitter_pid() {
  local emitter_pid="$BASHPID"
  sleep 0.02
  printf '%s\n' "$emitter_pid" > "$TEMP/emitter-pid.txt"
  export MOZC_TEST_FCITX_EMITTER_PID="$emitter_pid"
  exec python3 -c 'import os; print(os.getpid(), os.environ["MOZC_TEST_FCITX_EMITTER_PID"])'
}

start_exec_preserving_emitter_pid > "$TEMP/child.txt" &
spawned_pid=$!
emitter_pid=
for attempt in {1..100}; do
  if [[ -f "$TEMP/emitter-pid.txt" ]]; then
    emitter_pid=$(cat "$TEMP/emitter-pid.txt")
    [[ "$emitter_pid" == "$spawned_pid" ]] && break
  fi
  kill -0 "$spawned_pid" 2>/dev/null
  sleep 0.01
done
[[ "$emitter_pid" == "$spawned_pid" ]]
wait "$spawned_pid"
read -r child_pid child_hint < "$TEMP/child.txt"
[[ "$spawned_pid" == "$emitter_pid" ]]
[[ "$spawned_pid" == "$child_pid" ]]
[[ "$spawned_pid" == "$child_hint" ]]
printf 'Fcitx exec PID hint preserves emitter identity: PASS\n'
