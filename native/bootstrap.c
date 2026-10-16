// SPDX-License-Identifier: GPL-3.0-or-later
#include "mzed_native.h"
#include "mzed_palette_abi.h"
#include "moonbit.h"
#include <stddef.h>

extern void moonbit_runtime_init(int, char **);
extern void moonbit_init(void);
extern int32_t mzed_mb_v1_init(int32_t);
extern int32_t mzed_mb_v1_create(int32_t, int32_t, int32_t, int32_t);
extern int32_t mzed_mb_v1_dispatch(int32_t, int32_t, int32_t, int32_t, int32_t, int32_t);
extern int32_t mzed_mb_v1_snapshot(int32_t, int32_t, int32_t, int32_t);
extern int32_t mzed_mb_v1_destroy(int32_t, int32_t);

extern int32_t mzed_mb_palette_v1_init(int32_t);
extern int32_t mzed_mb_palette_v1_open(
    int32_t, int32_t, moonbit_bytes_t, int32_t,
    int32_t *, int32_t, double *, int32_t, double, double, double);
extern int32_t mzed_mb_palette_v1_input(
    int32_t, int32_t, int32_t, int32_t, int32_t, int32_t, int32_t, int32_t,
    moonbit_bytes_t, int32_t);
extern int32_t mzed_mb_palette_v1_replace(
    int32_t, int32_t, int32_t, int32_t, int32_t, int32_t, int32_t,
    moonbit_bytes_t, int32_t);
extern int32_t mzed_mb_palette_v1_mark(
    int32_t, int32_t, int32_t, int32_t, int32_t, int32_t, int32_t, int32_t,
    int32_t, int32_t, moonbit_bytes_t, int32_t);
extern int32_t mzed_mb_palette_v1_cancel_composition(int32_t, int32_t, int32_t, int32_t);
extern int32_t mzed_mb_palette_v1_unmark_composition(int32_t, int32_t, int32_t, int32_t);
extern int32_t mzed_mb_palette_v1_pending_text(
    int32_t, int32_t, int32_t, int32_t, moonbit_bytes_t, int32_t);
extern int32_t mzed_mb_palette_v1_stage_layout(
    int32_t, int32_t, int32_t, int32_t, int32_t,
    int32_t *, int32_t, double *, int32_t);
extern int32_t mzed_mb_palette_v1_continue(int32_t, int32_t, int32_t, int32_t, int32_t);
extern int32_t mzed_mb_palette_v1_snapshot(int32_t, int32_t, moonbit_bytes_t, int32_t);
extern int32_t mzed_mb_palette_v1_geometry(int32_t, int32_t, double *, int32_t);
extern int32_t mzed_mb_palette_v1_resize(int32_t, int32_t, int32_t, int32_t, double, double);
extern int32_t mzed_mb_palette_v1_finish_close(int32_t, int32_t, int32_t);
extern int32_t mzed_mb_palette_v1_abort_pending(int32_t, int32_t, int32_t, int32_t);
extern int32_t mzed_mb_palette_v1_destroy(int32_t, int32_t);

static int initialized;

static void ensure_runtime(void) {
    if (!initialized) {
        moonbit_runtime_init(0, NULL);
        moonbit_init();
        initialized = 1;
    }
}

