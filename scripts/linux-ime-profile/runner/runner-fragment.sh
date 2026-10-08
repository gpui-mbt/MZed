# Shared implementation fragment sourced by run-session-inner.sh.

MOZC_SERVER="$PREFIX/usr/lib/mozc/mozc_server"
MOZC_BUILD_IDENTITY="$RUNNER_CANDIDATE_DIR/build-identity.json"
MOZC_IDENTITY_SHA256="${MOZC_IDENTITY_SHA256:-}"
MOZC_OWNER_SUPERVISOR="$RUNNER_CANDIDATE_DIR/mozc_supervisor.py"
MOZC_OWNER_CHECK="$RUNNER_CANDIDATE_DIR/check_mozc_owner.py"
MOZC_SUPERVISOR_SHA256="d26f4a9d03ca699faad02e7aec4ac2e54bc9793f708d694b585aa2c604a961d6"
MOZC_OWNER_CHECK_SHA256="b9e37ac9adac6e9548f0eed2fcf83088bd71b3dd46b13086ad71c9bd53483293"
MOZC_SUPERVISOR_PID=
MOZC_SUPERVISOR_PARENT_PID=
MOZC_SUPERVISOR_IN_FD=
MOZC_SUPERVISOR_OUT_FD=
MOZC_PID=
MOZC_PARENT_PID=
MOZC_START_TIME=
MOZC_CHILD_STARTED=0
MOZC_CHILD_WAITED=0
MOZC_SUPERVISOR_WAITED=0
MOZC_CHILD_STATUS=not-observed
MOZC_SUPERVISOR_STATUS=not-observed
FCITX_COMPILED_MOZC_SERVER=
PEER_PID_CHECK="$RUNNER_CANDIDATE_DIR/peer_pid_check.py"
PEER_PID_CHECK_SHA256="bb9f11c3da3be1ed953c0475669f83930249bd6780a3d2f9521717a01cf9e99f"

run_without_mozc_control_fds() (
  if [[ -n "$MOZC_SUPERVISOR_IN_FD" ]]; then
    exec {MOZC_SUPERVISOR_IN_FD}>&-
  fi
  if [[ -n "$MOZC_SUPERVISOR_OUT_FD" ]]; then
    exec {MOZC_SUPERVISOR_OUT_FD}<&-
  fi
  if [[ -n "${ZED_SUPERVISOR_IN_FD:-}" ]]; then
    exec {ZED_SUPERVISOR_IN_FD}>&-
  fi
  if [[ -n "${ZED_SUPERVISOR_OUT_FD:-}" ]]; then
    exec {ZED_SUPERVISOR_OUT_FD}<&-
  fi
  exec "$@"
)

sha256_of_file() {
  local result
  result=$(run_without_mozc_control_fds sha256sum "$1") || return 1
  printf '%s' "${result%% *}"
}

identity_value() {
  run_without_mozc_control_fds python3 - "$MOZC_BUILD_IDENTITY" "$1" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print(json.load(stream)[sys.argv[2]])
PY
}

