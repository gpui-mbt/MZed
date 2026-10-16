#!/usr/bin/env bash
set -euo pipefail

# Exercise the Bash pipe-lifetime behavior with a dummy supervisor, not Mozc.
# Bash unsets the auto-generated <COPROC_NAME>_PID variable on wait. Do not use
# MOZC_SUPERVISOR as the coproc name when MOZC_SUPERVISOR_PID is our retained
# PID variable; a distinct channel name keeps the explicit PID after wait.
coproc MOZC_SUPERVISOR { exit 0; }
collision_pid=$MOZC_SUPERVISOR_PID
wait "$collision_pid"
[[ ! ${MOZC_SUPERVISOR_PID+x} ]]
coproc MOZC_OWNER_PIPE { exit 0; }
MOZC_SUPERVISOR_PID=$!
retained_supervisor_pid=$MOZC_SUPERVISOR_PID
wait "$retained_supervisor_pid"
[[ "$MOZC_SUPERVISOR_PID" == "$retained_supervisor_pid" ]]
printf 'named coproc PID collision reproduced; distinct channel name retains explicit PID\n'

# The owned Zed supervisor must use a distinct coproc name too. Its EXIT trap
# calls finish_zed_supervisor after the coprocess may already have exited.
(
  set -euo pipefail
  ZED_SUPERVISOR_PID=
  ZED_SUPERVISOR_WAITED=0
  coproc ZED_SUPERVISOR_PIPE { exit 0; }
  ZED_SUPERVISOR_PID=$!
  retained_zed_supervisor_pid=$ZED_SUPERVISOR_PID
  trap '[[ -n "$ZED_SUPERVISOR_PID" && "$ZED_SUPERVISOR_WAITED" != 1 && "$ZED_SUPERVISOR_PID" == "$retained_zed_supervisor_pid" ]]' EXIT
  wait "$ZED_SUPERVISOR_PID" || true
)
printf 'Zed supervisor PID remains available to EXIT cleanup after coprocess exit\n'

runner_pid=$$
coproc TEST_SUPERVISOR {
  exec python3 -u -c 'import json,os,sys; print(json.dumps({"event":"started","pid":os.getpid(),"supervisor_parent_pid":os.getppid()}),flush=True); command=sys.stdin.readline().strip(); assert command=="stop"; print(json.dumps({"event":"reaped","pid":os.getpid(),"wait_completed":True}),flush=True)'
}
supervisor_pid=$!
exec {supervisor_in_fd}>&"${TEST_SUPERVISOR[1]}"
exec {supervisor_out_fd}<&"${TEST_SUPERVISOR[0]}"
original_supervisor_in_fd=${TEST_SUPERVISOR[1]}
original_supervisor_out_fd=${TEST_SUPERVISOR[0]}
exec {original_supervisor_in_fd}>&-
exec {original_supervisor_out_fd}<&-

IFS= read -r started_record <&"$supervisor_out_fd"
python_pid=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["pid"])' "$started_record")
python_parent=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["supervisor_parent_pid"])' "$started_record")
[[ "$python_pid" == "$supervisor_pid" ]]
[[ "$python_parent" == "$runner_pid" ]]
in_target=$(readlink "/proc/$$/fd/$supervisor_in_fd")
out_target=$(readlink "/proc/$$/fd/$supervisor_out_fd")
run_without_mozc_control_fds() (
  exec {supervisor_in_fd}>&-
  exec {supervisor_out_fd}<&-
  exec "$@"
)
run_without_mozc_control_fds python3 - "$in_target" "$out_target" <<'PY'
import os, sys
targets = set(sys.argv[1:])
for fd in os.listdir('/proc/self/fd'):
    try:
        target = os.readlink(f'/proc/self/fd/{fd}')
    except OSError:
        continue
    if target in targets:
        raise SystemExit('coproc control descriptor leaked into child process')
print('coproc control descriptors were closed before child exec')
PY
printf 'stop\n' >&"$supervisor_in_fd"
wait "$supervisor_pid" || true
IFS= read -r final_record <&"$supervisor_out_fd"
[[ "$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["event"])' "$final_record")" == reaped ]]
[[ "$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["wait_completed"])' "$final_record")" == True ]]

