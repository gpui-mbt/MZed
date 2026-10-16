// SPDX-License-Identifier: GPL-3.0-or-later
#define main palette_abi_baseline_main
#include "native_palette_abi.c"
#undef main

static const uint8_t commands[] = {1, 0, 1, 0, 1, 'c', 'C'};
static const uint8_t text_a[] = {'a'};
static const uint8_t no_text[] = {0};
static uint8_t snapshot[MZED_PALETTE_MAX_SNAPSHOT_BYTES];
static int32_t initial_integers[5];
static double initial_doubles[17];

static void open_slot(int32_t slot, int32_t generation) {
    assert(mzed_native_palette_v1_open(
        slot, generation, commands, (int32_t)sizeof(commands),
        initial_integers, 5, initial_doubles, 17, 500.0, 40.0, 16.0) == 0);
}

static void read_snapshot(int32_t slot, int32_t generation) {
    assert(mzed_native_palette_v1_snapshot(
        slot, generation, snapshot, sizeof(snapshot)) > 0);
}

static void assert_unchanged_field(
    int32_t slot, int32_t generation, int32_t revision,
    const char *text, int32_t text_length) {
    read_snapshot(slot, generation);
    assert(snapshot_i32(snapshot, 4) == revision);
    assert(snapshot_i32(snapshot, 5) == text_length);
    assert(snapshot_i32(snapshot, 6) == text_length);
    assert(memcmp(snapshot + 88, text, (size_t)text_length) == 0);
}

