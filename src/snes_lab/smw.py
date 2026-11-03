"""Super Mario World (USA 1.0): level data, RAM map and emulator recipes.

The game-specific half of snes-lab. Facts here were found the hard way;
docs/smw-notes.md lists the gotchas behind them."""

from __future__ import annotations

from typing import Any

from . import rom as R
from .core import LEFT, B, Core, CoreError, mask
from .image import Frame

CLEAN_SHA256 = '0838e531fe22c077528febe14cb3ff7c492f1f5fa8de354192bdff7137c27f5b'

# ROM tables (file offsets in a headerless LoROM image)
LAYER1_POINTERS = 0x2E000  # $05:E000, 3 bytes per level
LAYER2_POINTERS = 0x2E600  # $05:E600, 3 bytes per level
SPRITE_POINTERS = 0x2EC00  # $05:EC00, 2 bytes per level, bank $07
SECONDARY = (0x2F000, 0x2F200, 0x2F400, 0x2F600)  # $05:F000/F200/F400/F600, 1 byte per level each
LEVEL_COUNT = 0x200
PREVIEW_LEVEL, PREVIEW_TRANSLEVEL = 0x105, 0x29  # Yoshi's Island 1: the slot boot() parks next to
NO_INTRO = 0x80

# WRAM (SMWDisX rammap.asm)
RAM = {
    'game_mode': 0x0100,
    'player_x': 0x0094,
    'player_y': 0x0096,
    'player_x_speed': 0x007B,
    'player_y_speed': 0x007D,
    'player_animation': 0x0071,
    'power_up': 0x0019,
    'goal_timer': 0x1493,
    'midway': 0x13CE,
    'lives': 0x0DBE,
    'translevel': 0x13BF,
    'star_timer': 0x1490,
    'invuln_timer': 0x1497,
    'ow_level_settings': 0x1EA2,
}
MODE_OVERWORLD, MODE_LEVEL = 0x0E, 0x14
ANIM_DYING = 0x09
MIDWAY_BIT = 0x40
ROWS = 27  # horizontal levels are 27 tiles tall

MODES = {
    0x00: 'horizontal',
    0x01: 'horizontal, layer 2',
    0x02: 'horizontal, layer 2',
    0x03: 'vertical?',
    0x07: 'vertical',
    0x08: 'vertical, layer 2',
    0x09: 'boss',
    0x0A: 'vertical, layer 2',
    0x0B: 'boss',
    0x0C: 'dark background',
    0x0D: 'vertical, dark',
    0x0E: 'dark, layer 3',
    0x0F: 'horizontal, layer 2',
    0x10: 'boss',
    0x11: 'dark, layer 2',
    0x1E: 'horizontal, translucent layer 2',
    0x1F: 'horizontal, translucent layer 2',
}
TILESETS = (
    'normal 1',
    'castle 1',
    'rope 1',
    'underground 1',
    'switch palace 1',
    'ghost house 1',
    'rope 2',
    'normal 2',
    'rope 3',
    'underground 2',
    'switch palace 2',
    'castle 2',
    'cloud/forest',
    'ghost house 2',
    'underground 3',
    'unused',
)


def check_clean(rom: bytes) -> None:
    """Level tools assume the USA 1.0 layout; patched ROMs work if they keep the vanilla tables."""
    if R.detect_mapping(rom) != 'lorom' or len(rom) < 0x80000:
        raise R.RomError('Not a Super Mario World ROM (expected a LoROM image of at least 512 KiB).')
    if b'SUPER MARIOWORLD' not in rom[0x7FC0:0x7FD5]:
        raise R.RomError(f'Header title is {rom[0x7FC0:0x7FD5]!r}, not SUPER MARIOWORLD.')


def _ptr3(rom: bytes, table: int, level: int) -> int:
    return int.from_bytes(rom[table + level * 3 : table + level * 3 + 3], 'little')


def level_pointers(rom: bytes, level: int) -> dict[str, int]:
    if not 0 <= level < LEVEL_COUNT:
        raise R.RomError(f'Level {level:#x} is out of range (0-1FF).')
    sprite = int.from_bytes(rom[SPRITE_POINTERS + level * 2 : SPRITE_POINTERS + level * 2 + 2], 'little')
    return {
        'layer1': _ptr3(rom, LAYER1_POINTERS, level),
        'layer2': _ptr3(rom, LAYER2_POINTERS, level),
        'sprites': 0x070000 | sprite,
    }


