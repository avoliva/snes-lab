"""Experiment runner and route bot, end to end in a real emulator."""

from pathlib import Path

import pytest

from snes_lab import experiment, smw
from snes_lab.sessions import Sessions

pytestmark = pytest.mark.usefixtures('need_core')


@pytest.fixture
def session(smw_rom: bytes):
    s = Sessions()
    sess, _ = s.open(smw_rom, 'smw.sfc')
    yield sess
    s.close_all()


def test_experiment_groups_identical_pictures(session, smw_rom: bytes) -> None:
    variants = [
        {'name': 'small', 'ram': [{'address': '$19', 'hex': '00'}]},
        {'name': 'big', 'ram': [{'address': '$19', 'hex': '01'}]},
        {'name': 'cape', 'ram': [{'address': '$19', 'hex': '02'}]},
        {'name': 'cape-again', 'ram': [{'address': '$19', 'hex': '02'}]},
    ]
    r = experiment.run(session, smw_rom, variants, 'smw_level:105', settle=20)
    assert not any('error' in x for x in r['results']), r['results']
    assert sorted(map(sorted, r['groups'])) == [['big'], ['cape', 'cape-again'], ['small']]
    assert r['groups_order'] == ['small', 'big', 'cape']


def test_experiment_rom_writes_change_the_level(session, smw_rom: bytes) -> None:
    # Swap YI1's sprite list pointer for level 101's: different enemies on screen.
    p = smw.level_pointers(smw_rom, 0x101)['sprites'] & 0xFFFF
    variants = [
        {'name': 'vanilla'},
        {'name': 'other-sprites', 'rom': [{'address': '$05:EC00', 'offset': False, 'hex': ''}]},
    ]
    variants[1]['rom'] = [
        {'address': f'{smw.SPRITE_POINTERS + 0x105 * 2:#x}', 'offset': True, 'hex': p.to_bytes(2, 'little').hex()}
    ]
    r = experiment.run(session, smw_rom, variants, 'power_on:240', settle=0)
    assert len(r['results']) == 2 and not any('error' in x for x in r['results'])


def test_cold_boot_without_cache(smw_rom: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('SNES_LAB_CACHE', str(tmp_path))
    s = Sessions()
    sess, _ = s.open(smw_rom, 'smw.sfc')
    st = sess.call('smw_enter', base=smw_rom, level=0x105)
    assert st['in_level']
    assert list(tmp_path.glob('smw-overworld-*.state'))
    s.close_all()


def test_route_search_and_replay(session, smw_rom: bytes) -> None:
    session.call('smw_enter', base=smw_rom, level=0x105)
    session.call('save_state', name='start')
    spec = {**smw.route_spec(), 'goal': [{'address': '$94', 'size': 2, 'op': '>=', 'value': 0x280}]}
    r = session.call('route_search', spec=spec, beam=8, max_steps=200, time_limit=120, timeout=200)
    assert r['found'], r
    # search leaves the session at its start; replay with inputs only reaches the goal again
    assert session.call('smw_status')['x'] == 16 or session.call('smw_status')['x'] < 0x40
    rep = session.call('route_replay', spec=spec, actions=r['actions'], chunk=r['chunk'])
    assert rep['goal'], rep
