//! HAL Laboratory's compression (Kirby Super Star, EarthBound and other HAL games).
//!
//! A port of exhal/inhal 1.21 by Devin Acker (MIT licence, see `LICENSES/exhal.txt`).
//! [`pack`] produces the same bytes as `inhal` for the same options; [`unpack`] matches `exhal`.
//! Where exhal would silently write a truncated stream (input that does not fit in 64 KiB once
//! compressed) this returns an error instead.
//!
//! Stream format: command bytes `CCCLLLLL` (length L+1) or long form `111CCCLL LLLLLLLL`;
//! 0 raw, 1 byte RLE, 2 word RLE, 3 increasing byte sequence, 4 back reference, 5 bit-reversed
//! back reference, 6 backwards back reference (offsets big-endian, from the start of the
//! output); 0xFF ends the data.

use crate::{Error, Result};
use std::collections::HashMap;

/// The largest input and output the format (and the games' decompressors) handle.
pub const DATA_SIZE: usize = 0x10000;
const RUN_SIZE: usize = 32;
const LONG_RUN_SIZE: usize = 1024;

/// Compression settings, as `inhal`'s `-1` (fast), `-2` (default), `-3` (fast + optimal) and
/// `-4` (optimal).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Options {
    /// Skip the less common methods (sequence RLE, rotated and backwards references).
    pub fast: bool,
    /// Shortest-path search over all candidate encodings instead of a greedy pass.
    pub optimal: bool,
}

/// Reverse the bit order of a byte (method 5 back references use it).
fn rotate(b: u8) -> u8 {
    b.reverse_bits()
}

fn combine(w: u8, x: u8, y: u8, z: u8) -> u32 {
    u32::from(w) << 24 | u32::from(x) << 16 | u32::from(y) << 8 | u32::from(z)
}

/// Decompress one stream from the start of `packed`. Returns the data and how many bytes of
/// `packed` it used (including the 0xFF terminator).
pub fn unpack(packed: &[u8]) -> Result<(Vec<u8>, usize)> {
    let truncated = || Error::new("The compressed data ends early.");
    let too_big = || Error::new("The compressed data unpacks to more than 64 KiB.");
    let bad_ref = || Error::new("The compressed data has a back reference outside its output.");
    // The games decompress into a 64 KiB buffer; reads ahead of the output see zeros, as in exhal.
    let mut out = vec![0u8; DATA_SIZE];
    let (mut inpos, mut outpos) = (0usize, 0usize);
    let next = |inpos: &mut usize| -> Result<u8> {
        let b = *packed
            .get(*inpos)
            .filter(|_| *inpos < DATA_SIZE)
            .ok_or_else(truncated)?;
        *inpos += 1;
        Ok(b)
    };
    loop {
        let input = next(&mut inpos)?;
        if input == 0xFF {
            break;
        }
        let (command, length) = if input & 0xE0 == 0xE0 {
            let low = next(&mut inpos)?;
            (
                (input >> 2) & 7,
                ((usize::from(input & 3) << 8) | usize::from(low)) + 1,
            )
        } else {
            (input >> 5, usize::from(input & 0x1F) + 1)
        };
        if (command == 2 && outpos + 2 * length > DATA_SIZE) || outpos + length > DATA_SIZE {
            return Err(too_big());
        }
        match command {
            0 => {
                let src = packed.get(inpos..inpos + length).ok_or_else(truncated)?;
                out[outpos..outpos + length].copy_from_slice(src);
                outpos += length;
                inpos += length;
            }
            1 => {
                let v = next(&mut inpos)?;
                out[outpos..outpos + length].fill(v);
                outpos += length;
            }
            2 => {
                let (a, b) = (next(&mut inpos)?, next(&mut inpos)?);
                for _ in 0..length {
                    out[outpos] = a;
                    out[outpos + 1] = b;
                    outpos += 2;
                }
            }
            3 => {
                let v = next(&mut inpos)?;
                for i in 0..length {
                    out[outpos] = v.wrapping_add(i as u8);
                    outpos += 1;
                }
            }
            _ => {
                let offset = (usize::from(next(&mut inpos)?) << 8) | usize::from(next(&mut inpos)?);
                match command {
                    // 7 is not a real method but the games' decompressor treats it as 4.
                    4 | 7 => {
                        if offset + length > DATA_SIZE {
                            return Err(bad_ref());
                        }
                        for i in 0..length {
                            out[outpos] = out[offset + i];
                            outpos += 1;
                        }
                    }
                    5 => {
                        if offset + length > DATA_SIZE {
                            return Err(bad_ref());
                        }
                        for i in 0..length {
                            out[outpos] = rotate(out[offset + i]);
                            outpos += 1;
                        }
                    }
                    _ => {
                        if offset + 1 < length {
                            return Err(bad_ref());
                        }
                        for i in 0..length {
                            out[outpos] = out[offset - i];
                            outpos += 1;
                        }
                    }
                }
            }
        }
    }
    out.truncate(outpos);
    Ok((out, inpos))
}