int32_t mzed_native_v1_init(int32_t version) {
    if (version != 1) return -2;
    ensure_runtime();
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

int32_t mzed_native_palette_v1_init(int32_t version) {
    if (version != 1) return -2;
    ensure_runtime();
    return mzed_mb_palette_v1_init(version);
}
#include <string.h>

static moonbit_bytes_t palette_copy_bytes(const uint8_t *source, int32_t length) {
    moonbit_bytes_t destination = moonbit_make_bytes(length, 0);
    if (length > 0) memcpy(destination, source, (size_t)length);
    return destination;
}

static int32_t *palette_copy_ints(const int32_t *source, int32_t length) {
    int32_t *destination = moonbit_make_int32_array(length, 0);
    if (length > 0) memcpy(destination, source, (size_t)length * sizeof(int32_t));
    return destination;
}

static double *palette_copy_doubles(const double *source, int32_t length) {
    double *destination = moonbit_make_double_array(length, 0.0);
    if (length > 0) memcpy(destination, source, (size_t)length * sizeof(double));
    return destination;
}

int32_t mzed_native_palette_v1_open(
    int32_t s, int32_t g, const uint8_t *commands, int32_t commands_len,
    const int32_t *ints, int32_t ints_len, const double *doubles, int32_t doubles_len,
    double width, double height, double font_size) {
    if (!initialized) return -1;
    if (commands_len < 0 || commands_len > MZED_PALETTE_MAX_COMMAND_BYTES ||
        (commands_len > 0 && commands == NULL) ||
        ints_len < 0 || ints_len > MZED_PALETTE_MAX_METRIC_INTS ||
        (ints_len > 0 && ints == NULL) ||
        doubles_len < 0 || doubles_len > MZED_PALETTE_MAX_METRIC_DOUBLES ||
        (doubles_len > 0 && doubles == NULL)) return -9;
    moonbit_bytes_t command_copy = palette_copy_bytes(commands, commands_len);
    int32_t *int_copy = palette_copy_ints(ints, ints_len);
    double *double_copy = palette_copy_doubles(doubles, doubles_len);
    int32_t result = mzed_mb_palette_v1_open(s, g, command_copy, commands_len,
        int_copy, ints_len, double_copy, doubles_len, width, height, font_size);
    moonbit_decref(command_copy);
    moonbit_decref(int_copy);
    moonbit_decref(double_copy);
    return result;
}

int32_t mzed_native_palette_v1_input(
    int32_t s, int32_t g, int32_t epoch, int32_t request, int32_t kind,
    int32_t key, int32_t modifiers, int32_t repeat, const uint8_t *text, int32_t text_len) {
    if (!initialized) return -1;
    if (text_len < 0 || text_len > MZED_PALETTE_MAX_TEXT_BYTES ||
        (text_len > 0 && text == NULL)) return -9;
    moonbit_bytes_t copy = palette_copy_bytes(text, text_len);
    int32_t result = mzed_mb_palette_v1_input(s, g, epoch, request, kind, key,
        modifiers, repeat, copy, text_len);
    moonbit_decref(copy);
    return result;
}

int32_t mzed_native_palette_v1_replace(
    int32_t s, int32_t g, int32_t epoch, int32_t request,
    int32_t has_range, int32_t start, int32_t end,
    const uint8_t *text, int32_t text_len) {
    if (!initialized) return -1;
    if (text_len < 0 || text_len > MZED_PALETTE_MAX_TEXT_BYTES ||
        (text_len > 0 && text == NULL)) return -9;
    moonbit_bytes_t copy = palette_copy_bytes(text, text_len);
    int32_t result = mzed_mb_palette_v1_replace(s, g, epoch, request,
        has_range, start, end, copy, text_len);
    moonbit_decref(copy);
    return result;
}

int32_t mzed_native_palette_v1_mark(
    int32_t s, int32_t g, int32_t epoch, int32_t request,
    int32_t has_range, int32_t range_start, int32_t range_end,
    int32_t has_selection, int32_t selection_start, int32_t selection_end,
    const uint8_t *text, int32_t text_len) {
    if (!initialized) return -1;
    if (text_len < 0 || text_len > MZED_PALETTE_MAX_TEXT_BYTES ||
        (text_len > 0 && text == NULL)) return -9;
    moonbit_bytes_t copy = palette_copy_bytes(text, text_len);
    int32_t result = mzed_mb_palette_v1_mark(s, g, epoch, request,
        has_range, range_start, range_end, has_selection, selection_start,
        selection_end, copy, text_len);
    moonbit_decref(copy);
    return result;
}

int32_t mzed_native_palette_v1_cancel_composition(int32_t s, int32_t g, int32_t e, int32_t r) {
    return initialized ? mzed_mb_palette_v1_cancel_composition(s, g, e, r) : -1;
}

int32_t mzed_native_palette_v1_unmark_composition(int32_t s, int32_t g, int32_t e, int32_t r) {
    return initialized ? mzed_mb_palette_v1_unmark_composition(s, g, e, r) : -1;
}

int32_t mzed_native_palette_v1_pending_text(
    int32_t s, int32_t g, int32_t e, int32_t r, uint8_t *out, int32_t capacity) {
    if (!initialized) return -1;
    if (capacity < 0 || capacity > MZED_PALETTE_MAX_TEXT_BYTES ||
        (capacity > 0 && out == NULL)) return -9;
    moonbit_bytes_t copy = moonbit_make_bytes(capacity, 0);
    int32_t result = mzed_mb_palette_v1_pending_text(s, g, e, r, copy, capacity);
    if (result >= 0 && result <= capacity && result > 0) memcpy(out, copy, (size_t)result);
    moonbit_decref(copy);
    return result;
}

int32_t mzed_native_palette_v1_stage_layout(
    int32_t s, int32_t g, int32_t e, int32_t r, int32_t revision,
    const int32_t *ints, int32_t ints_len, const double *doubles, int32_t doubles_len) {
    if (!initialized) return -1;
    if (ints_len < 0 || ints_len > MZED_PALETTE_MAX_METRIC_INTS ||
        (ints_len > 0 && ints == NULL) ||
        doubles_len < 0 || doubles_len > MZED_PALETTE_MAX_METRIC_DOUBLES ||
        (doubles_len > 0 && doubles == NULL)) return -9;
    int32_t *int_copy = palette_copy_ints(ints, ints_len);
    double *double_copy = palette_copy_doubles(doubles, doubles_len);
    int32_t result = mzed_mb_palette_v1_stage_layout(s, g, e, r, revision,
        int_copy, ints_len, double_copy, doubles_len);
    moonbit_decref(int_copy);
    moonbit_decref(double_copy);
    return result;
}

int32_t mzed_native_palette_v1_continue(int32_t s, int32_t g, int32_t e, int32_t r, int32_t revision) {
    return initialized ? mzed_mb_palette_v1_continue(s, g, e, r, revision) : -1;
}

int32_t mzed_native_palette_v1_snapshot(int32_t s, int32_t g, uint8_t *out, int32_t capacity) {
    if (!initialized) return -1;
    if (capacity < 0 || capacity > MZED_PALETTE_MAX_SNAPSHOT_BYTES ||
        (capacity > 0 && out == NULL)) return -9;
    moonbit_bytes_t copy = moonbit_make_bytes(capacity, 0);
    int32_t result = mzed_mb_palette_v1_snapshot(s, g, copy, capacity);
    if (result >= 0 && result <= capacity && result > 0) memcpy(out, copy, (size_t)result);
    moonbit_decref(copy);
    return result;
}

int32_t mzed_native_palette_v1_geometry(int32_t s, int32_t g, double *out, int32_t capacity) {
    if (!initialized) return -1;
    if (capacity != MZED_PALETTE_GEOMETRY_DOUBLE_COUNT || out == NULL) return -9;
    double *copy = moonbit_make_double_array(capacity, 0.0);
    int32_t result = mzed_mb_palette_v1_geometry(s, g, copy, capacity);
    if (result == MZED_PALETTE_GEOMETRY_DOUBLE_COUNT) {
        memcpy(out, copy, (size_t)result * sizeof(double));
    }
    moonbit_decref(copy);
    return result;
}

int32_t mzed_native_palette_v1_resize(
    int32_t s, int32_t g, int32_t e, int32_t r, double width, double height) {
    return initialized ? mzed_mb_palette_v1_resize(s, g, e, r, width, height) : -1;
}

int32_t mzed_native_palette_v1_finish_close(int32_t s, int32_t g, int32_t e) {
    return initialized ? mzed_mb_palette_v1_finish_close(s, g, e) : -1;
}

int32_t mzed_native_palette_v1_abort_pending(int32_t s, int32_t g, int32_t e, int32_t r) {
    return initialized ? mzed_mb_palette_v1_abort_pending(s, g, e, r) : -1;
}

int32_t mzed_native_palette_v1_destroy(int32_t s, int32_t g) {
    return initialized ? mzed_mb_palette_v1_destroy(s, g) : -1;
}
