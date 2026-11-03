"""The MCP server over real stdio, as a client sees it."""

import json
import os
import sys

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def call(tool_calls: list[tuple[str, dict]]) -> list:
    async def go() -> list:
        params = StdioServerParameters(command=sys.executable, args=['-m', 'snes_lab.server'], env=dict(os.environ))
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            out: list = [sorted(t.name for t in tools.tools)]
            for name, args in tool_calls:
                out.append(await s.call_tool(name, args))
            return out

    return anyio.run(go)


def text(result) -> dict:
    assert not result.is_error, result.content
    return json.loads(result.content[0].text)


def test_tools_over_stdio(smw_path, disasm_root, need_core) -> None:
    names, info, look, level, sheet, bad = call(
        [
            ('rom_info', {'rom': str(smw_path)}),
            ('disasm_lookup', {'query': '$1493'}),
            ('smw_level', {'level': '105', 'rom': str(smw_path), 'objects': False}),
            ('smw_level_sheet', {'level': '0C7', 'rom': str(smw_path)}),
            ('rom_read', {'rom': str(smw_path), 'address': '$7E0000'}),
        ]
    )
    assert {'rom_info', 'emu_open', 'smw_level_sheet', 'experiment_run', 'route_search'} <= set(names)
    assert text(info)['checksum_ok']
    assert text(look)['hits'][0]['name'] == 'EndLevelTimer'
    assert text(level)['header']['screens'] == 20
    assert sheet.content[1].type == 'image' and sheet.content[1].mime_type == 'image/png'
    assert bad.is_error and 'WRAM' in bad.content[0].text


def test_session_flow_over_stdio(smw_path, need_core) -> None:
    async def go() -> None:
        params = StdioServerParameters(command=sys.executable, args=['-m', 'snes_lab.server'], env=dict(os.environ))
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            sid = text(await s.call_tool('emu_open', {'rom': str(smw_path)}))['session']
            assert text(await s.call_tool('smw_enter_level', {'session': sid, 'level': '101'}))['in_level']
            await s.call_tool('emu_run', {'session': sid, 'frames': 30, 'buttons': 'RIGHT'})
            shot = await s.call_tool('emu_screenshot', {'session': sid})
            assert shot.content[1].type == 'image'
            tour = await s.call_tool('smw_tour', {'session': sid})
            assert text(tour)['screens'] == 8
            spec = {
                'progress': {'address': '$94', 'size': 2},
                'goal': [{'address': '$94', 'size': 2, 'op': '>=', 'value': 0x200}],
                'fail': [{'address': '$71', 'op': '==', 'value': 9}],
                'dedupe': [{'address': '$94', 'size': 2, 'div': 6}, {'address': '$96', 'size': 2, 'div': 12}],
            }
            await s.call_tool('smw_enter_level', {'session': sid, 'level': '105'})
            found = text(await s.call_tool('route_search', {'session': sid, 'spec': spec, 'beam': 8}))
            assert found['found'], found
            replay = text(await s.call_tool('route_replay', {'session': sid, 'route_file': found['route']}))
            assert replay['goal'], replay
            await s.call_tool('emu_close', {'session': sid})
            exp = await s.call_tool(
                'experiment_run',
                {
                    'start': 'smw_level:105',
                    'variants': [{'name': 'small'}, {'name': 'cape', 'ram': [{'address': '$19', 'hex': '02'}]}],
                    'settle': 10,
                },
            )
            assert len(text(exp)['groups']) == 2 and exp.content[1].type == 'image'

    anyio.run(go)