#[derive(Debug, Clone, Copy, Default)]
struct Backref {
    offset: usize,
    size: usize,
    method: u8, // 0 forward, 1 bit-reversed, 2 backwards
}

#[derive(Debug, Clone, Copy, Default)]
struct Rle {
    size: usize,
    data: u16,
    method: u8, // 0 byte, 1 word, 2 increasing sequence
}

const RLE_16: u8 = 1;

fn backref_outsize(b: &Backref) -> usize {
    // sizes are at least 3, so this is exhal's `size - 1 >= RUN_SIZE`
    if b.size > RUN_SIZE { 4 } else { 3 }
}

fn rle_outsize(r: &Rle) -> usize {
    (if r.size > RUN_SIZE { 3 } else { 2 }) + usize::from(r.method == RLE_16)
}

/// First occurrence of `needle` in `hay` (C's memmem).
fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.len() > hay.len() {
        return None;
    }
    hay.windows(needle.len()).position(|w| w == needle)
}

struct Packer<'a> {
    input: &'a [u8],
    inpos: usize,
    out: Vec<u8>,
    dontpack: Vec<u8>,
    /// First position of every 4-byte sequence (except the last one, as in exhal).
    offsets: HashMap<u32, usize>,
}

/// A write did not fit in the 64 KiB output.
struct Full;

impl<'a> Packer<'a> {
    fn new(input: &'a [u8]) -> Self {
        let mut offsets = HashMap::new();
        if input.len() >= 4 {
            for i in 0..input.len() - 4 {
                offsets
                    .entry(combine(input[i], input[i + 1], input[i + 2], input[i + 3]))
                    .or_insert(i);
            }
        }
        Packer {
            input,
            inpos: 0,
            out: Vec::new(),
            dontpack: Vec::new(),
            offsets,
        }
    }

    fn left(&self) -> usize {
        self.input.len() - self.inpos
    }

    fn rle_candidate(c: &mut Rle, mut size: usize, data: u16, method: u8) {
        if method == RLE_16 && size >= 2 * LONG_RUN_SIZE {
            size = 2 * LONG_RUN_SIZE;
        } else if size > LONG_RUN_SIZE {
            size = LONG_RUN_SIZE;
        }
        if size > 2 && size > c.size {
            *c = Rle { size, data, method };
        }
    }

    fn rle_check(&self, fast: bool) -> Rle {
        let d = self.input;
        let (cur, n) = (self.inpos, d.len());
        let mut c = Rle::default();
        let mut size = 0;
        while size <= LONG_RUN_SIZE && cur + size < n && d[cur + size] == d[cur] {
            size += 1;
        }
        Self::rle_candidate(&mut c, size, u16::from(d[cur]), 0);
        if n - cur >= 2 {
            let first = u16::from(d[cur]) | u16::from(d[cur + 1]) << 8;
            size = 0;
            while size <= 2 * LONG_RUN_SIZE
                && cur + size < n - 1
                && (u16::from(d[cur + size]) | u16::from(d[cur + size + 1]) << 8) == first
            {
                size += 2;
            }
            Self::rle_candidate(&mut c, size, first, RLE_16);
        }
        if fast {
            return c;
        }
        // The sequence must not wrap past 0xFF (exhal compares as int).
        size = 0;
        while size <= LONG_RUN_SIZE
            && cur + size < n
            && usize::from(d[cur + size]) == usize::from(d[cur]) + size
        {
            size += 1;
        }
        Self::rle_candidate(&mut c, size, u16::from(d[cur]), 2);
        c
    }

