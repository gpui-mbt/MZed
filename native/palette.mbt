// SPDX-License-Identifier: GPL-3.0-or-later
///|
/// Independent copied-value ABI for the shared MoonBit command palette.
/// Existing native-island v1 symbols and layouts remain unchanged.

const PALETTE_SLOT_COUNT : Int = 4
const PALETTE_MAX_GENERATION : Int = 1_000_000
const PALETTE_MAX_REQUEST : Int = 1_000_000
// 128 records * (5-byte header + 64-byte ID + 128-byte label).
const PALETTE_MAX_COMMAND_BYTES : Int = 25_216
const PALETTE_MAX_SNAPSHOT_BYTES : Int = 16_384
const PALETTE_GEOMETRY_DOUBLE_COUNT : Int = 19

const PALETTE_OK : Int = 0
const PALETTE_NEED_LAYOUT : Int = 1
const PALETTE_ERR_ABI : Int = -2
const PALETTE_ERR_SLOT : Int = -3
const PALETTE_ERR_GENERATION : Int = -4
const PALETTE_ERR_STALE : Int = -5
const PALETTE_ERR_NOT_LIVE : Int = -6
const PALETTE_ERR_REQUEST : Int = -7
const PALETTE_ERR_VALUE : Int = -9
const PALETTE_ERR_STALE_EPOCH : Int = -13
const PALETTE_ERR_STALE_REVISION : Int = -14
const PALETTE_ERR_LAYOUT : Int = -15
const PALETTE_ERR_MODEL : Int = -16

///|
priv enum PaletteOperation {
  Input(@primitives.InputEvent)
  Replace(range~ : @text.TextRange?, text~ : String)
  Mark(range~ : @text.TextRange?, text~ : String, anchor~ : Int, head~ : Int)
  CancelComposition
  UnmarkComposition
}

///|
priv struct PaletteSlot {
  mut generation : Int
  mut live : Bool
  mut palette : @command_palette.CommandPalette?
  mut tree : Ref[@element.ElementTree]?
  mut epoch : Int
  mut last_request : Int
  mut pending_request : Int
  mut pending_epoch : Int
  mut pending_revision : Int
  mut pending_operation : PaletteOperation?
  mut pending_measure_text : String?
  mut pending_layout_text : String?
  mut pending_layout_measurement : @text_layout.TextMeasurement?
  mut cached_text : String?
  mut cached_measurement : @text_layout.TextMeasurement?
  mut last_effect : @command_palette.PaletteEffect
  mut action_id : String?
}

///|
fn palette_new_slot() -> PaletteSlot {
  {
    generation: 0,
    live: false,
    palette: None,
    tree: None,
    epoch: 0,
    last_request: 0,
    pending_request: 0,
    pending_epoch: 0,
    pending_revision: 0,
    pending_operation: None,
    pending_measure_text: None,
    pending_layout_text: None,
    pending_layout_measurement: None,
    cached_text: None,
    cached_measurement: None,
    last_effect: @command_palette.None,
    action_id: None,
  }
}

///|
let palette_slots : Array[PaletteSlot] = [
  palette_new_slot(),
  palette_new_slot(),
  palette_new_slot(),
  palette_new_slot(),
]

///|
fn palette_valid_slot(slot : Int) -> Bool {
  slot >= 0 && slot < PALETTE_SLOT_COUNT
}

///|
fn palette_valid_generation(generation : Int) -> Bool {
  generation >= 1 && generation <= PALETTE_MAX_GENERATION
}

///|
fn palette_validate_live(slot : Int, generation : Int) -> Int {
  if !palette_valid_slot(slot) {
    PALETTE_ERR_SLOT
  } else if !palette_valid_generation(generation) {
    PALETTE_ERR_GENERATION
  } else {
    let state = palette_slots[slot]
    if state.generation != generation {
      PALETTE_ERR_STALE
    } else if !state.live {
      PALETTE_ERR_NOT_LIVE
    } else {
      PALETTE_OK
    }
  }
}

///|
fn palette_error_code(_error : @command_palette.PaletteError) -> Int {
  PALETTE_ERR_MODEL
}

///|
fn palette_rebase_measurement(
  document : @text.TextDocument,
  source : @text_layout.TextMeasurement,
) -> Result[@text_layout.TextMeasurement, @text_field.TextFieldError] {
  if source.document().text() != document.text() {
    return Err(@text_field.UnsupportedLayout)
  }
  match
    @text_layout.TextMeasurement::new(
      document,
      source.logical_rect(),
      source.ink_rect(),
      source.first_line_baseline(),
      source.line_count(),
      source.unknown_glyph_count(),
      source.carets(),
    ) {
    Ok(measurement) => Ok(measurement)
    Err(_) => Err(@text_field.UnsupportedLayout)
  }
}

///|
fn palette_measure(
  state : PaletteSlot,
  document : @text.TextDocument,
) -> Result[@text_layout.TextMeasurement, @text_field.TextFieldError] {
  let text_length = match document.utf8_length() {
    Ok(value) => value
    Err(_) => return Err(@text_field.MalformedText)
  }
  if text_length > @command_palette.MAX_QUERY_BYTES {
    return Err(@text_field.TextTooLarge)
  }
  match (state.pending_layout_text, state.pending_layout_measurement) {
    (Some(text), Some(measurement)) if text == document.text() =>
      return palette_rebase_measurement(document, measurement)
    _ => ()
  }
  match state.cached_text {
    Some(text) if text == document.text() =>
      match state.cached_measurement {
        Some(measurement) if measurement.document().text() == text =>
          palette_rebase_measurement(document, measurement)
        _ => {
          state.pending_measure_text = Some(document.text())
          Err(@text_field.MeasurementFailed)
        }
      }
    _ => {
      state.pending_measure_text = Some(document.text())
      Err(@text_field.MeasurementFailed)
    }
  }
}

