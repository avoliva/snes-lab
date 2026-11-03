import pytest

from snes_lab.sessions import SessionError, Sessions

pytestmark = pytest.mark.usefixtures('need_core')


@pytest.fixture(scope='module')
def session(smw_rom: bytes):
    s = Sessions()
    sess, info = s.open(smw_rom, 'smw.sfc')
    assert info['title'] == 'SUPER MARIOWORLD'
    yield sess
    s.close_all()


def test_enter_any_level(session, smw_rom: bytes) -> None:
    for level in (0x105, 0x101, 0x024):
        st = session.call('smw_enter', base=smw_rom, level=level)
        assert st['in_level'] and not st['dying']


def test_ram_read_write_and_states(session, smw_rom: bytes) -> None:
    session.call('smw_enter', base=smw_rom, level=0x105)
    session.call('save_state', name='start')
    x0 = session.call('smw_status')['x']
    session.call('run', frames=60, buttons='RIGHT+Y')
    assert session.call('smw_status')['x'] > x0 + 40
    session.call('load_state', name='start')
    assert session.call('smw_status')['x'] == x0
    session.call('write', address='$7E0019', data=b'\x02')
    assert session.call('read', address='$19', length=1) == b'\x02'


def test_play_captures_frames(session, smw_rom: bytes) -> None:
    session.call('smw_enter', base=smw_rom, level=0x105)
    r = session.call('play', inputs=[[30, 'RIGHT'], [30, 'RIGHT+B']], capture_every=20)
    assert r['frames'] == 60 and len(r['frames_png']) == 3
    assert r['sheet_png'].startswith(b'\x89PNG')


def test_tour_glides_across_screens(session, smw_rom: bytes) -> None:
    session.call('smw_enter', base=smw_rom, level=0x024)
    r = session.call('smw_tour', screens=4)
    assert len(r['digests']) == 4 and len(set(r['digests'])) == 4


def test_errors_are_reported_not_fatal(session) -> None:
    with pytest.raises(SessionError, match='not a WRAM address'):
        session.call('read', address='$05:E000')
    with pytest.raises(SessionError, match='No savestate'):
        session.call('load_state', name='nope')
    assert session.call('smw_status')['in_level']


def test_crash_is_isolated(smw_rom: bytes) -> None:
    s = Sessions()
    sess, _ = s.open(smw_rom, 'smw.sfc')
    sess.process.kill()
    sess.process.join(2)
    with pytest.raises(SessionError, match='exited'):
        sess.call('smw_status')
    s.close_all()


def test_two_sessions_at_once(smw_rom: bytes) -> None:
    s = Sessions()
    a, _ = s.open(smw_rom, 'a.sfc')
    b, _ = s.open(smw_rom, 'b.sfc')
    a.call('smw_enter', base=smw_rom, level=0x105)
    b.call('smw_enter', base=smw_rom, level=0x101)
    a.call('run', frames=30, buttons='RIGHT')
    assert a.call('smw_status')['x'] != b.call('smw_status')['x']
    s.close_all()


def test_run_until_stops_early(session, smw_rom: bytes) -> None:
    session.call('smw_enter', base=smw_rom, level=0x105)
    x0 = session.call('smw_status')['x']
    r = session.call(
        'run_until',
        until=[{'address': '$94', 'size': 2, 'op': '>=', 'value': x0 + 64}],
        buttons='RIGHT+Y',
        max_frames=600,
    )
    assert r['met'] and 10 < r['frames'] < 200 and r['values']['$94'] >= x0 + 64
    assert session.call('smw_status')['x'] == r['values']['$94']
    never = session.call('run_until', until=[{'address': '$19', 'op': '==', 'value': 3}], max_frames=20)
    assert not never['met'] and never['frames'] == 20


def test_run_until_changed_and_inputs(session, smw_rom: bytes) -> None:
    session.call('smw_enter', base=smw_rom, level=0x105)
    r = session.call('run_until', until=[{'address': '$96', 'size': 2, 'op': 'changed'}], inputs=[[60, 'B']])
    assert r['met'] and r['values']['$96'] != r['start']['$96']


def test_watch_logs_changes(session, smw_rom: bytes) -> None:
    session.call('smw_enter', base=smw_rom, level=0x105)
    r = session.call(
        'watch', items=[{'address': '$94', 'size': 2, 'name': 'x'}, {'address': '$19'}], frames_=40, buttons='RIGHT'
    )
    xs = [e['new'] for e in r['changes'] if e['name'] == 'x']
    assert len(xs) > 5 and xs == sorted(xs) and r['end']['x'] == xs[-1]
    assert not any(e['name'] == '$19' for e in r['changes'])
    capped = session.call('watch', items=[{'address': '$13'}], frames_=50, limit=5)  # $13 = frame counter
    assert len(capped['changes']) == 5 and capped['truncated']


def test_vram_and_sram_dumps(session, smw_rom: bytes) -> None:
    session.call('smw_enter', base=smw_rom, level=0x105)
    assert len(session.call('dump', kind='vram')) == 0x10000
    assert len(session.call('dump', kind='sram')) == 0x800
    with pytest.raises(SessionError, match='past the end'):
        session.call('run_until', until=[{'address': '0x900', 'kind': 'sram'}], max_frames=1)
