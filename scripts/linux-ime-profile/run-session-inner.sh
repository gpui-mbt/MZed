#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

RUN=${MZED_RUN_ROOT:?MZED_RUN_ROOT must name this fresh run directory}
PROFILE="$RUN/profile"
PREFIX=${MZED_RUNTIME_PREFIX:?MZED_RUNTIME_PREFIX must name the private runtime prefix}
BUNDLE="$RUN/runner"
ZED=${MZED_ZED_BINARY:?MZED_ZED_BINARY must name the compiled MZed ELF}
PROJECT="$RUN/empty-project"
RUNNER_CANDIDATE_DIR="$RUN/runner"
runtime_input_sha256() {
  python3 - "$RUN/integration-manifest.json" "$1" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)["runtime_inputs"].get(sys.argv[2])
if not isinstance(value, str):
    raise SystemExit(1)
print(value)
PY
}
MOZC_FRAGMENT_SHA256=$(runtime_input_sha256 "runner/runner-fragment.sh")
ZED_SUPERVISOR_SHA256=$(runtime_input_sha256 "runner/zed_supervisor.py")
PEER_PID_LOG_FILTER="$RUNNER_CANDIDATE_DIR/peer_pid_log_filter.py"
PEER_PID_LOG_FILTER_SHA256=$(runtime_input_sha256 "runner/peer_pid_log_filter.py")
MONITOR_DEADLINE="$RUNNER_CANDIDATE_DIR/monitor_deadline.py"
MONITOR_DEADLINE_SHA256=$(runtime_input_sha256 "runner/monitor_deadline.py")
MOZC_IDENTITY_SHA256=$(runtime_input_sha256 "runner/build-identity.json")
SOCKET=wayland-0
ZED_USER_DATA="$PROFILE/zed-stateless-user-data"
LOG="$RUN/logs/ime-diagnostic"
MONITOR_TIMEOUT_SECONDS=900
MONITOR_MAX_ATTEMPTS=3600
TEST_MODE=0
if [[ "${1:-}" == "--test-fail-before-components" ]]; then
  TEST_MODE=1
  LOG="$RUN/logs/wrapper-failure-test"
elif [[ $# -gt 0 ]]; then
  echo "unsupported session argument" >&2
  exit 2
fi
[[ ! -e "$LOG" && ! -L "$LOG" ]] || { echo "refusing reused attempt log directory" >&2; exit 2; }
mkdir -p "$LOG"
chmod 700 "$LOG"
for item in \
  "$RUNNER_CANDIDATE_DIR/runner-fragment.sh" \
  "$RUNNER_CANDIDATE_DIR/mozc_supervisor.py" \
  "$RUNNER_CANDIDATE_DIR/zed_supervisor.py" \
  "$RUNNER_CANDIDATE_DIR/check_mozc_owner.py" \
  "$RUNNER_CANDIDATE_DIR/peer_pid_check.py" \
  "$RUNNER_CANDIDATE_DIR/peer_pid_log_filter.py" \
  "$MONITOR_DEADLINE" \
  "$RUNNER_CANDIDATE_DIR/build-identity.json"; do
  [[ -f "$item" && ! -L "$item" ]] || { echo "private Mozc runner input is missing or not a regular file" >&2; exit 2; }
done
[[ "$(sha256sum "$RUNNER_CANDIDATE_DIR/runner-fragment.sh" | awk '{print $1}')" == "$MOZC_FRAGMENT_SHA256" ]] || { echo "private Mozc runner fragment hash mismatch" >&2; exit 2; }
[[ "$(sha256sum "$RUNNER_CANDIDATE_DIR/zed_supervisor.py" | awk '{print $1}')" == "$ZED_SUPERVISOR_SHA256" ]] || { echo "private Zed supervisor hash mismatch" >&2; exit 2; }
[[ "$(sha256sum "$PEER_PID_LOG_FILTER" | awk '{print $1}')" == "$PEER_PID_LOG_FILTER_SHA256" ]] || { echo "private peer log filter hash mismatch" >&2; exit 2; }
[[ "$(sha256sum "$MONITOR_DEADLINE" | awk '{print $1}')" == "$MONITOR_DEADLINE_SHA256" ]] || { echo "private monitor deadline hash mismatch" >&2; exit 2; }
DBUS_PID=
LABWC_PID=
FCITX_PID=
FCITX_EMITTER_PID=
FCITX_PARENT_PID=
FCITX_START_TIME=
FCITX_REAL_EXE=
ZED_PID=
ZED_SUPERVISOR_PID=
ZED_SUPERVISOR_IN_FD=
ZED_SUPERVISOR_OUT_FD=
ZED_SUPERVISOR_WAITED=0
ZED_PARENT_PID=
ZED_START_TIME=
TAIL_PID=
FCITX_FILTER_PID=
FCITX_FILTER_WAITED=0
FCITX_FILTER_STATUS=not-started
FCITX_FILTER_WRITE_FD=
FCITX_FILTER_READ_FD=
FCITX_CONTEXT_MONITOR_PID=
FCITX_CONTEXT_MONITOR_WAITED=0
FCITX_CONTEXT_MONITOR_STATUS=not-started
ZED_STATUS=not-observed
ZED_SUPERVISOR_STATUS=not-observed
ZED_ATTEMPTED=0
ZED_EXEC_VERIFIED=0
ZED_WAITED=0
ZED_OWNED_CHILD_CHECK_FAILED=0

# Defines the pinned private build identity check and owned-child lifecycle.
. "$RUNNER_CANDIDATE_DIR/runner-fragment.sh"

run_without_runner_control_fds() (
  if [[ -n "$MOZC_SUPERVISOR_IN_FD" ]]; then
    exec {MOZC_SUPERVISOR_IN_FD}>&-
  fi
  if [[ -n "$MOZC_SUPERVISOR_OUT_FD" ]]; then
    exec {MOZC_SUPERVISOR_OUT_FD}<&-
  fi
  if [[ -n "$ZED_SUPERVISOR_IN_FD" ]]; then
    exec {ZED_SUPERVISOR_IN_FD}>&-
  fi
  if [[ -n "$ZED_SUPERVISOR_OUT_FD" ]]; then
    exec {ZED_SUPERVISOR_OUT_FD}<&-
  fi
  exec "$@"
)

zed_event_field() {
  run_without_runner_control_fds python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2], ""))' "$1" "$2"
}

record_zed_event() {
  printf '%s\n' "$1" >> "$LOG/zed-supervisor-events.jsonl"
}

consume_zed_reaped_event() {
  local reaped_status
  if reaped_status=$(run_without_runner_control_fds python3 - "$RUNNER_CANDIDATE_DIR/mozc_supervisor.py" "$1" "$ZED_PID" <<'PY'
import sys
sys.path.insert(0, __import__("os").path.dirname(sys.argv[1]))
from mozc_supervisor import verified_reaped_status

status = verified_reaped_status(sys.argv[2], int(sys.argv[3]))
if status is None:
    raise SystemExit(1)
print(status)
PY
  ); then
    ZED_STATUS=$reaped_status
    ZED_WAITED=1
    return 0
  fi
  ZED_STATUS=unverified-reaped-event
  return 1
}

read_zed_event() {
  IFS= read -r ZED_EVENT_RECORD <&"$ZED_SUPERVISOR_OUT_FD"
}

