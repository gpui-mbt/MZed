#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$(realpath "$1")"
cc -std=gnu11 -Wall -Wextra -Werror -I "$root/native" \
  "$root/tests/native_palette_abi.c" "$out/libmzed_native.a" \
  -o "$out/palette-abi-tests"
"$out/palette-abi-tests"
cc -std=gnu11 -Wall -Wextra -Werror -I "$root/native" \
  "$root/tests/native_palette_adversarial.c" "$out/libmzed_native.a" \
  -o "$out/palette-adversarial-tests"
"$out/palette-adversarial-tests"
cc -std=gnu11 -Wall -Wextra -Werror -I "$root/native" \
  "$root/tests/native_palette_lifecycle.c" "$out/libmzed_native.a" \
  -o "$out/palette-lifecycle-tests"
"$out/palette-lifecycle-tests"
cc -std=gnu11 -Wall -Wextra -Werror -I "$root/native" \
  "$root/tests/native_palette_host_lifecycle.c" "$out/libmzed_native.a" \
  -o "$out/palette-host-lifecycle-tests"
"$out/palette-host-lifecycle-tests"
cc -std=gnu11 -Wall -Wextra -Werror -I "$root/native" \
  "$root/tests/native_palette_field_height.c" "$out/libmzed_native.a" \
  -o "$out/palette-field-height-tests"
"$out/palette-field-height-tests"
