"""snes-lab MCP server: SNES ROM, disassembly and emulator tools, plus Super Mario World recipes.

Run with `snes-lab` (stdio). Paths may use ~. Addresses accept $05:E000, $7E0094, 0x94 or 05E000."""

from __future__ import annotations

import atexit
import functools
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import asar, config, cpu65816, disasm, experiment, mesen, route, smw, tiles
from . import rom as R
from .core import CoreError
from .sessions import SessionError, Sessions

INSTRUCTIONS = """SNES reverse-engineering and modding lab.
- ROM tools (rom_*, bps_*) work on any LoROM/HiROM/SA-1 image. Patches always go to a new file.
- rom_disasm / rom_xrefs disassemble 65816 code and find callers straight from the ROM (no disassembly needed);
  asm_patch assembles an Asar patch onto a copy.
- disasm_* look up labels, RAM addresses and code in the disassembly (SMWDisX by default).
- emu_* drive Snes9x sessions (each its own process): open, run frames with buttons like "RIGHT+Y+B",
  read/write WRAM, savestates, screenshots. emu_run_until / emu_watch run while reading memory every frame;
  emu_vram_tiles draws the tiles in VRAM; emu_mem_dump saves a whole memory to a file.
- smw_* are Super Mario World recipes: decode a level, jump straight into any level (0-1FF),
  glide-camera tours as contact sheets.
- experiment_run varies ROM/RAM writes, photographs each variant and groups identical pictures.
- route_search finds controller inputs that reach a RAM goal (beam search over savestates).
- mesen_trace / mesen_cdl run the game headless in Mesen, from power-on or a Mesen savestate: breakpoints and
  read/write/exec watches with registers (main CPU or SA-1), and the code/data log, which gives rom_disasm
  the real M/X flags (rom_disasm cdl=...). Slower than emu_*; use them to answer "who writes/runs this".
Read docs/smw-notes.md in the snes-lab repository for SMW gotchas before guessing at addresses."""

app = MCPServer('snes-lab', instructions=INSTRUCTIONS)
sessions = Sessions()
atexit.register(sessions.close_all)
EXPECTED = (R.RomError, SessionError, CoreError, asar.AsarError, ValueError, KeyError, OSError, re.error)


def tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Register a tool. Expected failures reach the model as their message (mcp 2 hides other errors)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except EXPECTED as e:
            raise ToolError(str(e) or type(e).__name__) from e

    return app.tool(structured_output=False)(wrapper)


def _path(p: str) -> Path:
    return Path(p).expanduser().resolve()


def _rom(p: str | None) -> tuple[bytes, Path]:
    path = _path(p) if p else config.default_rom()
    if path is None:
        raise ValueError('Give a ROM path (no default ROM found; set SNES_LAB_ROM).')
    return R.load(path), Path(path)


def _out(name: str) -> Path:
    stamp = time.strftime('%Y%m%d-%H%M%S')
    return config.out_dir() / f'{stamp}-{name}'


def _disasm(root: str | None) -> str:
    r = _path(root) if root else config.find_disasm()
    if r is None:
        raise ValueError('No disassembly found. Set SNES_LAB_DISASM or run scripts/fetch_smwdisx.sh.')
    return str(r)


def _image(png: bytes, name: str) -> tuple[Path, Image]:
    p = _out(name)
    p.write_bytes(png)
    return p, Image(data=png, format='png')


# --- ROM -------------------------------------------------------------------


@tool
def rom_info(rom: str) -> dict[str, Any]:
    """Internal header, mapping (LoROM/HiROM), checksum (stored vs computed), CRC32 and SHA-256."""
    data, path = _rom(rom)
    return {'path': str(path), **R.header(data).as_dict()}


