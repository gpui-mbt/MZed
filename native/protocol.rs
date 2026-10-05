// SPDX-License-Identifier: GPL-3.0-or-later
use std::{
    cell::RefCell,
    marker::PhantomData,
    rc::Rc,
    sync::OnceLock,
    thread::{self, ThreadId},
};

mod ffi {
    unsafe extern "C" {
        pub fn mzed_native_v1_init(version: i32) -> i32;
        pub fn mzed_native_v1_create(slot: i32, generation: i32, width: i32, height: i32) -> i32;
        pub fn mzed_native_v1_dispatch(
            slot: i32,
            generation: i32,
            request: i32,
            opcode: i32,
            a: i32,
            b: i32,
        ) -> i32;
        pub fn mzed_native_v1_snapshot(slot: i32, generation: i32, request: i32, field: i32)
        -> i32;
        pub fn mzed_native_v1_destroy(slot: i32, generation: i32) -> i32;
    }
}

static OWNER: OnceLock<ThreadId> = OnceLock::new();
thread_local! { static SLOTS: RefCell<[(bool, i32); 4]> = const { RefCell::new([(false, 0); 4]) }; }

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Scene {
    pub counter: i32,
    pub width: i32,
    pub height: i32,
    pub rgb: u32,
}

pub struct Native {
    slot: i32,
    pub generation: i32,
    request: i32,
    scene: Scene,
    _owner_thread: PhantomData<Rc<()>>,
}

fn success(status: i32) -> Result<(), i32> {
    if status == 0 { Ok(()) } else { Err(status) }
}

impl Native {
    pub fn new(width: i32, height: i32) -> Result<Self, i32> {
        let owner = OWNER.get_or_init(|| thread::current().id());
        if *owner != thread::current().id() {
            return Err(-20);
        }
        // The process owner is fixed before the first call; the facade is !Send/!Sync.
        success(unsafe { ffi::mzed_native_v1_init(1) })?;
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
        let status = unsafe { ffi::mzed_native_v1_create(slot, generation, width, height) };
        if status != 0 {
            SLOTS.with(|slots| slots.borrow_mut()[slot as usize].0 = false);
            return Err(status);
        }
        let mut native = Self {
            slot,
            generation,
            request: 0,
            scene: Scene {
                counter: 0,
                width,
                height,
                rgb: 0,
            },
            _owner_thread: PhantomData,
        };
        native.scene = native.copy_scene()?;
        Ok(native)
    }

    pub fn scene(&self) -> Scene {
        self.scene
    }

    fn copy_scene(&self) -> Result<Scene, i32> {
        let mut fields = [0_i32; 14];
        for (field, value) in fields.iter_mut().enumerate() {
            *value = unsafe {
                ffi::mzed_native_v1_snapshot(self.slot, self.generation, self.request, field as i32)
            };
            if *value < 0 {
                return Err(*value);
            }
        }
        if fields[..4] != [1, self.slot, self.generation, self.request]
            || fields[4] > 65535
            || fields[5..8] != [1, 0, 0]
            || !(1..=4096).contains(&fields[8])
            || !(1..=4096).contains(&fields[9])
            || fields[10..13].iter().any(|value| *value > 255)
            || fields[13] != 255
        {
            return Err(-22);
        }
        Ok(Scene {
            counter: fields[4],
            width: fields[8],
            height: fields[9],
            rgb: ((fields[10] as u32) << 16) | ((fields[11] as u32) << 8) | fields[12] as u32,
        })
    }

    fn dispatch(&mut self, opcode: i32, a: i32, b: i32) -> Result<(), i32> {
        let request = self
            .request
            .checked_add(1)
            .filter(|value| *value <= 1_000_000)
            .ok_or(-7)?;
        success(unsafe {
            ffi::mzed_native_v1_dispatch(self.slot, self.generation, request, opcode, a, b)
        })?;
        self.request = request;
        self.scene = self.copy_scene()?;
        Ok(())
    }

    pub fn increment(&mut self) -> Result<(), i32> {
        self.dispatch(1, 1, 0)
    }

    pub fn reject_increment_for_probe(&mut self) -> Result<(), i32> {
        self.dispatch(0, 1, 0)
    }

    pub fn resize(&mut self, width: i32, height: i32) -> Result<(), i32> {
        if self.scene.width == width && self.scene.height == height {
            return Ok(());
        }
        self.dispatch(2, width, height)
    }
}

impl Drop for Native {
    fn drop(&mut self) {
        let status = unsafe { ffi::mzed_native_v1_destroy(self.slot, self.generation) };
        if status != 0 {
            eprintln!("MZed native destroy rejected: {status}");
        }
        SLOTS.with(|slots| slots.borrow_mut()[self.slot as usize].0 = false);
    }
}

