"""snes_kit's HAL codec against the exhal/inhal it was ported from, byte for byte.

Inputs: the real compressed blocks Kirby Super Star's menus use, a sweep of offsets across the
ROM (most are not compressed data, which exercises the error paths), slices of the ROM as
compression input, and synthetic edge cases."""

import random
import re
import subprocess
from pathlib import Path

import pytest
import snes_kit as sk

MENU_POINTERS = (0x15 * 0x8000 + (0xB3CA & 0x7FFF), 7)  # Kirby menu_text.POINTER_TABLE
LEVELS = {'-1': (True, False), '-2': (False, False), '-3': (True, True), '-4': (False, True)}


def exhal(exhal_dir: Path, rom: Path, offset: int, tmp: Path) -> tuple[bytes, int] | None:
    out = tmp / 'out.bin'
    run = subprocess.run([str(exhal_dir / 'exhal'), str(rom), hex(offset), str(out)], capture_output=True, text=True)
    m = re.search(r'Compressed size:\s+(\d+)', run.stdout)
    if run.returncode != 0 or not m:
        return None
    return out.read_bytes(), int(m.group(1))


def inhal(exhal_dir: Path, data: bytes, level: str, tmp: Path) -> bytes | None:
    src, out = tmp / 'in.bin', tmp / 'out.bin'
    src.write_bytes(data)
    out.unlink(missing_ok=True)
    run = subprocess.run([str(exhal_dir / 'inhal'), level, '-n', str(src), str(out)], capture_output=True)
    return out.read_bytes() if run.returncode == 0 and out.exists() else None


def menu_offsets(rom: bytes) -> list[int]:
    table, count = MENU_POINTERS
    out = []
    for i in range(count):
        p = rom[table + 4 * i : table + 4 * i + 3]
        out.append((p[2] - 0xC0) << 16 | p[1] << 8 | p[0])
    return out


def ours_unpack(rom: bytes, offset: int) -> tuple[bytes, int] | None:
    try:
        return sk.hal_unpack(rom[offset : offset + sk.HAL_DATA_SIZE])
    except sk.RomError:
        return None


def test_unpack_matches_exhal(exhal_dir, kss_rom, tmp_path):
    rom_path = tmp_path / 'kss.sfc'
    rom_path.write_bytes(kss_rom)
    rng = random.Random(1)
    offsets = menu_offsets(kss_rom) + [rng.randrange(0, len(kss_rom) - 0x100) for _ in range(150)]
    agreed = 0
    for offset in offsets:
        theirs = exhal(exhal_dir, rom_path, offset, tmp_path)
        ours = ours_unpack(kss_rom, offset)
        if theirs is None:
            # exhal refused. Ours must refuse too, except for a bare 0xFF: a valid empty stream
            # (what packing b'' gives), which exhal's CLI reports as a failure because its
            # unpack returns the size, 0, for both.
            assert ours is None or ours == (b'', 1), hex(offset)
            continue
        assert ours == theirs, hex(offset)
        agreed += 1
    assert agreed >= 7, 'at least the real menu blocks decompressed'


def pack_inputs(rom: bytes) -> list[bytes]:
    rng = random.Random(2)
    inputs = [sk.hal_unpack(rom[o : o + sk.HAL_DATA_SIZE])[0] for o in menu_offsets(rom)]
    for size in (1, 2, 3, 4, 5, 31, 32, 33, 100, 1023, 1024, 1025, 2049, 4096, 9000):
        start = rng.randrange(0, len(rom) - size)
        inputs.append(rom[start : start + size])
    inputs += [
        bytes(2100),  # past the 1024-byte run limit (and the 2048 word-run limit below)
        bytes([1, 2]) * 1100,
        bytes(range(256)) * 12,
        bytes([0xFC, 0xFD, 0xFE, 0xFF, 0, 1, 2, 3]) * 40,
        bytes(rng.randrange(4) for _ in range(6000)),
    ]
    blob = bytes(rng.randrange(256) for _ in range(800))
    inputs.append(blob + blob[::-1] + bytes(int(f'{b:08b}'[::-1], 2) for b in blob))
    return inputs


@pytest.mark.parametrize('level', LEVELS)
def test_pack_matches_inhal(exhal_dir, kss_rom, tmp_path, level):
    fast, optimal = LEVELS[level]
    for i, data in enumerate(pack_inputs(kss_rom)):
        theirs = inhal(exhal_dir, data, level, tmp_path)
        assert theirs is not None, f'inhal failed on input {i}'
        ours = sk.hal_pack(data, fast=fast, optimal=optimal)
        assert ours == theirs, f'input {i} ({len(data)} bytes): {len(ours)} vs {len(theirs)} bytes'
        assert sk.hal_unpack(ours)[0] == data