///|
/// The bounded host profile admits only geometry copied from the exact GPUI
/// shape request. Raster failure is still checked by the host paint path.
fn palette_admit(
  state : PaletteSlot,
  document : @text.TextDocument,
  measurement : @text_layout.TextMeasurement,
) -> Result[Unit, @text_field.TextFieldError] {
  match state.cached_text {
    Some(text) if text == document.text() && measurement.document() == document =>
      Ok(())
    _ =>
      match state.pending_layout_text {
        Some(text) if text == document.text() && measurement.document() == document =>
          Ok(())
        _ => Err(@text_field.AdmissionFailed)
      }
  }
}

///|
fn palette_hit(
  _document : @text.TextDocument,
  _point : @primitives.Point,
) -> Result[@text_layout.TextHit, @text_field.TextFieldError] {
  Err(@text_field.HitTestFailed)
}

///|
fn palette_decode_string(
  input : FixedArray[Byte],
  start : Int,
  length : Int,
) -> String? {
  if start < 0 || length < 0 || length > PALETTE_MAX_COMMAND_BYTES {
    return None
  }
  let bytes = Bytes::makei(length, index => input[start + index])
  try Some(@utf8.decode(bytes)) catch {
    _ => None
  }
}

///|
fn palette_read_u16(input : FixedArray[Byte], index : Int) -> Int {
  input[index].to_int() | (input[index + 1].to_int() << 8)
}

///|
fn palette_make_binding(
  id : String,
  label : String,
) -> Result[@capability.GuiBinding[Unit, Unit], Int] {
  let descriptor = match @capability.Descriptor::new(
    id,
    label,
    "MZed allowlisted command",
    @capability.Command,
    @capability.Schema::unit(),
    @capability.Schema::unit(),
    @capability.StateChange,
    false,
    @capability.Local,
    None,
    1,
  ) {
    Ok(value) => value
    Err(_) => return Err(PALETTE_ERR_VALUE)
  }
  Ok(@capability.TypedCapability::new(
    descriptor,
    _ => Ok(()),
    _ => Ok(@capability.Value::unit()),
    _ => Ok(()),
    () => Ok(()),
    _ => Ok(()),
  ).gui_binding())
}

///|
fn palette_parse_commands(
  input : FixedArray[Byte],
  input_length : Int,
) -> Result[Array[@command_palette.PaletteCommand], Int] {
  if input_length < 0 || input_length > PALETTE_MAX_COMMAND_BYTES {
    return Err(PALETTE_ERR_VALUE)
  }
  let commands : Array[@command_palette.PaletteCommand] = []
  let mut offset = 0
  while offset < input_length {
    if input_length - offset < 5 || commands.length() >= @command_palette.MAX_COMMANDS {
      return Err(PALETTE_ERR_VALUE)
    }
    let id_length = palette_read_u16(input, offset)
    let label_length = palette_read_u16(input, offset + 2)
    let enabled = input[offset + 4].to_int()
    offset += 5
    if id_length <= 0 || id_length > @command_palette.MAX_COMMAND_ID_BYTES ||
      label_length <= 0 || label_length > @command_palette.MAX_COMMAND_LABEL_BYTES ||
      (enabled != 0 && enabled != 1) ||
      id_length + label_length > input_length - offset {
      return Err(PALETTE_ERR_VALUE)
    }
    let id = match palette_decode_string(input, offset, id_length) {
      Some(value) => value
      None => return Err(PALETTE_ERR_VALUE)
    }
    offset += id_length
    let label = match palette_decode_string(input, offset, label_length) {
      Some(value) => value
      None => return Err(PALETTE_ERR_VALUE)
    }
    offset += label_length
    let metadata = match
      @command_palette.CommandMetadata::new(id, label, enabled == 1) {
      Ok(value) => value
      Err(_) => return Err(PALETTE_ERR_VALUE)
    }
    let binding = match palette_make_binding(id, label) {
      Ok(value) => value
      Err(error) => return Err(error)
    }
    commands.push(@command_palette.PaletteCommand::new(metadata, binding))
  }
  Ok(commands)
}

///|
fn palette_rect(
  values : FixedArray[Double],
  offset : Int,
) -> Result[@primitives.Rect, Int] {
  if offset < 0 {
    return Err(PALETTE_ERR_LAYOUT)
  }
  match
    @primitives.Rect::new(
      values[offset],
      values[offset + 1],
      values[offset + 2],
      values[offset + 3],
    ) {
    Ok(value) => Ok(value)
    Err(_) => Err(PALETTE_ERR_LAYOUT)
  }
}