@tool
def rom_address(value: str, mapping: str = 'lorom', to: str = 'auto') -> dict[str, Any]:
    """Convert between a SNES CPU address and a file offset. to='offset' | 'address' | 'auto'
    (auto: values written with $ or a bank colon are CPU addresses, plain 0x... are file offsets)."""
    n = R.parse_address(value)
    direction = to
    if to == 'auto':
        direction = 'offset' if value.strip().startswith('$') or ':' in value else 'address'
    if direction == 'offset':
        off = R.to_offset(n, mapping)
        return {
            'address': f'${n >> 16:02X}:{n & 0xFFFF:04X}',
            'file_offset': f'{off:#x}',
            'headered_offset': f'{off + 0x200:#x}',
        }
    a = R.to_address(n, mapping)
    return {
        'file_offset': f'{n:#x}',
        'address': f'${a >> 16:02X}:{a & 0xFFFF:04X}',
        'fastrom_mirror': f'${(a | 0x800000) >> 16:02X}:{a & 0xFFFF:04X}',
    }


@tool
def rom_read(rom: str, address: str, length: int = 64, offset: bool = False) -> dict[str, Any]:
    """Hex dump ROM bytes at a CPU address (or a file offset with offset=true)."""
    data, _ = _rom(rom)
    length = max(1, min(length, 4096))
    mapping = R.detect_mapping(data)
    at = R.parse_address(address) if offset else R.to_offset(R.parse_address(address), mapping)
    chunk = R.read(data, at, length, offset=True)
    return {'file_offset': f'{at:#x}', 'hex': chunk.hex(), 'dump': R.hexdump(chunk, at)}


@tool
def rom_patch(
    rom: str, writes: list[dict[str, Any]], out: str | None = None, fix_checksum: bool = True, make_bps: bool = True
) -> dict[str, Any]:
    """Write bytes into a copy of the ROM. writes: [{"address": "$05:E000", "hex": "A9 01"}, ...]
    (add "offset": true for file offsets). Never touches the source file; also writes a .bps by default."""
    data, path = _rom(rom)
    patched = experiment.build_rom(data, experiment.parse_writes(writes), fix=fix_checksum)
    target = _path(out) if out else _out(path.stem + '-patched.sfc')
    if target == path:
        raise ValueError('Refusing to overwrite the source ROM; give a different out path.')
    target.write_bytes(patched)
    res: dict[str, Any] = {
        'out': str(target),
        'bytes_changed': sum(a != b for a, b in zip(data, patched, strict=False)),
        'checksum': R.header(patched).checksum,
    }
    if make_bps:
        bps = target.with_suffix('.bps')
        bps.write_bytes(R.bps_make(data, patched))
        res['bps'] = str(bps)
    return res


@tool
def bps_make(source: str, target: str, out: str | None = None) -> dict[str, Any]:
    """Make a BPS patch that turns `source` into `target`."""
    s, t = R.load(_path(source)), R.load(_path(target))
    p = _path(out) if out else _path(target).with_suffix('.bps')
    p.write_bytes(R.bps_make(s, t))
    return {'out': str(p), 'size': p.stat().st_size}


@tool
def bps_apply(source: str, patch: str, out: str | None = None) -> dict[str, Any]:
    """Apply a BPS patch to a ROM (CRCs checked; a copier-headered source is handled)."""
    src = _path(source).read_bytes()
    result = R.bps_apply(src, _path(patch).read_bytes())
    p = _path(out) if out else _path(patch).with_suffix('.sfc')
    if p == _path(source):
        raise ValueError('Refusing to overwrite the source ROM.')
    p.write_bytes(result)
    h = R.header(result)
    return {'out': str(p), 'size': len(result), 'title': h.title, 'checksum_ok': h.checksum_ok}


