// SPDX-License-Identifier: GPL-3.0-or-later
///|
const SLOT_COUNT : Int = 4

///|
const MAX_GENERATION : Int = 1_000_000

///|
const MAX_DIMENSION : Int = 4_096

///|
const MAX_COUNTER : Int = 65_535

///|
const MAX_REQUEST : Int = 1_000_000

///|
priv struct SceneSummary {
  quad_count : Int
  x : Int
  y : Int
  width : Int
  height : Int
  red : Int
  green : Int
  blue : Int
  alpha : Int
}

///|
priv struct SlotState {
  mut live : Bool
  mut generation : Int
  mut last_request : Int
  mut counter : Int
  mut quad_count : Int
  mut x : Int
  mut y : Int
  mut width : Int
  mut height : Int
  mut red : Int
  mut green : Int
  mut blue : Int
  mut alpha : Int
}

///|
let slot_states : Array[SlotState] = [
  {
    live: false,
    generation: 0,
    last_request: 0,
    counter: 0,
    quad_count: 0,
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    red: 0,
    green: 0,
    blue: 0,
    alpha: 0,
  },
  {
    live: false,
    generation: 0,
    last_request: 0,
    counter: 0,
    quad_count: 0,
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    red: 0,
    green: 0,
    blue: 0,
    alpha: 0,
  },
  {
    live: false,
    generation: 0,
    last_request: 0,
    counter: 0,
    quad_count: 0,
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    red: 0,
    green: 0,
    blue: 0,
    alpha: 0,
  },
  {
    live: false,
    generation: 0,
    last_request: 0,
    counter: 0,
    quad_count: 0,
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    red: 0,
    green: 0,
    blue: 0,
    alpha: 0,
  },
]

///|
fn valid_slot(slot : Int) -> Bool {
  slot >= 0 && slot < SLOT_COUNT
}

///|
fn valid_generation(generation : Int) -> Bool {
  generation >= 1 && generation <= MAX_GENERATION
}

///|
fn valid_dimensions(width : Int, height : Int) -> Bool {
  width >= 1 && width <= MAX_DIMENSION && height >= 1 && height <= MAX_DIMENSION
}

///|
fn validate_live_slot(slot : Int, generation : Int) -> Int {
  if !valid_slot(slot) {
    -3
  } else if !valid_generation(generation) {
    -4
  } else {
    let state = slot_states[slot]
    if state.generation != generation {
      -5
    } else if !state.live {
      -6
    } else {
      0
    }
  }
}

///|
fn SlotState::store_scene(self : SlotState, scene : SceneSummary) -> Unit {
  self.quad_count = scene.quad_count
  self.x = scene.x
  self.y = scene.y
  self.width = scene.width
  self.height = scene.height
  self.red = scene.red
  self.green = scene.green
  self.blue = scene.blue
  self.alpha = scene.alpha
}

///|
fn SlotState::clear_live_state(self : SlotState) -> Unit {
  self.live = false
  self.last_request = 0
  self.counter = 0
  self.quad_count = 0
  self.x = 0
  self.y = 0
  self.width = 0
  self.height = 0
  self.red = 0
  self.green = 0
  self.blue = 0
  self.alpha = 0
}

///|
fn summarize_scene(
  snapshot : @scene.SceneSnapshot,
  expected_width : Int,
  expected_height : Int,
  expected_red : Int,
) -> Result[SceneSummary, Int] {
  if snapshot.schema_version() != 1 || snapshot.scale() != 1.0 {
    Err(-12)
  } else if snapshot.resources().length() != 0 ||
    snapshot.clip_chains().length() != 0 {
    Err(-12)
  } else {
    let viewport = snapshot.viewport()
    if viewport.left() != 0.0 ||
      viewport.top() != 0.0 ||
      viewport.size().width() != expected_width.to_double() ||
      viewport.size().height() != expected_height.to_double() {
      Err(-12)
    } else {
      let items = snapshot.items()
      if items.length() != 1 {
        Err(-12)
      } else {
        match items[0] {
          @scene.SceneItem::QuadItem(
            id~,
            bounds~,
            color~,
            transform~,
            opacity~,
            clip_chain_id~
          ) =>
            if id != 1 ||
              bounds.left() != 0.0 ||
              bounds.top() != 0.0 ||
              bounds.size().width() != expected_width.to_double() ||
              bounds.size().height() != expected_height.to_double() ||
              color.red() != expected_red ||
              color.green() != 96 ||
              color.blue() != 160 ||
              color.alpha() != 255 ||
              opacity != 1.0 ||
              transform.a() != 1.0 ||
              transform.b() != 0.0 ||
              transform.c() != 0.0 ||
              transform.d() != 1.0 ||
              transform.tx() != 0.0 ||
              transform.ty() != 0.0 ||
              clip_chain_id is Some(_) {
              Err(-12)
            } else {
              Ok({
                quad_count: 1,
                x: 0,
                y: 0,
                width: expected_width,
                height: expected_height,
                red: color.red(),
                green: color.green(),
                blue: color.blue(),
                alpha: color.alpha(),
              })
            }
        }
      }
    }
  }
}

