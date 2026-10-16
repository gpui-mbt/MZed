#!/usr/bin/env bash
set -Eeuo pipefail

WORK=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
TEMP=$(mktemp -d)
trap 'rm -rf -- "$TEMP"' EXIT

# Exercise the exact two runtime functions without starting Fcitx or a desktop.
sed -n \
  '/^fcitx_engine_class() {/,/^}/p' \
  "$WORK/run-session-inner.sh" > "$TEMP/functions.sh"
sed -n \
  '/^fcitx_record_default_before_context() {/,/^}/p' \
  "$WORK/run-session-inner.sh" >> "$TEMP/functions.sh"
sed -n \
  '/^fcitx_select_active_context_mozc() {/,/^}/p' \
  "$WORK/run-session-inner.sh" >> "$TEMP/functions.sh"
source "$TEMP/functions.sh"

run_without_runner_control_fds() {
  "$@"
}

mkdir -p "$TEMP/bin"
cat > "$TEMP/bin/fcitx5-remote" <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\n' "$*" >> "$MOCK_CALLS"
case "${1:-}" in
  -s)
    [[ "${2:-}" == mozc ]] || exit 2
    exit "${MOCK_SELECT_STATUS:-0}"
    ;;
  -n)
    [[ "${MOCK_READ_STATUS:-0}" == 0 ]] || exit "$MOCK_READ_STATUS"
    cat -- "$MOCK_ENGINE_FILE"
    ;;
  *) exit 2 ;;
esac
SH
chmod 700 "$TEMP/bin/fcitx5-remote"

export MOCK_CALLS="$TEMP/calls"
export MOCK_ENGINE_FILE="$TEMP/current-engine"
REMOTE="$TEMP/bin/fcitx5-remote"
RECEIPT="$TEMP/selection.txt"

run_case() {
  local expected_status="$1" expected_class="$2" engine="$3" select_status="$4" read_status="$5"
  : > "$MOCK_CALLS"
  printf '%s' "$engine" > "$MOCK_ENGINE_FILE"
  MOCK_SELECT_STATUS="$select_status" MOCK_READ_STATUS="$read_status" \
    fcitx_select_active_context_mozc "$REMOTE" "$RECEIPT" keyboard-us > "$TEMP/class" && status=0 || status=$?
  [[ "$status" == "$expected_status" ]]
  [[ "$(<"$TEMP/class")" == "$expected_class" ]]
  [[ "$(sed -n 's/^selection_request=//p' "$RECEIPT")" == \
     "$([[ "$select_status" == 0 ]] && printf accepted || printf failed)" ]]
  [[ "$(sed -n 's/^engine_class_after=//p' "$RECEIPT")" == "$expected_class" ]]
  if [[ "$expected_status" == 0 ]]; then
    [[ "$(sed -n 's/^readback_confirmed=//p' "$RECEIPT")" == yes ]]
  else
    [[ "$(sed -n 's/^readback_confirmed=//p' "$RECEIPT")" == no ]]
  fi
}

run_case 0 mozc mozc 0 0
[[ "$(<"$MOCK_CALLS")" == $'-s mozc\n-n' ]]

run_case 1 keyboard-us keyboard-us 1 0
run_case 1 keyboard-us keyboard-us 0 0
run_case 1 mozc mozc 1 0
run_case 1 empty ignored 0 1
run_case 1 other private-unrecognized-engine-name 0 0
! grep -Eq 'private-unrecognized-engine-name|ignored' "$RECEIPT"

for value in empty mozc keyboard-us private-unrecognized-engine-name; do
  : > "$MOCK_CALLS"
  if [[ "$value" == empty ]]; then
    : > "$MOCK_ENGINE_FILE"
  else
    printf '%s' "$value" > "$MOCK_ENGINE_FILE"
  fi
  fcitx_record_default_before_context "$REMOTE" "$RECEIPT"
  case "$value" in
    empty) expected_class=empty ;;
    mozc) expected_class=mozc ;;
    keyboard-us) expected_class=keyboard-us ;;
    *) expected_class=other ;;
  esac
  [[ "$(sed -n 's/^engine_class_before=//p' "$RECEIPT")" == "$expected_class" ]]
  [[ "$(<"$MOCK_CALLS")" == '-n' ]]
done
! grep -q 'private-unrecognized-engine-name' "$RECEIPT"

[[ "$(fcitx_engine_class '')" == empty ]]
[[ "$(fcitx_engine_class mozc)" == mozc ]]
[[ "$(fcitx_engine_class keyboard-us)" == keyboard-us ]]
[[ "$(fcitx_engine_class arbitrary)" == other ]]
printf 'active context Mozc request/readback and redaction: PASS (10 cases)\n'
