// SPDX-License-Identifier: GPL-3.0-or-later
#define main palette_abi_baseline_main
#include "native_palette_abi.c"
#undef main

static int32_t generation = 1;
static int32_t request = 0;
static uint8_t snapshot[MZED_PALETTE_MAX_SNAPSHOT_BYTES];

static void snapshot_current(void) {
    assert(mzed_native_palette_v1_snapshot(
               0, generation, snapshot, sizeof(snapshot)) >= 88);
}

static int32_t stage_pending_layout(int32_t status) {
    if (status != 1) {
        return status;
    }

    uint8_t text[MZED_PALETTE_MAX_QUERY_BYTES];
    const int32_t text_length = mzed_native_palette_v1_pending_text(
        0, generation, 1, request, text, sizeof(text));
    assert(text_length >= 0);

    int32_t integers[MZED_PALETTE_MAX_METRIC_INTS];
    double doubles[MZED_PALETTE_MAX_METRIC_DOUBLES];
    int32_t utf16_offsets[MZED_PALETTE_MAX_QUERY_BYTES + 1];
    int32_t caret_count = 1;
    int32_t utf16_offset = 0;
    utf16_offsets[0] = 0;
    for (int32_t byte_offset = 0; byte_offset < text_length;) {
        const uint8_t first = text[byte_offset++];
        uint32_t scalar = first;
        if (first >= 0xf0) {
            scalar = ((uint32_t)(first & 0x07) << 18)
                | ((uint32_t)(text[byte_offset] & 0x3f) << 12)
                | ((uint32_t)(text[byte_offset + 1] & 0x3f) << 6)
                | (uint32_t)(text[byte_offset + 2] & 0x3f);
            byte_offset += 3;
        } else if (first >= 0xe0) {
            scalar = ((uint32_t)(first & 0x0f) << 12)
                | ((uint32_t)(text[byte_offset] & 0x3f) << 6)
                | (uint32_t)(text[byte_offset + 1] & 0x3f);
            byte_offset += 2;
        } else if (first >= 0xc0) {
            scalar = ((uint32_t)(first & 0x1f) << 6)
                | (uint32_t)(text[byte_offset] & 0x3f);
            byte_offset += 1;
        }
        utf16_offset += scalar > 0xffff ? 2 : 1;
        utf16_offsets[caret_count++] = utf16_offset;
    }

    write_metrics(caret_count - 1, 0, integers, doubles);
    for (int32_t caret = 0; caret < caret_count; ++caret) {
        integers[3 + caret * 2] = utf16_offsets[caret];
    }
    snapshot_current();
    const int32_t revision = snapshot_i32(snapshot, 4);
    assert(mzed_native_palette_v1_stage_layout(
               0, generation, 1, request, revision,
               integers, 3 + caret_count * 2,
               doubles, 9 + caret_count * 8) == 0);
    return mzed_native_palette_v1_continue(
        0, generation, 1, request, revision);
}

static int32_t replace_text(const char *text) {
    ++request;
    const int32_t status = mzed_native_palette_v1_replace(
        0, generation, 1, request, 0, 0, 0,
        (const uint8_t *)text, (int32_t)strlen(text));
    return stage_pending_layout(status);
}

static int32_t send_key(int32_t key_tag, int32_t modifiers, const char *text) {
    ++request;
    const int32_t status = mzed_native_palette_v1_input(
        0, generation, 1, request, 1, key_tag, modifiers, 0,
        (const uint8_t *)text, (int32_t)strlen(text));
    return stage_pending_layout(status);
}

static int32_t mark_text(const char *text, int32_t anchor, int32_t head) {
    ++request;
    const int32_t status = mzed_native_palette_v1_mark(
        0, generation, 1, request, 0, 0, 0, 1, anchor, head,
        (const uint8_t *)text, (int32_t)strlen(text));
    return stage_pending_layout(status);
}

static void assert_displayed_text(const char *text, int32_t composing) {
    snapshot_current();
    const int32_t text_length = (int32_t)strlen(text);
    assert(snapshot_i32(snapshot, 5) == text_length);
    assert(memcmp(snapshot + 88, text, (size_t)text_length) == 0);
    assert(snapshot_i32(snapshot, 9) == composing);
}

