#!/usr/bin/env bash
set -euo pipefail
if [[ -e _build/island-smoke-${GPUI_X11_SCALE_FACTOR} || -e _build/island-openbox-${GPUI_X11_SCALE_FACTOR}.log || -e _build/island-xcompmgr-${GPUI_X11_SCALE_FACTOR}.log ]]; then
  echo 'Smoke/session evidence already exists; preserve it and use a fresh attempt directory' >&2
  exit 1
fi
mkdir -p _build
openbox --sm-disable >_build/island-openbox-${GPUI_X11_SCALE_FACTOR}.log 2>&1 &
wm_pid=$!
xcompmgr -a >_build/island-xcompmgr-${GPUI_X11_SCALE_FACTOR}.log 2>&1 &
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
python3 scripts/smoke_island.py \
  --binary _build/zed-island/target/mzed-island/debug/zed \
  --source _build/zed-island --output _build/island-smoke-${GPUI_X11_SCALE_FACTOR}