exec {supervisor_in_fd}>&-
exec {supervisor_out_fd}<&-
printf 'coproc final record remained readable after wait\n'

# Exercise the actual pidfd Zed supervisor through the launcher's final
# `exec python3` coproc form with only a harmless Python child.
work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT
cat > "$work_dir/fake-owner-check.py" <<'PY'
import json
print(json.dumps({"admitted": True, "reason": "dummy-test"}))
PY
expected_exe=$(readlink -f "$(command -v python3)")
supervisor_script="$(dirname "$0")/zed_supervisor.py"
cat > "$work_dir/dummy-zed-child.py" <<'PYCHILD'
import json
import os
from pathlib import Path
import sys
import time

def pipe_targets(pid):
    result = set()
    for name in os.listdir(f"/proc/{pid}/fd"):
        try:
            target = os.readlink(f"/proc/{pid}/fd/{name}")
        except OSError:
            continue
        if target.startswith("pipe:["):
            result.add(target)
    return result

record = {
    "stdin": os.readlink("/proc/self/fd/0"),
    "shared_pipe_targets": sorted(pipe_targets(os.getppid()) & pipe_targets("self")),
}
Path(sys.argv[1]).write_text(json.dumps(record))
time.sleep(30)
PYCHILD
coproc OWNED_ZED_SUPERVISOR {
  exec python3 "$supervisor_script" \
    --expected-exe "$expected_exe" \
    --child-log "$work_dir/child.log" \
    --event-log "$work_dir/events.jsonl" \
    --guard-log "$work_dir/guard.jsonl" \
    --owner-check "$work_dir/fake-owner-check.py" \
    --owner-pid 321 --owner-start-time 987654 --owner-parent-pid 123 \
    --server-path /private/test/mozc_server \
    --client-path /private/test/mozc_server \
    -- python3 "$work_dir/dummy-zed-child.py" "$work_dir/child-fds.json"
}
zed_supervisor_pid=$!
original_zed_in_fd=${OWNED_ZED_SUPERVISOR[1]}
original_zed_out_fd=${OWNED_ZED_SUPERVISOR[0]}
exec {zed_supervisor_in_fd}>&"$original_zed_in_fd"
exec {zed_supervisor_out_fd}<&"$original_zed_out_fd"
exec {original_zed_in_fd}>&-
exec {original_zed_out_fd}<&-
IFS= read -r started_record <&"$zed_supervisor_out_fd"
python3 - "$started_record" "$zed_supervisor_pid" "$$" <<'PY'
import json,sys
r=json.loads(sys.argv[1])
assert r['event']=='started'
assert r['supervisor_pid']==int(sys.argv[2])
assert r['supervisor_parent_pid']==int(sys.argv[3])
assert r['parent_pid']==int(sys.argv[2])
assert r['pidfd_open'] is True
PY
IFS= read -r exec_record <&"$zed_supervisor_out_fd"
python3 - "$exec_record" "$expected_exe" <<'PY'
import json,sys
r=json.loads(sys.argv[1])
assert r['event']=='exec_verified'
assert r['proc_exe']==sys.argv[2]
PY
for attempt in {1..50}; do [[ -f "$work_dir/child-fds.json" ]] && break; sleep 0.02; done
python3 - "$work_dir/child-fds.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
assert r['stdin']=='/dev/null'
assert r['shared_pipe_targets']==[]
PY
printf 'stop\n' >&"$zed_supervisor_in_fd"
wait "$zed_supervisor_pid" || true
IFS= read -r reaped_record <&"$zed_supervisor_out_fd"
python3 - "$reaped_record" <<'PY'
import json,sys
r=json.loads(sys.argv[1])
assert r['event']=='reaped'
assert r['wait_completed'] is True
assert r['pidfd_used'] is True
assert r['reason']=='runner-stop'
PY
exec {zed_supervisor_in_fd}>&-
exec {zed_supervisor_out_fd}<&-
printf 'owned Zed supervisor coproc PID/PPID and pidfd reaped event verified\n'
