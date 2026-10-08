// SPDX-License-Identifier: GPL-3.0-or-later
//! Copied-value facade for the shared MoonBit command palette.
//!
//! No MoonBit-managed address or Rust/OS handle crosses this boundary. Every
//! input is copied by the native ABI before returning, and every output is a
//! bounded byte/double record copied into Rust-owned values.

use std::{cell::RefCell, marker::PhantomData, rc::Rc};

mod ffi {
    unsafe extern "C" {
        pub fn mzed_native_palette_v1_init(version: i32) -> i32;
        pub fn mzed_native_palette_v1_open(
            slot: i32,
            generation: i32,
            commands: *const u8,
            commands_length: i32,
            metrics_ints: *const i32,
            metrics_ints_length: i32,
            metrics_doubles: *const f64,
            metrics_doubles_length: i32,
            bounds_width: f64,
            bounds_height: f64,
            font_size: f64,
        ) -> i32;
        pub fn mzed_native_palette_v1_input(
            slot: i32,
            generation: i32,
            epoch: i32,
            request: i32,
            kind: i32,
            key: i32,
            modifier_bits: i32,
            repeat: i32,
            text: *const u8,
            text_length: i32,
        ) -> i32;
        pub fn mzed_native_palette_v1_replace(
            slot: i32,
            generation: i32,
            epoch: i32,
            request: i32,
            range_present: i32,
            range_start: i32,
            range_end: i32,
            text: *const u8,
            text_length: i32,
        ) -> i32;
        pub fn mzed_native_palette_v1_mark(
            slot: i32,
            generation: i32,
            epoch: i32,
            request: i32,
            range_present: i32,
            range_start: i32,
            range_end: i32,
            selection_present: i32,
            selection_start: i32,
            selection_end: i32,
            text: *const u8,
            text_length: i32,
        ) -> i32;
        pub fn mzed_native_palette_v1_cancel_composition(
            slot: i32,
            generation: i32,
            epoch: i32,
            request: i32,
        ) -> i32;
        pub fn mzed_native_palette_v1_unmark_composition(
            slot: i32,
            generation: i32,
            epoch: i32,
            request: i32,
        ) -> i32;
        pub fn mzed_native_palette_v1_pending_text(
            slot: i32,
            generation: i32,
            epoch: i32,
            request: i32,
            out: *mut u8,
            capacity: i32,
        ) -> i32;
        pub fn mzed_native_palette_v1_stage_layout(
            slot: i32,
            generation: i32,
            epoch: i32,
            request: i32,
            expected_revision: i32,
            metrics_ints: *const i32,
            metrics_ints_length: i32,
            metrics_doubles: *const f64,
            metrics_doubles_length: i32,
        ) -> i32;
        pub fn mzed_native_palette_v1_continue(
            slot: i32,
            generation: i32,
            epoch: i32,
            request: i32,
            expected_revision: i32,
        ) -> i32;
        pub fn mzed_native_palette_v1_snapshot(
            slot: i32,
            generation: i32,
            out: *mut u8,
            capacity: i32,
        ) -> i32;
        pub fn mzed_native_palette_v1_geometry(
            slot: i32,
            generation: i32,
            out: *mut f64,
            capacity: i32,
        ) -> i32;
        pub fn mzed_native_palette_v1_resize(
            slot: i32,
            generation: i32,
            epoch: i32,
            request: i32,
            width: f64,
            height: f64,
        ) -> i32;
        pub fn mzed_native_palette_v1_finish_close(slot: i32, generation: i32, epoch: i32) -> i32;
        pub fn mzed_native_palette_v1_abort_pending(
            slot: i32,
            generation: i32,
            epoch: i32,
            request: i32,
        ) -> i32;
        pub fn mzed_native_palette_v1_destroy(slot: i32, generation: i32) -> i32;
    }
}

thread_local! {
    static SLOTS: RefCell<[(bool, i32); 4]> = const { RefCell::new([(false, 0); 4]) };
}

