#!/usr/bin/env bash
set -euo pipefail
if [[ -e _build/smoke || -e _build/openbox.log || -e _build/xcompmgr.log ]]; then
  echo 'Smoke/session evidence already exists; preserve it and use a fresh attempt directory' >&2
  exit 1
fi
mkdir -p _build
openbox --sm-disable >_build/openbox.log 2>&1 &
wm_pid=$!
xcompmgr -a >_build/xcompmgr.log 2>&1 &
compositor_pid=$!
trap 'kill "$wm_pid" "$compositor_pid" 2>/dev/null || true' EXIT
ready=false
for attempt in {1..100}; do
  if xprop -root _NET_SUPPORTING_WM_CHECK | grep -q 'window id'; then
    ready=true
    break
  fi
  sleep 0.1
done
if [[ "$ready" != true ]]; then
  echo 'X11 window manager did not advertise readiness' >&2
  exit 1
fi
python3 scripts/smoke_x11.py \
  --binary _build/zed/target/mzed-baseline/debug/zed \
  --source _build/zed --output _build/smoke