    fn backref_candidate(c: &mut Backref, offset: usize, mut size: usize, method: u8) {
        if size > LONG_RUN_SIZE {
            size = LONG_RUN_SIZE;
        }
        if size >= 4 && size > c.size {
            *c = Backref {
                offset,
                size,
                method,
            };
        }
    }

    fn ref_search(&self, fast: bool) -> Backref {
        let d = self.input;
        let (cur, n) = (self.inpos, d.len());
        let mut c = Backref::default();

        // Forward references: start at the first copy of these 4 bytes, then hop to each later
        // copy of the longest match so far.
        let mut size = 4;
        if let Some(&first) = self
            .offsets
            .get(&combine(d[cur], d[cur + 1], d[cur + 2], d[cur + 3]))
        {
            let mut pos = Some(first);
            while let Some(p) = pos.filter(|&p| p < cur) {
                while size <= LONG_RUN_SIZE && cur + size < n && d[p + size] == d[cur + size] {
                    size += 1;
                }
                Self::backref_candidate(&mut c, p, size, 0);
                pos = find(&d[p + 1..n], &d[p..p + size]).map(|k| p + 1 + k);
            }
        }
        if fast {
            return c;
        }

        // Bit-reversed references.
        size = 4;
        let key = combine(
            rotate(d[cur]),
            rotate(d[cur + 1]),
            rotate(d[cur + 2]),
            rotate(d[cur + 3]),
        );
        if let Some(&first) = self.offsets.get(&key) {
            let mut pos = Some(first);
            while let Some(p) = pos.filter(|&p| p < cur) {
                while size <= LONG_RUN_SIZE
                    && cur + size < n
                    && d[p + size] == rotate(d[cur + size])
                {
                    size += 1;
                }
                Self::backref_candidate(&mut c, p, size, 1);
                pos = find(&d[p + 1..n], &d[p..p + size]).map(|k| p + 1 + k);
            }
        }

        // Backwards references: every position after the first reversed copy.
        if let Some(&first) = self
            .offsets
            .get(&combine(d[cur + 3], d[cur + 2], d[cur + 1], d[cur]))
        {
            let mut p = first + 3;
            while p < cur {
                let mut size = 0;
                while size <= LONG_RUN_SIZE
                    && size <= p
                    && cur + size < n
                    && d[p - size] == d[cur + size]
                {
                    size += 1;
                }
                Self::backref_candidate(&mut c, p, size, 2);
                p += 1;
            }
        }
        c
    }

    fn fits(&self, size: usize) -> bool {
        self.out.len() + self.dontpack.len() + size < DATA_SIZE
    }

    fn write_raw(&mut self) -> std::result::Result<(), Full> {
        if self.dontpack.is_empty() {
            return Ok(());
        }
        let size = self.dontpack.len() - 1;
        if size >= RUN_SIZE {
            if !self.fits(2) {
                return Err(Full);
            }
            self.out.push(0xE0 + (size >> 8) as u8);
            self.out.push((size & 0xFF) as u8);
        } else {
            if !self.fits(1) {
                return Err(Full);
            }
            self.out.push(size as u8);
        }
        self.out.append(&mut self.dontpack);
        Ok(())
    }

    fn write_backref(&mut self, b: &Backref) -> std::result::Result<(), Full> {
        if !self.fits(backref_outsize(b)) {
            return Err(Full);
        }
        self.write_raw()?;
        let size = b.size - 1;
        if size >= RUN_SIZE {
            self.out.push((0xF0 + (b.method << 2)) | (size >> 8) as u8);
            self.out.push((size & 0xFF) as u8);
        } else {
            self.out.push((0x80 + (b.method << 5)) | size as u8);
        }
        self.out.push((b.offset >> 8) as u8);
        self.out.push((b.offset & 0xFF) as u8);
        self.inpos += b.size;
        Ok(())
    }

    fn write_rle(&mut self, r: &Rle) -> std::result::Result<(), Full> {
        if !self.fits(rle_outsize(r)) {
            return Err(Full);
        }
        let size = if r.method == RLE_16 {
            r.size / 2 - 1
        } else {
            r.size - 1
        };
        self.write_raw()?;
        if size >= RUN_SIZE {
            self.out.push((0xE4 + (r.method << 2)) | (size >> 8) as u8);
            self.out.push((size & 0xFF) as u8);
        } else {
            self.out.push((0x20 + (r.method << 5)) | size as u8);
        }
        self.out.push((r.data & 0xFF) as u8);
        if r.method == RLE_16 {
            self.out.push((r.data >> 8) as u8);
        }
        self.inpos += r.size;
        Ok(())
    }

