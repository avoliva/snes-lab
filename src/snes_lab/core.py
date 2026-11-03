"""A libretro core (Snes9x) driven through ctypes.

libretro callbacks are process-global, so one Core
per process, and every call must come from the thread that made it. snes-lab runs each core in
its own child process (see sessions.py), which also keeps a core abort from killing the server.

Snes9x aborts the whole process if retro_run is called before a game is loaded; run() guards it."""

from __future__ import annotations

import ctypes as C
from pathlib import Path

from . import rom as romlib
from .image import Frame

BUTTONS = ('B', 'Y', 'SELECT', 'START', 'UP', 'DOWN', 'LEFT', 'RIGHT', 'A', 'X', 'L', 'R')
B, Y, SELECT, START, UP, DOWN, LEFT, RIGHT, A, X, L, R = range(12)

ENV_GET_CAN_DUPE, ENV_GET_SYSTEM_DIRECTORY, ENV_SET_PIXEL_FORMAT = 3, 9, 10
ENV_GET_VARIABLE, ENV_GET_VARIABLE_UPDATE = 15, 17
ENV_GET_CORE_ASSETS_DIRECTORY, ENV_GET_SAVE_DIRECTORY = 30, 31
PIXEL_RGB565 = 2
MEMORY = {'sram': 0, 'wram': 2, 'vram': 3}


class CoreError(RuntimeError):
    pass


def mask(buttons: str | int | list[str] | None) -> int:
    """'RIGHT+Y+B', ['RIGHT', 'B'], 0x81 or None -> a libretro joypad bit mask."""
    if buttons is None or buttons == '':
        return 0
    if isinstance(buttons, int):
        return buttons
    names = buttons.replace(',', '+').split('+') if isinstance(buttons, str) else buttons
    out = 0
    for n in names:
        n = n.strip().upper()
        if not n:
            continue
        if n not in BUTTONS:
            raise CoreError(f'Unknown button {n!r}. Use {"/".join(BUTTONS)}.')
        out |= 1 << BUTTONS.index(n)
    return out


class _Game(C.Structure):
    _fields_ = [('path', C.c_char_p), ('data', C.c_void_p), ('size', C.c_size_t), ('meta', C.c_char_p)]


_ENV = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
_VIDEO = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
_AUDIO = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
_AUDIO_BATCH = C.CFUNCTYPE(C.c_size_t, C.c_void_p, C.c_size_t)
_POLL = C.CFUNCTYPE(None)
_INPUT = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)


