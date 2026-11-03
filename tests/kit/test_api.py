"""The Python surface of snes_kit: types, defaults, errors and messages."""

import zlib

import pytest
import snes_kit as sk


def test_crc32_matches_zlib():
    for data in (b'', b'a', bytes(range(256)) * 9):
        assert sk.crc32(data) == zlib.crc32(data)


def test_errors_are_value_errors():
    assert issubclass(sk.RomError, ValueError)
    with pytest.raises(sk.RomError, match="Unknown mapping 'rom'"):
        sk.to_offset(0x8000, 'rom')


@pytest.mark.parametrize('text', ['$05:E000', '05E000', '0x5e000', '05_E000'])
def test_parse_address(text):
    assert sk.parse_address(text) == 0x05E000


def test_parse_address_rejects_junk():
    with pytest.raises(sk.RomError, match='Not an address'):
        sk.parse_address('xyz')


def test_address_maths():
    assert sk.to_offset(0x05E000) == 0x2E000
    assert sk.to_address(0x80000) == 0x108000
    assert sk.to_address(0x80000, fast=True) == 0x908000
    assert sk.to_offset(0xC12345, 'hirom') == 0x12345
    assert sk.to_offset(0xC08000, 'sa1') == 0x8000
    with pytest.raises(sk.RomError, match='WRAM'):
        sk.to_offset(0x7E0019)
    with pytest.raises(OverflowError):
        sk.to_offset(-1)


def _lorom(size=0x80000):
    rom = bytearray(size)
    rom[0x7FC0 : 0x7FC0 + 21] = b'TEST GAME            '
    rom[0x7FD5] = 0x20
    rom[0x7FFC:0x7FFE] = (0x8000).to_bytes(2, 'little')
    return sk.fix_checksum(bytes(rom))


def test_header_and_checksum():
    rom = _lorom()
    assert sk.detect_mapping(rom) == 'lorom' and sk.code_mapping(rom) == 'lorom'
    c = sk.checksum(rom)
    assert c == sum(rom) & 0xFFFF
    assert int.from_bytes(rom[0x7FDE:0x7FE0], 'little') == c
    assert sk.header_offset('hirom') == 0xFFC0
    assert sk.strip_copier_header(bytes(0x200) + rom) == rom
    assert sk.fix_checksum(rom, 'lorom') == rom
    with pytest.raises(sk.RomError, match='too small'):
        sk.fix_checksum(b'tiny')


def test_bps():
    src, dst = b'abc' * 100, b'abd' * 120
    patch = sk.bps_make(src, dst, 'notes')
    assert sk.bps_apply(src, patch) == dst
    with pytest.raises(sk.RomError, match='different ROM'):
        sk.bps_apply(b'xyz', patch)
    with pytest.raises(sk.RomError, match='corrupt'):
        sk.bps_apply(src, patch[:-1] + bytes([patch[-1] ^ 1]))
    with pytest.raises(sk.RomError, match='magic'):
        sk.bps_apply(src, b'IPS!')


def test_bps_headered_source_is_retried():
    src = bytes(range(256)) * 8  # 2 KiB
    dst = src[::-1]
    assert sk.bps_apply(bytes(0x200) + src, sk.bps_make(src, dst)) == dst


def test_hal_round_trip_and_options():
    data = bytes(range(40)) + bytes(300) + b'kirby' * 50
    for fast in (False, True):
        for optimal in (False, True):
            packed = sk.hal_pack(data, fast=fast, optimal=optimal)
            assert sk.hal_unpack(packed) == (data, len(packed))
    assert sk.hal_unpack(sk.hal_pack(b'') + b'trailing') == (b'', 1)
    with pytest.raises(sk.RomError, match='ends early'):
        sk.hal_unpack(b'\x05ab')
    with pytest.raises(sk.RomError, match='64 KiB'):
        sk.hal_pack(bytes(sk.HAL_DATA_SIZE + 1))


def test_smw_encoders():
    assert sk.smw_layer1([(20, 24, 0x14, 0x2F), (0, 24, 0x14, 0x22)]) == bytes(
        [0x38, 0x40, 0x22, 0x01, 0x00, 0x01, 0x38, 0x44, 0x2F, 0xFF]
    )
    assert sk.smw_sprites(0, [(90, 23, 0x7B), (10, 23, 0x0F)]) == bytes([0, 0x71, 0xA0, 0x0F, 0x71, 0xA5, 0x7B, 0xFF])
    with pytest.raises(sk.RomError, match='below the 32-row'):
        sk.smw_layer1([(0, 40, 1, 0)])
    with pytest.raises(OverflowError):
        sk.smw_layer1([(0, 0, 300, 0)])