///|
/// Integers: line_count, unknown_glyph_count, caret_count, then
/// (utf16_offset, is_cursor_position) for each scalar boundary.
/// Doubles: logical rect, ink rect, baseline, then strong and weak rects.
fn palette_make_measurement(
  document : @text.TextDocument,
  ints : FixedArray[Int],
  ints_length : Int,
  doubles : FixedArray[Double],
  doubles_length : Int,
) -> Result[@text_layout.TextMeasurement, Int] {
  if ints_length < 3 || doubles_length < 9 {
    return Err(PALETTE_ERR_LAYOUT)
  }
  let line_count = ints[0]
  let unknown_glyph_count = ints[1]
  let caret_count = ints[2]
  if caret_count < 1 || caret_count > @command_palette.MAX_QUERY_BYTES + 1 ||
    ints_length != 3 + caret_count * 2 ||
    doubles_length != 9 + caret_count * 8 {
    return Err(PALETTE_ERR_LAYOUT)
  }
  let logical = match palette_rect(doubles, 0) {
    Ok(value) => value
    Err(error) => return Err(error)
  }
  let ink = match palette_rect(doubles, 4) {
    Ok(value) => value
    Err(error) => return Err(error)
  }
  let carets : Array[@text_layout.CaretGeometry] = []
  for index = 0; index < caret_count; index = index + 1 {
    let int_offset = 3 + index * 2
    let double_offset = 9 + index * 8
    let cursor_flag = ints[int_offset + 1]
    if cursor_flag != 0 && cursor_flag != 1 {
      return Err(PALETTE_ERR_LAYOUT)
    }
    let strong = match palette_rect(doubles, double_offset) {
      Ok(value) => value
      Err(error) => return Err(error)
    }
    let weak = match palette_rect(doubles, double_offset + 4) {
      Ok(value) => value
      Err(error) => return Err(error)
    }
    let caret = match
      @text_layout.CaretGeometry::new(
        ints[int_offset],
        cursor_flag == 1,
        strong,
        weak,
      ) {
      Ok(value) => value
      Err(_) => return Err(PALETTE_ERR_LAYOUT)
    }
    carets.push(caret)
  }
  match
    @text_layout.TextMeasurement::new(
      document,
      logical,
      ink,
      doubles[8],
      line_count,
      unknown_glyph_count,
      carets,
    ) {
    Ok(value) => Ok(value)
    Err(_) => Err(PALETTE_ERR_LAYOUT)
  }
}

///|
#export_name("mzed_mb_palette_v1_init")
pub fn mzed_mb_palette_v1_init(version : Int) -> Int {
  if version == 1 { PALETTE_OK } else { PALETTE_ERR_ABI }
}

///|
#export_name("mzed_mb_palette_v1_open")
pub fn mzed_mb_palette_v1_open(
  slot : Int,
  generation : Int,
  commands : FixedArray[Byte],
  commands_length : Int,
  metrics_ints : FixedArray[Int],
  metrics_ints_length : Int,
  metrics_doubles : FixedArray[Double],
  metrics_doubles_length : Int,
  bounds_width : Double,
  bounds_height : Double,
  font_size : Double,
) -> Int {
  if !palette_valid_slot(slot) {
    return PALETTE_ERR_SLOT
  }
  if !palette_valid_generation(generation) {
    return PALETTE_ERR_GENERATION
  }
  if bounds_width.is_nan() || bounds_width.is_inf() ||
    bounds_height.is_nan() || bounds_height.is_inf() ||
    font_size.is_nan() || font_size.is_inf() || bounds_width < 32.0 ||
    bounds_width > 2048.0 || bounds_height < 24.0 ||
    bounds_height > 128.0 || font_size < 6.0 || font_size > 32.0 {
    return PALETTE_ERR_VALUE
  }
  let state = palette_slots[slot]
  if state.live {
    return PALETTE_ERR_NOT_LIVE
  }
  if generation <= state.generation {
    return PALETTE_ERR_STALE
  }
  let command_values = match palette_parse_commands(commands, commands_length) {
    Ok(value) => value
    Err(error) => return error
  }
  let initial_document = match @text.TextDocument::new("") {
    Ok(value) => value
    Err(_) => return PALETTE_ERR_VALUE
  }
  let initial_measurement = match
    palette_make_measurement(
      initial_document,
      metrics_ints,
      metrics_ints_length,
      metrics_doubles,
      metrics_doubles_length,
    ) {
    Ok(value) => value
    Err(error) => return error
  }
  let bounds = match
    @primitives.Rect::new(0.0, 0.0, bounds_width, bounds_height) {
    Ok(value) => value
    Err(_) => return PALETTE_ERR_VALUE
  }
  state.cached_text = Some("")
  state.cached_measurement = Some(initial_measurement)
  state.pending_measure_text = None
  state.pending_layout_text = None
  state.pending_layout_measurement = None
  let field = match
    @text_field.TextField::new(
      generation.to_uint64() * 8 + slot.to_uint64() + 1,
      bounds,
      font_size,
      initial_document,
      document => palette_measure(state, document),
      (document, measurement) => palette_admit(state, document, measurement),
    ) {
    Ok(value) => value
    Err(_) => {
      state.cached_text = None
      state.cached_measurement = None
      return PALETTE_ERR_LAYOUT
    }
  }
  let palette = match @command_palette.CommandPalette::new(field, command_values) {
    Ok(value) => value
    Err(error) => {
      state.cached_text = None
      state.cached_measurement = None
      return palette_error_code(error)
    }
  }
  let viewport = match
    @primitives.Rect::new(0.0, 0.0, bounds_width, bounds_height) {
    Ok(value) => value
    Err(_) => return PALETTE_ERR_VALUE
  }
  let tree = Ref(
    match
      @element.ElementTree::new([
        @element.ElementNode::new(1, None, viewport).with_focusable(true),
        field.element_node(Some(1)),
      ]) {
      Ok(value) =>
        match value.with_focus(1) {
          Ok(focused) => focused
          Err(_) => return PALETTE_ERR_MODEL
        }
      Err(_) => return PALETTE_ERR_MODEL
    },
  )
  let palette = palette
  match palette.open(tree) {
    Ok(_) => ()
    Err(error) => return palette_error_code(error)
  }
  state.generation = generation
  state.live = true
  state.epoch = palette.open_epoch()
  state.last_request = 0
  state.pending_request = 0
  state.pending_epoch = 0
  state.pending_revision = 0
  state.pending_operation = None
  state.pending_measure_text = None
  state.pending_layout_text = None
  state.pending_layout_measurement = None
  state.last_effect = @command_palette.None
  state.tree = Some(tree)
  state.palette = Some(palette)
  PALETTE_OK
}