class Core:
    _instance: Core | None = None

    def __init__(self, library: str | Path) -> None:
        if Core._instance is not None:
            raise CoreError('Only one emulator core can run per process.')
        library = Path(library)
        self._lib = C.CDLL(str(library))
        self._dir = str(library.parent).encode()
        self.buttons = 0
        self.width = self.height = 0
        self.frame = b''
        self.frames_run = 0
        self.loaded = False
        self._rom: C.Array[C.c_char] | None = None
        self._callbacks = (
            _ENV(self._env),
            _VIDEO(self._video),
            _AUDIO(lambda left, right: None),
            _AUDIO_BATCH(lambda data, frames: frames),
            _POLL(lambda: None),
            _INPUT(self._input),
        )
        names = ('environment', 'video_refresh', 'audio_sample', 'audio_sample_batch', 'input_poll', 'input_state')
        for name, cb in zip(names, self._callbacks, strict=True):
            getattr(self._lib, 'retro_set_' + name)(cb)
        lib = self._lib
        lib.retro_load_game.argtypes = [C.POINTER(_Game)]
        lib.retro_load_game.restype = C.c_bool
        lib.retro_get_memory_data.restype = C.c_void_p
        lib.retro_get_memory_data.argtypes = [C.c_uint]
        lib.retro_get_memory_size.restype = C.c_size_t
        lib.retro_get_memory_size.argtypes = [C.c_uint]
        lib.retro_serialize_size.restype = C.c_size_t
        lib.retro_serialize.argtypes = [C.c_void_p, C.c_size_t]
        lib.retro_serialize.restype = C.c_bool
        lib.retro_unserialize.argtypes = [C.c_void_p, C.c_size_t]
        lib.retro_unserialize.restype = C.c_bool
        lib.retro_init()
        Core._instance = self

    # --- callbacks ---
    def _env(self, cmd: int, data: int) -> bool:
        if cmd == ENV_SET_PIXEL_FORMAT:
            return C.cast(data, C.POINTER(C.c_int))[0] == PIXEL_RGB565
        if cmd in (ENV_GET_SYSTEM_DIRECTORY, ENV_GET_SAVE_DIRECTORY, ENV_GET_CORE_ASSETS_DIRECTORY):
            C.cast(data, C.POINTER(C.c_char_p))[0] = self._dir
            return True
        if cmd in (ENV_GET_CAN_DUPE, ENV_GET_VARIABLE_UPDATE):
            C.cast(data, C.POINTER(C.c_bool))[0] = cmd == ENV_GET_CAN_DUPE
            return True
        return False

    def _video(self, data: int, width: int, height: int, pitch: int) -> None:
        if not data:  # duplicate frame
            return
        row = width * 2
        raw = C.string_at(data, height * pitch)
        self.frame = raw if pitch == row else b''.join(raw[y * pitch : y * pitch + row] for y in range(height))
        self.width, self.height = width, height

    def _input(self, port: int, device: int, index: int, button: int) -> int:
        return int(port == 0 and device == 1 and bool(self.buttons >> button & 1))

    # --- API ---
    def load(self, rom: bytes, name: str = 'game.sfc') -> None:
        if self.loaded:
            self._lib.retro_unload_game()
            self.loaded = False
        self._rom = C.create_string_buffer(rom, len(rom))
        game = _Game(name.encode(), C.cast(self._rom, C.c_void_p), len(rom), None)
        if not self._lib.retro_load_game(C.byref(game)):
            raise CoreError('The emulator rejected the ROM.')
        self.loaded = True
        self.frames_run = 0

    def memory(self, kind: str = 'wram') -> C.Array[C.c_ubyte]:
        if not self.loaded:
            raise CoreError('No game loaded.')
        if kind not in MEMORY:
            raise CoreError(f'Unknown memory {kind!r}; use {"/".join(MEMORY)}.')
        address = self._lib.retro_get_memory_data(MEMORY[kind])
        size = self._lib.retro_get_memory_size(MEMORY[kind])
        if not address or not size:
            raise CoreError(f'This core does not expose {kind}.')
        return (C.c_ubyte * size).from_address(address)

    @property
    def ram(self) -> C.Array[C.c_ubyte]:
        return self.memory('wram')

    def run(self, frames: int = 1, buttons: int = 0) -> None:
        if not self.loaded:
            raise CoreError('No game loaded (Snes9x would abort the process).')
        self.buttons = buttons
        for _ in range(frames):
            self._lib.retro_run()
        self.frames_run += frames

    def reset(self) -> None:
        if not self.loaded:
            raise CoreError('No game loaded.')
        self._lib.retro_reset()

    def snapshot(self) -> Frame:
        if not self.frame:
            raise CoreError('No frame yet: run at least one frame first.')
        return Frame(self.frame, self.width, self.height)

    def save_state(self) -> bytes:
        size = self._lib.retro_serialize_size()
        buf = C.create_string_buffer(size)
        if not self._lib.retro_serialize(buf, size):
            raise CoreError('Could not save state.')
        return buf.raw

    def load_state(self, state: bytes) -> None:
        buf = C.create_string_buffer(state, len(state))
        if not self._lib.retro_unserialize(buf, len(state)):
            raise CoreError('Could not restore state (made with a different core or game?).')


def wram_offset(address: int | str) -> int:
    """$7E0094, $94, $7F:0000, 0x1A0 -> an offset into the 128 KiB of WRAM."""
    a = romlib.parse_address(address)
    bank, low = a >> 16, a & 0xFFFF
    if bank in (0x7E, 0x7F):
        return a - 0x7E0000
    if bank == 0 and a < 0x20000:
        return a
    if (bank & 0x7F) < 0x40 and low < 0x2000:
        return low  # low-RAM mirror in banks $00-$3F/$80-$BF
    raise CoreError(f'${a:06X} is not a WRAM address.')
