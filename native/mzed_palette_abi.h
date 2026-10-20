// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef MZED_PALETTE_ABI_V1_H
#define MZED_PALETTE_ABI_V1_H
#include <stdint.h>

#define MZED_PALETTE_MAX_COMMAND_BYTES 25216
#define MZED_PALETTE_MAX_QUERY_BYTES 256
#define MZED_PALETTE_MAX_METRIC_INTS 517
#define MZED_PALETTE_MAX_METRIC_DOUBLES 2065
#define MZED_PALETTE_MAX_TEXT_BYTES 4096
#define MZED_PALETTE_MAX_SNAPSHOT_BYTES 16384
#define MZED_PALETTE_SNAPSHOT_HEADER_INTS 22
#define MZED_PALETTE_GEOMETRY_DOUBLE_COUNT 19

/*
 * Separate from native-island v1. All calls are serialized on one owner
 * thread. Every input/output pointer is borrowed only for the call; MoonBit
 * copies values before returning. No managed pointer is retained.
 *
 * Commands are repeated records: u16le id byte length, u16le label byte
 * length, u8 enabled, then UTF-8 id bytes and label bytes.
 * Metrics integers: line_count, unknown_glyph_count, caret_count, then
 * (utf16_offset, cursor_flag) pairs.
 * Metrics doubles: logical rect, ink rect, baseline, then per-caret strong
 * rect and weak rect. Each rect is x,y,width,height as f64.
 * Snapshot is a little-endian, counted byte record. Its 22 i32 header fields
 * are, by index: schema, generation, open_epoch, is_open, field_revision,
 * displayed_utf8_length, committed_utf8_length, selection_anchor_utf16,
 * selection_head_utf16, is_composing, marked_start_utf16, marked_end_utf16,
 * match_count, active_index, visible_start, row_count, effect_kind,
 * action_id_length, last_request, pending_request, pending_epoch,
 * pending_field_revision. Then come displayed UTF-8 bytes, committed UTF-8
 * bytes, action-id UTF-8 bytes, and row records. Each row record is five
 * little-endian i32 values (id byte length, label byte length, enabled,
 * selected, command index), followed by its UTF-8 id and label bytes.
 * effect_kind is 0 none, 1 composition cancelled, 2 closed, 3 field action.
 *
 * Geometry doubles are palette-local logical coordinates: field bounds,
 * content bounds, shared scroll offset, painted caret bounds, text-run origin,
 * and text-run bounds. Field bounds start at (0, 0); a host adds the modal's
 * window-local origin exactly once for screen-facing IME geometry. These are
 * not desktop-screen coordinates.
 *
 * Input kind is 1 key-down or 2 key-up. Key tags are 1 Enter, 2 Escape,
 * 3 Backspace, 4 Delete, 5 Tab, 6 Left, 7 Right, 8 Up, 9 Down, 10 Home,
 * 11 End, 12 PageUp, 13 PageDown, 14 Space, 15 Character. Modifier bits are
 * 0x1 Shift, 0x2 Control, 0x4 Alt, 0x8 Meta. Repeat is exactly 0 or 1.
 * Return values: 0 success, 1 layout requested, -1 uninitialized, -2 ABI,
 * -3 slot, -4 generation range, -5 stale generation, -6 not live, -7 request,
 * -9 invalid value, -13 stale epoch, -14 stale revision, -15 layout,
 * -16 shared model failure. Mutating submit calls return 0 on completion and
 * 1 only when the host must measure pending_text and stage a layout. The
 * snapshot and pending_text getters return copied byte counts; geometry
 * returns its copied double count (19). Positive getter counts are not
 * status codes.
 */
int32_t mzed_native_palette_v1_init(int32_t abi_version);
int32_t mzed_native_palette_v1_open(
    int32_t slot, int32_t generation,
    const uint8_t *commands, int32_t commands_length,
    const int32_t *metrics_ints, int32_t metrics_ints_length,
    const double *metrics_doubles, int32_t metrics_doubles_length,
    double bounds_width, double bounds_height, double font_size);
int32_t mzed_native_palette_v1_input(
    int32_t slot, int32_t generation, int32_t epoch, int32_t request,
    int32_t kind, int32_t key, int32_t modifier_bits, int32_t repeat,
    const uint8_t *text, int32_t text_length);
int32_t mzed_native_palette_v1_replace(
    int32_t slot, int32_t generation, int32_t epoch, int32_t request,
    int32_t range_present, int32_t range_start, int32_t range_end,
    const uint8_t *text, int32_t text_length);
int32_t mzed_native_palette_v1_mark(
    int32_t slot, int32_t generation, int32_t epoch, int32_t request,
    int32_t range_present, int32_t range_start, int32_t range_end,
    int32_t selection_present, int32_t selection_start, int32_t selection_end,
    const uint8_t *text, int32_t text_length);
int32_t mzed_native_palette_v1_cancel_composition(
    int32_t slot, int32_t generation, int32_t epoch, int32_t request);
int32_t mzed_native_palette_v1_unmark_composition(
    int32_t slot, int32_t generation, int32_t epoch, int32_t request);
int32_t mzed_native_palette_v1_pending_text(
    int32_t slot, int32_t generation, int32_t epoch, int32_t request,
    uint8_t *out, int32_t capacity);
int32_t mzed_native_palette_v1_stage_layout(
    int32_t slot, int32_t generation, int32_t epoch, int32_t request,
    int32_t expected_revision,
    const int32_t *metrics_ints, int32_t metrics_ints_length,
    const double *metrics_doubles, int32_t metrics_doubles_length);
int32_t mzed_native_palette_v1_continue(
    int32_t slot, int32_t generation, int32_t epoch, int32_t request,
    int32_t expected_revision);
int32_t mzed_native_palette_v1_snapshot(
    int32_t slot, int32_t generation, uint8_t *out, int32_t capacity);
int32_t mzed_native_palette_v1_geometry(
    int32_t slot, int32_t generation, double *out, int32_t capacity);
/* Resize retains query, selection, composition snapshot and history. */
int32_t mzed_native_palette_v1_resize(
    int32_t slot, int32_t generation, int32_t epoch, int32_t request,
    double width, double height);
int32_t mzed_native_palette_v1_finish_close(
    int32_t slot, int32_t generation, int32_t epoch);
/* Retire a measurement request without installing any candidate state. */
int32_t mzed_native_palette_v1_abort_pending(
    int32_t slot, int32_t generation, int32_t epoch, int32_t request);
/* Tear down an owner unconditionally; this never dispatches a saved action. */
int32_t mzed_native_palette_v1_destroy(int32_t slot, int32_t generation);

#endif