@tool
def rom_disasm(
    rom: str,
    address: str,
    count: int = 40,
    m8: bool = False,
    x8: bool = False,
    stop_at_return: bool = False,
    mapping: str | None = None,
    cdl: str | None = None,
) -> dict[str, Any]:
    """Disassemble 65816 code from the ROM at a CPU address ($01:FBD0). The M/X flags (8-bit accumulator /
    index) can't be known from the bytes: give the ones in force at the start (m8/x8; reset code and many
    NMI handlers start with both 8-bit), REP/SEP are followed from there, and each line shows the flags used.
    If immediates look wrong (an LDA # that swallows the next opcode), the flags are wrong.
    cdl: a code/data log from mesen_cdl. Opcodes the game ran then take their real M/X flags from it,
    and lines it never saw run end in "; not run in the log" (or "; data in the log").
    mapping: lorom/hirom/exhirom/sa1 (default from the header; SA-1 games get sa1)."""
    data, _ = _rom(rom)
    log = _path(cdl).read_bytes() if cdl else None
    insns = cpu65816.disassemble(
        data, address, max(1, min(count, 2000)), m8=m8, x8=x8, mapping=mapping, stop_at_return=stop_at_return,
        cdl=log,
    )  # fmt: skip
    last = insns[-1] if insns else None
    nxt = (last.address & 0xFF0000) | ((last.address + len(last.raw)) & 0xFFFF) if last else None
    return {
        'mapping': mapping or R.code_mapping(data),
        'lines': [i.text() for i in insns],
        'next': f'${nxt >> 16:02X}:{nxt & 0xFFFF:04X}' if nxt is not None else None,
        'flags_after': {'m8': insns[-1].m8, 'x8': insns[-1].x8} if insns else None,
    }


@tool
def rom_xrefs(
    rom: str, target: str, kinds: list[str] | None = None, limit: int = 200, mapping: str | None = None
) -> dict[str, Any]:
    """Find what calls or jumps to an address, scanning the whole ROM: kinds ["call"] (default: JSL/JML,
    and JSR/JMP within the same bank) and/or "long" (long-address loads/stores, e.g. STA $7E1234 or
    LDA $40:1000). Mirrors count as the same address. A byte scan: data can match by chance; check a hit
    with rom_disasm a few bytes before it. Indirect calls (JSR ($1234,x), pointer tables) are not found."""
    data, _ = _rom(rom)
    k = tuple(kinds or ['call'])
    bad = set(k) - {'call', 'long'}
    if bad:
        raise ValueError(f'Unknown kinds {sorted(bad)}; use "call" and/or "long".')
    return cpu65816.xrefs(data, target, mapping=mapping, kinds=k, limit=max(1, min(limit, 2000)))


@tool
def asm_patch(
    rom: str, asm: str, out: str | None = None, fix_checksum: bool = True, make_bps: bool = True
) -> dict[str, Any]:
    """Assemble an Asar patch onto a copy of the ROM. asm: path to an .asm file (incsrc paths resolve
    from its folder) or the assembly text itself (start it with lorom / sa1rom / hirom as needed).
    Returns the changed byte ranges as CPU addresses, Asar's output and warnings. Gotcha: Asar encodes
    `BRA $1FF010` with the number as a raw offset; branch to labels (this tool warns when it sees one)."""
    data, path = _rom(rom)
    src: str | Path = _path(asm) if asm.strip().endswith('.asm') and '\n' not in asm else asm
    r = asar.assemble(data, src)
    patched = R.fix_checksum(r['rom']) if fix_checksum else r['rom']
    target = _path(out) if out else _out(path.stem + '-asm.sfc')
    if target == path:
        raise ValueError('Refusing to overwrite the source ROM; give a different out path.')
    target.write_bytes(patched)
    mapping = R.code_mapping(data)
    ranges = []
    for start, end in r['ranges']:
        a = R.to_address(start, mapping) if start < len(data) else None
        ranges.append(
            {
                'address': f'${a >> 16:02X}:{a & 0xFFFF:04X}' if a is not None else None,
                'file_offset': f'{start:#x}',
                'length': end - start,
                'hex': patched[start : min(end, start + 32)].hex(' '),
            }
        )
    res: dict[str, Any] = {
        'out': str(target),
        'changed': ranges,
        'ranges_truncated': r['truncated'],
        'warnings': r['warnings'],
        'asar_output': r['output'][-2000:],
        'checksum': R.header(patched).checksum,
    }
    if make_bps:
        bps = target.with_suffix('.bps')
        bps.write_bytes(R.bps_make(data, patched))
        res['bps'] = str(bps)
    return res


# --- disassembly -------------------------------------------------------------


@tool
def disasm_lookup(query: str, context: int = 16, root: str | None = None) -> dict[str, Any]:
    """Find a label or address in the disassembly: RAM labels with notes ($7E0094, $94, PlayerXPosNext),
    ROM labels with the code around them (Layer1Ptrs, $05:E000). Partial names list matches."""
    return disasm.lookup(_disasm(root), query, context=max(1, min(context, 200)))