///|
fn palette_key(key : Int, text : String) -> @primitives.Key? {
  match key {
    1 => Some(@primitives.Enter)
    2 => Some(@primitives.Escape)
    3 => Some(@primitives.Backspace)
    4 => Some(@primitives.Delete)
    5 => Some(@primitives.Tab)
    6 => Some(@primitives.ArrowLeft)
    7 => Some(@primitives.ArrowRight)
    8 => Some(@primitives.ArrowUp)
    9 => Some(@primitives.ArrowDown)
    10 => Some(@primitives.Home)
    11 => Some(@primitives.End)
    12 => Some(@primitives.PageUp)
    13 => Some(@primitives.PageDown)
    14 => Some(@primitives.Space)
    15 if text.length() > 0 => Some(@primitives.Character(text))
    _ => None
  }
}

///|
fn palette_modifiers(bits : Int) -> @primitives.Modifiers? {
  if bits < 0 || bits > 15 {
    None
  } else {
    Some(
      @primitives.Modifiers::new(
        shift=(bits & 1) != 0,
        control=(bits & 2) != 0,
        alt=(bits & 4) != 0,
        meta=(bits & 8) != 0,
      ),
    )
  }
}

///|
fn palette_range_is_visible(field : @text_field.TextField, range : @text.TextRange) -> Bool {
  if field.is_composing() {
    match field.marked_range() {
      Some(marked) if marked == range => true
      None => field.selection().normalized_range() == range
      _ => false
    }
  } else {
    field.selection().normalized_range() == range
  }
}

///|
fn palette_install_field(
  palette : @command_palette.CommandPalette,
  candidate : @text_field.TextField,
) -> Result[Unit, @command_palette.PaletteError] {
  palette.install_field_at(palette.open_epoch(), candidate)
}

///|
fn palette_run_operation(state : PaletteSlot, request : Int) -> Int {
  let palette = match state.palette {
    Some(value) => value
    None => return PALETTE_ERR_NOT_LIVE
  }
  state.pending_measure_text = None
  let result : Result[@command_palette.PaletteEffect, @command_palette.PaletteError] =
    match state.pending_operation {
      Some(PaletteOperation::Input(event)) =>
        match
          palette.handle_input(
            event,
            document => palette_measure(state, document),
            (document, measurement) => palette_admit(state, document, measurement),
            palette_hit,
          ) {
          Ok(input) => Ok(input.effect)
          Err(error) => Err(error)
        }
      Some(PaletteOperation::Replace(range~, text~)) => {
        let field = palette.field()
        let range_valid = match range {
          Some(value) => palette_range_is_visible(field, value)
          None => true
        }
        if !range_valid {
          Err(@command_palette.InvalidField)
        } else {
          let candidate = if field.is_composing() {
            field.commit_composition(
              text,
              document => palette_measure(state, document),
              (document, measurement) => palette_admit(state, document, measurement),
            )
          } else {
            field.replace_selection(
              text,
              document => palette_measure(state, document),
              (document, measurement) => palette_admit(state, document, measurement),
            )
          }
          match candidate {
            Ok(value) =>
              match palette_install_field(palette, value) {
                Ok(_) => Ok(@command_palette.None)
                Err(error) => Err(error)
              }
            Err(error) => Err(@command_palette.Field(error))
          }
        }
      }
      Some(PaletteOperation::Mark(range~, text~, anchor~, head~)) => {
        let field = palette.field()
        let range_valid = match range {
          Some(value) => palette_range_is_visible(field, value)
          None => true
        }
        if !range_valid {
          Err(@command_palette.InvalidField)
        } else {
          let selection = match @text.TextSelection::new(anchor, head) {
            Ok(value) => value
            Err(_) => return PALETTE_ERR_VALUE
          }
          match
            field.update_composition(
              text,
              selection,
              document => palette_measure(state, document),
              (document, measurement) => palette_admit(state, document, measurement),
            ) {
            Ok(value) =>
              match palette_install_field(palette, value) {
                Ok(_) => Ok(@command_palette.None)
                Err(error) => Err(error)
              }
            Err(error) => Err(@command_palette.Field(error))
          }
        }
      }
      Some(PaletteOperation::CancelComposition) => {
        let field = palette.field()
        match
          field.cancel_composition(
            document => palette_measure(state, document),
            (document, measurement) => palette_admit(state, document, measurement),
          ) {
          Ok(value) =>
            match palette_install_field(palette, value) {
              Ok(_) => Ok(@command_palette.None)
              Err(error) => Err(error)
            }
          Err(error) => Err(@command_palette.Field(error))
        }
      }
      Some(PaletteOperation::UnmarkComposition) => {
        let field = palette.field()
        let candidate = match field.marked_range() {
          Some(marked) =>
            match field.document().slice(marked) {
              Ok(text) =>
                field.commit_composition(
                  text,
                  document => palette_measure(state, document),
                  (document, measurement) => palette_admit(state, document, measurement),
                )
              Err(_) => Err(@text_field.InvalidSelection)
            }
          None if field.is_composing() =>
            field.cancel_composition(
              document => palette_measure(state, document),
              (document, measurement) => palette_admit(state, document, measurement),
            )
          None => Ok(field)
        }
        match candidate {
          Ok(value) =>
            match palette_install_field(palette, value) {
              Ok(_) => Ok(@command_palette.None)
              Err(error) => Err(error)
            }
          Err(error) => Err(@command_palette.Field(error))
        }
      }
      None => return PALETTE_ERR_REQUEST
    }
  match result {
    Ok(effect) => {
      let current_field = palette.field()
      state.cached_text = Some(current_field.document().text())
      state.cached_measurement = Some(current_field.measurement())
      state.last_effect = effect
      match effect {
        @command_palette.Closed(epoch~, command_id~) => {
          state.epoch = epoch
          state.action_id = command_id
        }
        _ => ()
      }
      state.last_request = request
      state.pending_request = 0
      state.pending_epoch = 0
      state.pending_revision = 0
      state.pending_operation = None
      state.pending_measure_text = None
      state.pending_layout_text = None
      state.pending_layout_measurement = None
      PALETTE_OK
    }
    Err(error) =>
      match state.pending_measure_text {
        Some(_) => {
          state.pending_request = request
          state.pending_epoch = palette.open_epoch()
          state.pending_revision = palette.field().revision()
          PALETTE_NEED_LAYOUT
        }
        None => {
          state.last_request = request
          state.pending_request = 0
          state.pending_epoch = 0
          state.pending_revision = 0
          state.pending_operation = None
          state.pending_measure_text = None
          state.pending_layout_text = None
          state.pending_layout_measurement = None
          palette_error_code(error)
        }
      }
  }
}