def decode_header(h: bytes) -> dict[str, Any]:
    return {
        'screens': (h[0] & 0x1F) + 1,
        'bg_palette': h[0] >> 5,
        'mode': h[1] & 0x1F,
        'mode_name': MODES.get(h[1] & 0x1F, 'other'),
        'back_area_color': h[1] >> 5,
        'layer3_priority': bool(h[2] & 0x80),
        'music': (h[2] >> 4) & 7,
        'sprite_gfx': h[2] & 0x0F,
        'time': ('none', '200', '300', '400')[h[3] >> 6],
        'sprite_palette': (h[3] >> 3) & 7,
        'fg_palette': h[3] & 7,
        'item_memory': h[4] >> 6,
        'vertical_scroll': (h[4] >> 4) & 3,
        'tileset': h[4] & 0x0F,
        'tileset_name': TILESETS[h[4] & 0x0F],
    }


def decode_objects(data: bytes, at: int) -> tuple[list[dict[str, Any]], int]:
    """Layer-1 objects from file offset `at` (just after the 5-byte header). Returns objects and end offset."""
    out: list[dict[str, Any]] = []
    screen, i = 0, at
    while i < len(data) and data[i] != 0xFF:
        b1, b2, b3 = data[i], data[i + 1], data[i + 2]
        if b1 & 0x80:
            screen += 1
        number = (b1 >> 1 & 0x30) | b2 >> 4
        y, x_lo = b1 & 0x1F, b2 & 0x0F
        if number == 0 and b3 == 0:  # screen exit: 4 bytes
            b4 = data[i + 3]
            out.append(
                {
                    'kind': 'exit',
                    'screen': b1 & 0x1F,
                    'secondary': bool(b2 & 2),
                    'water': bool(b2 & 8),
                    'destination': ((b2 & 1) << 8) | b4,
                }
            )
            i += 4
            continue
        if number == 0 and b3 == 1:  # screen jump
            screen = b1 & 0x1F
            out.append({'kind': 'jump', 'screen': screen})
            i += 3
            continue
        x = screen * 16 + x_lo
        if number == 0:
            out.append({'kind': 'extended', 'number': b3, 'x': x, 'y': y, 'screen': screen})
        else:
            out.append(
                {
                    'kind': 'object',
                    'number': number,
                    'settings': b3,
                    'x': x,
                    'y': y,
                    'screen': screen,
                    'width': (b3 & 15) + 1,
                    'height': (b3 >> 4) + 1,
                }
            )
        i += 3
    return out, i


def decode_sprites(data: bytes, at: int) -> tuple[int, list[dict[str, Any]]]:
    header = data[at]
    out: list[dict[str, Any]] = []
    i = at + 1
    while i < len(data) and data[i] != 0xFF:
        b1, b2, b3 = data[i], data[i + 1], data[i + 2]
        screen = ((b1 & 2) << 3) | (b2 & 0x0F)
        y = ((b1 & 1) << 4) | b1 >> 4
        out.append({'number': b3, 'x': screen * 16 + (b2 >> 4), 'y': y, 'screen': screen, 'extra_bits': (b1 >> 2) & 3})
        i += 3
    return header, out


def decode_level(rom: bytes, level: int) -> dict[str, Any]:
    check_clean(rom)
    p = level_pointers(rom, level)
    l1 = R.to_offset(p['layer1'])
    h = rom[l1 : l1 + 5]
    objects, end = decode_objects(rom, l1 + 5)
    sprite_header, sprites = decode_sprites(rom, R.to_offset(p['sprites']))
    f0, f2, f4, f6 = (rom[t + level] for t in SECONDARY)
    return {
        'level': f'{level:03X}',
        'pointers': {k: f'${v:06X}' for k, v in p.items()},
        'layer1_bytes': end + 1 - l1,
        'header': decode_header(h),
        'header_hex': h.hex(' ').upper(),
        'secondary_header': {
            'hex': bytes((f0, f2, f4, f6)).hex(' ').upper(),
            'layer2_scroll': f0 >> 4,
            'start_y': f0 & 0x0F,
            'layer3': f2 >> 6,
            'entrance_type': (f2 >> 3) & 7,
            'start_x': f2 & 7,
            'midway_screen': f4 >> 4,
            'fg_bg_offset': (f4 >> 2) & 3,
            'no_intro': bool(f6 & 0x80),
            'unknown_f6': f6 & 0x7F,
        },
        'sprite_header': {
            'hex': f'{sprite_header:02X}',
            'sprite_memory': sprite_header & 0x1F,
            'buoyancy': sprite_header >> 6,
        },
        'objects': objects,
        'sprites': sprites,
    }


def preview_rom(rom: bytes, level: int) -> bytes:
    """A copy of `rom` where Yoshi's Island 1 (level 105) plays `level` instead.

    Copies the level's layer 1, layer 2 and sprite pointers and its four secondary-header bytes into
    level 105's slots, and sets the no-intro bit so castles and ghost houses skip the walk-in."""
    check_clean(rom)
    out = bytearray(rom)
    src, dst = level, PREVIEW_LEVEL
    for table, size in ((LAYER1_POINTERS, 3), (LAYER2_POINTERS, 3), (SPRITE_POINTERS, 2)):
        out[table + dst * size : table + dst * size + size] = rom[table + src * size : table + src * size + size]
    for t in SECONDARY:
        out[t + dst] = rom[t + src]
    out[SECONDARY[3] + dst] |= NO_INTRO
    return R.fix_checksum(out)