int main(void) {
    write_metrics(0, 0, initial_integers, initial_doubles);
    assert(mzed_native_palette_v1_init(1) == 0);
    open_slot(0, 1);
    open_slot(1, 1);

    assert(mzed_native_palette_v1_finish_close(0, 1, 1) == -13);
    assert(mzed_native_palette_v1_open(
        0, 2, commands, (int32_t)sizeof(commands),
        initial_integers, 5, initial_doubles, 17, 500.0, 40.0, 16.0) == -6);
    assert(mzed_native_palette_v1_replace(
        0, 1, 2, 1, 0, 0, 0, text_a, 1) == -13);
    assert_unchanged_field(0, 1, 1, "", 0);
    assert(snapshot_i32(snapshot, 18) == 0);

    assert(mzed_native_palette_v1_replace(
        0, 1, 1, 1, 0, 0, 0, text_a, 1) == 1);
    double original_geometry[MZED_PALETTE_GEOMETRY_DOUBLE_COUNT];
    double after_geometry[MZED_PALETTE_GEOMETRY_DOUBLE_COUNT];
    assert(mzed_native_palette_v1_geometry(
        0, 1, original_geometry, MZED_PALETTE_GEOMETRY_DOUBLE_COUNT) ==
        MZED_PALETTE_GEOMETRY_DOUBLE_COUNT);
    assert(mzed_native_palette_v1_continue(0, 1, 1, 1, 0) == -14);
    assert(mzed_native_palette_v1_continue(0, 1, 1, 1, 2) == -14);
    assert(install_layout(0, 1, 1, 1, 2, 1, 0) == -14);
    assert(install_layout(0, 1, 2, 1, 1, 1, 0) == -7);
    assert(install_layout(0, 1, 1, 2, 1, 1, 0) == -7);
    assert(mzed_native_palette_v1_input(
        0, 1, 1, 2, 1, 2, 0, 0, no_text, 0) == -7);
    assert(mzed_native_palette_v1_resize(0, 1, 1, 2, 300.0, 40.0) == -7);
    assert(mzed_native_palette_v1_abort_pending(0, 1, 1, 2) == -7);
    assert(mzed_native_palette_v1_abort_pending(0, 1, 2, 1) == -7);

    uint8_t pending[8];
    assert(mzed_native_palette_v1_pending_text(
        0, 1, 1, 1, pending, sizeof(pending)) == 1);
    assert(pending[0] == 'a');
    int32_t bad_integers[5];
    double bad_doubles[17];
    write_metrics(0, 0, bad_integers, bad_doubles);
    assert(mzed_native_palette_v1_stage_layout(
        0, 1, 1, 1, 1, bad_integers, 5, bad_doubles, 17) == -15);
    assert_unchanged_field(0, 1, 1, "", 0);
    assert(snapshot_i32(snapshot, 18) == 0);
    assert(snapshot_i32(snapshot, 19) == 1);

    assert(install_layout(0, 1, 1, 1, 1, 1, 0) == 0);
    assert(mzed_native_palette_v1_geometry(
        0, 1, after_geometry, MZED_PALETTE_GEOMETRY_DOUBLE_COUNT) ==
        MZED_PALETTE_GEOMETRY_DOUBLE_COUNT);
    assert(memcmp(original_geometry, after_geometry, sizeof(original_geometry)) == 0);
    assert_unchanged_field(0, 1, 1, "", 0);
    assert(mzed_native_palette_v1_abort_pending(0, 1, 1, 1) == 0);
    assert(mzed_native_palette_v1_geometry(
        0, 1, after_geometry, MZED_PALETTE_GEOMETRY_DOUBLE_COUNT) ==
        MZED_PALETTE_GEOMETRY_DOUBLE_COUNT);
    assert(memcmp(original_geometry, after_geometry, sizeof(original_geometry)) == 0);
    assert(install_layout(0, 1, 1, 1, 1, 1, 0) == -7);
    assert(mzed_native_palette_v1_continue(0, 1, 1, 1, 1) == -7);
    assert(mzed_native_palette_v1_abort_pending(0, 1, 1, 1) == -7);
    assert(mzed_native_palette_v1_replace(
        0, 1, 1, 1, 0, 0, 0, text_a, 1) == -7);

    assert(mzed_native_palette_v1_replace(
        0, 1, 1, 2, 0, 0, 0, text_a, 1) == 1);
    assert(install_layout(0, 1, 1, 2, 1, 1, 0) == 0);
    assert(mzed_native_palette_v1_continue(0, 1, 1, 2, 1) == 0);
    assert_unchanged_field(0, 1, 2, "a", 1);
    assert(mzed_native_palette_v1_continue(0, 1, 1, 2, 1) == -7);
    assert(mzed_native_palette_v1_init(1) == 0);
    assert_unchanged_field(0, 1, 2, "a", 1);
    assert_unchanged_field(1, 1, 1, "", 0);

    assert(mzed_native_palette_v1_replace(
        0, 1, 1, 3, 0, 0, 0, text_a, 1) == 1);
    assert(mzed_native_palette_v1_destroy(0, 1) == 0);
    assert(mzed_native_palette_v1_destroy(0, 1) == -6);
    assert(mzed_native_palette_v1_open(
        0, 1, commands, (int32_t)sizeof(commands),
        initial_integers, 5, initial_doubles, 17, 500.0, 40.0, 16.0) == -5);
    open_slot(0, 2);
    assert(mzed_native_palette_v1_snapshot(0, 1, snapshot, sizeof(snapshot)) == -5);
    assert(mzed_native_palette_v1_input(0, 1, 1, 4, 1, 1, 0, 0, no_text, 0) == -5);
    assert(mzed_native_palette_v1_replace(0, 1, 1, 4, 0, 0, 0, text_a, 1) == -5);
    assert(mzed_native_palette_v1_mark(0, 1, 1, 4, 0, 0, 0, 1, 1, 1, text_a, 1) == -5);
    assert(mzed_native_palette_v1_cancel_composition(0, 1, 1, 4) == -5);
    assert(mzed_native_palette_v1_unmark_composition(0, 1, 1, 4) == -5);
    assert(mzed_native_palette_v1_pending_text(0, 1, 1, 3, pending, sizeof(pending)) == -5);
    assert(install_layout(0, 1, 1, 3, 2, 2, 0) == -5);
    assert(mzed_native_palette_v1_continue(0, 1, 1, 3, 2) == -5);
    assert(mzed_native_palette_v1_abort_pending(0, 1, 1, 3) == -5);
    assert(mzed_native_palette_v1_geometry(0, 1, after_geometry, MZED_PALETTE_GEOMETRY_DOUBLE_COUNT) == -5);
    assert(mzed_native_palette_v1_resize(0, 1, 1, 4, 300.0, 40.0) == -5);
    assert(mzed_native_palette_v1_finish_close(0, 1, 1) == -5);
    assert(mzed_native_palette_v1_destroy(0, 1) == -5);

    assert_unchanged_field(0, 2, 1, "", 0);
    assert(snapshot_i32(snapshot, 18) == 0);
    assert(snapshot_i32(snapshot, 19) == 0);
    assert(snapshot_i32(snapshot, 17) == 0);
    assert(mzed_native_palette_v1_input(0, 2, 1, 1, 1, 1, 0, 0, no_text, 0) == 0);
    read_snapshot(0, 2);
    assert(snapshot_i32(snapshot, 3) == 0);
    assert(snapshot_i32(snapshot, 17) == 1);
    assert(mzed_native_palette_v1_finish_close(0, 2, 2) == -13);
    read_snapshot(0, 2);
    assert(snapshot_i32(snapshot, 17) == 1);
    assert(mzed_native_palette_v1_replace(0, 2, 1, 2, 0, 0, 0, text_a, 1) == -16);
    assert(mzed_native_palette_v1_mark(0, 2, 1, 3, 0, 0, 0, 1, 1, 1, text_a, 1) == -16);
    read_snapshot(0, 2);
    assert(snapshot_i32(snapshot, 19) == 0);
    assert(snapshot_i32(snapshot, 17) == 1);
    assert(mzed_native_palette_v1_finish_close(0, 2, 1) == 0);
    assert(mzed_native_palette_v1_finish_close(0, 2, 1) == 0);
    read_snapshot(0, 2);
    assert(snapshot_i32(snapshot, 17) == 0);
    assert(mzed_native_palette_v1_destroy(0, 2) == 0);

    open_slot(0, 3);
    assert(mzed_native_palette_v1_input(0, 3, 1, 1, 1, 1, 0, 0, no_text, 0) == 0);
    read_snapshot(0, 3);
    assert(snapshot_i32(snapshot, 17) == 1);
    assert(mzed_native_palette_v1_destroy(0, 3) == 0);
    open_slot(0, 4);
    read_snapshot(0, 4);
    assert(snapshot_i32(snapshot, 17) == 0);
    assert(mzed_native_palette_v1_destroy(0, 4) == 0);
    assert(mzed_native_palette_v1_destroy(1, 1) == 0);

    puts("stale revision, request, epoch, generation, slot, and close ACK probes: PASS");
    return 0;
}
