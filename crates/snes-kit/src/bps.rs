//! BPS patches (beat v1). [`make`] writes SourceRead/TargetRead runs (fast, not the smallest
//! patch); [`apply`] reads all four actions and checks all three CRCs.

use crate::crc32::crc32;
use crate::header::strip_copier_header;
use crate::{Error, Result};

fn corrupt() -> Error {
    Error::new("The patch file is corrupt (patch CRC mismatch).")
}

fn encode(mut n: u64, out: &mut Vec<u8>) {
    loop {
        let digit = (n & 0x7F) as u8;
        n >>= 7;
        if n == 0 {
            out.push(digit | 0x80);
            return;
        }
        out.push(digit);
        n -= 1;
    }
}

fn decode(data: &[u8], i: &mut usize) -> Result<u64> {
    let (mut n, mut shift) = (0u64, 1u64);
    loop {
        let b = *data.get(*i).ok_or_else(corrupt)?;
        *i += 1;
        n = n
            .checked_add(u64::from(b & 0x7F).checked_mul(shift).ok_or_else(corrupt)?)
            .ok_or_else(corrupt)?;
        if b & 0x80 != 0 {
            return Ok(n);
        }
        shift = shift
            .checked_shl(7)
            .filter(|&s| s < 1 << 56)
            .ok_or_else(corrupt)?;
        n = n.checked_add(shift).ok_or_else(corrupt)?;
    }
}

/// A patch that turns `source` into `target`.
pub fn make(source: &[u8], target: &[u8], metadata: &str) -> Vec<u8> {
    let mut patch = b"BPS1".to_vec();
    encode(source.len() as u64, &mut patch);
    encode(target.len() as u64, &mut patch);
    encode(metadata.len() as u64, &mut patch);
    patch.extend_from_slice(metadata.as_bytes());
    let same_at = |k: usize| k < source.len() && source[k] == target[k];
    let mut i = 0;
    while i < target.len() {
        let same = same_at(i);
        let mut j = i + 1;
        while j < target.len() && same_at(j) == same {
            j += 1;
        }
        encode((((j - i - 1) as u64) << 2) | u64::from(!same), &mut patch);
        if !same {
            patch.extend_from_slice(&target[i..j]);
        }
        i = j;
    }
    patch.extend_from_slice(&crc32(source).to_le_bytes());
    patch.extend_from_slice(&crc32(target).to_le_bytes());
    let own = crc32(&patch);
    patch.extend_from_slice(&own.to_le_bytes());
    patch
}

fn le32(b: &[u8]) -> u32 {
    u32::from_le_bytes([b[0], b[1], b[2], b[3]])
}

