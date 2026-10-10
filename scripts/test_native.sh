#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$(realpath "$1")"
mode="${2:-normal}"
system="$(uname -s)"
set -- +1.98.1 --edition 2024 -D warnings --test "$root/native/protocol.rs" \
  -L "native=$out" -l static=mzed_native
case "$mode" in
 normal) ;;
 ubsan) set -- "$@" -C link-arg=-lubsan ;;
 asan_ubsan) set -- "$@" -C link-arg=-lasan -C link-arg=-lubsan ;;
 *) exit 2 ;;
esac
if [[ "$system" == Darwin && "$mode" != normal ]]; then
  echo 'UBSan and ASan ABI lanes are qualified on Linux only' >&2
  exit 2
fi
set -- "$@" -o "$out/protocol-tests"
rustc "$@"
if [[ "$mode" == asan_ubsan ]]; then
 if [[ "$system" == Darwin ]]; then
  echo 'ASan runtime preloading is Linux-specific' >&2
  exit 2
 fi
 LD_PRELOAD="$(cc -print-file-name=libasan.so)" ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 "$out/protocol-tests" --nocapture
else
 "$out/protocol-tests" --nocapture
fi