@tool
def disasm_search(pattern: str, limit: int = 40, files: str | None = None, root: str | None = None) -> dict[str, Any]:
    """Regex search the .asm sources (case-insensitive). files: regex on file names, e.g. 'bank_0[57]'."""
    return disasm.search(_disasm(root), pattern, limit=max(1, min(limit, 400)), files=files)


# --- emulator sessions -------------------------------------------------------


@tool
def emu_open(rom: str | None = None, frames: int = 0) -> dict[str, Any]:
    """Start an emulator session on a ROM (power-on). Optionally run some frames. Returns the session id."""
    data, path = _rom(rom)
    s, info = sessions.open(data, str(path), path.name)
    if frames:
        s.call('run', frames=frames)
    return {'session': s.id, 'rom': str(path), **info}


@tool
def emu_close(session: str) -> dict[str, Any]:
    """Stop a session's emulator process."""
    sessions.close(session)
    return {'closed': session}


@tool
def emu_list() -> list[dict[str, Any]]:
    """Open sessions."""
    return sessions.list()


@tool
def emu_run(session: str, frames: int = 1, buttons: str = '') -> dict[str, Any]:
    """Advance frames holding buttons (e.g. "RIGHT+Y+B"; empty = nothing held)."""
    return sessions.get(session).call('run', frames=frames, buttons=buttons, timeout=max(60, frames / 200))


@tool
def emu_play(session: str, inputs: list[Any], capture_every: int = 0) -> list[Any]:
    """Play an input script: [[frames, "BUTTONS"], ...]. With capture_every=N, returns a contact sheet of
    a frame every N frames."""
    r = sessions.get(session).call('play', inputs=inputs, capture_every=capture_every, timeout=600)
    if 'sheet_png' not in r:
        return [r]
    p, img = _image(r['sheet_png'], 'play.png')
    return [
        {'frames_run': r['frames_run'], 'frames': r['frames'], 'captured': len(r['frames_png']), 'sheet': str(p)},
        img,
    ]


@tool
def emu_ram_read(session: str, address: str, length: int = 16, kind: str = 'wram') -> dict[str, Any]:
    """Read memory: kind wram (default; $7E0000-$7FFFF, or $0000-$1FFF), sram or vram."""
    data = sessions.get(session).call('read', address=address, length=max(1, min(length, 0x20000)), kind=kind)
    out: dict[str, Any] = {'hex': data.hex(), 'dump': R.hexdump(data[:1024], R.parse_address(address))}
    if len(data) in (1, 2):
        out['value'] = int.from_bytes(data, 'little')
    return out


@tool
def emu_ram_write(session: str, address: str, hex: str, kind: str = 'wram') -> dict[str, Any]:
    """Write bytes into memory (hex like "40" or "00 01")."""
    return sessions.get(session).call('write', address=address, data=bytes.fromhex(hex.replace(' ', '')), kind=kind)


@tool
def emu_state_save(session: str, name: str = 'default', path: str | None = None) -> dict[str, Any]:
    """Savestate in the session under a name, and optionally to a file."""
    return sessions.get(session).call('save_state', name=name, path=str(_path(path)) if path else None)


@tool
def emu_state_load(session: str, name: str = 'default', path: str | None = None) -> dict[str, Any]:
    """Restore a named savestate, or one from a file."""
    return sessions.get(session).call('load_state', name=name, path=str(_path(path)) if path else None)


@tool
def emu_screenshot(session: str, scale: int = 1, crop: list[int] | None = None) -> list[Any]:
    """The current frame as a PNG (also saved to disk). crop: [x, y, w, h]."""
    r = sessions.get(session).call('screenshot', scale=max(1, min(scale, 4)), crop=crop)
    p, img = _image(r['png'], f'{session}.png')
    return [{'path': str(p), 'width': r['width'], 'height': r['height'], 'digest': r['digest']}, img]


@tool
def emu_swap_rom(session: str, rom: str, keep_state: bool = True) -> dict[str, Any]:
    """Load another ROM into the session, keeping the machine state (for A/B tests of a patch)."""
    data, _ = _rom(rom)
    return sessions.get(session).call('swap_rom', rom=data, keep_state=keep_state)


