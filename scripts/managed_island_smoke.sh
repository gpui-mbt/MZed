#!/usr/bin/env bash
set -euo pipefail
scenario=${1:-normal}
case "$scenario" in
  normal)
    evidence="_build/island-smoke-${GPUI_X11_SCALE_FACTOR}"
    wm_log="_build/island-openbox-${GPUI_X11_SCALE_FACTOR}.log"
    compositor_log="_build/island-xcompmgr-${GPUI_X11_SCALE_FACTOR}.log"
    ;;
  dispatch-rejection)
    evidence="_build/island-smoke-dispatch-rejection-${GPUI_X11_SCALE_FACTOR}"
    wm_log="_build/island-openbox-dispatch-rejection-${GPUI_X11_SCALE_FACTOR}.log"
    compositor_log="_build/island-xcompmgr-dispatch-rejection-${GPUI_X11_SCALE_FACTOR}.log"
    ;;
  *)
    echo "Unsupported smoke scenario: $scenario" >&2
    exit 2
    ;;
esac
if [[ -e "$evidence" || -e "$wm_log" || -e "$compositor_log" ]]; then
  echo 'Smoke/session evidence already exists; preserve it and use a fresh attempt directory' >&2
  exit 1
fi
mkdir -p _build
openbox --sm-disable >"$wm_log" 2>&1 &
wm_pid=$!
xcompmgr -a >"$compositor_log" 2>&1 &
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
  --source _build/zed-island --output "$evidence" --scenario "$scenario"