#[derive(Default)]
pub struct Press {
    generation: Option<i32>,
    canceled: bool,
}
impl Press {
    pub fn begin(&mut self, generation: i32) -> bool {
        if self.generation.is_some() && !self.canceled {
            return false;
        }
        self.generation = Some(generation);
        self.canceled = false;
        true
    }
    pub fn owned(&self) -> bool {
        self.generation.is_some()
    }
    pub fn cancel(&mut self) {
        self.canceled = true;
    }
    pub fn abandon(&mut self) {
        self.generation = None;
        self.canceled = false;
    }
    pub fn finish(&mut self, generation: Option<i32>, valid_release: bool) -> bool {
        let owned = self.generation.take();
        let accept = owned.is_some() && owned == generation && !self.canceled && valid_release;
        self.canceled = false;
        accept
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_scene_and_lifecycle() {
        unsafe {
            assert_eq!(ffi::mzed_native_v1_create(0, 1, 120, 18), -1);
            assert_eq!(ffi::mzed_native_v1_init(2), -2);
            assert_eq!(ffi::mzed_native_v1_dispatch(0, 1, 1, 1, 1, 0), -1);
            assert_eq!(ffi::mzed_native_v1_snapshot(0, 1, 0, 0), -1);
            assert_eq!(ffi::mzed_native_v1_destroy(0, 1), -1);
        }
        let mut native = Native::new(120, 18).expect("pinned native image");
        for slot in [i32::MIN, -1, 4, i32::MAX] {
            assert_eq!(unsafe { ffi::mzed_native_v1_create(slot, 1, 1, 1) }, -3);
        }
        for generation in [i32::MIN, -1, 0, 1_000_001, i32::MAX] {
            assert_eq!(
                unsafe { ffi::mzed_native_v1_create(0, generation, 1, 1) },
                -4
            );
        }
        let original = native.scene();
        assert_eq!(
            original,
            Scene {
                counter: 0,
                width: 120,
                height: 18,
                rgb: 0x2860a0
            }
        );
        let original_request = native.request;
        assert_eq!(native.reject_increment_for_probe(), Err(-8));
        assert_eq!(native.request, original_request);
        assert_eq!(native.scene(), original);
        native.increment().expect("increment");
        assert_eq!(native.scene().rgb, 0xc860a0);
        assert_eq!(native.scene().counter, 1);
        native.resize(240, 36).expect("resize");
        assert_eq!(native.scene().width, 240);
        assert_eq!(native.scene().counter, 1);
        assert_eq!(original.width, 120);
        assert_eq!(original.counter, 0);
        let generation = native.generation;
        let slot = native.slot;
        let request = native.request;
        for bad in [i32::MIN, -1, 0, request, request + 2, 1_000_001, i32::MAX] {
            assert_eq!(
                unsafe { ffi::mzed_native_v1_dispatch(slot, generation, bad, 1, 1, 0) },
                -7
            );
        }
        for bad in [i32::MIN, -1, 0, 4097, i32::MAX] {
            assert_eq!(native.resize(bad, 18), Err(-9));
        }
        assert_eq!(native.scene().counter, 1);
        assert_eq!(
            unsafe { ffi::mzed_native_v1_snapshot(slot, generation, request - 1, 0) },
            -7
        );
        assert_eq!(
            unsafe { ffi::mzed_native_v1_snapshot(slot, generation, request, 14) },
            -10
        );
        assert_eq!(
            unsafe { ffi::mzed_native_v1_dispatch(slot, generation, request + 1, 99, 0, 0) },
            -8
        );
        assert_eq!(
            unsafe { ffi::mzed_native_v1_dispatch(slot, generation, request + 1, 1, 2, 0) },
            -9
        );
        assert_eq!(native.copy_scene(), Ok(native.scene()));
        drop(native);
        assert_eq!(
            unsafe { ffi::mzed_native_v1_snapshot(slot, generation, request, 0) },
            -6
        );
        let remounted = Native::new(120, 18).expect("remount");
        assert_eq!(remounted.slot, slot);
        assert!(remounted.generation > generation);
        assert_eq!(
            unsafe { ffi::mzed_native_v1_dispatch(slot, generation, 1, 1, 1, 0) },
            -5
        );
        assert_eq!(remounted.scene(), original);
        drop(remounted);
        let mut live = Vec::new();
        for _ in 0..4 {
            live.push(Native::new(1, 1).expect("bounded slot"));
        }
        assert!(matches!(Native::new(1, 1), Err(-21)));
        live[0].increment().expect("independent slot mutation");
        assert_eq!(live[0].scene().counter, 1);
        assert!(live[1..].iter().all(|native| native.scene().counter == 0));
        drop(live);
        // Declared test budget: 1,000 mount/dispatch/copy/destroy cycles, <=4 live slots.
        for index in 0..1000 {
            let mut native = Native::new(120, 18).expect("bounded churn");
            native.increment().expect("bounded dispatch");
            assert_eq!(native.scene().counter, 1, "cycle {index}");
        }
        assert!(matches!(
            std::thread::spawn(|| matches!(Native::new(1, 1), Err(-20))).join(),
            Ok(true)
        ));
    }

    #[test]
    fn press_owns_one_sequence_and_rejects_stale_or_canceled_release() {
        let mut press = Press::default();
        assert!(!press.finish(Some(1), true));
        assert!(press.begin(1));
        assert!(!press.begin(1));
        assert!(press.owned());
        assert!(press.finish(Some(1), true));
        assert!(!press.finish(Some(1), true));
        assert!(press.begin(1));
        assert!(!press.finish(Some(1), false));
        assert!(press.begin(1));
        assert!(!press.finish(Some(2), true));
        assert!(press.begin(2));
        press.cancel();
        assert!(!press.finish(None, true));
        assert!(press.begin(3));
        press.cancel();
        assert!(press.begin(4));
        assert!(press.finish(Some(4), true));
        assert!(press.begin(5));
        press.cancel();
        press.abandon();
        assert!(!press.owned());
        assert!(!press.finish(Some(5), true));
    }
}