@tool
def emu_run_until(
    session: str,
    until: list[dict[str, Any]],
    buttons: str = '',
    inputs: list[Any] | None = None,
    max_frames: int = 600,
    mode: str = 'any',
) -> dict[str, Any]:
    """Run frame by frame until a memory condition holds (any, or all with mode="all"), or max_frames.
    until: [{"address": "$94", "size": 2, "op": ">=", "value": 512}, {"address": "0x1C", "kind": "sram",
    "op": "changed"}]. kind: wram (default), sram (cartridge RAM / SA-1 BW-RAM as a byte offset: $40:001C
    and $6000+$1C are offset 0x1C) or vram (byte offset). op: == != > < >= <= & or changed (differs from
    its value at the start). Holds `buttons`, or plays `inputs` [[frames, "BUTTONS"], ...] then holds nothing.
    Returns met, frames taken, and the start and final values."""
    return sessions.get(session).call(
        'run_until',
        until=until,
        buttons=buttons,
        inputs=inputs,
        max_frames=max(1, min(max_frames, 216000)),
        mode=mode,
        timeout=max(60, max_frames / 100),
    )


@tool
def emu_watch(
    session: str,
    items: list[dict[str, Any]],
    frames: int = 60,
    buttons: str = '',
    inputs: list[Any] | None = None,
    every: int = 1,
    limit: int = 400,
) -> dict[str, Any]:
    """Run `frames` frames and log every change to the watched values as {frame, name, old, new}
    (sampled every `every` frames). items: [{"address": "$0DBE", "name": "lives"}, {"address": "0x1C",
    "kind": "sram", "size": 2, "name": "hp"}]; same addressing as emu_run_until. Find which value moves
    when something happens in the game, without writing a script."""
    return sessions.get(session).call(
        'watch',
        items=items,
        frames_=max(1, min(frames, 216000)),
        buttons=buttons,
        inputs=inputs,
        every=every,
        limit=max(1, min(limit, 5000)),
        timeout=max(60, frames / 100),
    )


# --- Mesen (debugger) -------------------------------------------------------


def _mesen_inputs(inputs: list[Any] | None, buttons: str, frames: int) -> list[Any]:
    if inputs:
        return inputs
    return [[frames, buttons]] if frames > 0 else []


def _mesen_state(save_state: bool, name: str) -> Path | None:
    return _out(f'{name}.mss') if save_state else None


def _with_shot(result: dict[str, Any], shot: Path | None) -> Any:
    if shot is None:
        return result
    return [result | {'screenshot': str(shot)}, Image(data=shot.read_bytes(), format='png')]


