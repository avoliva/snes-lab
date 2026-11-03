"""Emulator sessions as child processes, so several can run at once and a core abort can't take
the server down. Thread-safe: each session has a lock, and only that session's process touches its core."""

from __future__ import annotations

import contextlib
import itertools
import multiprocessing as mp
import threading
from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any

from . import config
from .worker import main as worker_main


class SessionError(RuntimeError):
    pass


@dataclass
class Session:
    id: str
    rom_path: str
    process: BaseProcess
    conn: Connection
    lock: threading.Lock = field(default_factory=threading.Lock)
    base: bytes = b''  # the ROM as opened, for SMW level previews
    level: int = 0x105  # last SMW level entered

    def call(self, op: str, timeout: float = 120, **kwargs: Any) -> Any:
        with self.lock:
            if not self.process.is_alive():
                raise SessionError(f'Session {self.id} has exited (exit code {self.process.exitcode}). Open a new one.')
            self.conn.send((op, kwargs))
            if not self.conn.poll(timeout):
                self.process.kill()
                raise SessionError(f'{op} took longer than {timeout:.0f}s; session {self.id} was stopped.')
            try:
                status, result = self.conn.recv()
            except EOFError:
                self.process.join(1)
                raise SessionError(
                    f'Session {self.id} crashed during {op} (exit code {self.process.exitcode}). '
                    'Snes9x aborts the process on some misuse, e.g. running before a game is loaded.'
                ) from None
        if status == 'err':
            raise SessionError(result)
        return result

    def close(self) -> None:
        if self.process.is_alive():
            with contextlib.suppress(SessionError):
                self.call('quit', timeout=5)
            self.process.join(3)
            if self.process.is_alive():
                self.process.kill()


class Sessions:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def open(self, rom: bytes, rom_path: str, name: str = 'game.sfc') -> tuple[Session, dict[str, Any]]:
        core = config.find_core()
        if core is None:
            raise SessionError('No Snes9x libretro core found. Set SNES_LAB_CORE or run scripts/build_core.sh.')
        ctx = mp.get_context('spawn')
        parent, child = ctx.Pipe()
        proc = ctx.Process(target=worker_main, args=(child, str(core), str(config.cache_dir())), daemon=True)
        proc.start()
        child.close()
        if not parent.poll(30):
            proc.kill()
            raise SessionError('The emulator worker did not start in 30 s.')
        status, msg = parent.recv()
        if status != 'ok':
            proc.join(1)
            raise SessionError(msg)
        with self._lock:
            sid = f's{next(self._ids)}'
        s = Session(sid, rom_path, proc, parent, base=rom)
        info = s.call('load', rom=rom, name=name)
        self._sessions[sid] = s
        return s, info

    def get(self, sid: str) -> Session:
        s = self._sessions.get(sid)
        if s is None:
            have = ', '.join(self._sessions) or 'none'
            raise SessionError(f'No session {sid!r} (open: {have}). Use emu_open first.')
        return s

    def close(self, sid: str) -> None:
        self.get(sid).close()
        del self._sessions[sid]

    def list(self) -> list[dict[str, Any]]:
        return [{'id': s.id, 'rom': s.rom_path, 'alive': s.process.is_alive()} for s in self._sessions.values()]

    def close_all(self) -> None:
        for sid in list(self._sessions):
            self.close(sid)
