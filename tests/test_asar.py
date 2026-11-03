import pytest

from snes_lab import asar, config

pytestmark = pytest.mark.skipif(config.find_asar() is None, reason='Asar not built (scripts/build_asar.sh).')


def blank() -> bytes:
    rom = bytearray(0x80000)
    rom[0x7FC0:0x7FD5] = b'TEST ROM'.ljust(21)
    rom[0x7FD5] = 0x20
    rom[0x7FFC:0x7FFE] = b'\x00\x80'
    return bytes(rom)


def test_patch_bytes_and_ranges() -> None:
    r = asar.assemble(blank(), 'lorom\norg $008000\nloop:\nLDA #$01\nBRA loop\norg $018000\ndb $AA\n')
    assert r['rom'][0:4] == bytes.fromhex('a901 80fc')
    assert r['ranges'] == [(0, 4), (0x8000, 0x8001)] and not r['warnings']


def test_numeric_branch_warning() -> None:
    r = asar.assemble(blank(), 'lorom\norg $0FF000\nBRA $0FF010\n')
    assert r['rom'][0x7F000:0x7F002] == bytes.fromhex('8010')  # the gotcha itself: 80 10, not 80 0E
    assert len(r['warnings']) == 1 and 'line 3' in r['warnings'][0]


def test_file_source_with_incsrc(tmp_path) -> None:
    (tmp_path / 'part.asm').write_text('db $42\n')
    (tmp_path / 'main.asm').write_text('lorom\norg $008000\nincsrc "part.asm"\n')
    assert asar.assemble(blank(), tmp_path / 'main.asm')['rom'][0] == 0x42


def test_errors_are_reported() -> None:
    with pytest.raises(asar.AsarError, match='Asar failed'):
        asar.assemble(blank(), 'lorom\norg $008000\nLDQ #$01\n')


def test_missing_asar(monkeypatch) -> None:
    monkeypatch.setattr(config, 'find_asar', lambda: None)
    with pytest.raises(asar.AsarError, match='build_asar.sh'):
        asar.assemble(blank(), 'lorom\n')