load_and_verify_mozc_build_identity() {
  local gyp_receipt gyp_sha server_sha plugin_path plugin_sha
  [[ "$(sha256_of_file "$MOZC_BUILD_IDENTITY")" == "$MOZC_IDENTITY_SHA256" ]] || return 1
  [[ "$(identity_value server_and_fcitx_share_base_core)" == True ]] || return 1
  [[ "$(identity_value test_only_authenticated_peer_pid_hook_enabled)" == True ]] || return 1
  [[ "$(identity_value peer_pid_emitter_environment)" == "MOZC_TEST_FCITX_EMITTER_PID" ]] || return 1
  [[ "$(identity_value peer_pid_log_record)" == "MOZC_TEST_AUTHENTICATED_UNIX_PEER_PID=<numeric-pid>" ]] || return 1
  gyp_receipt=$(identity_value gyp_configure_receipt) || return 1
  if [[ "$gyp_receipt" != /* ]]; then
    gyp_receipt="$RUNNER_CANDIDATE_DIR/$gyp_receipt"
  fi
  gyp_sha=$(identity_value gyp_configure_receipt_sha256) || return 1
  [[ -f "$gyp_receipt" && ! -L "$gyp_receipt" ]] || return 1
  [[ "$(sha256_of_file "$gyp_receipt")" == "$gyp_sha" ]] || return 1
  [[ "$(identity_value configured_server_directory)" == "$PREFIX/usr/lib/mozc" ]] || return 1
  run_without_mozc_control_fds python3 - "$MOZC_BUILD_IDENTITY" "$gyp_receipt" "$PREFIX" <<'PY' || return 1
import hashlib
import json
import pathlib
import re
import sys
identity = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
gyp = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
prefix = pathlib.Path(sys.argv[3])
expected_server_dir = str(prefix / "usr/lib/mozc")
if identity.get("configured_server_directory") != expected_server_dir:
    raise SystemExit(1)
if gyp.get("server_dir_absolute") != expected_server_dir or gyp.get("server_dir_relative") != "usr/lib/mozc":
    raise SystemExit(1)
expected_graphs = {"build.ninja", "obj/base/base_core.ninja", "obj/ipc/ipc.ninja"}
graphs = gyp.get("build_graph_files_sha256")
if not isinstance(graphs, dict) or set(graphs) != expected_graphs:
    raise SystemExit(1)
if any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in graphs.values()):
    raise SystemExit(1)
if re.fullmatch(r"[0-9a-f]{64}", str(gyp.get("target_commands_sha256", ""))) is None:
    raise SystemExit(1)
expected_configs = {
    "usr/share/fcitx5/addon/mozc.conf",
    "usr/share/fcitx5/inputmethod/mozc.conf",
}
configs = identity.get("fcitx5_config_files_sha256")
if not isinstance(configs, dict) or set(configs) != expected_configs:
    raise SystemExit(1)
for relative, digest in configs.items():
    path = pathlib.PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or re.fullmatch(r"[0-9a-f]{64}", str(digest)) is None:
        raise SystemExit(1)
    item = prefix.joinpath(*path.parts)
    if not item.is_file() or item.is_symlink() or hashlib.sha256(item.read_bytes()).hexdigest() != digest:
        raise SystemExit(1)
PY
  FCITX_COMPILED_MOZC_SERVER=$(identity_value fcitx_compiled_server_path) || return 1
  if [[ "$FCITX_COMPILED_MOZC_SERVER" != /* ]]; then
    FCITX_COMPILED_MOZC_SERVER="$PREFIX/$FCITX_COMPILED_MOZC_SERVER"
  fi
  [[ "$FCITX_COMPILED_MOZC_SERVER" == "$MOZC_SERVER" ]] || return 1
  server_sha=$(identity_value server_sha256) || return 1
  [[ -f "$MOZC_SERVER" && ! -L "$MOZC_SERVER" && -x "$MOZC_SERVER" ]] || return 1
  [[ "$(sha256_of_file "$MOZC_SERVER")" == "$server_sha" ]] || return 1
  plugin_path=$(identity_value fcitx5_mozc_path) || return 1
  if [[ "$plugin_path" != /* ]]; then
    plugin_path="$PREFIX/$plugin_path"
  fi
  plugin_sha=$(identity_value fcitx5_mozc_sha256) || return 1
  [[ -f "$plugin_path" && ! -L "$plugin_path" ]] || return 1
  [[ "$(sha256_of_file "$plugin_path")" == "$plugin_sha" ]] || return 1
  [[ -f "$PEER_PID_CHECK" && ! -L "$PEER_PID_CHECK" ]] || return 1
  [[ "$(sha256_of_file "$PEER_PID_CHECK")" == "$PEER_PID_CHECK_SHA256" ]] || return 1
}

verify_owned_mozc() {
  local evidence_path="${1:-$LOG/mozc-owned-child-checks-startup.jsonl}"
  [[ "$MOZC_CHILD_STARTED" == 1 && -n "$MOZC_PID" ]] || return 1
  run_without_mozc_control_fds python3 "$MOZC_OWNER_CHECK" \
    "$MOZC_PID" "$MOZC_START_TIME" "$MOZC_PARENT_PID" \
    "$MOZC_SERVER" "$FCITX_COMPILED_MOZC_SERVER" \
    >> "$evidence_path"
}

verify_owned_mozc_startup() {
  local output record
  output="$LOG/mozc-owned-child-checks-startup.jsonl"
  : > "$output"
  verify_owned_mozc "$output" || return 1
  record=$(run_without_mozc_control_fds tail -n 1 "$output") || return 1
  run_without_mozc_control_fds python3 - "$record" <<'PY' || return 1
import json
import sys
try:
    report = json.loads(sys.argv[1])
except json.JSONDecodeError:
    raise SystemExit(1)
if not (
    report.get("admitted") is True
    and report.get("owned_child_identity_verified") is True
    and report.get("verification_scope") == "single-known-owned-child"
    and report.get("global_process_completeness_claimed") is False
    and report.get("reason") == "owned-child-identity-matches"
):
    raise SystemExit(1)
PY
  printf '%s\n' "$record" > "$LOG/mozc-owned-child-startup.json"
}

start_owned_mozc() {
  [[ -f "$MOZC_SERVER" && ! -L "$MOZC_SERVER" && -x "$MOZC_SERVER" ]] || return 1
  [[ "$HOME" == "$PROFILE/home" ]] || return 1
  [[ "$XDG_CONFIG_HOME" == "$PROFILE/xdg-config" ]] || return 1
  load_and_verify_mozc_build_identity || return 1
  # A fresh private profile is required; do not clear stale profile data.
  [[ ! -e "$HOME/.mozc" ]] || return 1
  if [[ -e "$XDG_CONFIG_HOME/mozc" ]]; then
    [[ -d "$XDG_CONFIG_HOME/mozc" && ! -L "$XDG_CONFIG_HOME/mozc" ]] || return 1
    [[ -z "$(find "$XDG_CONFIG_HOME/mozc" -mindepth 1 -maxdepth 1 -print -quit)" ]] || return 1
  fi

  # Use a distinct coproc name: Bash owns <NAME>_PID and clears it after wait.
  # MOZC_SUPERVISOR_PID is an ordinary retained variable, so the channel name
  # must not be MOZC_SUPERVISOR.
  coproc MOZC_OWNER_PIPE {
    exec python3 "$MOZC_OWNER_SUPERVISOR" "$MOZC_SERVER" "$LOG/mozc-server.log"
  }
  MOZC_SUPERVISOR_PID=$!
  local original_supervisor_in_fd="${MOZC_OWNER_PIPE[1]}"
  local original_supervisor_out_fd="${MOZC_OWNER_PIPE[0]}"
  # Bash may close the original coproc descriptors when the child exits.
  # Keep private duplicates so the final reaped line remains readable after wait.
  exec {MOZC_SUPERVISOR_IN_FD}>&"$original_supervisor_in_fd"
  exec {MOZC_SUPERVISOR_OUT_FD}<&"$original_supervisor_out_fd"
  exec {original_supervisor_in_fd}>&-
  exec {original_supervisor_out_fd}<&-
  local start_record event
  IFS= read -r start_record <&"$MOZC_SUPERVISOR_OUT_FD" || return 1
  event=$(run_without_mozc_control_fds python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("event", ""))' "$start_record") || return 1
  [[ "$event" == started ]] || return 1
  MOZC_PID=$(run_without_mozc_control_fds python3 -c 'import json,sys; print(json.loads(sys.argv[1])["pid"])' "$start_record") || return 1
  MOZC_PARENT_PID=$(run_without_mozc_control_fds python3 -c 'import json,sys; print(json.loads(sys.argv[1])["parent_pid"])' "$start_record") || return 1
  MOZC_START_TIME=$(run_without_mozc_control_fds python3 -c 'import json,sys; print(json.loads(sys.argv[1])["start_time"])' "$start_record") || return 1
  local reported_supervisor_pid reported_supervisor_parent_pid
  reported_supervisor_pid=$(run_without_mozc_control_fds python3 -c 'import json,sys; print(json.loads(sys.argv[1])["supervisor_pid"])' "$start_record") || return 1
  reported_supervisor_parent_pid=$(run_without_mozc_control_fds python3 -c 'import json,sys; print(json.loads(sys.argv[1])["supervisor_parent_pid"])' "$start_record") || return 1
  [[ "$MOZC_SUPERVISOR_PID" == "$reported_supervisor_pid" && "$reported_supervisor_parent_pid" == "$$" && "$MOZC_PARENT_PID" == "$reported_supervisor_pid" ]] || return 1
  MOZC_SUPERVISOR_PARENT_PID=$reported_supervisor_parent_pid
  MOZC_CHILD_STARTED=1
  printf 'mozc_server_supervisor\t%s\nmozc_server_child\t%s\n' "$MOZC_SUPERVISOR_PID" "$MOZC_PID" >> "$LOG/stage-pids.tsv"
  printf '%s\n' "$start_record" > "$LOG/mozc-server-started.json"
  verify_owned_mozc_startup
}

stop_owned_mozc() {
  [[ -n "$MOZC_SUPERVISOR_PID" && "$MOZC_SUPERVISOR_WAITED" != 1 ]] || return 0
  # Stop through the supervisor's private pipe; never signal a remembered raw PID.
  printf 'stop\n' >&"$MOZC_SUPERVISOR_IN_FD" 2>/dev/null || true
  local final_record reaped_status
  if IFS= read -r final_record <&"$MOZC_SUPERVISOR_OUT_FD"; then
    printf '%s\n' "$final_record" > "$LOG/mozc-server-final-record.jsonl"
    if reaped_status=$(run_without_mozc_control_fds python3 - "$MOZC_OWNER_SUPERVISOR" "$final_record" "$MOZC_PID" <<'PY'
import sys
sys.path.insert(0, __import__("os").path.dirname(sys.argv[1]))
from mozc_supervisor import verified_reaped_status

status = verified_reaped_status(sys.argv[2], int(sys.argv[3]))
if status is None:
    raise SystemExit(1)
print(status)
PY
    ); then
      MOZC_CHILD_STATUS=$reaped_status
      MOZC_CHILD_WAITED=1
    else
      MOZC_CHILD_STATUS=unverified-reaped-event
    fi
  else
    printf '{"event":"missing"}\n' > "$LOG/mozc-server-final-record.jsonl"
    MOZC_CHILD_STATUS=missing-reaped-event
  fi
  set +e
  wait "$MOZC_SUPERVISOR_PID"
  MOZC_SUPERVISOR_STATUS=$?
  set -e
  MOZC_SUPERVISOR_WAITED=1
  if [[ -n "$MOZC_SUPERVISOR_IN_FD" ]]; then
    exec {MOZC_SUPERVISOR_IN_FD}>&-
    MOZC_SUPERVISOR_IN_FD=
  fi
  if [[ -n "$MOZC_SUPERVISOR_OUT_FD" ]]; then
    exec {MOZC_SUPERVISOR_OUT_FD}<&-
    MOZC_SUPERVISOR_OUT_FD=
  fi
}

# This candidate checks only the one Popen-owned child by its retained PID,
# parent, start time, UID and executable. It does not enumerate /proc or claim
# that no other server process exists. The live Fcitx connection is separately
# admitted by matching its compile-gated SO_PEERCRED peer-PID record to MOZC_PID.
# EXIT cleanup still stops/waits for Fcitx before stopping/reaping the child.