    fn write_next_byte(&mut self) -> std::result::Result<(), Full> {
        if !self.fits(1) {
            return Err(Full);
        }
        self.dontpack.push(self.input[self.inpos]);
        self.inpos += 1;
        if self.dontpack.len() == LONG_RUN_SIZE {
            self.write_raw()?;
        }
        Ok(())
    }

    fn write_trailer(&mut self) -> std::result::Result<(), Full> {
        if !self.fits(1) {
            return Err(Full);
        }
        self.write_raw()?;
        self.out.push(0xFF);
        Ok(())
    }

    /// The candidates at the current position: (backref, rle), as exhal weighs them.
    fn candidates(&self, fast: bool) -> (Backref, Rle) {
        let rle = self.rle_check(fast);
        let backref = if rle.size < LONG_RUN_SIZE && self.left() >= 4 {
            self.ref_search(fast)
        } else {
            Backref::default()
        };
        (backref, rle)
    }

    fn pack_greedy(&mut self, fast: bool) -> std::result::Result<(), Full> {
        while self.left() > 0 {
            let (backref, rle) = self.candidates(fast);
            if backref.size > rle.size {
                self.write_backref(&backref)?;
            } else if rle.size >= 2 {
                self.write_rle(&rle)?;
            } else {
                self.write_next_byte()?;
            }
        }
        Ok(())
    }

    fn pack_optimal(&mut self, fast: bool) -> std::result::Result<(), Full> {
        #[derive(Clone, Copy, Default)]
        struct Node {
            next: Option<usize>,
            prev: Option<usize>,
            neighbor: usize,
            length: usize,
            distance: usize,
            backref: bool,
            data: u16,
            method: u8,
        }
        let n = self.input.len();
        let mut nodes = vec![Node::default(); n + 1];
        for (pos, node) in nodes.iter_mut().enumerate().take(n) {
            self.inpos = pos;
            node.distance = 1 << 16;
            let (backref, rle) = self.candidates(fast);
            if backref.size > rle.size {
                node.neighbor = backref.size;
                node.length = backref_outsize(&backref);
                node.method = backref.method;
                node.data = backref.offset as u16;
                node.backref = true;
            } else if rle.size >= 2 {
                node.neighbor = rle.size;
                node.length = rle_outsize(&rle);
                node.method = rle.method;
                node.data = rle.data;
            }
        }
        // Shortest path: each byte either goes out raw (cost 2, a literal plus its share of a
        // command byte) or jumps ahead by its candidate's length.
        nodes[0].distance = 0;
        nodes[n].distance = 1 << 16;
        for i in 0..n {
            let d = nodes[i].distance + 2;
            if d < nodes[i + 1].distance {
                nodes[i + 1].distance = d;
                nodes[i + 1].prev = Some(i);
            }
            if nodes[i].neighbor == 0 {
                continue;
            }
            let other = i + nodes[i].neighbor;
            let d = nodes[i].distance + nodes[i].length;
            if d < nodes[other].distance {
                nodes[other].distance = d;
                nodes[other].prev = Some(i);
            }
        }
        let mut at = n;
        while let Some(p) = nodes[at].prev {
            nodes[p].next = Some(at);
            at = p;
        }
        self.inpos = 0;
        let mut at = 0;
        while let Some(next) = nodes[at].next {
            let node = nodes[at];
            if next == at + 1 {
                self.write_next_byte()?;
            } else if node.backref {
                self.write_backref(&Backref {
                    offset: usize::from(node.data),
                    size: node.neighbor,
                    method: node.method,
                })?;
            } else {
                self.write_rle(&Rle {
                    size: node.neighbor,
                    data: node.data,
                    method: node.method,
                })?;
            }
            at = next;
        }
        Ok(())
    }
}

