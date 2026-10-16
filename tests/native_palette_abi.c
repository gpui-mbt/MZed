// SPDX-License-Identifier: GPL-3.0-or-later
#include "mzed_palette_abi.h"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static int32_t read_i32_le(const uint8_t *bytes) {
    return (int32_t)((uint32_t)bytes[0] |
                     ((uint32_t)bytes[1] << 8) |
                     ((uint32_t)bytes[2] << 16) |
                     ((uint32_t)bytes[3] << 24));
}

static int32_t snapshot_i32(const uint8_t *snapshot, int index) {
    return read_i32_le(snapshot + (size_t)index * 4);
}

static void write_metrics(int scalar_count, int invalid_lines,
                          int32_t *ints, double *doubles) {
    int caret_count = scalar_count + 1;
    ints[0] = invalid_lines ? 2 : 1;
    ints[1] = 0;
    ints[2] = caret_count;
    for (int i = 0; i < caret_count; ++i) {
        ints[3 + i * 2] = i;
        ints[4 + i * 2] = 1;
    }
    const double base[] = {
        0.0, 0.0, 400.0, 32.0,
        0.0, 0.0, 400.0, 32.0,
        20.0,
    };
    memcpy(doubles, base, sizeof(base));
    for (int i = 0; i < caret_count; ++i) {
        double x = (double)i * 8.0;
        const double caret[] = {x, 0.0, 0.0, 32.0, x, 0.0, 0.0, 32.0};
        memcpy(doubles + 9 + i * 8, caret, sizeof(caret));
    }
}

static int32_t install_layout(int32_t slot, int32_t generation, int32_t epoch,
                              int32_t request, int32_t revision,
                              int scalar_count, int invalid_lines) {
    int32_t ints[3 + (MZED_PALETTE_MAX_QUERY_BYTES + 1) * 2];
    double doubles[9 + (MZED_PALETTE_MAX_QUERY_BYTES + 1) * 8];
    write_metrics(scalar_count, invalid_lines, ints, doubles);
    return mzed_native_palette_v1_stage_layout(
        slot, generation, epoch, request, revision,
        ints, 3 + (scalar_count + 1) * 2,
        doubles, 9 + (scalar_count + 1) * 8);
}

