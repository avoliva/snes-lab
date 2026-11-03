"""SNES planar tiles (2, 4 or 8 bits per pixel) from VRAM bytes to a PNG sheet.

Tile format: 8x8 pixels. Each row is a pair of bytes per two bitplanes: 2bpp = 16 bytes/tile
(planes 0,1); 4bpp = 32 bytes (planes 0,1 then 2,3 at +16); 8bpp = 64 bytes (pairs at +0, +16, +32, +48).
The libretro core does not expose CGRAM, so colours are grey levels unless a palette is given
(BGR555 words, e.g. read from the game's palette buffer in WRAM)."""

from __future__ import annotations

from .image import _scale, png

BYTES_PER_TILE = {2: 16, 4: 32, 8: 64}


def decode(tile: bytes, bpp: int) -> list[int]:
    """64 colour indices, row by row."""
    out = []
    for row in range(8):
        for col in range(8):
            bit = 7 - col
            v = 0
            for pair in range(bpp // 2):
                base = pair * 16 + row * 2
                v |= (tile[base] >> bit & 1) << (2 * pair)
                v |= (tile[base + 1] >> bit & 1) << (2 * pair + 1)
            out.append(v)
    return out


def bgr555(word: int) -> tuple[int, int, int]:
    return ((word & 31) * 255 // 31, (word >> 5 & 31) * 255 // 31, (word >> 10 & 31) * 255 // 31)


def grey(bpp: int) -> list[tuple[int, int, int]]:
    n = 1 << bpp
    return [(i * 255 // (n - 1),) * 3 for i in range(n)]


def sheet(
    vram: bytes,
    bpp: int = 4,
    start: int = 0,
    count: int = 256,
    cols: int = 16,
    scale: int = 2,
    palette: list[int] | None = None,
) -> tuple[bytes, int]:
    """PNG of `count` tiles from byte offset `start`, `cols` per row. Returns (png, tiles drawn)."""
    if bpp not in BYTES_PER_TILE:
        raise ValueError('bpp must be 2, 4 or 8.')
    size = BYTES_PER_TILE[bpp]
    count = max(0, min(count, (len(vram) - start) // size))
    if count == 0:
        raise ValueError(f'No whole tiles at offset {start:#x} (VRAM is {len(vram):#x} bytes).')
    colours = [bgr555(w) for w in palette] if palette else grey(bpp)
    colours += [(255, 0, 255)] * ((1 << bpp) - len(colours))  # magenta = index past the given palette
    cols = max(1, min(cols, count))
    rows = (count + cols - 1) // cols
    w, h = cols * 8, rows * 8
    rgb = bytearray(w * h * 3)
    for t in range(count):
        tile = vram[start + t * size : start + (t + 1) * size]
        ox, oy = t % cols * 8, t // cols * 8
        for i, idx in enumerate(decode(tile, bpp)):
            p = ((oy + i // 8) * w + ox + i % 8) * 3
            rgb[p : p + 3] = bytes(colours[idx])
    data = bytes(rgb)
    if scale > 1:
        data, w, h = _scale(data, w, h, scale), w * scale, h * scale
    return png(data, w, h), count
