// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef MZED_NATIVE_V1_H
#define MZED_NATIVE_V1_H
#include <stdint.h>
// All calls must be serialized on ONE owner thread, including init.
// No borrowed pointers, managed values, callbacks, OS/GPU/Rust handles, or strings.
// Limits: 4 slots, 14 snapshot fields, IDs 1..1000000, dimensions 1..4096.
// Errors: -1 not initialized; -2 ABI; -3 slot; -4 generation range;
// -5 stale generation; -6 not live; -7 request; -8 opcode; -9 value;
// -10 snapshot field; -11 already live; -12 unsupported/failed scene. Success is zero; fields are >=0.
int32_t mzed_native_v1_init(int32_t abi_version);
int32_t mzed_native_v1_create(int32_t slot, int32_t generation, int32_t width, int32_t height);
int32_t mzed_native_v1_dispatch(int32_t slot, int32_t generation, int32_t request, int32_t opcode, int32_t a, int32_t b);
int32_t mzed_native_v1_snapshot(int32_t slot, int32_t generation, int32_t request, int32_t field);
int32_t mzed_native_v1_destroy(int32_t slot, int32_t generation);
#endif
