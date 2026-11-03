from snes_lab import rom as R
from snes_lab import smw


def test_decode_yoshis_island_1(smw_rom: bytes) -> None:
    d = smw.decode_level(smw_rom, 0x105)
    assert d['pointers']['layer1'] == '$0688DD'
    assert d['header']['screens'] == 20
    assert d['header']['tileset_name'] == 'normal 2'
    numbers = {s['number'] for s in d['sprites']}
    assert {0x9F, 0xAB} <= numbers  # Banzai Bill, Rex
    assert all(0 <= o['y'] < 32 for o in d['objects'] if 'y' in o)


def test_every_level_decodes(smw_rom: bytes) -> None:
    for level in range(smw.LEVEL_COUNT):
        d = smw.decode_level(smw_rom, level)
        assert 1 <= d['header']['screens'] <= 32
        assert d['layer1_bytes'] >= 6


def test_preview_rom_moves_level_into_yi1_slot(smw_rom: bytes) -> None:
    p = smw.preview_rom(smw_rom, 0x101)
    assert smw.level_pointers(p, 0x105) == smw.level_pointers(smw_rom, 0x101)
    assert p[smw.SECONDARY[3] + 0x105] & smw.NO_INTRO
    assert R.header(p).checksum_ok
    assert smw.level_pointers(p, 0x101) == smw.level_pointers(smw_rom, 0x101)
