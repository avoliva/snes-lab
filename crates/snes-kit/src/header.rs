//! The internal header: where it is, which mapping it implies, and the checksum.

use crate::addr::Mapping;
use crate::{Error, Result};

/// Header locations checked by [`detect_mapping`], in tie-break order.
const CANDIDATES: [(Mapping, usize); 3] = [
    (Mapping::LoRom, 0x7FC0),
    (Mapping::HiRom, 0xFFC0),
    (Mapping::ExHiRom, 0x40_FFC0),
];

pub const COPIER_HEADER: usize = 0x200;

/// File offset of the 64-byte internal header for a mapping (SA-1 uses the LoROM spot).
pub fn header_offset(mapping: Mapping) -> usize {
    match mapping {
        Mapping::LoRom | Mapping::Sa1 => 0x7FC0,
        Mapping::HiRom => 0xFFC0,
        Mapping::ExHiRom => 0x40_FFC0,
    }
}

/// The image without a 512-byte copier header, if it has one (size % 1 KiB == 512).
pub fn strip_copier_header(data: &[u8]) -> &[u8] {
    if data.len() % 0x400 == COPIER_HEADER {
        &data[COPIER_HEADER..]
    } else {
        data
    }
}

fn word(h: &[u8], at: usize) -> u32 {
    u32::from(h[at]) | u32::from(h[at + 1]) << 8
}

/// How much the 64 bytes at `at` look like an internal header.
fn score(rom: &[u8], at: usize) -> i32 {
    let Some(h) = rom.get(at..at + 0x40) else {
        return -100;
    };
    let mut score = 0;
    if word(h, 0x1E) ^ word(h, 0x1C) == 0xFFFF {
        score += 8;
    }
    if h[..21].iter().all(|&b| (0x20..0x7F).contains(&b)) {
        score += 3;
    }
    if [
        0x20, 0x21, 0x22, 0x23, 0x25, 0x2A, 0x30, 0x31, 0x32, 0x35, 0x3A,
    ]
    .contains(&(h[0x15] & 0xEF))
    {
        score += 2;
    }
    if word(h, 0x3C) >= 0x8000 {
        score += 2;
    }
    score
}

/// Whether the header at `at` stores `sum` (the image's computed checksum) with its complement.
fn sum_matches(rom: &[u8], at: usize, sum: u16) -> bool {
    rom.get(at..at + 0x20)
        .is_some_and(|h| word(h, 0x1E) == u32::from(sum) && word(h, 0x1C) == u32::from(!sum))
}

/// LoROM, HiROM or ExHiROM: whichever header location looks most valid (LoROM wins ties).
///
/// A header whose stored checksum matches the image wins outright: some games keep a
/// well-formed leftover header at the other spot (Mario's Early Years: Fun with Numbers).
/// Hacks with a stale checksum fall back to the shape score alone.
pub fn detect_mapping(rom: &[u8]) -> Mapping {
    let sum = checksum(rom);
    let key = |(m, at): (Mapping, usize)| {
        (
            sum_matches(rom, at, sum),
            score(rom, at),
            m == Mapping::LoRom,
        )
    };
    let mut best = CANDIDATES[0];
    for c in &CANDIDATES[1..] {
        if key(*c) > key(best) {
            best = *c;
        }
    }
    best.0
}

/// The mapping for code addresses: SA-1 for SA-1 games (banks $C0-$FF differ), else [`detect_mapping`].
pub fn code_mapping(rom: &[u8]) -> Mapping {
    let mapping = detect_mapping(rom);
    if mapping == Mapping::LoRom && rom.get(0x7FC0 + 0x15) == Some(&0x23) {
        Mapping::Sa1
    } else {
        mapping
    }
}

/// The internal checksum: the sum of all bytes, a non-power-of-two size mirrored up to the next one.
pub fn checksum(rom: &[u8]) -> u16 {
    let sum = |b: &[u8]| b.iter().map(|&x| u64::from(x)).sum::<u64>();
    if rom.is_empty() {
        return 0;
    }
    let top = 1usize << (usize::BITS - 1 - rom.len().leading_zeros());
    if top == rom.len() {
        return (sum(rom) & 0xFFFF) as u16;
    }
    let (base, rest) = rom.split_at(top);
    let repeat = (top / rest.len()) as u64;
    ((sum(base) + sum(rest) * repeat) & 0xFFFF) as u16
}