@tool
def mesen_trace(
    rom: str,
    watches: list[dict[str, Any]],
    frames: int = 300,
    buttons: str = '',
    inputs: list[Any] | None = None,
    state: str | None = None,
    save_state: bool = False,
    limit: int = 100,
    screenshot: bool = False,
    timeout: int = 600,
) -> Any:
    """Breakpoints and memory watches in Mesen, run headless from power-on (or a Mesen savestate) for
    `frames` frames: who writes, reads or runs an address, with the CPU registers at each hit.
    watches: [{"kind": "write"|"read"|"exec", "address": "$7E0019", "end": "$7E001A" (optional),
    "cpu": "snes"|"sa1", "memory": "cpu" (default; CPU addresses) | "wram" | "sram" | "rom" | "iram" (sa1),
    "value": 3 (optional: only hits writing/reading that value)}].
    inputs: [[frames, "BUTTONS"], ...] like emu_play (else `buttons` held for `frames`).
    Each hit: frame, address, value, registers, `pc` (exec: the instruction; read/write: the instruction
    that did it, found from Mesen's pc_after, or null for DMA/RAM code), m8/x8. The first `limit` hits
    per watch are listed; `count` keeps counting. Mesen savestates (.mss) are not Snes9x ones:
    save_state=true writes one at the end for the next mesen_* call's `state`. Mesen is slower than the
    Snes9x sessions; prefer emu_* for plain playing and RAM reads. screenshot=true adds the last frame
    (also the way to see SuperFX games, which snes-lab's Snes9x core does not draw)."""
    data, path = _rom(rom)
    mapping = R.code_mapping(data)
    ws = [mesen.Watch.parse(w) for w in watches]
    if not ws:
        raise ValueError('Give at least one watch.')
    out_state = _mesen_state(save_state, path.stem)
    shot = _out(f'{path.stem}.png') if screenshot else None
    r = mesen.run(
        path, inputs=_mesen_inputs(inputs, buttons, frames), frames=frames, watches=ws, limit=limit,
        state_in=_path(state) if state else None, state_out=out_state, screenshot=shot, timeout=timeout,
    )  # fmt: skip
    result: list[dict[str, Any]] = []
    for w, hits, count in zip(ws, r['hits'], r['counts'], strict=True):
        rows = []
        for h in hits:
            m8, x8 = bool(h['ps'] & 0x20) or h['emulation'], bool(h['ps'] & 0x10) or h['emulation']
            row = {
                'frame': h['frame'],
                'address': f'${h["address"]:06X}',
                'value': f'${h["value"]:02X}' if h['value'] >= 0 else None,
                'pc': mesen.addr(mesen.writer(data, h, w.kind, mapping)) if w.cpu == 'snes' else None,
                'pc_after': mesen.addr(h['k'] << 16 | h['pc']) if w.kind != 'exec' else None,
                'a': f'${h["a"]:04X}', 'x': f'${h["x"]:04X}', 'y': f'${h["y"]:04X}', 'sp': f'${h["sp"]:04X}',
                'd': f'${h["d"]:04X}', 'dbr': f'${h["dbr"]:02X}', 'm8': m8, 'x8': x8,
            }  # fmt: skip
            if w.kind == 'exec':
                row['pc'] = mesen.addr(h['k'] << 16 | h['pc'])
            if h['emulation']:
                row['emulation'] = True
            rows.append({k: v for k, v in row.items() if v is not None})
        result.append({'watch': {'kind': w.kind, 'address': f'${w.start:06X}', 'end': f'${w.stop:06X}',
                                 'cpu': w.cpu, 'memory': w.memory}, 'count': count, 'hits': rows})  # fmt: skip
    return _with_shot({'frames_run': r['frames_run'], 'frame_count': r['frame_count'], 'watches': result,
                       'state': str(out_state) if out_state else None}, shot)  # fmt: skip


@tool
def mesen_cdl(
    rom: str,
    frames: int = 600,
    buttons: str = '',
    inputs: list[Any] | None = None,
    state: str | None = None,
    save_state: bool = False,
    screenshot: bool = False,
    timeout: int = 600,
) -> Any:
    """Run the game in Mesen and record its code/data log: which ROM bytes ran as code (with the M/X
    flags they ran with), which were read as data, and where subroutines start. This is the reliable way
    to tell code from data in a game with no disassembly: play the part you care about (inputs like
    emu_play), then pass the returned `cdl` path to rom_disasm. Only what ran in this run is logged.
    screenshot=true adds the last frame, to check the inputs got where you meant."""
    data, path = _rom(rom)
    cdl_path = _out(f'{path.stem}.cdl')
    out_state = _mesen_state(save_state, path.stem)
    shot = _out(f'{path.stem}.png') if screenshot else None
    r = mesen.run(
        path, inputs=_mesen_inputs(inputs, buttons, frames), frames=frames, cdl_out=cdl_path,
        state_in=_path(state) if state else None, state_out=out_state, screenshot=shot, timeout=timeout,
    )  # fmt: skip
    summary = mesen.cdl_summary(cdl_path.read_bytes(), data, R.code_mapping(data))
    return _with_shot({'cdl': str(cdl_path), 'frames_run': r['frames_run'], **summary,
                       'state': str(out_state) if out_state else None}, shot)  # fmt: skip


@tool
def emu_mem_dump(session: str, kind: str = 'vram', path: str | None = None) -> dict[str, Any]:
    """Save a whole memory (wram 128 KiB, sram, or vram 64 KiB) to a file, for offline digging."""
    data = sessions.get(session).call('dump', kind=kind)
    p = _path(path) if path else _out(f'{session}.{kind}')
    p.write_bytes(data)
    return {'path': str(p), 'bytes': len(data)}


