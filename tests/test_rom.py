import random
import subprocess
from pathlib import Path

import pytest

from snes_lab import rom as R


def fake_rom(mapping: str, size: int = 0x80000, title: bytes = b'TEST GAME') -> bytes:
    rng = random.Random(size)
    data = bytearray(rng.randbytes(size))
    at = 0x7FC0 if mapping == 'lorom' else 0xFFC0
    data[at : at + 21] = title.ljust(21)
    data[at + 0x15] = 0x20 if mapping == 'lorom' else 0x21
    data[at + 0x17] = (size // 1024).bit_length() - 1
    data[at + 0x3C : at + 0x3E] = b'\x00\x80'
    return R.fix_checksum(data)


@pytest.mark.parametrize('mapping', ['lorom', 'hirom'])
def test_header_detects_mapping_and_checksum(mapping: str) -> None:
    rom = fake_rom(mapping)
    h = R.header(rom)
    assert h.mapping == mapping
    assert h.title == 'TEST GAME'
    assert h.checksum_ok
    broken = bytearray(rom)
    broken[0x100] ^= 0xFF
    assert not R.header(bytes(broken)).checksum_ok


def test_copier_header_is_stripped(tmp_path: Path) -> None:
    rom = fake_rom('lorom')
    p = tmp_path / 'h.smc'
    p.write_bytes(bytes(0x200) + rom)
    assert R.load(p) == rom


def test_checksum_mirrors_odd_sizes() -> None:
    # 1.5 MiB: the last 512 KiB counts twice, as on hardware.
    rom = bytes([1]) * 0x100000 + bytes([2]) * 0x80000
    assert R.checksum(rom) == (0x100000 + 2 * 0x80000 * 2) & 0xFFFF


@pytest.mark.parametrize(
    ('address', 'mapping', 'offset'),
    [
        (0x008000, 'lorom', 0x0),
        (0x05E000, 'lorom', 0x2E000),
        (0x85E000, 'lorom', 0x2E000),
        (0x07C4CA, 'lorom', 0x3C4CA),
        (0xC12345, 'hirom', 0x12345),
        (0x018000, 'hirom', 0x18000),
    ],
)
def test_address_conversion(address: int, mapping: str, offset: int) -> None:
    assert R.to_offset(address, mapping) == offset
    assert R.to_offset(R.to_address(offset, mapping), mapping) == offset


@pytest.mark.parametrize('bad', [0x7E0000, 0x001000])
def test_lorom_rejects_non_rom(bad: int) -> None:
    with pytest.raises(R.RomError):
        R.to_offset(bad, 'lorom')


@pytest.mark.parametrize(
    ('text', 'value'), [('$05:E000', 0x05E000), ('05E000', 0x05E000), ('0x94', 0x94), ('$94', 0x94)]
)
def test_parse_address(text: str, value: int) -> None:
    assert R.parse_address(text) == value


def test_patch_refuses_to_grow_by_accident() -> None:
    with pytest.raises(R.RomError):
        R.patch(bytes(16), [(15, b'\x01\x02')])
    assert len(R.patch(bytes(16), [(15, b'\x01\x02')], grow=True)) == 17


def test_bps_round_trip_including_growth() -> None:
    src = fake_rom('lorom')
    dst = bytearray(src) + bytes(range(256)) * 16
    dst[0x1234:0x1240] = b'hello world!'
    patch = R.bps_make(src, bytes(dst), metadata='test')
    assert R.bps_apply(src, patch) == bytes(dst)


def test_bps_rejects_wrong_source_and_corruption() -> None:
    src, dst = fake_rom('lorom'), fake_rom('hirom')
    patch = R.bps_make(src, dst)
    with pytest.raises(R.RomError, match='different ROM'):
        R.bps_apply(dst, patch)
    bad = bytearray(patch)
    bad[10] ^= 1
    with pytest.raises(R.RomError, match='corrupt'):
        R.bps_apply(src, bytes(bad))


def test_bps_applies_to_headered_source() -> None:
    src = fake_rom('lorom')
    dst = R.patch(src, [(0x100, b'\xaa' * 8)])
    assert R.bps_apply(bytes(0x200) + src, R.bps_make(src, dst)) == dst


def test_bps_interop_with_flips(tmp_path: Path, flips: str) -> None:
    """Flips' delta patches use SourceCopy and TargetCopy, which our maker never emits."""
    src = fake_rom('lorom')
    dst = bytearray(src)
    dst[0x4000:0x4000] = src[0x10000:0x10400]  # moved block -> SourceCopy
    dst += b'\x55\x66' * 300  # repeated pattern -> TargetCopy
    a, b = tmp_path / 'a.sfc', tmp_path / 'b.sfc'
    a.write_bytes(src)
    b.write_bytes(bytes(dst))
    subprocess.run(
        [flips, '--create', '--bps-delta', str(a), str(b), str(tmp_path / 'f.bps')], check=True, capture_output=True
    )
    assert R.bps_apply(src, (tmp_path / 'f.bps').read_bytes()) == bytes(dst)
    (tmp_path / 'ours.bps').write_bytes(R.bps_make(src, bytes(dst)))
    subprocess.run(
        [flips, '--apply', str(tmp_path / 'ours.bps'), str(a), str(tmp_path / 'c.sfc')], check=True, capture_output=True
    )
    assert (tmp_path / 'c.sfc').read_bytes() == bytes(dst)


def test_header_of_a_file_too_small_to_hold_one() -> None:
    # 8 KiB pass-through adapters (Super 8, Tri-Star) have no header; say so instead of an IndexError.
    with pytest.raises(R.RomError, match='too small'):
        R.header(bytes(0x2000))
