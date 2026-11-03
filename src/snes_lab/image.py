"""Frames as PNGs, contact sheets and crop hashes. Standard library only."""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass


@dataclass
class Frame:
    """A packed RGB565 frame as the core delivers it."""

    data: bytes
    width: int
    height: int

    def rgb(self) -> bytes:
        out = bytearray(self.width * self.height * 3)
        d = self.data
        for i in range(self.width * self.height):
            v = d[2 * i] | d[2 * i + 1] << 8
            out[3 * i] = (v >> 11 & 31) * 255 // 31
            out[3 * i + 1] = (v >> 5 & 63) * 255 // 63
            out[3 * i + 2] = (v & 31) * 255 // 31
        return bytes(out)

    def crop(self, x: int, y: int, w: int, h: int) -> Frame:
        x, y = max(0, x), max(0, y)
        w, h = min(w, self.width - x), min(h, self.height - y)
        rows = [self.data[((y + r) * self.width + x) * 2 : ((y + r) * self.width + x + w) * 2] for r in range(h)]
        return Frame(b''.join(rows), w, h)

    def digest(self) -> str:
        return hashlib.sha1(self.data).hexdigest()[:12]


def png(rgb: bytes, width: int, height: int) -> bytes:
    stride = width * 3
    raw = b''.join(b'\x00' + rgb[y * stride : (y + 1) * stride] for y in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))

    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(raw, 6))
        + chunk(b'IEND', b'')
    )


def frame_png(frame: Frame, scale: int = 1) -> bytes:
    rgb, w, h = frame.rgb(), frame.width, frame.height
    if scale > 1:
        rgb, w, h = _scale(rgb, w, h, scale), w * scale, h * scale
    return png(rgb, w, h)


def _scale(rgb: bytes, w: int, h: int, k: int) -> bytes:
    out = bytearray()
    for y in range(h):
        row = bytearray()
        for x in range(w):
            row += rgb[(y * w + x) * 3 : (y * w + x) * 3 + 3] * k
        out += bytes(row) * k
    return bytes(out)


def sheet(frames: list[Frame], cols: int = 4, gap: int = 2) -> bytes:
    """Frames in a grid, left to right then down, with a dark gap between cells.

    No text is drawn (no font in the standard library): callers return captions alongside."""
    if not frames:
        raise ValueError('No frames for a contact sheet.')
    cw, ch = max(f.width for f in frames), max(f.height for f in frames)
    cols = max(1, min(cols, len(frames)))
    rows = (len(frames) + cols - 1) // cols
    W, H = cols * cw + (cols - 1) * gap, rows * ch + (rows - 1) * gap
    canvas = bytearray(b'\x20\x20\x20' * (W * H))
    for k, f in enumerate(frames):
        r, c = divmod(k, cols)
        ox, oy = c * (cw + gap), r * (ch + gap)
        rgb = f.rgb()
        for y in range(f.height):
            dst = ((oy + y) * W + ox) * 3
            canvas[dst : dst + f.width * 3] = rgb[y * f.width * 3 : (y + 1) * f.width * 3]
    return png(bytes(canvas), W, H)