int main(void) {
    const uint8_t commands[] = {
        9, 0, 13, 0, 1,
        't', 'e', 's', 't', '.', 'o', 'p', 'e', 'n',
        'O', 'p', 'e', 'n', ' ', 's', 'e', 't', 't', 'i', 'n', 'g', 's',
    };
    int32_t initial_integers[5];
    double initial_doubles[17];
    write_metrics(0, 0, initial_integers, initial_doubles);
    assert(mzed_native_palette_v1_init(1) == 0);
    assert(mzed_native_palette_v1_open(
        0, generation, commands, (int32_t)sizeof(commands),
        initial_integers, 5, initial_doubles, 17, 500.0, 40.0, 16.0) == 0);

    const char supplementary_text[] = "a\xf0\x9f\x98\x80" "b";
    assert(replace_text(supplementary_text) == 0);
    assert_displayed_text(supplementary_text, 0);
    assert(send_key(15, 2, "a") == 0); // Ctrl+A selects UTF-16 [0, 4).
    snapshot_current();
    assert(snapshot_i32(snapshot, 7) == 0);
    assert(snapshot_i32(snapshot, 8) == 4);

    // A surrogate-interior range, an invalid preedit selection, and a partial
    // marked-span replacement all fail without changing the shared document.
    ++request;
    assert(mzed_native_palette_v1_replace(
        0, generation, 1, request, 1, 1, 2,
        (const uint8_t *)"x", 1) == -16);
    assert_displayed_text(supplementary_text, 0);
    assert(mark_text("\xf0\x9f\x98\x80", 1, 1) == -16);
    assert_displayed_text(supplementary_text, 0);

    const char japanese_text[] = "\xe6\x97\xa5\xe6\x9c\xac";
    assert(mark_text(japanese_text, 2, 2) == 0);
    assert_displayed_text(japanese_text, 1);
    ++request;
    assert(mzed_native_palette_v1_replace(
        0, generation, 1, request, 1, 0, 1,
        (const uint8_t *)"x", 1) == -16);
    assert_displayed_text(japanese_text, 1);

    // Clearing preedit hides the preview but retains its original selected
    // range. An explicit empty commit deletes that range exactly once; undo
    // restores both the supplementary text and its directional selection.
    assert(mark_text("", 0, 0) == 0);
    assert_displayed_text(supplementary_text, 1);
    assert(replace_text("") == 0);
    assert_displayed_text("", 0);
    assert(send_key(15, 2, "z") == 0); // Ctrl+Z.
    assert_displayed_text(supplementary_text, 0);
    snapshot_current();
    assert(snapshot_i32(snapshot, 7) == 0);
    assert(snapshot_i32(snapshot, 8) == 4);

    // An empty-preview unmark follows the explicit cancel policy; stale epoch
    // and owner calls remain rejected after the visible text is restored.
    assert(mark_text(japanese_text, 2, 2) == 0);
    assert(mark_text("", 0, 0) == 0);
    ++request;
    assert(mzed_native_palette_v1_unmark_composition(
        0, generation, 1, request) == 0);
    assert_displayed_text(supplementary_text, 0);
    assert(mzed_native_palette_v1_destroy(0, generation) == 0);
    generation += 1;
    assert(mzed_native_palette_v1_open(
        0, generation, commands, (int32_t)sizeof(commands),
        initial_integers, 5, initial_doubles, 17, 500.0, 40.0, 16.0) == 0);
    snapshot_current();
    const int32_t revision = snapshot_i32(snapshot, 4);
    assert(mzed_native_palette_v1_replace(
        0, generation - 1, 1, request + 1, 0, 0, 0,
        (const uint8_t *)"x", 1) == -5);
    assert(mzed_native_palette_v1_replace(
        0, generation, 2, request + 1, 0, 0, 0,
        (const uint8_t *)"x", 1) == -13);
    snapshot_current();
    assert(snapshot_i32(snapshot, 4) == revision);
    assert(mzed_native_palette_v1_destroy(0, generation) == 0);

    puts("supplementary, empty-commit/undo, marked-range, and stale-owner probes: PASS");
    return 0;
}