finish_zed_supervisor() {
  [[ -n "$ZED_SUPERVISOR_PID" && "$ZED_SUPERVISOR_WAITED" != 1 ]] || return 0
  if [[ "$ZED_WAITED" != 1 ]]; then
    if [[ -n "$ZED_SUPERVISOR_IN_FD" ]]; then
      # The private supervisor protocol is one complete newline-delimited stop command.
      printf 'stop\n' >&"$ZED_SUPERVISOR_IN_FD" 2>/dev/null || true
    fi
    local event
    while read_zed_event; do
      record_zed_event "$ZED_EVENT_RECORD"
      event=$(zed_event_field "$ZED_EVENT_RECORD" event) || event=invalid
      if [[ "$event" == owned_child_identity_failed ]]; then
        ZED_OWNED_CHILD_CHECK_FAILED=1
        printf 'owned-child-identity-failed\n' > "$LOG/zed-owned-child-stop-reason.txt"
      elif [[ "$event" == error ]]; then
        printf '%s\n' "$ZED_EVENT_RECORD" > "$LOG/zed-supervisor-error.json"
      elif [[ "$event" == reaped ]]; then
        consume_zed_reaped_event "$ZED_EVENT_RECORD" || true
        break
      fi
    done
  fi
  set +e
  wait "$ZED_SUPERVISOR_PID"
  ZED_SUPERVISOR_STATUS=$?
  set -e
  ZED_SUPERVISOR_WAITED=1
  if [[ -n "$ZED_SUPERVISOR_IN_FD" ]]; then
    exec {ZED_SUPERVISOR_IN_FD}>&-
    ZED_SUPERVISOR_IN_FD=
  fi
  if [[ -n "$ZED_SUPERVISOR_OUT_FD" ]]; then
    exec {ZED_SUPERVISOR_OUT_FD}<&-
    ZED_SUPERVISOR_OUT_FD=
  fi
}

start_owned_zed() {
  ZED_ATTEMPTED=1
  # Keep the coprocess channel name distinct from the retained PID variable:
  # Bash unsets <COPROC_NAME>_PID when that coprocess exits, including before
  # the EXIT cleanup trap runs.
  coproc ZED_SUPERVISOR_PIPE {
    if [[ -n "$MOZC_SUPERVISOR_IN_FD" ]]; then exec {MOZC_SUPERVISOR_IN_FD}>&-; fi
    if [[ -n "$MOZC_SUPERVISOR_OUT_FD" ]]; then exec {MOZC_SUPERVISOR_OUT_FD}<&-; fi
    exec python3 "$RUNNER_CANDIDATE_DIR/zed_supervisor.py" \
      --expected-exe "$ZED" \
      --child-log "$LOG/zed.log" \
      --event-log "$LOG/zed-supervisor.jsonl" \
      --guard-log "$LOG/mozc-owned-child-checks-zed.jsonl" \
      --owner-check "$MOZC_OWNER_CHECK" \
      --owner-pid "$MOZC_PID" \
      --owner-start-time "$MOZC_START_TIME" \
      --owner-parent-pid "$MOZC_PARENT_PID" \
      --server-path "$MOZC_SERVER" \
      --client-path "$FCITX_COMPILED_MOZC_SERVER" \
      -- \
      python3 "$BUNDLE/scripts/prefix-env.py" \
        --prefix "$PREFIX" --profile "$PROFILE" --display "$DISPLAY" \
        --wayland-display "$SOCKET" --mzed-native-palette --direct-exec --allow-emulated-gpu -- \
        /usr/bin/env ZED_STATELESS=1 "$ZED" --user-data-dir "$ZED_USER_DATA" "$PROJECT"
  }
  ZED_SUPERVISOR_PID=$!
  local original_zed_in_fd="${ZED_SUPERVISOR_PIPE[1]}"
  local original_zed_out_fd="${ZED_SUPERVISOR_PIPE[0]}"
  exec {ZED_SUPERVISOR_IN_FD}>&"$original_zed_in_fd"
  exec {ZED_SUPERVISOR_OUT_FD}<&"$original_zed_out_fd"
  exec {original_zed_in_fd}>&-
  exec {original_zed_out_fd}<&-
  printf 'zed_supervisor\t%s\n' "$ZED_SUPERVISOR_PID" >> "$LOG/stage-pids.tsv"

  local event
  if ! read_zed_event; then
    echo "Zed supervisor closed before startup record" >&2
    return 1
  fi
  record_zed_event "$ZED_EVENT_RECORD"
  event=$(zed_event_field "$ZED_EVENT_RECORD" event) || return 1
  [[ "$event" == started ]] || { echo "Zed supervisor did not start cleanly" >&2; return 1; }
  ZED_PID=$(zed_event_field "$ZED_EVENT_RECORD" pid) || return 1
  local reported_supervisor_pid reported_supervisor_parent
  reported_supervisor_pid=$(zed_event_field "$ZED_EVENT_RECORD" supervisor_pid) || return 1
  reported_supervisor_parent=$(zed_event_field "$ZED_EVENT_RECORD" supervisor_parent_pid) || return 1
  ZED_PARENT_PID=$(zed_event_field "$ZED_EVENT_RECORD" parent_pid) || return 1
  ZED_START_TIME=$(zed_event_field "$ZED_EVENT_RECORD" start_time) || return 1
  [[ "$reported_supervisor_pid" == "$ZED_SUPERVISOR_PID" && "$reported_supervisor_parent" == "$$" && "$ZED_PARENT_PID" == "$ZED_SUPERVISOR_PID" && "$(zed_event_field "$ZED_EVENT_RECORD" pidfd_open)" == True ]] || {
    echo "Zed supervisor did not confirm exact child ownership and pidfd" >&2
    return 1
  }
  printf '%s\n' "$ZED_EVENT_RECORD" > "$LOG/zed-started.json"
  printf 'zed_child\t%s\n' "$ZED_PID" >> "$LOG/stage-pids.tsv"

  while read_zed_event; do
    record_zed_event "$ZED_EVENT_RECORD"
    event=$(zed_event_field "$ZED_EVENT_RECORD" event) || event=invalid
    if [[ "$event" == exec_verified ]]; then
      [[ "$(zed_event_field "$ZED_EVENT_RECORD" pid)" == "$ZED_PID" && "$(zed_event_field "$ZED_EVENT_RECORD" proc_exe)" == "$ZED" && "$(zed_event_field "$ZED_EVENT_RECORD" start_time)" == "$ZED_START_TIME" ]] || return 1
      ZED_EXEC_VERIFIED=1
      printf 'pid=%s\nparent_pid=%s\nstart_time=%s\nproc_exe=%s\nexec_verified=yes\n' "$ZED_PID" "$ZED_PARENT_PID" "$ZED_START_TIME" "$ZED" > "$LOG/zed-process-identity.txt"
      run_without_runner_control_fds python3 - "$ZED_PID" > "$LOG/zed-environment-proof.txt" <<'PYZEDENV'
from pathlib import Path
import sys
pid=sys.argv[1]
raw=Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
values={}
for entry in raw:
    if b'=' in entry:
        key,value=entry.split(b'=',1)
        if key in (b'ZED_STATELESS',b'WAYLAND_DISPLAY',b'XDG_RUNTIME_DIR',b'DISPLAY'):
            values[key.decode()]=value.decode('utf-8','replace')
for key in ('ZED_STATELESS','WAYLAND_DISPLAY','XDG_RUNTIME_DIR','DISPLAY'):
    print(f'{key}={values.get(key,"<missing>")}')
PYZEDENV
      return 0
    fi
    if [[ "$event" == owned_child_identity_failed || "$event" == error || "$event" == reaped ]]; then
      [[ "$event" != owned_child_identity_failed ]] || ZED_OWNED_CHILD_CHECK_FAILED=1
      if [[ "$event" == reaped ]]; then
        consume_zed_reaped_event "$ZED_EVENT_RECORD" || true
      fi
      printf '%s\n' "$ZED_EVENT_RECORD" > "$LOG/zed-supervisor-start-failure.json"
      return 1
    fi
  done
  echo "Zed supervisor event stream ended before executable verification" >&2
  return 1
}