/// Apply `patch` to `source`. A source with a copier header is retried without it.
pub fn apply(source: &[u8], patch: &[u8]) -> Result<Vec<u8>> {
    if patch.len() < 4 || &patch[..4] != b"BPS1" {
        return Err(Error::new("Not a BPS patch (missing BPS1 magic)."));
    }
    if patch.len() < 4 + 3 + 12
        || crc32(&patch[..patch.len() - 4]) != le32(&patch[patch.len() - 4..])
    {
        return Err(corrupt());
    }
    let end = patch.len() - 12;
    let (src_crc, dst_crc) = (le32(&patch[end..]), le32(&patch[end + 4..]));
    let mut source = source;
    if crc32(source) != src_crc {
        let stripped = strip_copier_header(source);
        if stripped.len() != source.len() && crc32(stripped) == src_crc {
            source = stripped;
        } else {
            return Err(Error::new(format!(
                "This patch is for a different ROM (needs CRC32 {src_crc:08X}, got {:08X}).",
                crc32(source)
            )));
        }
    }
    let mut i = 4;
    let src_size = decode(patch, &mut i)?;
    let dst_size = usize::try_from(decode(patch, &mut i)?).map_err(|_| corrupt())?;
    let meta_size = usize::try_from(decode(patch, &mut i)?).map_err(|_| corrupt())?;
    i = i
        .checked_add(meta_size)
        .filter(|&k| k <= end)
        .ok_or_else(corrupt)?;
    if dst_size > 1 << 30 {
        return Err(corrupt());
    }
    let mut out = vec![0u8; dst_size];
    let (mut pos, mut src_rel, mut dst_rel) = (0usize, 0i64, 0i64);
    let failed = || Error::new("Patched output failed its CRC check.");
    while i < end {
        let data = decode(patch, &mut i)?;
        let length = usize::try_from((data >> 2) + 1).map_err(|_| corrupt())?;
        let span = pos
            .checked_add(length)
            .filter(|&k| k <= out.len())
            .ok_or_else(failed)?;
        match data & 3 {
            0 => {
                let from = source.get(pos..span).ok_or_else(failed)?;
                out[pos..span].copy_from_slice(from);
            }
            1 => {
                let from = patch
                    .get(i..i + length)
                    .filter(|_| i + length <= end)
                    .ok_or_else(corrupt)?;
                out[pos..span].copy_from_slice(from);
                i += length;
            }
            action => {
                let rel = decode(patch, &mut i)?;
                let rel = if rel & 1 != 0 {
                    -((rel >> 1) as i64)
                } else {
                    (rel >> 1) as i64
                };
                if action == 2 {
                    src_rel += rel;
                    let at = usize::try_from(src_rel).map_err(|_| failed())?;
                    let from = source.get(at..at + length).ok_or_else(failed)?;
                    out[pos..span].copy_from_slice(from);
                    src_rel += length as i64;
                } else {
                    // TargetCopy may overlap its own output, so byte by byte.
                    dst_rel += rel;
                    for k in 0..length {
                        let at = usize::try_from(dst_rel + k as i64).map_err(|_| failed())?;
                        if at >= pos + k {
                            return Err(failed());
                        }
                        out[pos + k] = out[at];
                    }
                    dst_rel += length as i64;
                }
            }
        }
        pos = span;
    }
    if src_size != source.len() as u64 || crc32(&out) != dst_crc {
        return Err(failed());
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn number_encoding_round_trips() {
        for n in [
            0u64,
            1,
            127,
            128,
            129,
            16383,
            16384,
            16511,
            1 << 32,
            (1 << 50) + 12345,
        ] {
            let mut buf = Vec::new();
            encode(n, &mut buf);
            let mut i = 0;
            assert_eq!(decode(&buf, &mut i).unwrap(), n);
            assert_eq!(i, buf.len());
        }
    }

    #[test]
    fn round_trips() {
        let cases: [(&[u8], &[u8]); 5] = [
            (b"abc", b"axcdef"),
            (b"", b"new"),
            (b"same", b"same"),
            (&[0; 300], &[0; 200]),
            (b"longer source", b""),
        ];
        for (s, t) in cases {
            let p = make(s, t, "meta");
            assert_eq!(apply(s, &p).unwrap(), t);
        }
    }

    #[test]
    fn matches_the_python_encoder_byte_for_byte() {
        // Expected bytes from an independent Python BPS maker: make_bps(b'abc', b'axcdef'), no metadata.
        let expected = [
            0x42, 0x50, 0x53, 0x31, 0x83, 0x86, 0x80, 0x80, 0x81, 0x78, 0x80, 0x89, 0x64, 0x65,
            0x66,
        ];
        let p = make(b"abc", b"axcdef", "");
        assert_eq!(&p[..expected.len()], &expected);
        assert_eq!(p.len(), expected.len() + 12);
    }

    #[test]
    fn source_copy_and_target_copy() {
        // Hand-built patch: SourceCopy 3 bytes from 2, then TargetCopy 4 from 0 (overlapping).
        let source = b"..xyz".to_vec();
        let target = b"xyzxyzx".to_vec();
        let mut p = b"BPS1".to_vec();
        encode(5, &mut p);
        encode(7, &mut p);
        encode(0, &mut p);
        encode(((3 - 1) << 2) | 2, &mut p);
        encode(2 << 1, &mut p);
        encode(((4 - 1) << 2) | 3, &mut p);
        encode(0, &mut p);
        p.extend_from_slice(&crc32(&source).to_le_bytes());
        p.extend_from_slice(&crc32(&target).to_le_bytes());
        let c = crc32(&p);
        p.extend_from_slice(&c.to_le_bytes());
        assert_eq!(apply(&source, &p).unwrap(), target);
    }

    #[test]
    fn headered_source_is_retried() {
        let s = vec![7u8; 0x800];
        let mut t = s.clone();
        t[5] = 9;
        let p = make(&s, &t, "");
        let mut headered = vec![0u8; 0x200];
        headered.extend_from_slice(&s);
        assert_eq!(apply(&headered, &p).unwrap(), t);
    }

    #[test]
    fn bad_patches_are_errors_not_panics() {
        let p = make(b"abc", b"abd", "");
        assert!(apply(b"abc", b"nope").unwrap_err().0.contains("magic"));
        assert!(apply(b"abc", b"BPS1").unwrap_err().0.contains("corrupt"));
        let mut flipped = p.clone();
        flipped[6] ^= 1;
        assert!(apply(b"abc", &flipped).unwrap_err().0.contains("corrupt"));
        assert!(apply(b"xyz", &p).unwrap_err().0.contains("different ROM"));
        // Truncated body with a recomputed CRC: still an error.
        let mut short = p[..p.len() - 12].to_vec();
        short.truncate(short.len() - 1);
        short.extend_from_slice(&crc32(b"abc").to_le_bytes());
        short.extend_from_slice(&crc32(b"abd").to_le_bytes());
        let c = crc32(&short);
        short.extend_from_slice(&c.to_le_bytes());
        assert!(apply(b"abc", &short).is_err());
    }
}