/// Rewrite the checksum and its complement in the header of `mapping` (detected when `None`).
pub fn fix_checksum(rom: &[u8], mapping: Option<Mapping>) -> Result<Vec<u8>> {
    let at = header_offset(mapping.unwrap_or_else(|| detect_mapping(rom)));
    if rom.len() < at + 0x40 {
        return Err(Error::new(format!(
            "The ROM is {:#x} bytes, too small to hold a header at {at:#x}.",
            rom.len()
        )));
    }
    let mut out = rom.to_vec();
    out[at + 0x1C..at + 0x20].copy_from_slice(&[0xFF, 0xFF, 0, 0]);
    let c = checksum(&out);
    out[at + 0x1C..at + 0x1E].copy_from_slice(&(c ^ 0xFFFF).to_le_bytes());
    out[at + 0x1E..at + 0x20].copy_from_slice(&c.to_le_bytes());
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A fake LoROM or HiROM image with a plausible header.
    fn image(size: usize, at: usize) -> Vec<u8> {
        let mut rom = vec![0u8; size];
        rom[at..at + 21].copy_from_slice(b"TEST GAME            ");
        rom[at + 0x15] = if at == 0x7FC0 { 0x20 } else { 0x21 };
        rom[at + 0x3C..at + 0x3E].copy_from_slice(&0x8000u16.to_le_bytes());
        fix_checksum(&rom, None).unwrap()
    }

    #[test]
    fn detects_lorom_and_hirom() {
        assert_eq!(detect_mapping(&image(0x80000, 0x7FC0)), Mapping::LoRom);
        assert_eq!(detect_mapping(&image(0x80000, 0xFFC0)), Mapping::HiRom);
        assert_eq!(
            detect_mapping(&[0u8; 0x100]),
            Mapping::LoRom,
            "nothing valid: LoROM"
        );
        let mut sa1 = image(0x80000, 0x7FC0);
        sa1[0x7FD5] = 0x23;
        assert_eq!(code_mapping(&sa1), Mapping::Sa1);
    }

    #[test]
    fn a_matching_checksum_beats_a_decoy_header() {
        // Mario's Early Years - Fun with Numbers: a HiROM game with a leftover, well-formed
        // "SNES Kernel" header at the LoROM spot. Only the real header's checksum matches.
        let mut rom = image(0x80000, 0xFFC0);
        rom[0x7FC0..0x7FC0 + 21].copy_from_slice(b"SNES Kernel          ");
        rom[0x7FD5] = 0x31;
        rom[0x7FFC..0x7FFE].copy_from_slice(&0x8000u16.to_le_bytes());
        rom[0x7FDC..0x7FE0].copy_from_slice(&[0x34, 0x12, 0xCB, 0xED]); // valid pair, wrong sum
        let rom = fix_checksum(&rom, Some(Mapping::HiRom)).unwrap();
        assert_eq!(
            score(&rom, 0x7FC0),
            score(&rom, 0xFFC0),
            "equal without the sum"
        );
        assert_eq!(detect_mapping(&rom), Mapping::HiRom);
    }

    #[test]
    fn checksum_matches_definition() {
        let rom = image(0x80000, 0x7FC0);
        let c = checksum(&rom);
        assert_eq!(
            c,
            (rom.iter().map(|&b| b as u32).sum::<u32>() & 0xFFFF) as u16
        );
        assert_eq!(word(&rom, 0x7FDE) as u16, c);
        assert_eq!(word(&rom, 0x7FDC) as u16, c ^ 0xFFFF);
        assert_eq!(checksum(&[]), 0);
    }

    #[test]
    fn checksum_mirrors_odd_sizes() {
        // 3 MiB: the top 1 MiB counts twice.
        let mut rom = vec![1u8; 0x30_0000];
        rom[0x20_0000] = 2;
        let expected = (0x20_0000u64 + (0x10_0000 + 1) * 2) & 0xFFFF;
        assert_eq!(checksum(&rom) as u64, expected);
    }

    #[test]
    fn fix_checksum_at_a_given_mapping() {
        // No valid header anywhere: detection says LoROM, but HiROM was asked for.
        let out = fix_checksum(&vec![1u8; 0x10000], Some(Mapping::HiRom)).unwrap();
        assert_eq!(word(&out, 0xFFDE) as u16, checksum(&out));
        assert_eq!(&out[0x7FDC..0x7FE0], &[1, 1, 1, 1]);
    }

    #[test]
    fn copier_header() {
        assert_eq!(strip_copier_header(&[0u8; 0x80200]).len(), 0x80000);
        assert_eq!(strip_copier_header(&[0u8; 0x80000]).len(), 0x80000);
    }

    #[test]
    fn fix_checksum_refuses_tiny_files() {
        assert!(fix_checksum(&[0u8; 16], None).is_err());
    }
}