@tool
def emu_vram_tiles(
    session: str,
    bpp: int = 4,
    start: str = '0',
    count: int = 512,
    cols: int = 16,
    scale: int = 2,
    palette_wram: str | None = None,
    palette_index: int = 0,
) -> list[Any]:
    """Draw the tiles in VRAM as a sheet: find fonts, menu text and sprite graphics. bpp 2/4/8 (BG modes:
    text layers are often 2bpp, most graphics 4bpp). start is a byte offset (a VRAM word address x 2).
    Grey levels by default (the core does not expose CGRAM); palette_wram reads BGR555 colours from a
    palette buffer in WRAM, palette_index picks the 4/16-colour row."""
    s = sessions.get(session)
    vram = s.call('dump', kind='vram')
    palette = None
    if palette_wram:
        n = 1 << bpp
        raw = s.call('read', address=palette_wram, length=2 * n * (palette_index + 1))[2 * n * palette_index :]
        palette = [int.from_bytes(raw[i : i + 2], 'little') for i in range(0, len(raw), 2)]
    png, drawn = tiles.sheet(
        vram, bpp, R.parse_address(start), max(1, min(count, 4096)), cols, max(1, min(scale, 4)), palette
    )
    p, img = _image(png, f'{session}-vram.png')
    first = R.parse_address(start)
    return [{'sheet': str(p), 'tiles': drawn, 'bytes_per_tile': tiles.BYTES_PER_TILE[bpp], 'start': f'{first:#x}'}, img]


# --- Super Mario World -------------------------------------------------------


@tool
def smw_level(level: str, rom: str | None = None, objects: bool = True) -> dict[str, Any]:
    """Decode a level (hex 0-1FF): pointers, header, secondary header, layer-1 objects and sprites."""
    data, _ = _rom(rom)
    d = smw.decode_level(data, int(level, 16))
    if not objects:
        d['objects'], d['sprites'] = len(d['objects']), len(d['sprites'])
    return d


@tool
def smw_enter_level(session: str, level: str | None = None, midway: bool = False) -> dict[str, Any]:
    """Jump straight into a level (hex 0-1FF) with controller inputs: it is played from Yoshi's Island 1's
    slot. The first call boots the game (~2 s, cached on disk); later calls take ~0.1 s."""
    s = sessions.get(session)
    st = s.call('smw_enter', base=s.base, level=int(level, 16) if level else None, midway=midway)
    s.level = int(level, 16) if level else smw.PREVIEW_LEVEL
    return {'level': level or '105', **st}


@tool
def smw_status(session: str) -> dict[str, Any]:
    """Mario's position, power-up, game mode, goal/death/midway flags."""
    return sessions.get(session).call('smw_status')


@tool
def smw_tour(session: str, screens: int | None = None, every: int = 1, cols: int = 4, y: int = 0x100) -> list[Any]:
    """Glide the camera across the current horizontal level (writes Mario's position; a layout check,
    not play) and return a contact sheet, one frame per screen."""
    s = sessions.get(session)
    if screens is None:
        screens = smw.decode_level(s.base, s.level)['header']['screens']
    r = s.call('smw_tour', screens=screens, every=every, cols=cols, y=y, timeout=300)
    p, img = _image(r['sheet_png'], f'{session}-tour.png')
    return [{'sheet': str(p), 'screens': screens, 'digests': r['digests']}, img]


@tool
def smw_level_sheet(level: str, rom: str | None = None, every: int = 1, cols: int = 4) -> list[Any]:
    """One call: open a session, enter the level, tour every screen, close. Horizontal levels only."""
    data, path = _rom(rom)
    n = int(level, 16)
    info = smw.decode_level(data, n)
    s, _ = sessions.open(data, str(path), path.name)
    try:
        s.call('smw_enter', base=data, level=n)
        r = s.call('smw_tour', screens=info['header']['screens'], every=every, cols=cols, timeout=300)
    finally:
        sessions.close(s.id)
    p, img = _image(r['sheet_png'], f'level-{n:03X}.png')
    return [
        {
            'sheet': str(p),
            'level': f'{n:03X}',
            'screens': info['header']['screens'],
            'tileset': info['header']['tileset_name'],
            'mode': info['header']['mode_name'],
        },
        img,
    ]