///|
fn render_scene(
  counter : Int,
  width : Int,
  height : Int,
) -> Result[SceneSummary, Int] {
  let viewport = match
    @primitives.Size::new(width.to_double(), height.to_double()) {
    Err(_) => return Err(-12)
    Ok(value) => value
  }
  let red = 40 + counter % 2 * 160
  let color = match @primitives.Color::rgba(red, 96, 160, 255) {
    Err(_) => return Err(-12)
    Ok(value) => value
  }
  let root = @element.Element::root(
    1,
    @layout.FlexTreeStyle::new(@layout.FlowDirection::row()),
  ).with_background(Some(color))
  let requested_layout = match root.request_layout(viewport) {
    Err(_) => return Err(-12)
    Ok(value) => value
  }
  let prepainted = match requested_layout.prepaint() {
    Err(_) => return Err(-12)
    Ok(value) => value
  }
  if prepainted.overflow() {
    return Err(-12)
  }
  let snapshot = match prepainted.paint_snapshot(1.0) {
    Err(_) => return Err(-12)
    Ok(value) => value
  }
  summarize_scene(snapshot, width, height, red)
}

///|
#export_name("mzed_mb_v1_init")
pub fn mzed_mb_v1_init(version : Int) -> Int {
  if version == 1 {
    0
  } else {
    -2
  }
}

///|
#export_name("mzed_mb_v1_create")
pub fn mzed_mb_v1_create(
  slot : Int,
  generation : Int,
  width : Int,
  height : Int,
) -> Int {
  if !valid_slot(slot) {
    -3
  } else if !valid_generation(generation) {
    -4
  } else if !valid_dimensions(width, height) {
    -9
  } else {
    let state = slot_states[slot]
    if state.live {
      -11
    } else if generation <= state.generation {
      -5
    } else {
      match render_scene(0, width, height) {
        Err(_) => -12
        Ok(scene) => {
          state.generation = generation
          state.live = true
          state.last_request = 0
          state.counter = 0
          state.store_scene(scene)
          0
        }
      }
    }
  }
}

///|
#export_name("mzed_mb_v1_dispatch")
pub fn mzed_mb_v1_dispatch(
  slot : Int,
  generation : Int,
  request : Int,
  opcode : Int,
  a : Int,
  b : Int,
) -> Int {
  let validation = validate_live_slot(slot, generation)
  if validation != 0 {
    validation
  } else {
    let state = slot_states[slot]
    if request < 1 || request > MAX_REQUEST || request != state.last_request + 1 {
      -7
    } else if opcode == 1 {
      if a != 1 || b != 0 || state.counter >= MAX_COUNTER {
        -9
      } else {
        let next_counter = state.counter + 1
        match render_scene(next_counter, state.width, state.height) {
          Err(_) => -12
          Ok(scene) => {
            state.last_request = request
            state.counter = next_counter
            state.store_scene(scene)
            0
          }
        }
      }
    } else if opcode == 2 {
      if !valid_dimensions(a, b) {
        -9
      } else {
        match render_scene(state.counter, a, b) {
          Err(_) => -12
          Ok(scene) => {
            state.last_request = request
            state.store_scene(scene)
            0
          }
        }
      }
    } else {
      -8
    }
  }
}

///|
#export_name("mzed_mb_v1_snapshot")
pub fn mzed_mb_v1_snapshot(
  slot : Int,
  generation : Int,
  request : Int,
  field : Int,
) -> Int {
  let validation = validate_live_slot(slot, generation)
  if validation != 0 {
    validation
  } else {
    let state = slot_states[slot]
    if request != state.last_request {
      -7
    } else if field < 0 || field > 13 {
      -10
    } else {
      match field {
        0 => 1
        1 => slot
        2 => state.generation
        3 => state.last_request
        4 => state.counter
        5 => state.quad_count
        6 => state.x
        7 => state.y
        8 => state.width
        9 => state.height
        10 => state.red
        11 => state.green
        12 => state.blue
        13 => state.alpha
        _ => -10
      }
    }
  }
}

///|
#export_name("mzed_mb_v1_destroy")
pub fn mzed_mb_v1_destroy(slot : Int, generation : Int) -> Int {
  let validation = validate_live_slot(slot, generation)
  if validation != 0 {
    validation
  } else {
    slot_states[slot].clear_live_state()
    0
  }
}