///|
fn palette_submit_operation(
  state : PaletteSlot,
  operation : PaletteOperation,
  request : Int,
) -> Int {
  state.pending_operation = Some(operation)
  palette_run_operation(state, request)
}

///|
#export_name("mzed_mb_palette_v1_input")
pub fn mzed_mb_palette_v1_input(
  slot : Int,
  generation : Int,
  epoch : Int,
  request : Int,
  kind : Int,
  key : Int,
  modifier_bits : Int,
  repeat : Int,
  text : FixedArray[Byte],
  text_length : Int,
) -> Int {
  let validation = palette_validate_live(slot, generation)
  if validation != PALETTE_OK {
    return validation
  }
  let state = palette_slots[slot]
  let palette = match state.palette {
    Some(value) => value
    None => return PALETTE_ERR_NOT_LIVE
  }
  if epoch != palette.open_epoch() || epoch != state.epoch {
    return PALETTE_ERR_STALE_EPOCH
  }
  if request <= state.last_request || request > PALETTE_MAX_REQUEST ||
    state.pending_request != 0 {
    return PALETTE_ERR_REQUEST
  }
  let key_value = palette_key(key, match palette_decode_string(text, 0, text_length) {
    Some(value) => value
    None if text_length == 0 => ""
    None => return PALETTE_ERR_VALUE
  })
  let modifiers = match palette_modifiers(modifier_bits) {
    Some(value) => value
    None => return PALETTE_ERR_VALUE
  }
  let event = if kind == 1 {
    if repeat != 0 && repeat != 1 {
      return PALETTE_ERR_VALUE
    }
    match key_value {
      Some(value) =>
        @primitives.KeyPressed(
          key=value,
          modifiers~,
          repeat=repeat == 1,
        )
      None => return PALETTE_ERR_VALUE
    }
  } else if kind == 2 {
    match key_value {
      Some(value) => @primitives.KeyReleased(key=value, modifiers~)
      None => return PALETTE_ERR_VALUE
    }
  } else {
    return PALETTE_ERR_VALUE
  }
  palette_submit_operation(state, PaletteOperation::Input(event), request)
}

///|
#export_name("mzed_mb_palette_v1_replace")
pub fn mzed_mb_palette_v1_replace(
  slot : Int,
  generation : Int,
  epoch : Int,
  request : Int,
  range_present : Int,
  range_start : Int,
  range_end : Int,
  text : FixedArray[Byte],
  text_length : Int,
) -> Int {
  let validation = palette_validate_live(slot, generation)
  if validation != PALETTE_OK {
    return validation
  }
  let state = palette_slots[slot]
  let palette = match state.palette {
    Some(value) => value
    None => return PALETTE_ERR_NOT_LIVE
  }
  if epoch != palette.open_epoch() || epoch != state.epoch {
    return PALETTE_ERR_STALE_EPOCH
  }
  if request <= state.last_request || request > PALETTE_MAX_REQUEST ||
    state.pending_request != 0 || range_present < 0 || range_present > 1 {
    return PALETTE_ERR_REQUEST
  }
  let replacement = match palette_decode_string(text, 0, text_length) {
    Some(value) => value
    None => return PALETTE_ERR_VALUE
  }
  let range = if range_present == 0 {
    None
  } else {
    match @text.TextRange::new(range_start, range_end) {
      Ok(value) => Some(value)
      Err(_) => return PALETTE_ERR_VALUE
    }
  }
  palette_submit_operation(state, PaletteOperation::Replace(range~, text=replacement), request)
}