int main(void) {
    const uint8_t commands[] = {
        9, 0, 13, 0, 1,
        't', 'e', 's', 't', '.', 'o', 'p', 'e', 'n',
        'O', 'p', 'e', 'n', ' ', 's', 'e', 't', 't', 'i', 'n', 'g', 's',
    };
    const uint8_t empty[] = {0};
    const uint8_t typed[] = {'a'};
    const uint8_t japanese_preedit[] = {0xe3, 0x81, 0xab};
    int32_t initial_ints[5];
    double initial_doubles[17];
    uint8_t snapshot[MZED_PALETTE_MAX_SNAPSHOT_BYTES];
    write_metrics(0, 0, initial_ints, initial_doubles);

    assert(mzed_native_palette_v1_init(1) == 0);
    assert(mzed_native_palette_v1_init(2) == -2);
    assert(mzed_native_palette_v1_open(
        0, 1, commands, (int32_t)sizeof(commands),
        initial_ints, 5, initial_doubles, 17, 500.0, 40.0, 16.0) == 0);
    int32_t length = mzed_native_palette_v1_snapshot(0, 1, snapshot, sizeof(snapshot));
    assert(length == 130);
    assert(snapshot_i32(snapshot, 0) == 1);
    assert(snapshot_i32(snapshot, 1) == 1);
    assert(snapshot_i32(snapshot, 2) == 1);
    assert(snapshot_i32(snapshot, 3) == 1);
    assert(snapshot_i32(snapshot, 4) == 1);
    assert(snapshot_i32(snapshot, 18) == 0);
    assert(snapshot_i32(snapshot, 19) == 0);
    assert(snapshot_i32(snapshot, 20) == 0);
    assert(snapshot_i32(snapshot, 21) == 0);
    assert(snapshot_i32(snapshot, 22) == 9);
    assert(snapshot_i32(snapshot, 23) == 13);
    assert(snapshot_i32(snapshot, 24) == 1);
    assert(snapshot_i32(snapshot, 25) == 1);
    assert(memcmp(snapshot + 108, "test.open", 9) == 0);
    assert(memcmp(snapshot + 117, "Open settings", 13) == 0);

    // Close emits a deferred one-shot action, consumed only by finish_close.
    assert(mzed_native_palette_v1_input(0, 1, 1, 1, 1, 1, 0, 0, empty, 0) == 0);
    length = mzed_native_palette_v1_snapshot(0, 1, snapshot, sizeof(snapshot));
    assert(length == 139);
    assert(snapshot_i32(snapshot, 3) == 0);
    assert(snapshot_i32(snapshot, 16) == 2);
    assert(snapshot_i32(snapshot, 17) == 9);
    assert(snapshot_i32(snapshot, 18) == 1);
    assert(memcmp(snapshot + 88, "test.open", 9) == 0);
    assert(mzed_native_palette_v1_finish_close(0, 1, 1) == 0);
    assert(mzed_native_palette_v1_destroy(0, 1) == 0);
    assert(mzed_native_palette_v1_snapshot(0, 1, snapshot, sizeof(snapshot)) == -6);

    assert(mzed_native_palette_v1_open(
        0, 2, commands, (int32_t)sizeof(commands),
        initial_ints, 5, initial_doubles, 17, 500.0, 40.0, 16.0) == 0);

    // A refused host shape aborts exactly one pending request, preserving the
    // old document and retiring its request number against stale retries.
    assert(mzed_native_palette_v1_replace(
        0, 2, 1, 1, 0, 0, 0, typed, 1) == 1);
    uint8_t pending[8] = {0};
    assert(mzed_native_palette_v1_pending_text(0, 2, 1, 1, pending, sizeof(pending)) == 1);
    assert(pending[0] == 'a');
    assert(mzed_native_palette_v1_abort_pending(0, 2, 1, 1) == 0);
    assert(mzed_native_palette_v1_continue(0, 2, 1, 1, 0) == -7);
    assert(mzed_native_palette_v1_pending_text(0, 2, 1, 1, pending, sizeof(pending)) == -7);
    assert(mzed_native_palette_v1_replace(
        0, 2, 1, 1, 0, 0, 0, typed, 1) == -7);
    length = mzed_native_palette_v1_snapshot(0, 2, snapshot, sizeof(snapshot));
    assert(snapshot_i32(snapshot, 4) == 1);
    assert(snapshot_i32(snapshot, 5) == 0);
    assert(snapshot_i32(snapshot, 18) == 1);
    assert(snapshot_i32(snapshot, 19) == 0);

    // Invalid host geometry consumes and rejects the operation atomically.
    assert(mzed_native_palette_v1_replace(
        0, 2, 1, 2, 0, 0, 0, typed, 1) == 1);
    assert(install_layout(0, 2, 1, 2, 1, 1, 1) == 0);
    assert(mzed_native_palette_v1_continue(0, 2, 1, 2, 1) == -16);
    length = mzed_native_palette_v1_snapshot(0, 2, snapshot, sizeof(snapshot));
    assert(snapshot_i32(snapshot, 4) == 1);
    assert(snapshot_i32(snapshot, 5) == 0);
    assert(snapshot_i32(snapshot, 18) == 2);
    assert(snapshot_i32(snapshot, 19) == 0);
    assert(mzed_native_palette_v1_replace(
        0, 2, 1, 2, 0, 0, 0, typed, 1) == -7);

    // Text shape metrics carry no selection; the ABI rebases the immutable
    // geometry onto the exact requested caret document before shared install.
    assert(mzed_native_palette_v1_replace(
        0, 2, 1, 3, 0, 0, 0, typed, 1) == 1);
    assert(install_layout(0, 2, 1, 3, 1, 1, 0) == 0);
    assert(mzed_native_palette_v1_continue(0, 2, 1, 3, 1) == 0);
    length = mzed_native_palette_v1_snapshot(0, 2, snapshot, sizeof(snapshot));
    assert(snapshot_i32(snapshot, 4) == 2);
    assert(snapshot_i32(snapshot, 5) == 1);
    assert(snapshot_i32(snapshot, 6) == 1);
    assert(snapshot_i32(snapshot, 7) == 1);
    assert(snapshot_i32(snapshot, 8) == 1);
    assert(snapshot_i32(snapshot, 18) == 3);
    assert(snapshot[88] == 'a');
    assert(snapshot[89] == 'a');

    // Resize is a shared TextField::with_bounds operation and exposes the
    // shared scroll/caret/run geometry without Rust recomputation.
    assert(mzed_native_palette_v1_resize(0, 2, 1, 4, 450.0, 40.0) == 0);
    double geometry[MZED_PALETTE_GEOMETRY_DOUBLE_COUNT];
    assert(mzed_native_palette_v1_geometry(
        0, 2, geometry, MZED_PALETTE_GEOMETRY_DOUBLE_COUNT) ==
        MZED_PALETTE_GEOMETRY_DOUBLE_COUNT);
    assert(geometry[2] == 450.0 && geometry[3] == 40.0);
    assert(geometry[4] == 4.0 && geometry[5] == 4.0);
    length = mzed_native_palette_v1_snapshot(0, 2, snapshot, sizeof(snapshot));
    assert(snapshot_i32(snapshot, 4) == 3);
    assert(snapshot_i32(snapshot, 18) == 4);

    // Japanese preedit performs a second layout round trip. Selection-only
    // geometry rebasing permits the exact UTF-16 caret request to install.
    assert(mzed_native_palette_v1_mark(
        0, 2, 1, 5, 0, 0, 0, 1, 1, 1,
        japanese_preedit, (int32_t)sizeof(japanese_preedit)) == 1);
    memset(pending, 0, sizeof(pending));
    assert(mzed_native_palette_v1_pending_text(0, 2, 1, 5, pending, sizeof(pending)) == 4);
    assert(memcmp(pending, "a\xe3\x81\xab", 4) == 0);
    assert(install_layout(0, 2, 1, 5, 3, 2, 0) == 0);
    assert(mzed_native_palette_v1_continue(0, 2, 1, 5, 3) == 0);
    length = mzed_native_palette_v1_snapshot(0, 2, snapshot, sizeof(snapshot));
    assert(snapshot_i32(snapshot, 4) == 4);
    assert(snapshot_i32(snapshot, 5) == 4);
    assert(snapshot_i32(snapshot, 6) == 1);
    assert(snapshot_i32(snapshot, 9) == 1);
    assert(snapshot_i32(snapshot, 10) == 1);
    assert(snapshot_i32(snapshot, 11) == 2);
    assert(memcmp(snapshot + 88, "a\xe3\x81\xab", 4) == 0);
    assert(snapshot[92] == 'a');

    // Empty preedit restores the original text but retains composition. A
    // cancel then restores the transaction without history or text loss.
    assert(mzed_native_palette_v1_mark(0, 2, 1, 6, 0, 0, 0, 1, 0, 0, empty, 0) == 1);
    memset(pending, 0, sizeof(pending));
    assert(mzed_native_palette_v1_pending_text(0, 2, 1, 6, pending, sizeof(pending)) == 1);
    assert(pending[0] == 'a');
    assert(install_layout(0, 2, 1, 6, 4, 1, 0) == 0);
    assert(mzed_native_palette_v1_continue(0, 2, 1, 6, 4) == 0);
    length = mzed_native_palette_v1_snapshot(0, 2, snapshot, sizeof(snapshot));
    assert(snapshot_i32(snapshot, 5) == 1);
    assert(snapshot_i32(snapshot, 9) == 1);
    assert(snapshot_i32(snapshot, 10) == -1);
    assert(snapshot_i32(snapshot, 11) == -1);
    assert(snapshot[88] == 'a' && snapshot[89] == 'a');
    assert(mzed_native_palette_v1_cancel_composition(0, 2, 1, 7) == 0);
    length = mzed_native_palette_v1_snapshot(0, 2, snapshot, sizeof(snapshot));
    assert(snapshot_i32(snapshot, 9) == 0);
    assert(snapshot_i32(snapshot, 5) == 1);
    assert(snapshot[88] == 'a' && snapshot[89] == 'a');

    // Teardown is unconditional during an open/pending request and cannot
    // dispatch the command. The slot is reusable only with a newer generation.
    assert(mzed_native_palette_v1_replace(
        0, 2, 1, 8, 0, 0, 0, japanese_preedit, 3) == 1);
    assert(mzed_native_palette_v1_destroy(0, 2) == 0);
    assert(mzed_native_palette_v1_snapshot(0, 2, snapshot, sizeof(snapshot)) == -6);
    assert(mzed_native_palette_v1_continue(0, 2, 1, 8, 3) == -6);
    assert(mzed_native_palette_v1_open(
        0, 3, commands, (int32_t)sizeof(commands),
        initial_ints, 5, initial_doubles, 17, 500.0, 40.0, 16.0) == 0);
    assert(mzed_native_palette_v1_destroy(0, 3) == 0);
    assert(mzed_native_palette_v1_open(
        0, 4, commands, (int32_t)sizeof(commands),
        initial_ints, 5, initial_doubles, 17, 500.0, 40.0, 16.0) == 0);
    assert(mzed_native_palette_v1_destroy(0, 4) == 0);

    puts("palette ABI owner, geometry, composition, abort, and teardown: PASS");
    return 0;
}
