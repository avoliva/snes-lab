"""SNES ROM images: header, mapping, checksum, address maths, byte patches and BPS.

Works for any LoROM or HiROM game. A 512-byte copier header is detected and stripped.
The byte-level work (mapping detection, checksum, address maths, BPS) is snes-kit's
(crates/snes-kit in this repo)."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

import snes_kit as sk
from snes_kit import RomError, checksum, code_mapping, detect_mapping, strip_copier_header, to_address, to_offset

COPIER_HEADER = sk.COPIER_HEADER
_MAP_MODES = {
    0x20: 'LoROM',
    0x21: 'HiROM',
    0x23: 'SA-1',
    0x25: 'ExHiROM',
    0x30: 'LoROM FastROM',
    0x31: 'HiROM FastROM',
    0x32: 'SDD-1',
    0x35: 'ExHiROM FastROM',
    0x3A: 'SPC7110',
}

__all__ = [
    'COPIER_HEADER',
    'Header',
    'RomError',
    'bps_apply',
    'bps_make',
    'checksum',
    'code_mapping',
    'detect_mapping',
    'fix_checksum',
    'header',
    'hexdump',
    'load',
    'parse_address',
    'patch',
    'read',
    'strip_copier_header',
    'to_address',
    'to_offset',
]


def load(path: str | Path) -> bytes:
    """ROM bytes without any copier header."""
    data = Path(path).expanduser().read_bytes()
    return strip_copier_header(data)


@dataclass
class Header:
    title: str
    mapping: str
    map_mode: str
    rom_type: int
    rom_size_kb: int
    sram_size_kb: int
    region: int
    version: int
    checksum: str
    complement: str
    computed_checksum: str
    checksum_ok: bool
    file_size: int
    crc32: str
    sha256: str
    header_offset: str
    reset_vector: str
    nmi_vector: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def header(rom: bytes) -> Header:
    mapping = detect_mapping(rom)
    at = sk.header_offset(mapping)
    h = rom[at : at + 0x40]
    if len(h) < 0x40:
        raise RomError(f'The ROM is {len(rom):#x} bytes, too small to hold a header at {at:#x}.')
    stored = int.from_bytes(h[0x1E:0x20], 'little')
    computed = checksum(rom)
    return Header(
        title=h[:21].decode('ascii', 'replace').rstrip(),
        mapping=mapping,
        map_mode=_MAP_MODES.get(h[0x15], f'{h[0x15]:#04x}'),
        rom_type=h[0x16],
        rom_size_kb=1 << h[0x17] if h[0x17] < 16 else 0,
        sram_size_kb=(1 << h[0x18]) if h[0x18] else 0,
        region=h[0x19],
        version=h[0x1B],
        checksum=f'{stored:04X}',
        complement=f'{int.from_bytes(h[0x1C:0x1E], "little"):04X}',
        computed_checksum=f'{computed:04X}',
        checksum_ok=stored == computed,
        file_size=len(rom),
        crc32=f'{sk.crc32(rom):08X}',
        sha256=hashlib.sha256(rom).hexdigest(),
        header_offset=f'{at:#x}',
        reset_vector=f'${int.from_bytes(h[0x3C:0x3E], "little"):04X}',
        nmi_vector=f'${int.from_bytes(h[0x3A:0x3C], "little"):04X}',
    )


def fix_checksum(rom: bytes | bytearray) -> bytes:
    """Rewrite the internal checksum and complement."""
    return sk.fix_checksum(bytes(rom))


# --- address maths ---------------------------------------------------------


def parse_address(text: str | int) -> int:
    """'$05:E000', '05E000', '0x5e000', 385024 -> int."""
    return text if isinstance(text, int) else sk.parse_address(text)


def read(rom: bytes, address: int | str, length: int, mapping: str | None = None, offset: bool = False) -> bytes:
    at = parse_address(address) if offset else to_offset(parse_address(address), mapping or detect_mapping(rom))
    if at < 0 or at + length > len(rom):
        raise RomError(f'Read of {length} bytes at offset {at:#x} runs past the end of the ROM ({len(rom):#x}).')
    return rom[at : at + length]


def patch(rom: bytes, writes: list[tuple[int, bytes]], grow: bool = False) -> bytes:
    """Apply (file offset, bytes) writes. Refuses to write past the end unless grow=True."""
    out = bytearray(rom)
    for at, data in writes:
        if at + len(data) > len(out):
            if not grow:
                raise RomError(f'Write at {at:#x} runs past the end of the ROM ({len(out):#x}); pass grow to expand.')
            out += bytes(at + len(data) - len(out))
        out[at : at + len(data)] = data
    return bytes(out)


def hexdump(data: bytes, start: int = 0, width: int = 16) -> str:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        text = ''.join(chr(b) if 0x20 <= b < 0x7F else '.' for b in chunk)
        lines.append(f'{start + i:06X}  {chunk.hex(" ").upper():<{width * 3}} {text}')
    return '\n'.join(lines)


# --- BPS (beat patch v1) ---------------------------------------------------


def bps_make(source: bytes, target: bytes, metadata: str = '') -> bytes:
    """A BPS patch built from SourceRead and TargetRead runs (simple and fast; not the smallest patch)."""
    return sk.bps_make(source, target, metadata)


def bps_apply(source: bytes, patch_: bytes) -> bytes:
    """Apply a BPS patch, checking all three CRCs (a headered source is retried without the header)."""
    return sk.bps_apply(source, patch_)
