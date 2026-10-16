// SPDX-License-Identifier: GPL-3.0-or-later
#define main palette_abi_baseline_main
#include "native_palette_abi.c"
#undef main

static const int32_t slot = 0;
static const int32_t epoch = 1;
static int32_t generation = 1;
static int32_t request = 0;
static uint8_t snapshot[MZED_PALETTE_MAX_SNAPSHOT_BYTES];
static const uint8_t japanese[] = {0xe3, 0x81, 0xab};
static const uint8_t ascii[] = {'a'};
static const uint8_t no_text[] = {0};

static int scalar_count_utf8(const uint8_t *text, int32_t length) {
    int32_t offset = 0;
    int count = 0;
    while (offset < length) {
        const uint8_t first = text[offset];
        const int32_t width = first < 0x80 ? 1 : first < 0xe0 ? 2 : first < 0xf0 ? 3 : 4;
        assert(offset + width <= length);
        offset += width;
        ++count;
    }
    return count;
}

static int32_t snapshot_current(void) {
    const int32_t length = mzed_native_palette_v1_snapshot(
        slot, generation, snapshot, sizeof(snapshot));
    assert(length >= MZED_PALETTE_SNAPSHOT_HEADER_INTS * 4);
    return length;
}

static int32_t complete_layout_if_needed(int32_t status, int32_t op_request) {
    if (status != 1) {
        return status;
    }

    uint8_t pending[MZED_PALETTE_MAX_QUERY_BYTES];
    const int32_t text_length = mzed_native_palette_v1_pending_text(
        slot, generation, epoch, op_request, pending, sizeof(pending));
    assert(text_length >= 0);
    const int count = scalar_count_utf8(pending, text_length);
    int32_t integers[MZED_PALETTE_MAX_METRIC_INTS];
    double doubles[MZED_PALETTE_MAX_METRIC_DOUBLES];
    write_metrics(count, 0, integers, doubles);

    // The Japanese profile used here has one UTF-16 unit per scalar. Keep the
    // test's staged caret offsets explicit so the measured document is exact.
    for (int32_t caret = 0; caret <= count; ++caret) {
        integers[3 + caret * 2] = caret;
        integers[4 + caret * 2] = 1;
    }
    snapshot_current();
    const int32_t revision = snapshot_i32(snapshot, 4);
    assert(mzed_native_palette_v1_stage_layout(
        slot, generation, epoch, op_request, revision,
        integers, 3 + (count + 1) * 2,
        doubles, 9 + (count + 1) * 8) == 0);
    return mzed_native_palette_v1_continue(
        slot, generation, epoch, op_request, revision);
}

static int32_t send_mark(const uint8_t *text, int32_t text_length) {
    const int32_t op_request = ++request;
    const int32_t status = mzed_native_palette_v1_mark(
        slot, generation, epoch, op_request,
        0, 0, 0, 1, 1, 1, text, text_length);
    return complete_layout_if_needed(status, op_request);
}

static int32_t replace_text(const uint8_t *text, int32_t text_length) {
    const int32_t op_request = ++request;
    const int32_t status = mzed_native_palette_v1_replace(
        slot, generation, epoch, op_request,
        0, 0, 0, text, text_length);
    return complete_layout_if_needed(status, op_request);
}

static int32_t send_key(int32_t kind, int32_t key, int32_t modifiers, int32_t repeat) {
    const int32_t op_request = ++request;
    const int32_t status = mzed_native_palette_v1_input(
        slot, generation, epoch, op_request,
        kind, key, modifiers, repeat, no_text, 0);
    return complete_layout_if_needed(status, op_request);
}

static void assert_open_composing(int32_t is_open, int32_t composing) {
    snapshot_current();
    assert(snapshot_i32(snapshot, 3) == is_open);
    assert(snapshot_i32(snapshot, 9) == composing);
}

static void open_palette(void) {
    const uint8_t commands[] = {
        7, 0, 3, 0, 1,
        't', 'e', 's', 't', '.', 'j', 'p',
        0xe3, 0x81, 0xab,
    };
    int32_t integers[5];
    double doubles[17];
    write_metrics(0, 0, integers, doubles);
    assert(mzed_native_palette_v1_open(
        slot, generation, commands, sizeof(commands), integers, 5,
        doubles, 17, 500.0, 40.0, 16.0) == 0);
}

static void open_ascii_palette(void) {
    const uint8_t commands[] = {
        10, 0, 1, 0, 1,
        't', 'e', 's', 't', '.', 'a', 's', 'c', 'i', 'i', 'a',
    };
    int32_t integers[5];
    double doubles[17];
    write_metrics(0, 0, integers, doubles);
    assert(mzed_native_palette_v1_open(
        slot, generation, commands, sizeof(commands), integers, 5,
        doubles, 17, 500.0, 40.0, 16.0) == 0);
}

