// SPDX-License-Identifier: GPL-3.0-or-later
#include "mzed_palette_abi.h"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>

static const uint8_t commands[] = {
    9, 0, 13, 0, 1,
    't', 'e', 's', 't', '.', 'o', 'p', 'e', 'n',
    'O', 'p', 'e', 'n', ' ', 's', 'e', 't', 't', 'i', 'n', 'g', 's',
};

static int close_enough(double left, double right) {
    const double delta = left - right;
    return delta >= -0.001 && delta <= 0.001;
}

static int32_t open_field(int32_t slot, int32_t generation,
                          double line_height, double field_height) {
    const int32_t ints[] = {1, 0, 1, 0, 1};
    const double doubles[] = {
        0.0, 0.0, 0.0, line_height,
        0.0, 0.0, 0.0, line_height,
        line_height * 0.75,
        0.0, 0.0, 0.0, line_height,
        0.0, 0.0, 0.0, line_height,
    };
    return mzed_native_palette_v1_open(
        slot, generation, commands, sizeof(commands),
        ints, sizeof(ints) / sizeof(ints[0]),
        doubles, sizeof(doubles) / sizeof(doubles[0]),
        608.0, field_height, 14.5);
}

static void verify_resized_height(int32_t slot, int32_t generation,
                                  double line_height) {
    const double initial_height = 128.0;
    const int32_t initial_status = open_field(slot, generation, line_height, 32.0);
    if (line_height <= 24.0) {
        assert(initial_status == 0);
        assert(mzed_native_palette_v1_destroy(slot, generation) == 0);
        ++generation;
    } else {
        assert(initial_status == -15);
    }

    assert(open_field(slot, generation, line_height, initial_height) == 0);
    double geometry[MZED_PALETTE_GEOMETRY_DOUBLE_COUNT];
    assert(mzed_native_palette_v1_geometry(
        slot, generation, geometry, MZED_PALETTE_GEOMETRY_DOUBLE_COUNT) ==
        MZED_PALETTE_GEOMETRY_DOUBLE_COUNT);
    const double vertical_inset = geometry[3] - geometry[7];
    assert(close_enough(vertical_inset, 8.0));

    double content_height = line_height > 14.5 ? line_height : 14.5;
    if (geometry[18] > content_height) content_height = geometry[18];
    const double field_height = content_height + vertical_inset;
    assert(field_height <= 60.0); // Fits the fixed 8-row panel.
    assert(mzed_native_palette_v1_resize(
        slot, generation, 1, 1, 608.0, field_height) == 0);
    assert(mzed_native_palette_v1_geometry(
        slot, generation, geometry, MZED_PALETTE_GEOMETRY_DOUBLE_COUNT) ==
        MZED_PALETTE_GEOMETRY_DOUBLE_COUNT);
    assert(close_enough(geometry[3], field_height));
    assert(close_enough(geometry[7], content_height));
    assert(mzed_native_palette_v1_destroy(slot, generation) == 0);
}

int main(void) {
    assert(mzed_native_palette_v1_init(1) == 0);
    verify_resized_height(0, 1, 24.0);
    verify_resized_height(0, 3, 24.25);
    verify_resized_height(0, 4, 28.0);
    puts("field height tracks line height and shared inset at 24/24.25/28px: PASS");
    return 0;
}