fcitx_peer_guard_fail() {
  local reason="$1" record="${2:-}" engine_class="${3:-empty}"
  run_without_runner_control_fds python3 - "$reason" "$record" "$engine_class" > "$LOG/fcitx-peer-guard-failed.json" <<'PY'
import json, sys
try:
    detail = json.loads(sys.argv[2]) if sys.argv[2] else None
except json.JSONDecodeError:
    detail = {"reason": "monitor-record-invalid"}
print(json.dumps({
    "admitted": False,
    "reason": sys.argv[1],
    "fcitx_engine_class": sys.argv[3],
    "detail": detail,
    "verification_scope": "authenticated-fcitx-peer-to-owned-child",
    "global_process_completeness_claimed": False,
}, sort_keys=True))
PY
  if [[ -n "$ZED_SUPERVISOR_IN_FD" ]]; then
    printf 'stop\n' >&"$ZED_SUPERVISOR_IN_FD" 2>/dev/null || true
  fi
  return 1
}

fcitx_engine_class() {
  case "${1:-}" in
    "") printf 'empty' ;;
    mozc) printf 'mozc' ;;
    keyboard-us) printf 'keyboard-us' ;;
    *) printf 'other' ;;
  esac
}

fcitx_record_default_before_context() {
  local remote="$1" receipt="$2" current_engine=
  current_engine=$(run_without_runner_control_fds "$remote" -n 2>/dev/null) || current_engine=
  printf 'phase=before-context\ndefault_selection_request=accepted\nengine_class_before=%s\n' \
    "$(fcitx_engine_class "$current_engine")" > "$receipt"
}

fcitx_select_active_context_mozc() {
  local remote="$1" receipt="$2" engine_class_before="${3:-empty}"
  local selection_request=failed current_engine current_engine_class=empty readback_confirmed=no
  if run_without_runner_control_fds "$remote" -s mozc >/dev/null 2>&1; then
    selection_request=accepted
  fi
  current_engine=$(run_without_runner_control_fds "$remote" -n 2>/dev/null) || current_engine=
  current_engine_class=$(fcitx_engine_class "$current_engine")
  if [[ "$selection_request" == accepted && "$current_engine" == mozc ]]; then
    readback_confirmed=yes
  fi
  printf 'phase=active-context\nengine_class_before=%s\nselection_request=%s\nengine_class_after=%s\nreadback_confirmed=%s\n' \
    "$engine_class_before" "$selection_request" "$current_engine_class" "$readback_confirmed" > "$receipt"
  printf '%s\n' "$current_engine_class"
  [[ "$readback_confirmed" == yes ]]
}

start_fcitx_peer_test_child() {
  if [[ -n "$MOZC_SUPERVISOR_IN_FD" ]]; then exec {MOZC_SUPERVISOR_IN_FD}>&-; fi
  if [[ -n "$MOZC_SUPERVISOR_OUT_FD" ]]; then exec {MOZC_SUPERVISOR_OUT_FD}<&-; fi
  if [[ -n "$ZED_SUPERVISOR_IN_FD" ]]; then exec {ZED_SUPERVISOR_IN_FD}>&-; fi
  if [[ -n "$ZED_SUPERVISOR_OUT_FD" ]]; then exec {ZED_SUPERVISOR_OUT_FD}<&-; fi
  if [[ -n "$FCITX_FILTER_WRITE_FD" ]]; then exec {FCITX_FILTER_WRITE_FD}>&-; fi
  local emitter_pid="$BASHPID"
  printf '%s\n' "$emitter_pid" > "$LOG/fcitx5-emitter-pid.txt"
  export MOZC_TEST_FCITX_EMITTER_PID="$emitter_pid"
  exec "$@"
}

start_fcitx_peer_log_filter() {
  coproc PEER_PID_LOG_FILTER_PIPE {
    exec python3 "$PEER_PID_LOG_FILTER" \
      "$LOG/fcitx5-peer-pids.log" "$LOG/fcitx5-peer-filter-status.json" \
      "$LOG/fcitx5-peer-filter.stop" \
      >/dev/null 2>/dev/null
  }
  FCITX_FILTER_PID=$!
  local original_filter_in_fd="${PEER_PID_LOG_FILTER_PIPE[1]}"
  local original_filter_out_fd="${PEER_PID_LOG_FILTER_PIPE[0]}"
  exec {FCITX_FILTER_WRITE_FD}>&"$original_filter_in_fd"
  exec {original_filter_in_fd}>&-
  exec {original_filter_out_fd}<&-
  printf 'fcitx_peer_log_filter\t%s\n' "$FCITX_FILTER_PID" >> "$LOG/stage-pids.tsv"

  local status
  for attempt in {1..100}; do
    status=$(run_without_runner_control_fds python3 - "$LOG/fcitx5-peer-filter-status.json" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        record = json.load(stream)
except (OSError, json.JSONDecodeError):
    print("not-ready")
else:
    value = record.get("status")
    print(value if value in ("running", "failed") else "invalid")
PY
    ) || status=invalid
    case "$status" in
      running) FCITX_FILTER_STATUS=running; return 0 ;;
      failed|invalid) echo "private peer log filter failed before Fcitx startup" >&2; return 1 ;;
    esac
    if ! kill -0 "$FCITX_FILTER_PID" 2>/dev/null; then
      echo "private peer log filter exited before Fcitx startup" >&2
      return 1
    fi
    sleep 0.01
  done
  echo "private peer log filter did not become ready" >&2
  return 1
}

read_fcitx_child_identity() {
  run_without_runner_control_fds python3 - "$RUNNER_CANDIDATE_DIR/check_mozc_owner.py" \
    "$FCITX_PID" "$FCITX_PARENT_PID" "$FCITX_START_TIME" "$FCITX_REAL_EXE" <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent))
from check_mozc_owner import read_owned_identity, process_state_running

pid, parent, start = map(int, sys.argv[2:5])
expected_exe = sys.argv[5]
record = {
    "admitted": False,
    "reason": "not-verified",
    "verification_scope": "single-known-owned-fcitx-child",
    "global_process_completeness_claimed": False,
}
try:
    identity = read_owned_identity(Path("/proc"), pid)
except PermissionError:
    record["reason"] = "fcitx-process-permission-denied"
except FileNotFoundError:
    record["reason"] = "fcitx-process-disappeared"
except (OSError, StopIteration, ValueError, IndexError, RuntimeError):
    record["reason"] = "fcitx-process-identity-unreadable-or-unstable"
