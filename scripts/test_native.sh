#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$(realpath "$1")"
mode="${2:-normal}"
flags=()
case "$mode" in
 normal) ;;
 ubsan) flags+=(-C link-arg=-lubsan) ;;
 asan_ubsan) flags+=(-C link-arg=-lasan -C link-arg=-lubsan) ;;
 *) exit 2 ;;
esac
rustc --edition 2024 -D warnings --test "$root/native/protocol.rs" -L "native=$out" -l static=mzed_native "${flags[@]}" -o "$out/protocol-tests"
if [[ "$mode" == asan_ubsan ]]; then
 LD_PRELOAD="$(cc -print-file-name=libasan.so)" ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 "$out/protocol-tests" --nocapture
else
 "$out/protocol-tests" --nocapture
fi