///|
#export_name("mzed_mb_palette_v1_mark")
pub fn mzed_mb_palette_v1_mark(
  slot : Int,
  generation : Int,
  epoch : Int,
  request : Int,
  range_present : Int,
  range_start : Int,
  range_end : Int,
  selection_present : Int,
  selection_start : Int,
  selection_end : Int,
  text : FixedArray[Byte],
  text_length : Int,
) -> Int {
  let validation = palette_validate_live(slot, generation)
  if validation != PALETTE_OK {
    return validation
  }
  let state = palette_slots[slot]
  let palette = match state.palette {
    Some(value) => value
    None => return PALETTE_ERR_NOT_LIVE
  }
  if epoch != palette.open_epoch() || epoch != state.epoch {
    return PALETTE_ERR_STALE_EPOCH
  }
  if request <= state.last_request || request > PALETTE_MAX_REQUEST ||
    state.pending_request != 0 || range_present < 0 || range_present > 1 ||
    selection_present < 0 || selection_present > 1 {
    return PALETTE_ERR_REQUEST
  }
  let preedit = match palette_decode_string(text, 0, text_length) {
    Some(value) => value
    None => return PALETTE_ERR_VALUE
  }
  let range = if range_present == 0 {
    None
  } else {
    match @text.TextRange::new(range_start, range_end) {
      Ok(value) => Some(value)
      Err(_) => return PALETTE_ERR_VALUE
    }
  }
  let anchor = if selection_present == 0 { preedit.length() } else { selection_start }
  let head = if selection_present == 0 { preedit.length() } else { selection_end }
  if @text.TextSelection::new(anchor, head) is Err(_) {
    state.last_request = request
    return PALETTE_ERR_VALUE
  }
  palette_submit_operation(
    state,
    PaletteOperation::Mark(range~, text=preedit, anchor~, head~),
    request,
  )
}

///|
#export_name("mzed_mb_palette_v1_cancel_composition")
pub fn mzed_mb_palette_v1_cancel_composition(
  slot : Int,
  generation : Int,
  epoch : Int,
  request : Int,
) -> Int {
  let validation = palette_validate_live(slot, generation)
  if validation != PALETTE_OK {
    return validation
  }
  let state = palette_slots[slot]
  if epoch != state.epoch || request <= state.last_request ||
    request > PALETTE_MAX_REQUEST || state.pending_request != 0 {
    return PALETTE_ERR_REQUEST
  }
  palette_submit_operation(state, PaletteOperation::CancelComposition, request)
}

///|
#export_name("mzed_mb_palette_v1_unmark_composition")
pub fn mzed_mb_palette_v1_unmark_composition(
  slot : Int,
  generation : Int,
  epoch : Int,
  request : Int,
) -> Int {
  let validation = palette_validate_live(slot, generation)
  if validation != PALETTE_OK {
    return validation
  }
  let state = palette_slots[slot]
  if epoch != state.epoch || request <= state.last_request ||
    request > PALETTE_MAX_REQUEST || state.pending_request != 0 {
    return PALETTE_ERR_REQUEST
  }
  palette_submit_operation(state, PaletteOperation::UnmarkComposition, request)
}

///|
#export_name("mzed_mb_palette_v1_pending_text")
pub fn mzed_mb_palette_v1_pending_text(
  slot : Int,
  generation : Int,
  epoch : Int,
  request : Int,
  out : FixedArray[Byte],
  capacity : Int,
) -> Int {
  let validation = palette_validate_live(slot, generation)
  if validation != PALETTE_OK {
    return validation
  }
  let state = palette_slots[slot]
  if state.pending_request != request || state.pending_epoch != epoch ||
    request <= 0 || epoch != state.epoch {
    return PALETTE_ERR_REQUEST
  }
  let text = match state.pending_measure_text {
    Some(value) => value
    None => return PALETTE_ERR_LAYOUT
  }
  let bytes = @utf8.encode(text)
  if capacity < bytes.length() || capacity < 0 {
    return PALETTE_ERR_VALUE
  }
  for index = 0; index < bytes.length(); index = index + 1 {
    out[index] = bytes[index]
  }
  bytes.length()
}

///|
#export_name("mzed_mb_palette_v1_stage_layout")
pub fn mzed_mb_palette_v1_stage_layout(
  slot : Int,
  generation : Int,
  epoch : Int,
  request : Int,
  expected_revision : Int,
  metrics_ints : FixedArray[Int],
  metrics_ints_length : Int,
  metrics_doubles : FixedArray[Double],
  metrics_doubles_length : Int,
) -> Int {
  let validation = palette_validate_live(slot, generation)
  if validation != PALETTE_OK {
    return validation
  }
  let state = palette_slots[slot]
  let palette = match state.palette {
    Some(value) => value
    None => return PALETTE_ERR_NOT_LIVE
  }
  if state.pending_request != request || state.pending_epoch != epoch ||
    epoch != palette.open_epoch() || epoch != state.epoch {
    return PALETTE_ERR_REQUEST
  }
  if palette.field().revision() != expected_revision ||
    expected_revision != state.pending_revision {
    return PALETTE_ERR_STALE_REVISION
  }
  let text = match state.pending_measure_text {
    Some(value) => value
    None => return PALETTE_ERR_LAYOUT
  }
  if @utf8.encode(text).length() > @command_palette.MAX_QUERY_BYTES {
    return PALETTE_ERR_VALUE
  }
  let document = match @text.TextDocument::new(text) {
    Ok(value) => value
    Err(_) => return PALETTE_ERR_LAYOUT
  }
  let measurement = match
    palette_make_measurement(
      document,
      metrics_ints,
      metrics_ints_length,
      metrics_doubles,
      metrics_doubles_length,
    ) {
    Ok(value) => value
    Err(error) => return error
  }
  state.pending_layout_text = Some(text)
  state.pending_layout_measurement = Some(measurement)
  PALETTE_OK
}