else:
    record["pid"] = identity["pid"]
    record["parent_pid"] = identity["parent_pid"]
    record["start_time"] = identity["start_time"]
    record["uid"] = identity["uid"]
    record["executable"] = identity["executable"]
    if not process_state_running(str(identity["state"])):
        record["reason"] = "fcitx-child-not-running"
    elif identity["uid"] != os.geteuid():
        record["reason"] = "fcitx-child-uid-mismatch"
    elif identity["parent_pid"] != parent:
        record["reason"] = "fcitx-child-parent-mismatch"
    elif identity["start_time"] != start:
        record["reason"] = "fcitx-child-start-time-mismatch"
    elif identity["executable"] != expected_exe:
        record["reason"] = "fcitx-child-executable-mismatch"
    else:
        record["admitted"] = True
        record["reason"] = "fcitx-child-identity-matches"
print(json.dumps(record, sort_keys=True, separators=(",", ":")))
PY
}

capture_fcitx_child_start_time() {
  run_without_runner_control_fds python3 - "$RUNNER_CANDIDATE_DIR/check_mozc_owner.py" \
    "$FCITX_PID" "$FCITX_PARENT_PID" "$FCITX_REAL_EXE" <<'PY'
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent))
from check_mozc_owner import process_state_running, read_owned_identity

pid, parent = map(int, sys.argv[2:4])
expected_exe = sys.argv[4]
try:
    identity = read_owned_identity(Path("/proc"), pid)
except (OSError, StopIteration, ValueError, IndexError, RuntimeError):
    raise SystemExit(1)
if not (
    process_state_running(str(identity["state"]))
    and identity["parent_pid"] == parent
    and identity["uid"] == os.geteuid()
    and identity["executable"] == expected_exe
    and int(identity["start_time"]) > 0
):
    raise SystemExit(1)
print(identity["start_time"])
PY
}

monitor_fcitx_context() {
  local attempt current owner_record peer_record peer_state filter_state fcitx_identity_record
  local peer_admitted=0
  local emitter_pid
  local engine_class=empty
  local active_context_selection_complete=0
  local monitor_window monitor_started_ns monitor_deadline_ns deadline_state deadline_rc
  if [[ -n "$MOZC_SUPERVISOR_IN_FD" ]]; then exec {MOZC_SUPERVISOR_IN_FD}>&-; MOZC_SUPERVISOR_IN_FD=; fi
  if [[ -n "$MOZC_SUPERVISOR_OUT_FD" ]]; then exec {MOZC_SUPERVISOR_OUT_FD}<&-; MOZC_SUPERVISOR_OUT_FD=; fi
  if [[ -n "$ZED_SUPERVISOR_OUT_FD" ]]; then exec {ZED_SUPERVISOR_OUT_FD}<&-; ZED_SUPERVISOR_OUT_FD=; fi
  monitor_window=$(run_without_runner_control_fds python3 "$MONITOR_DEADLINE" start) || {
    fcitx_peer_guard_fail "monitor-deadline-start-failed"
    return 1
  }
  IFS=$'\t' read -r monitor_started_ns monitor_deadline_ns <<< "$monitor_window"
  if [[ ! "$monitor_started_ns" =~ ^[0-9]+$ || ! "$monitor_deadline_ns" =~ ^[0-9]+$ ]]; then
    fcitx_peer_guard_fail "monitor-deadline-start-invalid"
    return 1
  fi
  printf '{"timeout_seconds":%s,"maximum_attempts":%s,"poll_sleep_seconds":0.25,"start_monotonic_ns":%s,"deadline_monotonic_ns":%s}\n' \
    "$MONITOR_TIMEOUT_SECONDS" "$MONITOR_MAX_ATTEMPTS" "$monitor_started_ns" "$monitor_deadline_ns" \
    > "$LOG/fcitx-peer-monitor-window.json"
  emitter_pid=$(run_without_runner_control_fds cat "$LOG/fcitx5-emitter-pid.txt") || return 1
  [[ "$emitter_pid" == "$FCITX_PID" ]] || \
    { fcitx_peer_guard_fail "emitter-pid-does-not-match-fcitx-child"; return 1; }

  for ((attempt = 1; attempt <= MONITOR_MAX_ATTEMPTS; attempt++)); do
    deadline_rc=0
    deadline_state=$(run_without_runner_control_fds python3 "$MONITOR_DEADLINE" check "$monitor_deadline_ns") || deadline_rc=$?
    if [[ "$deadline_rc" == 1 && "$deadline_state" == expired ]]; then
      printf '{"attempt":%s,"timeout_seconds":%s,"reason":"monotonic-deadline-expired"}\n' \
        "$attempt" "$MONITOR_TIMEOUT_SECONDS" > "$LOG/fcitx-peer-monitor-timeout.json"
      if [[ "$peer_admitted" == 1 ]]; then
        fcitx_peer_guard_fail "fcitx-peer-monitor-timeout-after-admission" "" "$engine_class"
      else
        printf 'status=timeout-before-authenticated-peer\nengine=%s\ntimestamp_utc=%s\n' \
          "$engine_class" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LOG/fcitx-current-im-after-context.txt"
        fcitx_peer_guard_fail "authenticated-peer-gate-timeout" "" "$engine_class"
      fi
      return 1
    elif [[ "$deadline_rc" != 0 || "$deadline_state" != active ]]; then
      fcitx_peer_guard_fail "monitor-deadline-check-failed" "" "$engine_class"
      return 1
    fi

    if [[ -e "$LOG/fcitx-context-monitor.stop" ]]; then
      printf 'status=%s\nengine=\ntimestamp_utc=%s\n' \
        "$([[ "$peer_admitted" == 1 ]] && echo peer-verified-then-stopped || echo stopped-before-peer-verification)" \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LOG/fcitx-current-im-after-context.txt"
      if [[ "$peer_admitted" == 1 ]]; then
        return 0
      fi
      fcitx_peer_guard_fail "stopped-before-authenticated-peer-record"
      return 1
    fi

    if ! verify_owned_mozc "$LOG/mozc-owned-child-checks-monitor.jsonl"; then
      owner_record=$(run_without_runner_control_fds tail -n 1 "$LOG/mozc-owned-child-checks-monitor.jsonl") || owner_record=
      fcitx_peer_guard_fail "owned-child-identity-check-failed" "$owner_record"
      return 1
    fi
    owner_record=$(run_without_runner_control_fds tail -n 1 "$LOG/mozc-owned-child-checks-monitor.jsonl") || owner_record=
    if ! run_without_runner_control_fds python3 - "$owner_record" <<'PY'
import json, sys
try:
    record = json.loads(sys.argv[1])
except json.JSONDecodeError:
    raise SystemExit(1)
if not (
    record.get("admitted") is True
    and record.get("owned_child_identity_verified") is True
    and record.get("verification_scope") == "single-known-owned-child"
    and record.get("global_process_completeness_claimed") is False
):
    raise SystemExit(1)
PY
    then
      fcitx_peer_guard_fail "owned-child-identity-record-invalid" "$owner_record"
      return 1
    fi

    filter_state=$(run_without_runner_control_fds python3 - "$LOG/fcitx5-peer-filter-status.json" <<'PY'
import json, sys, time
try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        record = json.load(stream)
except (OSError, json.JSONDecodeError):
    print("invalid")
else:
    status = record.get("status")
    records = record.get("peer_record_count")
    discarded = record.get("discarded_nonmarker_line_count")
    heartbeat = record.get("heartbeat_monotonic_ns")
    reason = record.get("reason")
    age = time.monotonic_ns() - heartbeat if type(heartbeat) is int else -1
    if (status not in ("running", "clean-eof", "failed")
            or type(records) is not int or records < 0 or records > 256
            or type(discarded) is not int or discarded < 0
            or type(heartbeat) is not int or heartbeat <= 0 or age < 0 or age > 2_000_000_000
            or (reason is not None and not isinstance(reason, str))):
        print("invalid")
    else:
        print(status)
PY
    ) || filter_state=invalid
    if [[ "$filter_state" == failed || "$filter_state" == invalid || "$filter_state" == clean-eof ]]; then
      fcitx_peer_guard_fail "fcitx-peer-log-filter-$filter_state"
      return 1
    fi

    fcitx_identity_record=$(read_fcitx_child_identity) || fcitx_identity_record=
    printf '%s\n' "$fcitx_identity_record" >> "$LOG/fcitx-process-checks.jsonl"
    if ! run_without_runner_control_fds python3 - "$fcitx_identity_record" <<'PY'
import json, sys
try:
    record = json.loads(sys.argv[1])
except json.JSONDecodeError:
    raise SystemExit(1)
if not (
    record.get("admitted") is True
    and record.get("verification_scope") == "single-known-owned-fcitx-child"
    and record.get("global_process_completeness_claimed") is False
):
    raise SystemExit(1)
PY
    then
      fcitx_peer_guard_fail "fcitx-child-identity-check-failed" "$fcitx_identity_record"
      return 1
    fi

    peer_record=$(run_without_runner_control_fds python3 "$PEER_PID_CHECK" \
      "$LOG/fcitx5-peer-pids.log" "$MOZC_PID" "$FCITX_PID" "$emitter_pid" --allow-pending) || {
        peer_record=${peer_record:-}
        fcitx_peer_guard_fail "authenticated-peer-record-rejected" "$peer_record"
        return 1
      }
    printf '%s\n' "$peer_record" >> "$LOG/fcitx-peer-record-checks.jsonl"
    peer_state=$(run_without_runner_control_fds python3 -c \
      'import json,sys; r=json.loads(sys.argv[1]); print("admitted" if r.get("admitted") is True else "pending" if r.get("pending") is True else "rejected")' \
      "$peer_record") || peer_state=rejected
    if [[ "$peer_state" == rejected ]]; then
      fcitx_peer_guard_fail "authenticated-peer-record-rejected" "$peer_record"
      return 1
    fi

    current=$(run_without_runner_control_fds "$PREFIX/usr/bin/fcitx5-remote" -n 2>/dev/null) || current=
    engine_class=$(fcitx_engine_class "$current")
    if [[ "$current" == mozc && "$active_context_selection_complete" != 1 ]]; then
      printf 'phase=active-context\nengine_class_before=mozc\nselection_request=not-needed\nengine_class_after=mozc\nreadback_confirmed=yes\n' \
        > "$LOG/fcitx-active-context-selection.txt"
      active_context_selection_complete=1
    elif [[ "$current" == keyboard-us && "$active_context_selection_complete" != 1 ]]; then
      active_context_selection_complete=1
      local selected_engine_class
      if selected_engine_class=$(fcitx_select_active_context_mozc \
        "$PREFIX/usr/bin/fcitx5-remote" "$LOG/fcitx-active-context-selection.txt" "$engine_class"); then
        current=mozc
        engine_class=mozc
      else
        selected_engine_class=${selected_engine_class:-other}
        fcitx_peer_guard_fail "active-context-mozc-selection-not-confirmed" "" "$selected_engine_class"
        return 1
      fi
    elif [[ -n "$current" && "$current" != mozc ]]; then
      fcitx_peer_guard_fail "unexpected-fcitx-engine" "" "$engine_class"
      return 1
    fi

    if [[ "$peer_state" == admitted && "$current" == mozc && "$peer_admitted" != 1 ]]; then
      peer_admitted=1
      {
        printf '{"status":"authenticated-peer-matches-owned-child","fcitx_pid":%s,"owned_child_pid":%s,"observed_peer_pids":' "$FCITX_PID" "$MOZC_PID"
        run_without_runner_control_fds python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["observed_peer_pids"],separators=(",",":")))' "$peer_record"
        printf ',"record_count":%s,"emitter_pid_matches_fcitx":true,"timestamp_utc":"%s"}\n' \
          "$(zed_event_field "$peer_record" record_count)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      } > "$LOG/fcitx-peer-authenticated.json"
      printf 'status=matched\nengine=mozc\ntimestamp_utc=%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LOG/fcitx-current-im-after-context.txt"
    fi

    run_without_runner_control_fds sleep 0.25
  done
  printf '{"attempt":%s,"timeout_seconds":%s,"reason":"hard-attempt-cap-reached"}\n' \
    "$MONITOR_MAX_ATTEMPTS" "$MONITOR_TIMEOUT_SECONDS" > "$LOG/fcitx-peer-monitor-timeout.json"
  if [[ "$peer_admitted" == 1 ]]; then
    fcitx_peer_guard_fail "fcitx-peer-monitor-attempt-cap-after-admission" "" "$engine_class"
  else
    printf 'status=timeout-before-authenticated-peer\nengine=\ntimestamp_utc=%s\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LOG/fcitx-current-im-after-context.txt"
    fcitx_peer_guard_fail "authenticated-peer-gate-timeout" "" "$engine_class"
  fi
  return 1
}

