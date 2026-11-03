//! CPU address <-> file offset (headerless image).

use crate::{Error, Result};
use std::fmt;
use std::str::FromStr;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Mapping {
    LoRom,
    HiRom,
    ExHiRom,
    /// SA-1 with the default MMC: $C0-$FF map ROM linearly, $00-$3F/$80-$BF as LoROM.
    Sa1,
}

impl Mapping {
    pub const ALL: [Mapping; 4] = [
        Mapping::LoRom,
        Mapping::HiRom,
        Mapping::ExHiRom,
        Mapping::Sa1,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Mapping::LoRom => "lorom",
            Mapping::HiRom => "hirom",
            Mapping::ExHiRom => "exhirom",
            Mapping::Sa1 => "sa1",
        }
    }
}

impl fmt::Display for Mapping {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.name())
    }
}

impl FromStr for Mapping {
    type Err = Error;
    fn from_str(s: &str) -> Result<Self> {
        Mapping::ALL
            .into_iter()
            .find(|m| m.name() == s)
            .ok_or_else(|| Error::new(format!("Unknown mapping '{s}'.")))
    }
}

/// `"$05:E000"`, `"05E000"`, `"0x5e000"` -> 0x05E000. Colons and underscores are ignored.
pub fn parse_address(text: &str) -> Result<u32> {
    let t: String = text
        .trim()
        .chars()
        .filter(|&c| c != ':' && c != '_')
        .collect();
    let digits = t
        .strip_prefix('$')
        .or_else(|| t.strip_prefix("0x"))
        .or_else(|| t.strip_prefix("0X"))
        .unwrap_or(&t);
    if digits.is_empty() || !digits.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err(Error::new(format!("Not an address: '{}'.", text.trim())));
    }
    u32::from_str_radix(digits, 16)
        .map_err(|_| Error::new(format!("Address too large: '{}'.", text.trim())))
}

/// SNES CPU address -> file offset.
pub fn to_offset(address: u32, mapping: Mapping) -> Result<u32> {
    let (bank, low) = (address >> 16, address & 0xFFFF);
    match mapping {
        Mapping::LoRom => {
            if bank == 0x7E || bank == 0x7F {
                return Err(Error::new(format!("${address:06X} is WRAM, not ROM.")));
            }
            if low < 0x8000 && bank & 0x7F < 0x40 {
                return Err(Error::new(format!(
                    "${address:06X} is not ROM in LoROM (banks $00-$3F map ROM at $8000-$FFFF)."
                )));
            }
            Ok((bank & 0x7F) * 0x8000 + (low & 0x7FFF))
        }
        Mapping::HiRom => {
            if bank == 0x7E || bank == 0x7F {
                return Err(Error::new(format!("${address:06X} is WRAM, not ROM.")));
            }
            if bank & 0x7F < 0x40 && low < 0x8000 {
                return Err(Error::new(format!("${address:06X} is not ROM in HiROM.")));
            }
            Ok((bank & 0x3F) << 16 | low)
        }
        Mapping::ExHiRom => {
            Ok((if bank >= 0x80 { 0 } else { 0x40_0000 }) | (bank & 0x3F) << 16 | low)
        }
        Mapping::Sa1 => {
            if bank >= 0xC0 {
                Ok((bank - 0xC0) << 16 | low)
            } else {
                to_offset(address, Mapping::LoRom)
            }
        }
    }
}

/// File offset -> SNES CPU address: banks $00-$7D, or the $80+ mirror with `fast`.
pub fn to_address(offset: u32, mapping: Mapping, fast: bool) -> u32 {
    let lorom = ((offset / 0x8000) << 16) | 0x8000 | (offset % 0x8000);
    match mapping {
        Mapping::LoRom => lorom | if fast { 0x80_0000 } else { 0 },
        Mapping::HiRom => 0xC0_0000 | offset,
        Mapping::Sa1 => {
            let a = if offset < 0x20_0000 {
                lorom
            } else {
                0xC0_0000 | offset
            };
            if fast && a < 0xC0_0000 {
                a | 0x80_0000
            } else {
                a
            }
        }
        Mapping::ExHiRom => {
            if offset < 0x40_0000 {
                0xC0_0000 | offset
            } else {
                0x40_0000 | (offset - 0x40_0000)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_every_spelling() {
        for t in ["$05:E000", "05E000", "0x5e000", " 05_E000 ", "$05E000"] {
            assert_eq!(parse_address(t).unwrap(), 0x05E000, "{t}");
        }
        assert!(parse_address("").is_err());
        assert!(parse_address("$").is_err());
        assert!(parse_address("xyz").is_err());
        assert!(parse_address("-5").is_err());
        assert!(parse_address("123456789").is_err());
    }

    #[test]
    fn lorom_round_trip() {
        for offset in [0, 0x7FFF, 0x8000, 0x2E30F, 0xFFFFF, 0x37_FFFF] {
            assert_eq!(
                to_offset(to_address(offset, Mapping::LoRom, false), Mapping::LoRom).unwrap(),
                offset
            );
            assert_eq!(
                to_offset(to_address(offset, Mapping::LoRom, true), Mapping::LoRom).unwrap(),
                offset
            );
        }
        assert_eq!(to_offset(0x05E000, Mapping::LoRom).unwrap(), 0x2E000);
        assert_eq!(to_address(0x80000, Mapping::LoRom, false), 0x108000);
    }

    #[test]
    fn lorom_rejects_non_rom() {
        assert_eq!(
            to_offset(0x7E0019, Mapping::LoRom).unwrap_err().0,
            "$7E0019 is WRAM, not ROM."
        );
        assert!(
            to_offset(0x001234, Mapping::LoRom)
                .unwrap_err()
                .0
                .contains("not ROM in LoROM")
        );
        // Banks $40-$6F map ROM in both halves.
        assert_eq!(to_offset(0x400000, Mapping::LoRom).unwrap(), 0x200000);
    }

    #[test]
    fn hirom_and_sa1_and_exhirom() {
        assert_eq!(to_offset(0xC12345, Mapping::HiRom).unwrap(), 0x012345);
        assert_eq!(to_offset(0x018000, Mapping::HiRom).unwrap(), 0x018000);
        assert!(to_offset(0x011234, Mapping::HiRom).is_err());
        assert_eq!(to_address(0x12345, Mapping::HiRom, true), 0xC12345);
        assert_eq!(to_offset(0xC08000, Mapping::Sa1).unwrap(), 0x8000);
        assert_eq!(to_offset(0x008000, Mapping::Sa1).unwrap(), 0);
        assert_eq!(to_address(0x20_0000, Mapping::Sa1, true), 0xE0_0000);
        assert_eq!(to_address(0x8000, Mapping::Sa1, true), 0x818000);
        assert_eq!(to_offset(0xC00000, Mapping::ExHiRom).unwrap(), 0);
        assert_eq!(to_offset(0x400000, Mapping::ExHiRom).unwrap(), 0x40_0000);
        assert_eq!(to_offset(0x800000, Mapping::ExHiRom).unwrap(), 0);
        assert_eq!(to_address(0x40_1234, Mapping::ExHiRom, false), 0x40_1234);
        assert_eq!(to_address(0x1234, Mapping::ExHiRom, false), 0xC0_1234);
    }

    #[test]
    fn mapping_names() {
        for m in Mapping::ALL {
            assert_eq!(m.name().parse::<Mapping>().unwrap(), m);
        }
        assert_eq!(
            "rom".parse::<Mapping>().unwrap_err().0,
            "Unknown mapping 'rom'."
        );
    }
}
