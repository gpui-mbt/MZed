// SPDX-License-Identifier: GPL-3.0-or-later
// No native owner is constructed through FFI by these protocol/receipt tests.
mod native_island {
    pub fn claim_runtime_owner() -> Result<(), i32> { Ok(()) }
}
#[path = "../native/palette_protocol.rs"]
mod palette_protocol;