# --- emulator recipes (run inside the worker process) ----------------------


def word(ram: Any, at: int) -> int:
    return int(ram[at] | ram[at + 1] << 8)


def status(core: Core) -> dict[str, Any]:
    r = core.ram
    return {
        'game_mode': f'{r[RAM["game_mode"]]:#04x}',
        'in_level': r[RAM['game_mode']] == MODE_LEVEL,
        'x': word(r, RAM['player_x']),
        'y': word(r, RAM['player_y']),
        'animation': r[RAM['player_animation']],
        'dying': r[RAM['player_animation']] == ANIM_DYING,
        'power_up': r[RAM['power_up']],
        'goal': bool(r[RAM['goal_timer']]),
        'midway': bool(r[RAM['midway']]),
        'lives': r[RAM['lives']],
        'translevel': f'{r[RAM["translevel"]]:02X}',
        'frames_run': core.frames_run,
    }


def boot(core: Core, rom: bytes) -> bytes:
    """Power on, pass the title screen and stand next to Yoshi's Island 1 on the overworld.

    Controller inputs only. Returns that savestate; enter() reuses it for any ROM with the same base."""
    c = core
    c.load(rom)
    c.run(600)
    for _ in range(3):
        c.run(1, mask('START'))
        c.run(90)
    c.run(840)
    c.run(1, 1 << B)  # into YI1 (the first visit plays the intro message)
    c.run(450)
    c.run(50, 1 << LEFT)  # back out onto the map
    c.run(60)
    if c.ram[RAM['game_mode']] != MODE_OVERWORLD:
        raise CoreError(f'Boot did not reach the overworld (game mode {c.ram[RAM["game_mode"]]:#x}).')
    return c.save_state()


def enter(core: Core, rom: bytes, overworld: bytes, midway: bool = False, max_frames: int = 1200) -> None:
    """Load `rom`, restore the overworld state and press B: in the YI1 slot in well under a second.

    Polls the game mode instead of waiting a fixed time (sprite-tileset changes make entry slower)."""
    c = core
    c.load(rom)
    c.load_state(overworld)
    at = RAM['ow_level_settings'] + PREVIEW_TRANSLEVEL
    c.ram[at] = c.ram[at] | MIDWAY_BIT if midway else c.ram[at] & ~MIDWAY_BIT & 0xFF
    c.ram[RAM['lives']] = 4
    c.run(1, 1 << B)
    for _ in range(max_frames):
        c.run(1)
        if c.ram[RAM['game_mode']] == MODE_LEVEL:
            return
    raise CoreError(f'The level did not start (game mode {c.ram[RAM["game_mode"]]:#x}).')


def tour(core: Core, screens: int, step: int = 4, y: int = 0x0100, every: int = 1) -> list[Frame]:
    """Glide Mario across a horizontal level and grab one frame per `every` screens.

    Writes Mario's position each frame (layout check, not a play claim). Teleporting doesn't redraw
    layer 1, so the camera has to glide; y=0x100 keeps Mario above munchers, which hurt even with a star."""
    c, r = core, core.ram
    frames: list[Frame] = []
    x = int(word(r, RAM['player_x']))
    for screen in range(0, screens, every):
        target = screen * 256 + 128
        while x < target:
            x += step
            r[0x94], r[0x95], r[0x96], r[0x97] = x & 255, x >> 8, y & 255, y >> 8
            r[RAM['player_x_speed']] = r[RAM['player_y_speed']] = 0
            r[RAM['invuln_timer']] = r[RAM['star_timer']] = 0x40
            c.run(1)
        c.run(1)
        frames.append(c.snapshot())
    return frames


def route_spec() -> dict[str, Any]:
    """The route-bot spec for 'reach the goal tape in this level'."""
    return {
        'progress': {'address': '$94', 'size': 2},
        'goal': [{'address': '$1493', 'op': '!=', 'value': 0}],
        'fail': [
            {'address': '$71', 'op': '==', 'value': ANIM_DYING},
            {'address': '$100', 'op': '!=', 'value': MODE_LEVEL},
            {'address': '$96', 'size': 2, 'op': '>', 'value': 0x170},
        ],
        'penalty': [{'address': '$19', 'op': '==', 'value': 0, 'points': 400}],
        'dedupe': [
            {'address': '$94', 'size': 2, 'div': 6},
            {'address': '$96', 'size': 2, 'div': 12},
            {'address': '$7B', 'div': 16},
        ],
        'actions': 'platformer-right',
    }
