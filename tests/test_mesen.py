"""The Mesen backend: job building and parsing (no Mesen needed), then real runs on SMW and Kirby."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from snes_lab import config, cpu65816, mesen
from snes_lab import rom as R

TIMEOUT = int(os.environ.get('SNES_LAB_MESEN_TIMEOUT', '900'))  # a busy machine runs Mesen slowly
KSS = Path(os.environ.get('KSS_ROM', '/nonexistent'))


@pytest.fixture(scope='session')
def need_mesen() -> None:
    try:
        mesen.binary()
    except mesen.MesenError as e:
        pytest.skip(str(e))


# --- no Mesen ---------------------------------------------------------------


def test_lua_literals() -> None:
    assert mesen.lua({'a': 1, 'b': [True, 'x"y\\z'], 'c': None}) == '{["a"] = 1, ["b"] = {true, "x\\034y\\092z"}}'
    assert mesen.lua(Path('/tmp/a b')) == '"/tmp/a b"'


def test_watch_parse_and_errors() -> None:
    w = mesen.Watch.parse({'address': '$7E0019', 'end': '$7E001A'})
    assert (w.kind, w.start, w.stop, w.cpu, w.memory) == ('write', 0x7E0019, 0x7E001A, 'snes', 'cpu')
    for bad, msg in [
        ({'address': 1, 'kind': 'poke'}, 'Unknown watch kind'),
        ({'address': 1, 'cpu': 'gsu'}, 'Unknown cpu'),
        ({'address': 1, 'memory': 'iram'}, 'Unknown memory'),
        ({'kind': 'read'}, 'needs an "address"'),
        ({'address': '$10', 'end': '$0F'}, 'before its start'),
    ]:
        with pytest.raises(mesen.MesenError, match=msg):
            mesen.Watch.parse(bad)


def test_buttons() -> None:
    assert mesen.mesen_buttons('RIGHT+Y+b') == {'right': True, 'y': True, 'b': True}
    assert mesen.mesen_buttons('') == {}
    with pytest.raises(mesen.MesenError, match='Unknown button'):
        mesen.mesen_buttons('TURBO')


def test_parse_output() -> None:
    lines = ['progress\t60', 'hit\t1\t3\t8448\t128\t0\t32795\t180\t128\t0\t0\t511\t0\t0\t1', 'count\t1\t5',
             'read\t1\t0aff', 'done\t100\t100']  # fmt: skip
    r = mesen.parse(lines, 1)
    assert r['done'] and r['frames_run'] == 100 and r['counts'] == [5] and r['reads'][0] == b'\x0a\xff'
    hit = r['hits'][0][0]
    assert (hit['frame'], hit['address'], hit['value'], hit['pc'], hit['emulation']) == (3, 0x2100, 0x80, 0x801B, True)
    assert not mesen.parse(['progress\t60'], 0)['done']


def test_writer_recovers_the_store(smw_rom: bytes) -> None:
    # SMW reset: LDA #$80 at $8016, STA $2100 at $8018 (3 bytes); Mesen reports pc $801B.
    hit = {'k': 0, 'pc': 0x801B, 'ps': 0x34, 'emulation': True}
    assert mesen.writer(smw_rom, hit, 'write', 'lorom') == 0x8018
    assert mesen.writer(smw_rom, hit | {'pc': 0x8016}, 'exec', 'lorom') == 0x8016


def test_disassemble_takes_flags_from_the_log(smw_rom: bytes) -> None:
    # $8013 STZ $2143, $8016 LDA #$80, $8018 STA $2100. The log says the LDA ran with 8-bit A and X,
    # has nothing for the STZ, and (for this test) marks $8018 as data.
    cdl = bytearray(len(smw_rom))
    cdl[0x16] = mesen.CODE | mesen.M8 | mesen.X8
    cdl[0x18] = mesen.DATA
    lines = cpu65816.disassemble(smw_rom, '$00:8013', 3, m8=False, x8=False, cdl=bytes(cdl))
    assert [i.address & 0xFFFF for i in lines] == [0x8013, 0x8016, 0x8018]
    assert [i.seen for i in lines] == ['unseen', 'code', 'data']
    assert lines[1].text().endswith('LDA #$80'), lines[1].text()
    assert lines[0].text().endswith('; not run in the log') and lines[2].text().endswith('; data in the log')
    guessed = cpu65816.disassemble(smw_rom, '$00:8016', 1)[0]
    assert guessed.operand == '#$8D80', 'without the log the 16-bit guess swallows the STA'


def test_missing_mesen_is_a_clear_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('SNES_LAB_MESEN', str(tmp_path / 'nope'))
    monkeypatch.setenv('SNES_LAB_CACHE', str(tmp_path))
    with pytest.raises(mesen.MesenError, match='fetch_mesen.sh'):
        mesen.binary()
    exe = tmp_path / 'mesen' / 'Mesen'
    exe.parent.mkdir()
    exe.write_text('')
    with pytest.raises(mesen.MesenError, match='settings.json'):
        mesen.binary()
    (exe.parent / 'settings.json').write_text('{"Debug":{"ScriptWindow":{"AllowIoOsAccess":true}}}')
    with pytest.raises(mesen.MesenError, match='SnesController'):
        mesen.binary()
    (exe.parent / 'settings.json').write_text(mesen.SETTINGS)
    assert mesen.binary() == exe


# --- real Mesen runs --------------------------------------------------------


def test_watches_on_smw(need_mesen: None, smw_path: Path, smw_rom: bytes) -> None:
    r = mesen.run(
        smw_path,
        frames=120,
        watches=[mesen.Watch('exec', 0x8000, 0x8000), mesen.Watch('write', 0x2100, 0x2100)],
        limit=3,
        reads=[(0x0015, 1, 'wram')],
        inputs=[[60, ''], [60, 'RIGHT']],
        timeout=TIMEOUT,
    )
    reset, inidisp = r['hits']
    assert r['counts'][0] == 1 and reset[0]['frame'] == 0 and reset[0]['emulation']
    assert len(inidisp) == 3 and r['counts'][1] > 3, 'capped at the limit, counted past it'
    assert mesen.writer(smw_rom, inidisp[0], 'write', 'lorom') == 0x8018
    assert r['reads'][0][0] & 0x01, '$15 bit 0 is RIGHT, held for the last 60 frames'


def test_cdl_and_savestates_on_smw(need_mesen: None, smw_path: Path, smw_rom: bytes, tmp_path: Path) -> None:
    cdl_path, state = tmp_path / 'smw.cdl', tmp_path / 'smw.mss'
    r = mesen.run(smw_path, frames=300, cdl_out=cdl_path, state_out=state, timeout=TIMEOUT)
    cdl = cdl_path.read_bytes()
    assert len(cdl) == len(smw_rom) and state.stat().st_size > 10_000
    assert cdl[0] & mesen.CODE and cdl[0] & mesen.M8 and cdl[0] & mesen.X8, 'reset code: emulation mode'
    s = mesen.cdl_summary(cdl, smw_rom, 'lorom')
    assert s['code_bytes'] > 5000 and '$00' in s['code_by_bank'] and s['subroutines'] > 20
    lda = cpu65816.disassemble(smw_rom, '$00:8016', 1, cdl=cdl)[0]
    assert lda.operand == '#$80' and lda.seen == 'code'

    again = mesen.run(smw_path, frames=10, state_in=state, timeout=TIMEOUT)
    assert again['frame_count'] >= r['frame_count'] + 9, 'the run continued from the saved frame'


def test_sa1_iram_on_kirby(need_mesen: None) -> None:
    if not KSS.exists():
        pytest.skip('Set KSS_ROM to Kirby Super Star (USA) to test the SA-1.')
    w = mesen.Watch('write', 0x0000, 0x07FF, cpu='sa1', memory='iram')
    r = mesen.run(KSS, frames=60, watches=[w], limit=5, timeout=TIMEOUT)
    assert r['counts'][0] > 0, 'the SA-1 writes its I-RAM while the game boots'
    assert R.code_mapping(R.load(KSS)) == 'sa1'


def test_config_finds_an_app_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exe = tmp_path / 'Mesen.app' / 'Contents' / 'MacOS' / 'Mesen'
    exe.parent.mkdir(parents=True)
    exe.write_text('')
    monkeypatch.setenv('SNES_LAB_MESEN', str(tmp_path / 'Mesen.app'))
    assert config.find_mesen() == exe


def test_screenshot_of_a_superfx_game(need_mesen: None, tmp_path: Path) -> None:
    # snes-lab's Snes9x core draws nothing for SuperFX games (Yoshi's Island, Star Fox); Mesen does.
    from snes_kit import corpus

    folder = corpus.corpus_dir()
    yi = folder / "Super Mario World 2 - Yoshi's Island (USA).zip" if folder else None
    if yi is None or not yi.exists():
        pytest.skip("Needs Yoshi's Island in the romset (SNES_CORPUS).")
    rom = tmp_path / 'yi.sfc'
    rom.write_bytes(corpus.read(yi))
    shot = tmp_path / 'yi.png'
    mesen.run(rom, frames=600, screenshot=shot, timeout=TIMEOUT)
    png = shot.read_bytes()
    assert png[:8] == b'\x89PNG\r\n\x1a\n' and len(png) > 2000, 'a real picture, not one flat colour'