pub const PALETTE_MAX_TEXT_BYTES: usize = 4096;
pub const PALETTE_MAX_SNAPSHOT_BYTES: usize = 16_384;
pub const PALETTE_MAX_QUERY_BYTES: usize = 256;
pub const PALETTE_GEOMETRY_DOUBLE_COUNT: usize = 19;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PaletteRow {
    pub id: String,
    pub label: String,
    pub enabled: bool,
    pub selected: bool,
    pub command_index: i32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PaletteSnapshot {
    pub generation: i32,
    pub epoch: i32,
    pub is_open: bool,
    pub field_revision: i32,
    pub displayed: String,
    pub committed: String,
    pub anchor_utf16: i32,
    pub head_utf16: i32,
    pub composing: bool,
    pub marked: Option<std::ops::Range<usize>>,
    pub match_count: i32,
    pub active_index: Option<i32>,
    pub visible_start: i32,
    pub effect_kind: i32,
    pub action_id: Option<String>,
    pub last_request: i32,
    pub pending_request: i32,
    pub pending_epoch: i32,
    pub pending_field_revision: i32,
    pub rows: Vec<PaletteRow>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PaletteMetrics<'a> {
    pub ints: &'a [i32],
    pub doubles: &'a [f64],
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PendingLayout {
    slot: i32,
    generation: i32,
    pub request: i32,
    pub epoch: i32,
    pub expected_revision: i32,
}

#[derive(Debug)]
pub enum SubmitResult {
    Complete(PaletteSnapshot),
    NeedLayout {
        pending: PendingLayout,
        text: String,
    },
}

pub struct NativePalette {
    slot: i32,
    generation: i32,
    epoch: i32,
    request: i32,
    _owner_thread: PhantomData<Rc<()>>,
}

fn status(code: i32) -> Result<(), i32> {
    if code == 0 { Ok(()) } else { Err(code) }
}

fn checked_len(length: usize) -> Result<i32, i32> {
    i32::try_from(length).map_err(|_| -9)
}

fn checked_range(range: Option<std::ops::Range<usize>>) -> Result<(i32, i32, i32), i32> {
    match range {
        None => Ok((0, 0, 0)),
        Some(range) => Ok((
            1,
            i32::try_from(range.start).map_err(|_| -9)?,
            i32::try_from(range.end).map_err(|_| -9)?,
        )),
    }
}

fn encode_commands() -> Result<Vec<u8>, i32> {
    let commands = [
        (
            b"mzed.open-settings-file".as_slice(),
            b"Open Settings File".as_slice(),
        ),
        (
            b"mzed.toggle-full-screen".as_slice(),
            b"Toggle Full Screen".as_slice(),
        ),
    ];
    let mut output = Vec::new();
    for (id, label) in commands {
        let id_len = u16::try_from(id.len()).map_err(|_| -9)?;
        let label_len = u16::try_from(label.len()).map_err(|_| -9)?;
        output.extend_from_slice(&id_len.to_le_bytes());
        output.extend_from_slice(&label_len.to_le_bytes());
        output.push(1);
        output.extend_from_slice(id);
        output.extend_from_slice(label);
    }
    Ok(output)
}

impl NativePalette {
    pub fn new(
        metrics: PaletteMetrics<'_>,
        bounds_width: f64,
        bounds_height: f64,
        font_size: f64,
    ) -> Result<Self, i32> {
        let commands = encode_commands()?;
        Self::new_with_commands(&commands, metrics, bounds_width, bounds_height, font_size)
    }

    fn new_with_commands(
        commands: &[u8],
        metrics: PaletteMetrics<'_>,
        bounds_width: f64,
        bounds_height: f64,
        font_size: f64,
    ) -> Result<Self, i32> {
        super::native_island::claim_runtime_owner()?;
        status(unsafe { ffi::mzed_native_palette_v1_init(1) })?;
        let (slot, generation) = SLOTS.with(|slots| {
            let mut slots = slots.borrow_mut();
            let (index, state) = slots
                .iter_mut()
                .enumerate()
                .find(|(_, state)| !state.0 && state.1 < 1_000_000)
                .ok_or(-21)?;
            state.0 = true;
            state.1 += 1;
            Ok::<_, i32>((index as i32, state.1))
        })?;
        let code = unsafe {
            ffi::mzed_native_palette_v1_open(
                slot,
                generation,
                commands.as_ptr(),
                checked_len(commands.len())?,
                metrics.ints.as_ptr(),
                checked_len(metrics.ints.len())?,
                metrics.doubles.as_ptr(),
                checked_len(metrics.doubles.len())?,
                bounds_width,
                bounds_height,
                font_size,
            )
        };
        if let Err(error) = status(code) {
            SLOTS.with(|slots| slots.borrow_mut()[slot as usize].0 = false);
            return Err(error);
        }
        let mut owner = Self {
            slot,
            generation,
            epoch: 1,
            request: 0,
            _owner_thread: PhantomData,
        };
        let snapshot = owner.snapshot()?;
        owner.epoch = snapshot.epoch;
        Ok(owner)
    }

    pub fn slot(&self) -> i32 {
        self.slot
    }
    pub fn generation(&self) -> i32 {
        self.generation
    }
    pub fn epoch(&self) -> i32 {
        self.epoch
    }
    pub fn request(&self) -> i32 {
        self.request
    }

    fn next_request(&mut self) -> Result<i32, i32> {
        self.request = self
            .request
            .checked_add(1)
            .filter(|n| *n <= 1_000_000)
            .ok_or(-7)?;
        Ok(self.request)
    }

    fn finish_submit(&mut self, code: i32, request: i32) -> Result<SubmitResult, i32> {
        if code == 0 {
            let snapshot = self.snapshot()?;
            if snapshot.last_request != request || snapshot.pending_request != 0 {
                return Err(-7);
            }
            self.epoch = snapshot.epoch;
            Ok(SubmitResult::Complete(snapshot))
        } else if code == 1 {
            let snapshot = self.snapshot()?;
            if snapshot.pending_request != request
                || snapshot.pending_epoch != self.epoch
                || snapshot.pending_field_revision != snapshot.field_revision
            {
                return Err(-7);
            }
            let mut bytes = vec![0_u8; PALETTE_MAX_QUERY_BYTES];
            let count = unsafe {
                ffi::mzed_native_palette_v1_pending_text(
                    self.slot,
                    self.generation,
                    self.epoch,
                    request,
                    bytes.as_mut_ptr(),
                    checked_len(bytes.len())?,
                )
            };
            if count < 0 || count as usize > bytes.len() {
                return Err(if count < 0 { count } else { -9 });
            }
            bytes.truncate(count as usize);
            let text = String::from_utf8(bytes).map_err(|_| -9)?;
            if text.len() > PALETTE_MAX_QUERY_BYTES {
                return Err(-9);
            }
            Ok(SubmitResult::NeedLayout {
                pending: PendingLayout {
                    slot: self.slot,
                    generation: self.generation,
                    request,
                    epoch: self.epoch,
                    expected_revision: snapshot.pending_field_revision,
                },
                text,
            })
        } else {
            Err(code)
        }
    }

    pub fn input(
        &mut self,
        kind: i32,
        key: i32,
        modifier_bits: i32,
        repeat: bool,
        text: &str,
    ) -> Result<SubmitResult, i32> {
        if text.len() > PALETTE_MAX_TEXT_BYTES {
            return Err(-9);
        }
        let request = self.next_request()?;
        let code = unsafe {
            ffi::mzed_native_palette_v1_input(
                self.slot,
                self.generation,
                self.epoch,
                request,
                kind,
                key,
                modifier_bits,
                repeat as i32,
                text.as_ptr(),
                checked_len(text.len())?,
            )
        };
        self.finish_submit(code, request)
    }

    pub fn replace(
        &mut self,
        range: Option<std::ops::Range<usize>>,
        text: &str,
    ) -> Result<SubmitResult, i32> {
        if text.len() > PALETTE_MAX_TEXT_BYTES {
            return Err(-9);
        }
        let request = self.next_request()?;
        let (present, start, end) = checked_range(range)?;
        let code = unsafe {
            ffi::mzed_native_palette_v1_replace(
                self.slot,
                self.generation,
                self.epoch,
                request,
                present,
                start,
                end,
                text.as_ptr(),
                checked_len(text.len())?,
            )
        };
        self.finish_submit(code, request)
    }

    pub fn mark(
        &mut self,
        range: Option<std::ops::Range<usize>>,
        text: &str,
        selection: Option<std::ops::Range<usize>>,
    ) -> Result<SubmitResult, i32> {
        if text.len() > PALETTE_MAX_TEXT_BYTES {
            return Err(-9);
        }
        let request = self.next_request()?;
        let (range_present, range_start, range_end) = checked_range(range)?;
        let (selection_present, selection_start, selection_end) = checked_range(selection)?;
        let code = unsafe {
            ffi::mzed_native_palette_v1_mark(
                self.slot,
                self.generation,
                self.epoch,
                request,
                range_present,
                range_start,
                range_end,
                selection_present,
                selection_start,
                selection_end,
                text.as_ptr(),
                checked_len(text.len())?,
            )
        };
        self.finish_submit(code, request)
    }

    fn composition_op(&mut self, unmark: bool) -> Result<SubmitResult, i32> {
        let request = self.next_request()?;
        let code = unsafe {
            if unmark {
                ffi::mzed_native_palette_v1_unmark_composition(
                    self.slot,
                    self.generation,
                    self.epoch,
                    request,
                )
            } else {
                ffi::mzed_native_palette_v1_cancel_composition(
                    self.slot,
                    self.generation,
                    self.epoch,
                    request,
                )
            }
        };
        self.finish_submit(code, request)
    }

    pub fn cancel_composition(&mut self) -> Result<SubmitResult, i32> {
        self.composition_op(false)
    }
    pub fn unmark_composition(&mut self) -> Result<SubmitResult, i32> {
        self.composition_op(true)
    }

    pub fn pending_text(&self, pending: PendingLayout) -> Result<String, i32> {
        self.validate_pending_owner(pending)?;
        let mut bytes = vec![0_u8; PALETTE_MAX_QUERY_BYTES];
        let count = unsafe {
            ffi::mzed_native_palette_v1_pending_text(
                self.slot,
                self.generation,
                pending.epoch,
                pending.request,
                bytes.as_mut_ptr(),
                checked_len(bytes.len())?,
            )
        };
        if count < 0 || count as usize > bytes.len() {
            return Err(if count < 0 { count } else { -9 });
        }
        bytes.truncate(count as usize);
        String::from_utf8(bytes).map_err(|_| -9)
    }

    pub fn continue_with_layout(
        &mut self,
        pending: PendingLayout,
        metrics: PaletteMetrics<'_>,
    ) -> Result<SubmitResult, i32> {
        self.validate_pending_owner(pending)?;
        status(unsafe {
            ffi::mzed_native_palette_v1_stage_layout(
                self.slot,
                self.generation,
                pending.epoch,
                pending.request,
                pending.expected_revision,
                metrics.ints.as_ptr(),
                checked_len(metrics.ints.len())?,
                metrics.doubles.as_ptr(),
                checked_len(metrics.doubles.len())?,
            )
        })?;
        let code = unsafe {
            ffi::mzed_native_palette_v1_continue(
                self.slot,
                self.generation,
                pending.epoch,
                pending.request,
                pending.expected_revision,
            )
        };
        self.finish_submit(code, pending.request)
    }

    pub fn abort_pending(&mut self, pending: PendingLayout) -> Result<(), i32> {
        self.validate_pending_owner(pending)?;
        status(unsafe {
            ffi::mzed_native_palette_v1_abort_pending(
                self.slot,
                self.generation,
                pending.epoch,
                pending.request,
            )
        })
    }

    fn validate_pending_owner(&self, pending: PendingLayout) -> Result<(), i32> {
        validate_pending_owner_identity(self.slot, self.generation, pending)
    }

    pub fn resize(&mut self, width: f64, height: f64) -> Result<(), i32> {
        let request = self.next_request()?;
        status(unsafe {
            ffi::mzed_native_palette_v1_resize(
                self.slot,
                self.generation,
                self.epoch,
                request,
                width,
                height,
            )
        })?;
        Ok(())
    }

    pub fn snapshot(&self) -> Result<PaletteSnapshot, i32> {
        let mut bytes = vec![0_u8; PALETTE_MAX_SNAPSHOT_BYTES];
        let count = unsafe {
            ffi::mzed_native_palette_v1_snapshot(
                self.slot,
                self.generation,
                bytes.as_mut_ptr(),
                checked_len(bytes.len())?,
            )
        };
        if count < 0 || count as usize > bytes.len() {
            return Err(if count < 0 { count } else { -9 });
        }
        bytes.truncate(count as usize);
        decode_snapshot(&bytes, self.generation)
    }

    pub fn geometry(&self) -> Result<[f64; PALETTE_GEOMETRY_DOUBLE_COUNT], i32> {
        let mut output = [0.0; PALETTE_GEOMETRY_DOUBLE_COUNT];
        let count = unsafe {
            ffi::mzed_native_palette_v1_geometry(
                self.slot,
                self.generation,
                output.as_mut_ptr(),
                output.len() as i32,
            )
        };
        if count == PALETTE_GEOMETRY_DOUBLE_COUNT as i32
            && output.iter().all(|value| value.is_finite())
            && output[2] > 0.0
            && output[3] > 0.0
            && output[6] > 0.0
            && output[7] > 0.0
            && output[8] >= 0.0
            && output[11] >= 0.0
            && output[12] >= 0.0
        {
            Ok(output)
        } else {
            Err(if count < 0 { count } else { -9 })
        }
    }

    pub fn finish_close(&mut self, epoch: i32) -> Result<(), i32> {
        status(unsafe {
            ffi::mzed_native_palette_v1_finish_close(self.slot, self.generation, epoch)
        })
    }
}

fn validate_pending_owner_identity(
    slot: i32,
    generation: i32,
    pending: PendingLayout,
) -> Result<(), i32> {
    if pending.slot == slot && pending.generation == generation {
        Ok(())
    } else {
        Err(-5)
    }
}

impl Drop for NativePalette {
    fn drop(&mut self) {
        let code = unsafe { ffi::mzed_native_palette_v1_destroy(self.slot, self.generation) };
        if code != 0 {
            eprintln!("MZed palette destroy rejected: {code}");
        }
        SLOTS.with(|slots| slots.borrow_mut()[self.slot as usize].0 = false);
    }
}

fn read_i32(bytes: &[u8], offset: &mut usize) -> Result<i32, i32> {
    let end = offset.checked_add(4).ok_or(-9)?;
    let value = i32::from_le_bytes(
        bytes
            .get(*offset..end)
            .ok_or(-9)?
            .try_into()
            .map_err(|_| -9)?,
    );
    *offset = end;
    Ok(value)
}

fn read_string(bytes: &[u8], offset: &mut usize, length: usize) -> Result<String, i32> {
    let end = offset.checked_add(length).ok_or(-9)?;
    let value = String::from_utf8(bytes.get(*offset..end).ok_or(-9)?.to_vec()).map_err(|_| -9)?;
    *offset = end;
    Ok(value)
}

fn decode_snapshot(bytes: &[u8], expected_generation: i32) -> Result<PaletteSnapshot, i32> {
    let mut offset = 0;
    let mut header = [0_i32; 22];
    for value in &mut header {
        *value = read_i32(bytes, &mut offset)?;
    }
    if header[0] != 1
        || header[1] != expected_generation
        || header[2] <= 0
        || !(0..=1).contains(&header[3])
        || header[4] < 0
        || header[5] < 0
        || header[5] as usize > PALETTE_MAX_QUERY_BYTES
        || header[6] < 0
        || header[6] as usize > PALETTE_MAX_QUERY_BYTES
        || header[7] < 0
        || header[8] < 0
        || !(0..=1).contains(&header[9])
        || !(0..=128).contains(&header[12])
        || !(-1..header[12]).contains(&header[13])
        || header[14] < 0
        || header[14] > header[12]
        || !(0..=8).contains(&header[15])
        || !(0..=3).contains(&header[16])
        || !(0..=64).contains(&header[17])
        || header[18] < 0
        || header[19] < 0
        || header[20] < 0
        || header[21] < 0
    {
        return Err(-9);
    }
    if (header[10], header[11]) != (-1, -1) && (header[10] < 0 || header[11] < header[10]) {
        return Err(-9);
    }
    if (header[19] == 0 && (header[20] != 0 || header[21] != 0))
        || (header[19] > 0 && header[20] <= 0)
    {
        return Err(-9);
    }
    let displayed = read_string(bytes, &mut offset, header[5] as usize)?;
    let committed = read_string(bytes, &mut offset, header[6] as usize)?;
    let displayed_utf16 = utf16_len(&displayed);
    if header[7] as usize > displayed_utf16
        || header[8] as usize > displayed_utf16
        || !utf16_boundary(&displayed, header[7] as usize)
        || !utf16_boundary(&displayed, header[8] as usize)
        || (header[10] >= 0
            && (header[11] as usize > displayed_utf16
                || !utf16_boundary(&displayed, header[10] as usize)
                || !utf16_boundary(&displayed, header[11] as usize)))
        || (header[19] > 0
            && (header[20] != header[2] || header[19] <= header[18] || header[21] != header[4]))
        || header[15] > header[12] - header[14]
    {
        return Err(-9);
    }
    let action_raw = read_string(bytes, &mut offset, header[17] as usize)?;
    let mut rows = Vec::with_capacity(header[15] as usize);
    let mut selected_rows = 0;
    for _ in 0..header[15] {
        let id_len = read_i32(bytes, &mut offset)?;
        let label_len = read_i32(bytes, &mut offset)?;
        let enabled = read_i32(bytes, &mut offset)?;
        let selected = read_i32(bytes, &mut offset)?;
        let command_index = read_i32(bytes, &mut offset)?;
        if !(1..=64).contains(&id_len)
            || !(1..=128).contains(&label_len)
            || !(0..=1).contains(&enabled)
            || !(0..=1).contains(&selected)
            || command_index < 0
        {
            return Err(-9);
        }
        selected_rows += selected;
        if command_index < header[14] || command_index >= header[14] + header[15] {
            return Err(-9);
        }
        rows.push(PaletteRow {
            id: read_string(bytes, &mut offset, id_len as usize)?,
            label: read_string(bytes, &mut offset, label_len as usize)?,
            enabled: enabled == 1,
            selected: selected == 1,
            command_index,
        });
    }
    if offset != bytes.len() {
        return Err(-9);
    }
    if selected_rows > 1
        || rows.iter().any(|row| {
            row.selected != (Some(row.command_index) == (header[13] >= 0).then_some(header[13]))
        })
    {
        return Err(-9);
    }
    Ok(PaletteSnapshot {
        generation: header[1],
        epoch: header[2],
        is_open: header[3] == 1,
        field_revision: header[4],
        displayed,
        committed,
        anchor_utf16: header[7],
        head_utf16: header[8],
        composing: header[9] == 1,
        marked: if header[10] >= 0 && header[11] >= header[10] {
            Some(header[10] as usize..header[11] as usize)
        } else {
            None
        },
        match_count: header[12],
        active_index: (header[13] >= 0).then_some(header[13]),
        visible_start: header[14],
        effect_kind: header[16],
        action_id: (!action_raw.is_empty()).then_some(action_raw),
        last_request: header[18],
        pending_request: header[19],
        pending_epoch: header[20],
        pending_field_revision: header[21],
        rows,
    })
}

fn utf16_len(text: &str) -> usize {
    text.encode_utf16().count()
}

fn utf16_boundary(text: &str, target: usize) -> bool {
    let mut offset = 0;
    if target == 0 {
        return true;
    }
    for scalar in text.chars() {
        offset += scalar.len_utf16();
        if offset == target {
            return true;
        }
        if offset > target {
            return false;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_wire_is_bounded_and_uses_only_allowlisted_ids() {
        let bytes = encode_commands().unwrap();
        assert!(bytes.len() < 25216);
        assert!(
            bytes
                .windows(b"mzed.open-settings-file".len())
                .any(|w| w == b"mzed.open-settings-file")
        );
        assert!(
            bytes
                .windows(b"mzed.toggle-full-screen".len())
                .any(|w| w == b"mzed.toggle-full-screen")
        );
    }

    #[test]
    fn snapshot_decoder_rejects_stale_schema_generation_and_trailing_data() {
        let mut header = [0_i32; 22];
        header[..4].copy_from_slice(&[1, 7, 2, 1]);
        header[3] = 1;
        header[10] = -1;
        header[11] = -1;
        header[13] = -1;
        let mut bytes = Vec::new();
        for value in header {
            bytes.extend_from_slice(&value.to_le_bytes());
        }
        assert!(decode_snapshot(&bytes, 7).is_ok());
        assert_eq!(decode_snapshot(&bytes, 8).unwrap_err(), -9);
        bytes.push(0);
        assert_eq!(decode_snapshot(&bytes, 7).unwrap_err(), -9);
    }

    #[test]
    fn pending_layout_is_bound_to_the_exact_slot_generation() {
        let token = PendingLayout {
            slot: 1,
            generation: 7,
            request: 1,
            epoch: 1,
            expected_revision: 1,
        };
        assert_eq!(validate_pending_owner_identity(1, 8, token), Err(-5));
        let live_token = PendingLayout {
            generation: 8,
            ..token
        };
        assert_eq!(validate_pending_owner_identity(1, 8, live_token), Ok(()));
    }

    #[test]
    fn snapshot_decoder_rejects_bad_row_count_before_allocating() {
        let mut header = [0_i32; 22];
        header[..4].copy_from_slice(&[1, 7, 2, 1]);
        header[13] = -1;
        header[10] = -1;
        header[11] = -1;
        header[15] = -1;
        let mut bytes = Vec::new();
        for value in header {
            bytes.extend_from_slice(&value.to_le_bytes());
        }
        assert!(std::panic::catch_unwind(|| decode_snapshot(&bytes, 7)).is_ok());
        assert_eq!(decode_snapshot(&bytes, 7).unwrap_err(), -9);
    }
}