cleanup() {
  result=$?
  trap - EXIT INT TERM
  finish_zed_supervisor
  if [[ -n "$FCITX_CONTEXT_MONITOR_PID" && "$FCITX_CONTEXT_MONITOR_WAITED" != 1 ]]; then
    : > "$LOG/fcitx-context-monitor.stop"
    set +e
    wait "$FCITX_CONTEXT_MONITOR_PID"
    FCITX_CONTEXT_MONITOR_STATUS=$?
    set -e
    FCITX_CONTEXT_MONITOR_WAITED=1
    if [[ "$FCITX_CONTEXT_MONITOR_STATUS" != 0 && "$result" == 0 ]]; then
      result=12
    fi
    if [[ -e "$LOG/fcitx-peer-guard-failed.json" && "$result" == 0 ]]; then
      result=12
    fi
  fi
  for child in "$TAIL_PID" "$FCITX_PID"; do
    if [[ -n "$child" ]] && kill -0 "$child" 2>/dev/null; then
      kill -TERM "$child" 2>/dev/null || true
    fi
  done
  for child in "$TAIL_PID" "$FCITX_PID"; do
    if [[ -n "$child" ]]; then
      wait "$child" 2>/dev/null || true
    fi
  done
  if [[ -n "$FCITX_FILTER_PID" ]]; then
    : > "$LOG/fcitx5-peer-filter.stop"
  fi
  if [[ -n "$FCITX_FILTER_WRITE_FD" ]]; then
    exec {FCITX_FILTER_WRITE_FD}>&-
    FCITX_FILTER_WRITE_FD=
  fi
  if [[ -n "$FCITX_FILTER_PID" && "$FCITX_FILTER_WAITED" != 1 ]]; then
    set +e
    wait "$FCITX_FILTER_PID"
    FCITX_FILTER_STATUS=$?
    set -e
    FCITX_FILTER_WAITED=1
    if [[ "$FCITX_FILTER_STATUS" != 0 && "$result" == 0 ]]; then
      result=12
    fi
    if ! run_without_runner_control_fds python3 - "$LOG/fcitx5-peer-filter-status.json" "$LOG/fcitx5-peer-pids.log" <<'PY'
import json, pathlib, sys
try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        status = json.load(stream)
    peers = pathlib.Path(sys.argv[2]).read_bytes().splitlines()
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
if (status.get("status") not in ("clean-eof", "stopped-after-fcitx-reaped")
        or type(status.get("peer_record_count")) is not int
        or not (1 <= status.get("peer_record_count") <= 256)
        or status.get("reason") is not None
        or not peers):
    raise SystemExit(1)
PY
    then
      printf 'fcitx_peer_filter_final_status_invalid\n' > "$LOG/fcitx-peer-filter-final-error.txt"
      if [[ "$result" == 0 ]]; then result=12; fi
    fi
    if ! run_without_runner_control_fds python3 "$PEER_PID_CHECK" \
      "$LOG/fcitx5-peer-pids.log" "$MOZC_PID" "$FCITX_PID" "$FCITX_EMITTER_PID" \
      > "$LOG/fcitx-peer-final-validation.json"; then
      printf 'fcitx_final_peer_records_rejected\n' > "$LOG/fcitx-peer-filter-final-error.txt"
      if [[ "$result" == 0 ]]; then result=12; fi
    fi
    if [[ ! -f "$LOG/fcitx-peer-authenticated.json" ]]; then
      printf 'fcitx_peer_was_never_admitted_during_context_monitoring\n' \
        > "$LOG/fcitx-peer-filter-final-error.txt"
      if [[ "$result" == 0 ]]; then result=12; fi
    fi
  fi
  stop_owned_mozc
  for child in "$LABWC_PID" "$DBUS_PID"; do
    if [[ -n "$child" ]] && kill -0 "$child" 2>/dev/null; then
      kill -TERM "$child" 2>/dev/null || true
    fi
  done
  for child in "$LABWC_PID" "$DBUS_PID"; do
    if [[ -n "$child" ]]; then
      wait "$child" 2>/dev/null || true
    fi
  done
  local mozc_lifecycle_tsv mozc_lifecycle_header mozc_lifecycle_values
  mozc_lifecycle_tsv=$(run_without_mozc_control_fds python3 "$MOZC_OWNER_SUPERVISOR" \
    --summary-tsv "$LOG/mozc-server-started.json" "$LOG/mozc-server-final-record.jsonl" \
    "${MOZC_SUPERVISOR_STATUS:-not-observed}") || mozc_lifecycle_tsv=
  if [[ -n "$mozc_lifecycle_tsv" ]]; then
    printf '%s\n' "$mozc_lifecycle_tsv" > "$LOG/mozc-lifecycle-summary.tsv"
    mozc_lifecycle_header=${mozc_lifecycle_tsv%%$'\n'*}
    mozc_lifecycle_values=${mozc_lifecycle_tsv#*$'\n'}
    if [[ "$mozc_lifecycle_header" == $'child_started\tsupervisor_pid\tsupervisor_parent_pid\tchild_pid\tchild_parent_pid\tchild_start_time\tchild_wait_verified\tchild_returncode\tsupervisor_waited\tsupervisor_exit_status' && "$mozc_lifecycle_values" != *$'\n'* ]]; then
      IFS=$'\t' read -r MOZC_CHILD_STARTED MOZC_SUPERVISOR_PID MOZC_SUPERVISOR_PARENT_PID \
        MOZC_PID MOZC_PARENT_PID MOZC_START_TIME MOZC_CHILD_WAITED MOZC_CHILD_STATUS \
        MOZC_SUPERVISOR_WAITED MOZC_SUPERVISOR_STATUS <<< "$mozc_lifecycle_values"
    else
      printf 'summary_tsv_invalid\n' > "$LOG/mozc-lifecycle-summary.error"
      MOZC_CHILD_STARTED=not-observed
      MOZC_SUPERVISOR_PID=not-observed
      MOZC_SUPERVISOR_PARENT_PID=not-observed
      MOZC_PID=not-observed
      MOZC_PARENT_PID=not-observed
      MOZC_START_TIME=not-observed
      MOZC_CHILD_WAITED=not-observed
      MOZC_CHILD_STATUS=not-observed
      MOZC_SUPERVISOR_WAITED=not-observed
      MOZC_SUPERVISOR_STATUS=not-observed
    fi
  else
    printf 'summary_generation_failed\n' > "$LOG/mozc-lifecycle-summary.error"
    MOZC_CHILD_STARTED=not-observed
    MOZC_SUPERVISOR_PID=not-observed
    MOZC_SUPERVISOR_PARENT_PID=not-observed
    MOZC_PID=not-observed
    MOZC_PARENT_PID=not-observed
    MOZC_START_TIME=not-observed
    MOZC_CHILD_WAITED=not-observed
    MOZC_CHILD_STATUS=not-observed
    MOZC_SUPERVISOR_WAITED=not-observed
    MOZC_SUPERVISOR_STATUS=not-observed
  fi
  printf 'session_exit=%s\n' "$result" > "$LOG/session-result.txt"
  printf 'dbus_pid=%s\nlabwc_pid=%s\nfcitx5_pid=%s\nfcitx_peer_filter_pid=%s\nfcitx_context_monitor_pid=%s\nmozc_supervisor_pid=%s\nmozc_supervisor_parent_pid=%s\nmozc_server_child_pid=%s\nzed_supervisor_pid=%s\nzed_pid=%s\nzed_parent_pid=%s\n' \
    "${DBUS_PID:-not-started}" "${LABWC_PID:-not-started}" \
    "${FCITX_PID:-not-started}" "${FCITX_FILTER_PID:-not-started}" "${FCITX_CONTEXT_MONITOR_PID:-not-started}" "${MOZC_SUPERVISOR_PID:-not-started}" \
    "${MOZC_SUPERVISOR_PARENT_PID:-not-observed}" \
    "${MOZC_PID:-not-started}" "${ZED_SUPERVISOR_PID:-not-started}" \
    "${ZED_PID:-not-started}" "${ZED_PARENT_PID:-not-observed}" \
    > "$LOG/stage-pids.txt"
  printf 'mozc_child_started=%s\nmozc_supervisor_pid=%s\nmozc_supervisor_parent_pid=%s\nmozc_child_starttime=%s\nmozc_child_parent_pid=%s\nmozc_child_wait_verified=%s\nmozc_child_returncode=%s\nmozc_supervisor_waited=%s\nmozc_supervisor_exit_status=%s\n' \
    "$MOZC_CHILD_STARTED" "${MOZC_SUPERVISOR_PID:-not-observed}" \
    "${MOZC_SUPERVISOR_PARENT_PID:-not-observed}" "${MOZC_START_TIME:-not-observed}" \
    "${MOZC_PARENT_PID:-not-observed}" "$MOZC_CHILD_WAITED" \
    "$MOZC_CHILD_STATUS" "$MOZC_SUPERVISOR_WAITED" "$MOZC_SUPERVISOR_STATUS" \
    > "$LOG/mozc-cleanup-result.txt"
  printf 'mozc_child_wait_verified=%s\nmozc_child_status=%s\nmozc_supervisor_wait_status=%s\n' \
    "$MOZC_CHILD_WAITED" "$MOZC_CHILD_STATUS" "$MOZC_SUPERVISOR_STATUS" \
    >> "$LOG/session-result.txt"
  if [[ -f "$LOG/fcitx-current-im-after-context.txt" ]]; then
    cat "$LOG/fcitx-current-im-after-context.txt" >> "$LOG/session-result.txt"
  else
    printf 'fcitx_context_engine=not-observed\n' >> "$LOG/session-result.txt"
  fi
  printf 'fcitx_context_monitor_waited=%s\nfcitx_context_monitor_status=%s\n' \
    "$FCITX_CONTEXT_MONITOR_WAITED" "$FCITX_CONTEXT_MONITOR_STATUS" >> "$LOG/session-result.txt"
  printf 'fcitx_peer_filter_waited=%s\nfcitx_peer_filter_exit_status=%s\n' \
    "$FCITX_FILTER_WAITED" "$FCITX_FILTER_STATUS" >> "$LOG/session-result.txt"
  if [[ -f "$LOG/fcitx-peer-authenticated.json" ]]; then
    printf 'fcitx_authenticated_peer_gate=passed\n' >> "$LOG/session-result.txt"
  else
    printf 'fcitx_authenticated_peer_gate=not-observed\n' >> "$LOG/session-result.txt"
  fi
  if [[ -f "$LOG/fcitx-peer-guard-failed.json" ]]; then
    printf 'fcitx_authenticated_peer_guard=failed\n' >> "$LOG/session-result.txt"
  else
    printf 'fcitx_authenticated_peer_guard=not-failed\n' >> "$LOG/session-result.txt"
  fi
  if [[ "$ZED_EXEC_VERIFIED" == 1 ]]; then
    if [[ "$ZED_WAITED" == 1 ]]; then
      printf '%s\n' "$ZED_STATUS" > "$LOG/zed.exit-code"
      printf 'zed_started=yes\nzed_exit=%s\nzed_supervisor_waited=%s\nzed_supervisor_exit=%s\nzed_owned_child_check_failed=%s\n' \
        "$ZED_STATUS" "$ZED_SUPERVISOR_WAITED" "$ZED_SUPERVISOR_STATUS" "$ZED_OWNED_CHILD_CHECK_FAILED" >> "$LOG/session-result.txt"
    else
      printf 'not-observed\n' > "$LOG/zed.exit-code"
      printf 'zed_started=yes\nzed_exit=not-observed\nzed_supervisor_waited=%s\nzed_supervisor_exit=%s\nzed_owned_child_check_failed=%s\n' \
        "$ZED_SUPERVISOR_WAITED" "$ZED_SUPERVISOR_STATUS" "$ZED_OWNED_CHILD_CHECK_FAILED" >> "$LOG/session-result.txt"
    fi
  elif [[ "$ZED_ATTEMPTED" == 1 ]]; then
    printf 'not-observed\n' > "$LOG/zed.exit-code"
    printf 'zed_started=unverified\nzed_exit=not-observed\nzed_supervisor_waited=%s\nzed_supervisor_exit=%s\nzed_owned_child_check_failed=%s\n' \
      "$ZED_SUPERVISOR_WAITED" "$ZED_SUPERVISOR_STATUS" "$ZED_OWNED_CHILD_CHECK_FAILED" >> "$LOG/session-result.txt"
  else
    printf 'not-started\n' > "$LOG/zed.exit-code"
    printf 'zed_started=no\nzed_exit=not-started\n' >> "$LOG/session-result.txt"
  fi
  exit "$result"
}
trap cleanup EXIT INT TERM

: "${DISPLAY:?managed outer X11 DISPLAY is required}"
[[ -x "$ZED" && -d "$PROJECT" ]] || { echo "verified Zed ELF or empty project missing" >&2; exit 2; }
[[ -x "$PREFIX/usr/bin/labwc" && -x "$PREFIX/usr/bin/fcitx5" ]] || { echo "private labwc/Fcitx5 binaries missing" >&2; exit 2; }
[[ -x "$PREFIX/usr/lib/mozc/mozc_server" && -f "$PREFIX/usr/lib/x86_64-linux-gnu/fcitx5/fcitx5-mozc.so" ]] || { echo "private compiled Mozc targets missing" >&2; exit 2; }
for item in "$PROFILE/xdg-runtime/bus" "$PROFILE/xdg-runtime/$SOCKET" "$PROFILE/xdg-runtime/$SOCKET.lock"; do
  [[ ! -e "$item" && ! -L "$item" ]] || { echo "refusing stale private runtime path: $item" >&2; exit 2; }
done

if [[ "$TEST_MODE" == 1 ]]; then
  printf 'backend=wayland renderer=pixman socket=%s display=%s\n' "$SOCKET" "$DISPLAY" > "$LOG/preflight.txt"
  printf 'xauthority_set=%s\n' "$([[ -n "${XAUTHORITY:-}" ]] && echo yes || echo no)" >> "$LOG/preflight.txt"
  printf 'injected_failure=before_dbus_labwc_fcitx5_mozc_zed\n' > "$LOG/test-mode.txt"
  exit 91
fi

load_and_verify_mozc_build_identity || { echo "private Mozc build identity check failed" >&2; exit 2; }

export XDG_SESSION_TYPE=wayland
export GDK_BACKEND=wayland
export QT_QPA_PLATFORM=wayland
{
  printf 'backend=wayland renderer=pixman socket=%s display=%s\n' "$SOCKET" "$DISPLAY"
  printf 'xauthority_set=%s\n' "$([[ -n "${XAUTHORITY:-}" ]] && echo yes || echo no)"
  printf 'zed_stateless=1 (process-local diagnostic; in-memory databases; single-instance check skipped)\n'
  if [[ -e "$ZED_USER_DATA/zed-stable.sock" || -L "$ZED_USER_DATA/zed-stable.sock" ]]; then
    printf 'preexisting_cli_socket=yes\n'
  else
    printf 'preexisting_cli_socket=no\n'
  fi
  printf 'mozc_server_path=%s\n' "$MOZC_SERVER"
  printf 'fcitx_compiled_mozc_server_path=%s\n' "$FCITX_COMPILED_MOZC_SERVER"
  printf 'mozc_build_identity_sha256=%s\n' "$MOZC_IDENTITY_SHA256"
  printf 'mozc_supervisor_sha256=%s\n' "$MOZC_SUPERVISOR_SHA256"
  printf 'mozc_owner_check_sha256=%s\n' "$MOZC_OWNER_CHECK_SHA256"
  printf 'peer_pid_check_sha256='; sha256sum "$PEER_PID_CHECK" | cut -d' ' -f1
  printf 'peer_pid_log_filter_sha256='; sha256sum "$PEER_PID_LOG_FILTER" | cut -d' ' -f1
  printf 'peer_pid_hook=compile-gated numeric-only SO_PEERCRED peer PID; expected emitter PID must equal getpid\n'
  printf 'owner_check_scope=one known Popen-owned child only; global process completeness not claimed\n'
  printf 'zed_supervisor_sha256=%s\n' "$ZED_SUPERVISOR_SHA256"
  printf 'mozc_source_package=%s\n' "$(identity_value source_package)"
  printf 'mozc_server_sha256='; sha256sum "$PREFIX/usr/lib/mozc/mozc_server" | cut -d' ' -f1
  printf 'fcitx5_mozc_sha256='; sha256sum "$PREFIX/usr/lib/x86_64-linux-gnu/fcitx5/fcitx5-mozc.so" | cut -d' ' -f1
  printf 'zed_sha256='; sha256sum "$ZED" | cut -d' ' -f1
  printf 'socket_path_budget_sha256='; sha256sum "$RUN/socket-path-budget.json" | cut -d' ' -f1
} > "$LOG/preflight.txt"

"$PREFIX/usr/bin/dbus-daemon" --session --address="$DBUS_SESSION_BUS_ADDRESS" --nofork --nopidfile > "$LOG/dbus-daemon.log" 2>&1 &
DBUS_PID=$!
printf 'dbus-daemon\t%s\n' "$DBUS_PID" >> "$LOG/stage-pids.tsv"
for attempt in {1..80}; do
  kill -0 "$DBUS_PID" 2>/dev/null || { echo "private D-Bus session exited" >&2; exit 3; }
  [[ -S "$PROFILE/xdg-runtime/bus" ]] && break
  sleep 0.1
done
[[ -S "$PROFILE/xdg-runtime/bus" ]] || { echo "private D-Bus socket did not appear" >&2; exit 3; }

"$PREFIX/usr/bin/labwc" -d -C "$PROFILE/labwc" > "$LOG/labwc.log" 2>&1 &
LABWC_PID=$!
printf 'labwc\t%s\n' "$LABWC_PID" >> "$LOG/stage-pids.tsv"
for attempt in {1..100}; do
  kill -0 "$LABWC_PID" 2>/dev/null || { echo "private labwc exited" >&2; exit 4; }
  [[ -S "$PROFILE/xdg-runtime/$SOCKET" ]] && break
  sleep 0.1
done
[[ -S "$PROFILE/xdg-runtime/$SOCKET" ]] || { echo "private Wayland socket did not appear" >&2; exit 4; }

python3 "$BUNDLE/scripts/v3-smoke-gate.py" \
  --prefix "$PREFIX" --profile "$PROFILE" --display "$DISPLAY" \
  --wayland-display "$SOCKET" --evidence-dir "$RUN/evidence" \
  > "$LOG/v3-gate-run.json" 2> "$LOG/v3-gate-run.stderr"

start_owned_mozc

start_fcitx_peer_log_filter || { echo "private peer log filter failed to start" >&2; exit 5; }
start_fcitx_peer_test_child "$PREFIX/usr/bin/fcitx5" --replace >&"$FCITX_FILTER_WRITE_FD" 2>&1 &
FCITX_PID=$!
exec {FCITX_FILTER_WRITE_FD}>&-
FCITX_FILTER_WRITE_FD=
printf 'fcitx5\t%s\n' "$FCITX_PID" >> "$LOG/stage-pids.tsv"
FCITX_EMITTER_PID=
FCITX_PARENT_PID=$$
FCITX_REAL_EXE=$(readlink -f "$PREFIX/usr/bin/fcitx5")
[[ "$FCITX_REAL_EXE" == "$PREFIX/"* ]] || { echo "private Fcitx executable resolved outside its prefix" >&2; exit 5; }
for attempt in {1..100}; do
  if [[ -f "$LOG/fcitx5-emitter-pid.txt" ]]; then
    FCITX_EMITTER_PID=$(run_without_runner_control_fds cat "$LOG/fcitx5-emitter-pid.txt") || FCITX_EMITTER_PID=
    if [[ "$FCITX_EMITTER_PID" == "$FCITX_PID" ]]; then
      FCITX_START_TIME=$(capture_fcitx_child_start_time) || FCITX_START_TIME=
      [[ "$FCITX_START_TIME" =~ ^[1-9][0-9]*$ ]] && break
    fi
  fi
  kill -0 "$FCITX_PID" 2>/dev/null || { echo "private Fcitx5 exited before emitter identity was recorded" >&2; exit 5; }
  sleep 0.01
done
[[ "$FCITX_EMITTER_PID" == "$FCITX_PID" && "$FCITX_START_TIME" =~ ^[1-9][0-9]*$ ]] || { echo "Fcitx exec PID or retained-process identity check failed" >&2; exit 5; }
printf 'fcitx5_emitter_pid_matches_child=yes\n' > "$LOG/fcitx5-emitter-identity.txt"
printf 'pid=%s\nparent_pid=%s\nstart_time=%s\nuid=%s\nexe=%s\n' \
  "$FCITX_PID" "$FCITX_PARENT_PID" "$FCITX_START_TIME" "$(id -u)" "$FCITX_REAL_EXE" \
  > "$LOG/fcitx5-process-started.txt"
FCITX_DEFAULT_SELECTED=0
for attempt in {1..100}; do
  kill -0 "$FCITX_PID" 2>/dev/null || { echo "private Fcitx5 exited" >&2; exit 5; }
  if run_without_mozc_control_fds "$PREFIX/usr/bin/fcitx5-remote" -s mozc >/dev/null 2>&1; then
    FCITX_DEFAULT_SELECTED=1
    break
  fi
  sleep 0.1
done
[[ "$FCITX_DEFAULT_SELECTED" == 1 ]] || { echo "Fcitx5 did not accept the pinned Mozc default" >&2; exit 5; }
fcitx_record_default_before_context "$PREFIX/usr/bin/fcitx5-remote" "$LOG/fcitx-default-before-context.txt"
# Recheck only the known Popen-owned child; no global process snapshot is read.
verify_owned_mozc_startup || { echo "owned Mozc process admission failed after Fcitx selection" >&2; exit 5; }
printf 'fcitx5_pid=%s\nlabwc_pid=%s\ndbus_pid=%s\n' "$FCITX_PID" "$LABWC_PID" "$DBUS_PID" > "$LOG/session-pids.txt"

start_owned_zed || { echo "owned Zed supervisor failed to verify the pinned executable" >&2; exit 7; }
monitor_fcitx_context &
FCITX_CONTEXT_MONITOR_PID=$!
printf 'fcitx_context_monitor\t%s\n' "$FCITX_CONTEXT_MONITOR_PID" >> "$LOG/stage-pids.tsv"
run_without_runner_control_fds tail -n +1 -f "$LOG/zed.log" &
TAIL_PID=$!
printf 'zed_log_tail\t%s\n' "$TAIL_PID" >> "$LOG/stage-pids.tsv"
set +e
while read_zed_event; do
  record_zed_event "$ZED_EVENT_RECORD"
  zed_event=$(zed_event_field "$ZED_EVENT_RECORD" event) || zed_event=invalid
  case "$zed_event" in
    owned_child_identity_failed)
      ZED_OWNED_CHILD_CHECK_FAILED=1
      printf '%s\n' "$ZED_EVENT_RECORD" > "$LOG/zed-owned-child-stop-reason.json"
      ;;
    error)
      printf '%s\n' "$ZED_EVENT_RECORD" > "$LOG/zed-supervisor-error.json"
      ;;
    reaped)
      consume_zed_reaped_event "$ZED_EVENT_RECORD" || ZED_STATUS=unverified-reaped-event
      break
      ;;
  esac
done
set -e
if [[ "$ZED_WAITED" != 1 ]]; then
  ZED_STATUS=missing-reaped-event
fi
finish_zed_supervisor
if kill -0 "$TAIL_PID" 2>/dev/null; then
  kill -TERM "$TAIL_PID" 2>/dev/null || true
  wait "$TAIL_PID" 2>/dev/null || true
  TAIL_PID=
fi
if [[ "$ZED_OWNED_CHILD_CHECK_FAILED" == 1 ]]; then
  exit 12
fi
if [[ -e "$LOG/fcitx-peer-guard-failed.json" || ! -f "$LOG/fcitx-peer-authenticated.json" ]]; then
  exit 12
fi
if [[ "$ZED_STATUS" =~ ^-[0-9]+$ ]]; then
  exit $((128 - ZED_STATUS))
elif [[ "$ZED_STATUS" =~ ^[0-9]+$ ]]; then
  exit "$ZED_STATUS"
fi
exit 7
