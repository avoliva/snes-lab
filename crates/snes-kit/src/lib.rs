//! SNES ROM building blocks: address maths, headers and checksums, BPS, HAL compression, SMW levels.
//!
//! - [`addr`]: CPU address <-> file offset for LoROM, HiROM, ExHiROM and SA-1.
//! - [`header`]: internal header location, mapping detection and the checksum.
//! - [`bps`]: BPS patches (beat v1).
//! - [`hal`]: HAL Laboratory's compression (Kirby, EarthBound), a port of exhal/inhal 1.21.
//! - [`smw`]: Super Mario World layer-1 object streams and sprite lists.
//! - [`crc32`]: the CRC-32 that BPS and ROM databases use.
#![forbid(unsafe_code)]

pub mod addr;
pub mod bps;
pub mod crc32;
pub mod hal;
pub mod header;
pub mod smw;

mod error;
pub use error::{Error, Result};