///|
#export_name("mzed_mb_palette_v1_continue")
pub fn mzed_mb_palette_v1_continue(
  slot : Int,
  generation : Int,
  epoch : Int,
  request : Int,
  expected_revision : Int,
) -> Int {
  let validation = palette_validate_live(slot, generation)
  if validation != PALETTE_OK {
    return validation
  }
  let state = palette_slots[slot]
  let palette = match state.palette {
    Some(value) => value
    None => return PALETTE_ERR_NOT_LIVE
  }
  if state.pending_request != request || state.pending_epoch != epoch ||
    epoch != palette.open_epoch() || epoch != state.epoch {
    return PALETTE_ERR_REQUEST
  }
  if palette.field().revision() != expected_revision ||
    expected_revision != state.pending_revision {
    return PALETTE_ERR_STALE_REVISION
  }
  match state.pending_operation {
    Some(_) => ()
    None => return PALETTE_ERR_REQUEST
  }
  if state.pending_layout_text != state.pending_measure_text ||
    state.pending_layout_measurement is None {
    return PALETTE_ERR_LAYOUT
  }
  palette_run_operation(state, request)
}

///|
#export_name("mzed_mb_palette_v1_snapshot")
pub fn mzed_mb_palette_v1_snapshot(
  slot : Int,
  generation : Int,
  out : FixedArray[Byte],
  capacity : Int,
) -> Int {
  let validation = palette_validate_live(slot, generation)
  if validation != PALETTE_OK {
    return validation
  }
  if capacity < 0 || capacity > PALETTE_MAX_SNAPSHOT_BYTES {
    return PALETTE_ERR_VALUE
  }
  let state = palette_slots[slot]
  let palette = match state.palette {
    Some(value) => value
    None => return PALETTE_ERR_NOT_LIVE
  }
  let field = palette.field()
  let displayed = @utf8.encode(field.document().text())
  let committed = @utf8.encode(field.committed_document().text())
  let action = match state.action_id {
    Some(value) => @utf8.encode(value)
    None => @utf8.encode("")
  }
  let picker = palette.picker()
  let rows = picker.visible_commands()
  let start = picker.visible_start()
  let active = match picker.active_index() {
    Some(value) => value
    None => -1
  }
  let marked_start : Int = match field.marked_range() {
    Some(value) => value.start()
    None => -1
  }
  let marked_end : Int = match field.marked_range() {
    Some(value) => value.end()
    None => -1
  }
  let effect_kind = match state.last_effect {
    @command_palette.None => 0
    @command_palette.CompositionCancelled => 1
    @command_palette.Closed(..) => 2
    @command_palette.FieldAction(_) => 3
  }
  let mut required = 22 * 4 + displayed.length() + committed.length() + action.length()
  for row in rows {
    required += 5 * 4 + @utf8.encode(row.id()).length() + @utf8.encode(row.label()).length()
  }
  if required > capacity || required > PALETTE_MAX_SNAPSHOT_BYTES {
    return PALETTE_ERR_VALUE
  }
  let header = [
    1,
    generation,
    state.epoch,
    if palette.is_open() { 1 } else { 0 },
    field.revision(),
    displayed.length(),
    committed.length(),
    field.selection().anchor(),
    field.selection().head(),
    if field.is_composing() { 1 } else { 0 },
    marked_start,
    marked_end,
    picker.match_count(),
    active,
    start,
    rows.length(),
    effect_kind,
    action.length(),
    state.last_request,
    state.pending_request,
    state.pending_epoch,
    state.pending_revision,
  ]
  let mut offset = 0
  for value in header {
    palette_write_i32(out, offset, value)
    offset += 4
  }
  offset = palette_write_bytes(out, offset, displayed)
  offset = palette_write_bytes(out, offset, committed)
  offset = palette_write_bytes(out, offset, action)
  for row_offset = 0; row_offset < rows.length(); row_offset = row_offset + 1 {
    let row = rows[row_offset]
    let id = @utf8.encode(row.id())
    let label = @utf8.encode(row.label())
    let selected = active == start + row_offset
    for value in [
      id.length(),
      label.length(),
      if row.is_enabled() { 1 } else { 0 },
      if selected { 1 } else { 0 },
      start + row_offset,
    ] {
      palette_write_i32(out, offset, value)
      offset += 4
    }
    offset = palette_write_bytes(out, offset, id)
    offset = palette_write_bytes(out, offset, label)
  }
  offset
}

///|
fn palette_rect_values(rect : @primitives.Rect) -> Array[Double] {
  [rect.left(), rect.top(), rect.size().width(), rect.size().height()]
}

///|
#export_name("mzed_mb_palette_v1_geometry")
pub fn mzed_mb_palette_v1_geometry(
  slot : Int,
  generation : Int,
  out : FixedArray[Double],
  capacity : Int,
) -> Int {
  let validation = palette_validate_live(slot, generation)
  if validation != PALETTE_OK {
    return validation
  }
  if capacity < PALETTE_GEOMETRY_DOUBLE_COUNT {
    return PALETTE_ERR_VALUE
  }
  let palette = match palette_slots[slot].palette {
    Some(value) => value
    None => return PALETTE_ERR_NOT_LIVE
  }
  let field = palette.field()
  let caret = match field.caret_rect() {
    Ok(value) => value
    Err(_) => return PALETTE_ERR_LAYOUT
  }
  let (run_origin, run_bounds) = match
    @text_field.scene_text_run_geometry(field.measurement()) {
    Ok(value) => value
    Err(_) => return PALETTE_ERR_LAYOUT
  }
  let values = [
    ..palette_rect_values(field.bounds()),
    ..palette_rect_values(field.content_bounds()),
    field.scroll_offset(),
    ..palette_rect_values(caret),
    run_origin.x(),
    run_origin.y(),
    ..palette_rect_values(run_bounds),
  ]
  if values.length() != PALETTE_GEOMETRY_DOUBLE_COUNT {
    return PALETTE_ERR_LAYOUT
  }
  for index = 0; index < values.length(); index = index + 1 {
    out[index] = values[index]
  }
  values.length()
}