def test_v02_tools_over_stdio(smw_path, need_core, tmp_path) -> None:
    from snes_lab import config

    async def go() -> None:
        params = StdioServerParameters(command=sys.executable, args=['-m', 'snes_lab.server'], env=dict(os.environ))
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            assert {'rom_disasm', 'rom_xrefs', 'asm_patch', 'emu_run_until', 'emu_watch', 'emu_mem_dump',
                    'emu_vram_tiles'} <= names  # fmt: skip
            rom = str(smw_path)
            dis = text(await s.call_tool('rom_disasm', {'rom': rom, 'address': '$00:8000', 'count': 3, 'm8': True,
                                                        'x8': True}))  # fmt: skip
            assert dis['lines'][1].endswith('STZ $4200') and dis['next'] == '$00:8007'
            xr = text(await s.call_tool('rom_xrefs', {'rom': rom, 'target': '$01:B7B3'}))
            assert len(xr['hits']) == 32
            bad = await s.call_tool('rom_xrefs', {'rom': rom, 'target': '$01:B7B3', 'kinds': ['nope']})
            assert bad.is_error and 'Unknown kinds' in bad.content[0].text
            if config.find_asar():
                patch = text(await s.call_tool('asm_patch', {'rom': rom, 'asm': 'lorom\norg $00FFE0\ndb $11,$22\n',
                                                             'out': str(tmp_path / 'p.sfc')}))  # fmt: skip
                assert patch['changed'][0]['address'] == '$00:FFE0' and patch['changed'][0]['hex'] == '11 22'
                assert (tmp_path / 'p.bps').is_file() and not patch['warnings']
                (tmp_path / 'b.asm').write_text('lorom\norg $00FFE0\nBRA $00FFF0\n')
                warn = text(await s.call_tool('asm_patch', {'rom': rom, 'asm': str(tmp_path / 'b.asm')}))
                assert 'raw offset' in warn['warnings'][0]
                err = await s.call_tool('asm_patch', {'rom': rom, 'asm': 'lorom\nLDQ\n'})
                assert err.is_error and 'Asar failed' in err.content[0].text
            sid = text(await s.call_tool('emu_open', {'rom': rom}))['session']
            await s.call_tool('smw_enter_level', {'session': sid, 'level': '105'})
            until = text(await s.call_tool('emu_run_until', {'session': sid, 'buttons': 'RIGHT',
                         'until': [{'address': '$94', 'size': 2, 'op': 'changed'}]}))  # fmt: skip
            assert until['met'] and until['frames'] < 30
            watched = text(await s.call_tool('emu_watch', {'session': sid, 'frames': 20, 'buttons': 'RIGHT',
                           'items': [{'address': '$94', 'size': 2, 'name': 'x'}]}))  # fmt: skip
            assert watched['changes'] and watched['changes'][0]['name'] == 'x'
            dump = text(await s.call_tool('emu_mem_dump', {'session': sid, 'kind': 'vram'}))
            assert dump['bytes'] == 0x10000
            sheet = await s.call_tool('emu_vram_tiles', {'session': sid, 'bpp': 4, 'count': 64,
                                                         'palette_wram': '$0703'})  # fmt: skip
            assert text(sheet)['tiles'] == 64 and sheet.content[1].type == 'image'
            await s.call_tool('emu_close', {'session': sid})

    anyio.run(go)


if __name__ == '__main__':
    pytest.main([__file__])


def test_mesen_tools_over_stdio(smw_path, tmp_path) -> None:
    from snes_lab import mesen

    try:
        mesen.binary()
    except mesen.MesenError as e:
        pytest.skip(str(e))
    timeout = int(os.environ.get('SNES_LAB_MESEN_TIMEOUT', '900'))
    os.environ['SNES_LAB_OUT'] = str(tmp_path)
    names, trace, cdl, bad = call(
        [
            ('mesen_trace', {'rom': str(smw_path), 'frames': 30, 'limit': 2, 'timeout': timeout,
                             'watches': [{'kind': 'write', 'address': '$2100'}]}),
            ('mesen_cdl', {'rom': str(smw_path), 'frames': 300, 'timeout': timeout, 'screenshot': True}),
            ('mesen_trace', {'rom': str(smw_path), 'watches': [{'kind': 'poke', 'address': 1}]}),
        ]
    )  # fmt: skip
    assert {'mesen_trace', 'mesen_cdl'} <= set(names)
    w = text(trace)['watches'][0]
    assert w['hits'][0]['pc'] == '$00:8018' and w['hits'][0]['pc_after'] == '$00:801B' and w['count'] >= 1
    assert cdl.content[1].type == 'image' and cdl.content[1].mime_type == 'image/png'
    log = text(cdl)
    assert log['code_bytes'] > 5000 and log['cdl'].endswith('.cdl')
    assert bad.is_error and 'Unknown watch kind' in bad.content[0].text
    _, dis = call([('rom_disasm', {'rom': str(smw_path), 'address': '$00:8016', 'count': 2, 'cdl': log['cdl']})])
    assert text(dis)['lines'][0].endswith('LDA #$80')