/// Compress up to 64 KiB.
pub fn pack(input: &[u8], options: Options) -> Result<Vec<u8>> {
    if input.len() > DATA_SIZE {
        return Err(Error::new(format!(
            "HAL compression takes at most 64 KiB; this is {} bytes.",
            input.len()
        )));
    }
    let mut p = Packer::new(input);
    let done = if input.is_empty() {
        Ok(())
    } else if options.optimal {
        p.pack_optimal(options.fast)
    } else {
        p.pack_greedy(options.fast)
    };
    match done.and_then(|()| p.write_trailer()) {
        Ok(()) if p.inpos == input.len() => Ok(p.out),
        _ => Err(Error::new(
            "The data does not fit in 64 KiB once compressed.",
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const ALL: [Options; 4] = [
        Options {
            fast: true,
            optimal: false,
        },
        Options {
            fast: false,
            optimal: false,
        },
        Options {
            fast: true,
            optimal: true,
        },
        Options {
            fast: false,
            optimal: true,
        },
    ];

    fn round_trip(data: &[u8]) {
        for o in ALL {
            let packed = pack(data, o).unwrap();
            let (out, used) = unpack(&packed).unwrap();
            assert_eq!(out, data, "{o:?}");
            assert_eq!(used, packed.len(), "{o:?}");
        }
    }

    /// A small xorshift so tests are deterministic without a dependency.
    fn noise(n: usize, seed: u32) -> Vec<u8> {
        let mut s = seed;
        (0..n)
            .map(|_| {
                s ^= s << 13;
                s ^= s >> 17;
                s ^= s << 5;
                s as u8
            })
            .collect()
    }

    #[test]
    fn empty_is_just_the_terminator() {
        assert_eq!(pack(&[], Options::default()).unwrap(), vec![0xFF]);
        assert_eq!(unpack(&[0xFF]).unwrap(), (vec![], 1));
    }

    #[test]
    fn edge_inputs_round_trip() {
        round_trip(b"a");
        round_trip(b"abc");
        round_trip(b"abcd");
        round_trip(&[7; 31]);
        round_trip(&[7; 33]);
        round_trip(&[7; 1024]);
        round_trip(&[7; 1025]);
        round_trip(&[7; 5000]);
        round_trip(&(0..=255u8).cycle().take(3000).collect::<Vec<_>>());
        round_trip(&[0xFC, 0xFD, 0xFE, 0xFF, 0x00, 0x01, 0x02]);
        round_trip(&[1, 2].repeat(3000));
        let mut mixed = noise(2000, 1);
        mixed.extend(mixed.clone().iter().rev());
        mixed.extend(
            mixed[..500]
                .iter()
                .map(|b| b.reverse_bits())
                .collect::<Vec<_>>(),
        );
        round_trip(&mixed);
        round_trip(&noise(1500, 99));
    }

    #[test]
    fn methods_are_used() {
        let packed = pack(&[5; 100], Options::default()).unwrap();
        assert_eq!(packed, vec![0xE4, 99, 5, 0xFF], "one long byte run");
        let seq: Vec<u8> = (10..40).collect();
        assert_eq!(
            pack(&seq, Options::default()).unwrap(),
            vec![0x60 | 29, 10, 0xFF],
            "one sequence"
        );
        assert_eq!(
            pack(
                &seq,
                Options {
                    fast: true,
                    optimal: false
                }
            )
            .unwrap()[0],
            29,
            "fast: raw"
        );
    }

    #[test]
    fn full_size_input() {
        round_trip(&vec![0u8; DATA_SIZE]);
        let big = noise(DATA_SIZE, 5);
        assert!(
            pack(&big, Options::default()).is_err(),
            "random 64 KiB does not fit"
        );
        assert!(pack(&vec![0u8; DATA_SIZE + 1], Options::default()).is_err());
    }

    #[test]
    fn bad_streams_are_errors() {
        assert!(unpack(&[]).is_err());
        assert!(unpack(&[0x03, 1, 2]).is_err(), "raw run past the end");
        assert!(unpack(&[0x80, 0x00]).is_err(), "reference cut short");
        assert!(
            unpack(&[0xC5, 0x00, 0x00, 0xFF]).is_err(),
            "backwards reference before the start"
        );
        let mut big: Vec<u8> = [0xE7, 0xFF, 1].repeat(65);
        big.push(0xFF);
        assert!(unpack(&big).is_err(), "more than 64 KiB");
    }

    #[test]
    fn reads_ahead_of_the_output_see_zeros() {
        // A forward reference to bytes not written yet, like exhal's zeroed buffer.
        assert_eq!(unpack(&[0x83, 0x00, 0x10, 0xFF]).unwrap().0, vec![0; 4]);
    }
}
