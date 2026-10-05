// SPDX-License-Identifier: GPL-3.0-or-later
#include "mzed_native.h"
#include <stddef.h>
// Compiler/runtime bootstrap is private; it never receives user pointers.
extern void moonbit_runtime_init(int, char **);
extern void moonbit_init(void);
extern int32_t mzed_mb_v1_init(int32_t);
extern int32_t mzed_mb_v1_create(int32_t, int32_t, int32_t, int32_t);
extern int32_t mzed_mb_v1_dispatch(int32_t, int32_t, int32_t, int32_t, int32_t, int32_t);
extern int32_t mzed_mb_v1_snapshot(int32_t, int32_t, int32_t, int32_t);
extern int32_t mzed_mb_v1_destroy(int32_t, int32_t);
static int initialized;
int32_t mzed_native_v1_init(int32_t version) {
    if (version != 1) return -2;
    if (!initialized) {
        moonbit_runtime_init(0, NULL);
        moonbit_init();
        initialized = 1;
    }
    return mzed_mb_v1_init(version);
}
int32_t mzed_native_v1_create(int32_t s, int32_t g, int32_t w, int32_t h) {
    return initialized ? mzed_mb_v1_create(s, g, w, h) : -1;
}
int32_t mzed_native_v1_dispatch(int32_t s, int32_t g, int32_t r, int32_t op, int32_t a, int32_t b) {
    return initialized ? mzed_mb_v1_dispatch(s, g, r, op, a, b) : -1;
}
int32_t mzed_native_v1_snapshot(int32_t s, int32_t g, int32_t r, int32_t f) {
    return initialized ? mzed_mb_v1_snapshot(s, g, r, f) : -1;
}
int32_t mzed_native_v1_destroy(int32_t s, int32_t g) {
    return initialized ? mzed_mb_v1_destroy(s, g) : -1;
}