int main(void) {
    int32_t initial_integers[5];
    double initial_doubles[17];
    write_metrics(0, 0, initial_integers, initial_doubles);
    assert(mzed_native_palette_v1_init(1) == 0);
    open_palette();

    // Enter starts during Japanese preedit. The host retains the native owner,
    // consumes repeats while waiting, and does not send shared input until the
    // matching physical release. Mozc commits before that release.
    assert(send_mark(japanese, sizeof(japanese)) == 0);
    assert_open_composing(1, 1);
    assert(replace_text(japanese, sizeof(japanese)) == 0);
    assert_open_composing(1, 0);
    assert(snapshot_i32(snapshot, 12) == 1);
    assert(snapshot_i32(snapshot, 13) == 0);
    // Delayed press uses repeat=true because the physical sequence began while
    // composing. Its paired release clears the shared composition-enter latch.
    assert(send_key(1, 1, 0, 1) == 0);
    assert_open_composing(1, 0);
    assert(snapshot_i32(snapshot, 16) == 0);
    assert(send_key(2, 1, 0, 0) == 0);
    assert_open_composing(1, 0);

    // Control was present on Enter Down, then released before Enter Up. The
    // delayed shared press must use captured Down modifiers and stay open.
    assert(send_key(1, 1, 2, 0) == 0);
    assert_open_composing(1, 0);
    assert(send_key(2, 1, 0, 0) == 0);
    assert_open_composing(1, 0);

    // A fresh Enter selects the active row. Keep the terminal press snapshot
    // for the action token; the physical release clears only the effect tag.
    assert(send_key(1, 1, 0, 0) == 0);
    assert_open_composing(0, 0);
    assert(snapshot_i32(snapshot, 16) == 2);
    assert(snapshot_i32(snapshot, 17) == 7);
    const int32_t terminal_snapshot_length = snapshot_current();
    const size_t action_offset = 88u + (size_t)snapshot_i32(snapshot, 5)
        + (size_t)snapshot_i32(snapshot, 6);
    assert(action_offset + 7u <= (size_t)terminal_snapshot_length);
    assert(memcmp(snapshot + action_offset, "test.jp", 7) == 0);
    assert(send_key(2, 1, 0, 0) == 0);
    snapshot_current();
    assert(snapshot_i32(snapshot, 16) == 0);
    assert(memcmp(snapshot + action_offset, "test.jp", 7) == 0);
    assert(mzed_native_palette_v1_finish_close(slot, generation, epoch) == 0);
    snapshot_current();
    assert(snapshot_i32(snapshot, 17) == 0);
    assert(mzed_native_palette_v1_destroy(slot, generation) == 0);

    // The opt-in Wayland input-handler preference routes this one-byte ASCII
    // commit through InsertText while the physical composing Enter is still
    // pending. It must reach the shared owner before the matching key-up.
    generation = 2;
    request = 0;
    open_ascii_palette();
    assert(send_mark(ascii, sizeof(ascii)) == 0);
    assert_open_composing(1, 1);
    assert(replace_text(ascii, sizeof(ascii)) == 0);
    assert_open_composing(1, 0);
    assert(snapshot_i32(snapshot, 12) == 1);
    assert(snapshot_i32(snapshot, 13) == 0);
    assert(send_key(1, 1, 0, 1) == 0);
    assert_open_composing(1, 0);
    assert(send_key(2, 1, 0, 0) == 0);
    assert_open_composing(1, 0);
    assert(send_key(1, 1, 0, 0) == 0);
    assert_open_composing(0, 0);
    assert(snapshot_i32(snapshot, 17) == 10);
    const size_t ascii_action_offset = 88u + (size_t)snapshot_i32(snapshot, 5)
        + (size_t)snapshot_i32(snapshot, 6);
    assert(memcmp(snapshot + ascii_action_offset, "test.ascii", 10) == 0);
    assert(mzed_native_palette_v1_finish_close(slot, generation, epoch) == 0);
    assert(mzed_native_palette_v1_destroy(slot, generation) == 0);

    // Escape begun during preedit cancels once on its release. Cancellation
    // may request a new layout for the restored empty document; the host must
    // complete that request before releasing the physical key sequence.
    generation = 3;
    request = 0;
    open_palette();
    assert(send_mark(japanese, sizeof(japanese)) == 0);
    assert_open_composing(1, 1);
    assert(send_key(1, 2, 0, 0) == 0);
    assert_open_composing(1, 0);
    assert(snapshot_i32(snapshot, 16) == 1);
    assert(send_key(2, 2, 0, 0) == 0);
    assert_open_composing(1, 0);
    assert(send_key(1, 2, 0, 0) == 0);
    assert_open_composing(0, 0);
    assert(snapshot_i32(snapshot, 16) == 2);
    assert(mzed_native_palette_v1_finish_close(slot, generation, epoch) == 0);
    assert(mzed_native_palette_v1_destroy(slot, generation) == 0);

    puts("delayed Enter/Escape release, IME commit/cancel, repeat guard, modifiers, and one-shot action: PASS");
    return 0;
}
