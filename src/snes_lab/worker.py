"""The emulator worker: one child process, one core, one thread.

The parent (sessions.py) sends (op, kwargs) over a pipe and gets ('ok', result) or ('err', text).
Results are plain picklable data: ints, strings, bytes, lists, dicts."""

from __future__ import annotations

import hashlib
import os
import sys
import traceback
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from . import rom as R
from . import route, smw, watch
from .core import Core, CoreError, mask, wram_offset
from .image import Frame, frame_png, sheet


class Worker:
    def __init__(self, core_path: str, cache_dir: str) -> None:
        self.core = Core(core_path)
        self.cache = Path(cache_dir)
        self.rom = b''
        self.rom_name = ''
        self.states: dict[str, bytes] = {}
        self.overworld: bytes | None = None  # SMW boot state for the current base ROM

    # --- loading ---
    def op_load(self, rom: bytes, name: str = 'game.sfc', frames: int = 0) -> dict[str, Any]:
        self.core.load(rom, name)
        self.rom, self.rom_name = rom, name
        if frames:
            self.core.run(frames)
        h = R.header(rom)
        return {'title': h.title, 'mapping': h.mapping, 'frames_run': self.core.frames_run}

    def op_swap_rom(self, rom: bytes, keep_state: bool = True) -> dict[str, Any]:
        """Load a different ROM (same base game) and carry the current machine state over."""
        state = self.core.save_state() if keep_state and self.core.loaded else None
        self.core.load(rom, self.rom_name or 'game.sfc')
        self.rom = rom
        if state:
            self.core.load_state(state)
        return {'ok': True}

    def op_reset(self) -> dict[str, Any]:
        self.core.reset()
        return {'ok': True}

    # --- running ---
    def op_run(self, frames: int = 1, buttons: Any = None) -> dict[str, Any]:
        self.core.run(frames, mask(buttons))
        return {'frames_run': self.core.frames_run}

    def op_play(self, inputs: list[Any], capture_every: int = 0) -> dict[str, Any]:
        """inputs: [[frames, buttons], ...]. Optionally capture a frame every N frames."""
        shots: list[Frame] = []
        n = 0
        for item in inputs:
            frames, buttons = (item[0], item[1] if len(item) > 1 else None) if isinstance(item, list) else (item, None)
            m = mask(buttons)
            for _ in range(int(frames)):
                self.core.run(1, m)
                n += 1
                if capture_every and n % capture_every == 0:
                    shots.append(self.core.snapshot())
        out: dict[str, Any] = {'frames_run': self.core.frames_run, 'frames': n}
        if shots:
            out['frames_png'] = [frame_png(f) for f in shots]
            out['sheet_png'] = sheet(shots)
        return out

    # --- memory ---
    def op_read(self, address: Any, length: int = 1, kind: str = 'wram') -> bytes:
        mem = self.core.memory(kind)
        at = wram_offset(address) if kind == 'wram' else R.parse_address(address)
        if at + length > len(mem):
            raise CoreError(f'{kind} read past the end ({len(mem):#x} bytes).')
        return bytes(mem[at : at + length])

    def op_write(self, address: Any, data: bytes, kind: str = 'wram') -> dict[str, Any]:
        mem = self.core.memory(kind)
        at = wram_offset(address) if kind == 'wram' else R.parse_address(address)
        if at + len(data) > len(mem):
            raise CoreError(f'{kind} write past the end ({len(mem):#x} bytes).')
        for i, b in enumerate(data):
            mem[at + i] = b
        return {'written': len(data)}

    def op_dump(self, kind: str = 'wram') -> bytes:
        return bytes(self.core.memory(kind))

    # --- watching ---
    def op_run_until(self, **kwargs: Any) -> dict[str, Any]:
        return watch.run_until(self.core, **kwargs)

    def op_watch(self, **kwargs: Any) -> dict[str, Any]:
        return watch.watch(self.core, **kwargs)

    # --- states ---
    def op_save_state(self, name: str = 'default', path: str | None = None) -> dict[str, Any]:
        data = self.core.save_state()
        self.states[name] = data
        if path:
            Path(path).expanduser().write_bytes(data)
        return {'name': name, 'bytes': len(data), 'path': path}

    def op_load_state(self, name: str = 'default', path: str | None = None) -> dict[str, Any]:
        if path:
            data = Path(path).expanduser().read_bytes()
        elif name in self.states:
            data = self.states[name]
        else:
            raise CoreError(f'No savestate named {name!r} (have: {", ".join(self.states) or "none"}).')
        self.core.load_state(data)
        return {'name': name, 'bytes': len(data)}

    def op_states(self) -> list[str]:
        return sorted(self.states)

    # --- pictures ---
    def op_screenshot(self, scale: int = 1, crop: list[int] | None = None) -> dict[str, Any]:
        f = self.core.snapshot()
        if crop:
            f = f.crop(*crop)
        return {'png': frame_png(f, scale), 'width': f.width, 'height': f.height, 'digest': f.digest()}

    def op_frame(self, crop: list[int] | None = None) -> tuple[bytes, int, int]:
        f = self.core.snapshot()
        if crop:
            f = f.crop(*crop)
        return f.data, f.width, f.height

    # --- SMW recipes ---
    def _overworld(self, base: bytes) -> bytes:
        key = hashlib.sha256(base).hexdigest()[:16]
        cached = self.cache / f'smw-overworld-{key}.state'
        if self.overworld is not None and self._overworld_key == key:
            return self.overworld
        if cached.is_file():
            self.overworld = cached.read_bytes()
        else:
            self.overworld = smw.boot(self.core, base)
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(self.overworld)
        self._overworld_key = key
        return self.overworld

    _overworld_key = ''

    def op_smw_enter(
        self, base: bytes, level: int | None = None, midway: bool = False, rom: bytes | None = None
    ) -> dict[str, Any]:
        """Boot `base` (cached), then play `level` from the YI1 slot. level=None plays base's own YI1.

        `rom` overrides the ROM that is entered (e.g. a preview build with extra patches)."""
        smw.check_clean(base)
        overworld = self._overworld(base)
        if rom is None:
            rom = smw.preview_rom(base, level) if level is not None else base
        smw.enter(self.core, rom, overworld, midway)
        self.rom = rom
        return smw.status(self.core)

    def op_smw_status(self) -> dict[str, Any]:
        return smw.status(self.core)

    def op_smw_tour(self, screens: int, step: int = 4, y: int = 0x100, every: int = 1, cols: int = 4) -> dict[str, Any]:
        frames = smw.tour(self.core, screens, step, y, every)
        return {
            'sheet_png': sheet(frames, cols),
            'frames_png': [frame_png(f) for f in frames],
            'digests': [f.digest() for f in frames],
        }

    # --- route bot ---
    def op_route_search(
        self, spec: dict[str, Any], beam: int = 40, max_steps: int = 900, time_limit: float = 600
    ) -> dict[str, Any]:
        return route.search(self.core, spec, beam=beam, max_steps=max_steps, time_limit=time_limit)

    def op_route_replay(self, spec: dict[str, Any], actions: list[str], chunk: int) -> dict[str, Any]:
        return route.replay(self.core, spec, actions, chunk)


def main(conn: Connection, core_path: str, cache_dir: str) -> None:
    # stdout belongs to the MCP protocol in the parent; never let the core print there.
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    try:
        worker = Worker(core_path, cache_dir)
    except Exception as e:  # noqa: BLE001
        conn.send(('err', f'Could not start the core: {e}'))
        return
    conn.send(('ok', 'ready'))
    ops: dict[str, Callable[..., Any]] = {n[3:]: getattr(worker, n) for n in dir(worker) if n.startswith('op_')}
    while True:
        try:
            op, kwargs = conn.recv()
        except EOFError:
            return
        if op == 'quit':
            conn.send(('ok', None))
            return
        fn = ops.get(op)
        if fn is None:
            conn.send(('err', f'Unknown op {op!r}.'))
            continue
        try:
            conn.send(('ok', fn(**kwargs)))
        except (CoreError, R.RomError, ValueError, KeyError, OSError) as e:
            conn.send(('err', str(e)))
        except Exception as e:  # noqa: BLE001
            conn.send(('err', f'{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}'))