# --- experiments and routes --------------------------------------------------


@tool
def experiment_run(
    variants: list[dict[str, Any]],
    start: str,
    rom: str | None = None,
    inputs: list[Any] | None = None,
    settle: int = 30,
    crop: list[int] | None = None,
    cols: int = 4,
) -> list[Any]:
    """Vary ROM/RAM writes, photograph each variant, group identical pictures.

    variants: [{"name": "big", "rom": [{"address": "$05:F000", "hex": "03"}],
                "ram": [{"address": "$19", "hex": "01"}]}, ...]
    start: "power_on:<frames>", "smw_level:<hex level>" or "state:<path>".
    inputs: [[frames, "BUTTONS"], ...] after the start; settle: idle frames before the picture.
    crop: [x, y, w, h] to compare only part of the screen. Returns groups and a sheet with one picture per group."""
    data, path = _rom(rom)
    s, _ = sessions.open(data, str(path), path.name)
    try:
        r = experiment.run(s, data, variants, start, inputs, settle, crop, cols)
    finally:
        sessions.close(s.id)
    out: dict[str, Any] = {'results': r['results'], 'groups': r['groups']}
    images: list[Any] = []
    if 'sheet_png' in r:
        all_p = _out('experiment-all.png')
        all_p.write_bytes(r['sheet_png'])
        g, img = _image(r['groups_png'], 'experiment-groups.png')
        out.update(
            {
                'all_sheet': str(all_p),
                'all_order': r['sheet_order'],
                'groups_sheet': str(g),
                'groups_order': r['groups_order'],
            }
        )
        images.append(img)
    return [out, *images]


@tool
def route_search(
    session: str,
    spec: dict[str, Any] | None = None,
    beam: int = 40,
    max_steps: int = 900,
    time_limit: float = 600,
    save_as: str | None = None,
) -> dict[str, Any]:
    """Find controller inputs from the session's current state to a RAM goal (beam search over savestates).
    spec=None uses the SMW 'reach the goal tape' spec (enter a level first). A custom spec has keys
    progress, goal, fail, penalty, dedupe, actions, chunk (see route.py); include Y position in dedupe
    or the beam collapses to one state per X and walks into the first enemy. Saves the route and its start
    savestate so route_replay can check it. The session is left at the start state."""
    s = sessions.get(session)
    spec = spec or smw.route_spec()
    base = _path(save_as) if save_as else _out('route')
    start_state = base.with_suffix('.state')
    s.call('save_state', name='route-start', path=str(start_state))
    r = s.call(
        'route_search', spec=spec, beam=beam, max_steps=max_steps, time_limit=time_limit, timeout=time_limit + 120
    )
    route_file = base.with_suffix('.json')
    route_file.write_text(json.dumps({'spec': spec, 'start_state': str(start_state), **r}, indent=1) + '\n')
    return {
        'route': str(route_file),
        **{k: v for k, v in r.items() if k != 'actions'},
        'action_count': len(r.get('actions', [])),
    }


@tool
def route_replay(session: str, route_file: str, from_saved_start: bool = True) -> dict[str, Any]:
    """Replay a saved route with controller inputs only and report goal / failure."""
    s = sessions.get(session)
    data = json.loads(_path(route_file).read_text())
    if from_saved_start:
        s.call('load_state', path=data['start_state'])
    return s.call('route_replay', spec=data['spec'], actions=data['actions'], chunk=data['chunk'], timeout=600)


@tool
def lab_info() -> dict[str, Any]:
    """What snes-lab found: core, disassembly, default ROM, output folder."""
    d = config.find_disasm()
    return {
        'core': str(config.find_core()),
        'mesen': str(config.find_mesen()),
        'disassembly': str(d),
        'default_rom': str(config.default_rom()),
        'out_dir': str(config.out_dir()),
        'disasm_stats': disasm.stats(str(d)) if d else None,
        'route_presets': list(route.PRESETS),
    }


def main() -> None:
    app.run('stdio')


if __name__ == '__main__':
    main()
