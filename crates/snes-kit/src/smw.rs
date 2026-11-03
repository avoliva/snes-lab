//! Super Mario World level data: the layer-1 object stream and the sprite list.
//!
//! Coordinates are in tiles. A screen is 16 tiles wide; horizontal levels are 27 tiles tall.

use crate::{Error, Result};

/// Object number 0x01 is "screen jump" in SMW's object stream.
const SCREEN_JUMP: u8 = 0x01;

/// One layer-1 object: `number` 0 means an extended object whose number is `settings`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Object {
    pub x: i32,
    pub y: i32,
    pub number: u8,
    pub settings: u8,
}

/// One sprite: `y` is its bottom-left tile.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct Sprite {
    pub x: i32,
    pub y: i32,
    pub number: u8,
}

fn byte(value: i64, what: &str) -> Result<u8> {
    u8::try_from(value).map_err(|_| Error::new(format!("{what} does not fit in a byte ({value}).")))
}

/// The object stream that follows the 5-byte level header, ending in 0xFF. Objects are sorted by
/// screen and column, and a screen jump precedes each new screen, so any order of input is valid.
/// Objects at negative coordinates are left out.
pub fn layer1(objects: &[Object]) -> Result<Vec<u8>> {
    let mut sorted: Vec<&Object> = objects.iter().collect();
    sorted.sort_by_key(|o| (o.x.div_euclid(16), o.x, o.y));
    let mut out = Vec::with_capacity(objects.len() * 3 + 1);
    let mut page = 0i64;
    for o in sorted {
        if o.x < 0 || o.y < 0 {
            continue;
        }
        if o.y > 0x1F {
            return Err(Error::new(format!(
                "Object row {} is below the 32-row object grid.",
                o.y
            )));
        }
        if o.number > 0x3F {
            return Err(Error::new(format!(
                "Object number {:#x} is above 0x3F.",
                o.number
            )));
        }
        let p = i64::from(o.x / 16);
        if p != page {
            out.extend_from_slice(&[byte(p, "Screen number")?, 0, SCREEN_JUMP]);
            page = p;
        }
        let n = o.number;
        out.push(((n & 0x30) << 1) | o.y as u8);
        out.push(((n & 0x0F) << 4) | (o.x & 15) as u8);
        out.push(o.settings);
    }
    out.push(0xFF);
    Ok(out)
}

/// A sprite list: the one-byte sprite header, three bytes per sprite (sorted), then 0xFF.
pub fn sprites(header: u8, entries: &[Sprite]) -> Result<Vec<u8>> {
    let mut sorted = entries.to_vec();
    sorted.sort();
    let mut out = Vec::with_capacity(entries.len() * 3 + 2);
    out.push(header);
    for s in sorted {
        if s.x < 0 || s.y < 0 || s.y > 0x1F || s.x >= 32 * 16 {
            return Err(Error::new(format!(
                "Sprite at ({}, {}) is outside a 32-screen level.",
                s.x, s.y
            )));
        }
        let (x, y) = (s.x as u32, s.y as u32);
        let screen = x / 16;
        out.push((((y & 15) << 4) | ((screen >> 4) << 1) | (y >> 4)) as u8);
        out.push((((x & 15) << 4) | (screen & 15)) as u8);
        out.push(s.number);
    }
    out.push(0xFF);
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn o(x: i32, y: i32, number: u8, settings: u8) -> Object {
        Object {
            x,
            y,
            number,
            settings,
        }
    }

    #[test]
    fn layer1_matches_reference() {
        // Expected bytes from an independent Python encoder run on the same list.
        let objs = [
            o(20, 24, 0x14, 0x2F),
            o(0, 24, 0x14, 0x22),
            o(5, 10, 0, 0x46),
            o(-1, 3, 1, 1),
        ];
        assert_eq!(
            layer1(&objs).unwrap(),
            vec![
                0x38, 0x40, 0x22, 0x0A, 0x05, 0x46, 0x01, 0x00, 0x01, 0x38, 0x44, 0x2F, 0xFF
            ]
        );
        assert_eq!(layer1(&[]).unwrap(), vec![0xFF]);
    }

    #[test]
    fn layer1_rejects_what_does_not_fit() {
        assert!(layer1(&[o(0, 40, 1, 0)]).is_err());
        assert!(layer1(&[o(0, 0, 0x40, 0)]).is_err());
        assert!(layer1(&[o(16 * 300, 0, 1, 0)]).is_err());
    }

    #[test]
    fn sprites_match_reference() {
        // Expected bytes from an independent Python encoder: a goomba at (10, 23) and the goal tape.
        let list = [
            Sprite {
                x: 90,
                y: 23,
                number: 0x7B,
            },
            Sprite {
                x: 10,
                y: 23,
                number: 0x0F,
            },
        ];
        assert_eq!(
            sprites(0x00, &list).unwrap(),
            vec![0x00, 0x71, 0xA0, 0x0F, 0x71, 0xA5, 0x7B, 0xFF]
        );
        assert!(
            sprites(
                0,
                &[Sprite {
                    x: 600,
                    y: 0,
                    number: 1
                }]
            )
            .is_err()
        );
    }
}