///|
#export_name("mzed_mb_palette_v1_resize")
pub fn mzed_mb_palette_v1_resize(
  slot : Int,
  generation : Int,
  epoch : Int,
  request : Int,
  width : Double,
  height : Double,
) -> Int {
  let validation = palette_validate_live(slot, generation)
  if validation != PALETTE_OK {
    return validation
  }
  let state = palette_slots[slot]
  let palette = match state.palette {
    Some(value) => value
    None => return PALETTE_ERR_NOT_LIVE
  }
  if !palette.is_open() || epoch != state.epoch || epoch != palette.open_epoch() {
    return PALETTE_ERR_STALE_EPOCH
  }
  if request <= state.last_request || request > PALETTE_MAX_REQUEST ||
    state.pending_request != 0 {
    return PALETTE_ERR_REQUEST
  }
  if width.is_nan() || width.is_inf() || height.is_nan() || height.is_inf() ||
    width < 32.0 || width > 2048.0 || height < 24.0 || height > 128.0 {
    state.last_request = request
    return PALETTE_ERR_VALUE
  }
  let bounds = match @primitives.Rect::new(0.0, 0.0, width, height) {
    Ok(value) => value
    Err(_) => {
      state.last_request = request
      return PALETTE_ERR_VALUE
    }
  }
  let candidate = match palette.field().with_bounds(bounds) {
    Ok(value) => value
    Err(_) => {
      state.last_request = request
      return PALETTE_ERR_LAYOUT
    }
  }
  match palette_install_field(palette, candidate) {
    Ok(_) => {
      let resized = palette.field()
      state.cached_text = Some(resized.document().text())
      state.cached_measurement = Some(resized.measurement())
      state.last_request = request
      PALETTE_OK
    }
    Err(_) => {
      state.last_request = request
      PALETTE_ERR_MODEL
    }
  }
}

///|
fn palette_write_i32(out : FixedArray[Byte], offset : Int, value : Int) -> Unit {
  for byte_index = 0; byte_index < 4; byte_index = byte_index + 1 {
    out[offset + byte_index] =
      ((value >> (byte_index * 8)) & 0xFF).to_byte()
  }
}

///|
fn palette_write_bytes(
  out : FixedArray[Byte],
  offset : Int,
  bytes : Bytes,
) -> Int {
  for index = 0; index < bytes.length(); index = index + 1 {
    out[offset + index] = bytes[index]
  }
  offset + bytes.length()
}

///|
#export_name("mzed_mb_palette_v1_finish_close")
pub fn mzed_mb_palette_v1_finish_close(
  slot : Int,
  generation : Int,
  epoch : Int,
) -> Int {
  let validation = palette_validate_live(slot, generation)
  if validation != PALETTE_OK {
    return validation
  }
  let state = palette_slots[slot]
  let palette = match state.palette {
    Some(value) => value
    None => return PALETTE_ERR_NOT_LIVE
  }
  if epoch != state.epoch || epoch != palette.open_epoch() || palette.is_open() {
    return PALETTE_ERR_STALE_EPOCH
  }
  let tree = match state.tree {
    Some(value) => value
    None => return PALETTE_ERR_MODEL
  }
  match palette.finish_close(epoch, tree) {
    Ok(_) => {
      state.action_id = None
      PALETTE_OK
    }
    Err(_) => PALETTE_ERR_MODEL
  }
}

///|
#export_name("mzed_mb_palette_v1_abort_pending")
pub fn mzed_mb_palette_v1_abort_pending(
  slot : Int,
  generation : Int,
  epoch : Int,
  request : Int,
) -> Int {
  let validation = palette_validate_live(slot, generation)
  if validation != PALETTE_OK {
    return validation
  }
  let state = palette_slots[slot]
  if state.pending_request != request || state.pending_epoch != epoch ||
    request <= state.last_request || epoch != state.epoch {
    return PALETTE_ERR_REQUEST
  }
  // Retire even a refused request so delayed retries cannot resurrect it.
  state.last_request = request
  state.pending_request = 0
  state.pending_epoch = 0
  state.pending_revision = 0
  state.pending_operation = None
  state.pending_measure_text = None
  state.pending_layout_text = None
  state.pending_layout_measurement = None
  PALETTE_OK
}

///|
#export_name("mzed_mb_palette_v1_destroy")
pub fn mzed_mb_palette_v1_destroy(slot : Int, generation : Int) -> Int {
  let validation = palette_validate_live(slot, generation)
  if validation != PALETTE_OK {
    return validation
  }
  let state = palette_slots[slot]
  // Owner teardown is unconditional and cannot finish a pending command.
  // The host first disables native input and dismisses this owner.
  state.live = false
  state.palette = None
  state.tree = None
  state.cached_text = None
  state.cached_measurement = None
  state.pending_request = 0
  state.pending_epoch = 0
  state.pending_revision = 0
  state.pending_operation = None
  state.pending_measure_text = None
  state.pending_layout_text = None
  state.pending_layout_measurement = None
  state.action_id = None
  state.last_effect = @command_palette.None
  PALETTE_OK
}
