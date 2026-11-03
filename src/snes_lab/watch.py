"""Run the emulator while reading memory every frame: stop when a condition holds, or log every change.

A watch item is {"address": "$94", "size": 2, "kind": "wram", "name": "x"}; kind is wram (default),
sram (cartridge RAM / SA-1 BW-RAM, as a byte offset) or vram (byte offset). A condition adds
"op" and "value": ==, !=, >, <, >=, <=, & (any bit set), or "changed" (differs from the value at the start)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from . import rom as R
from .core import Core, CoreError, mask, wram_offset
from .route import OPS


def _offset(item: dict[str, Any]) -> tuple[str, int]:
    kind = item.get('kind', 'wram')
    at = wram_offset(item['address']) if kind == 'wram' else R.parse_address(item['address'])
    return kind, at


class Reader:
    """Resolves watch items once; read() is then cheap enough to call every frame."""

    def __init__(self, core: Core, items: list[dict[str, Any]]) -> None:
        self.items = []
        mems: dict[str, Any] = {}
        for item in items:
            kind, at = _offset(item)
            if kind not in mems:
                mems[kind] = core.memory(kind)
            size = int(item.get('size', 1))
            if not 1 <= size <= 4:
                raise CoreError('Watch size must be 1-4 bytes.')
            if at + size > len(mems[kind]):
                raise CoreError(f'{item["address"]} is past the end of {kind} ({len(mems[kind]):#x} bytes).')
            name = item.get('name') or str(item['address']) + (f'@{kind}' if kind != 'wram' else '')
            self.items.append((name, mems[kind], at, size))

    def read(self) -> list[int]:
        return [int.from_bytes(bytes(mem[at : at + size]), 'little') for _, mem, at, size in self.items]

    @property
    def names(self) -> list[str]:
        return [n for n, *_ in self.items]


def frames(buttons: str = '', inputs: list[Any] | None = None, limit: int = 600) -> Iterator[int]:
    """Per-frame button masks: the input script [[frames, "BUTTONS"], ...] if given (then nothing held),
    else `buttons` held. Yields at most `limit` frames."""
    n = 0
    if inputs:
        for item in inputs:
            count, b = (item[0], item[1] if len(item) > 1 else '') if isinstance(item, list) else (item, '')
            m = mask(b)
            for _ in range(int(count)):
                if n >= limit:
                    return
                yield m
                n += 1
        m = 0
    else:
        m = mask(buttons)
    while n < limit:
        yield m
        n += 1


def _check(cond: dict[str, Any], value: int, start: int) -> bool:
    op = cond.get('op', '!=')
    if op == 'changed':
        return value != start
    if op not in OPS:
        raise CoreError(f'Unknown op {op!r}; use {", ".join([*OPS, "changed"])}.')
    return OPS[op](value, int(cond.get('value', 0)))


def run_until(
    core: Core,
    until: list[dict[str, Any]],
    buttons: str = '',
    inputs: list[Any] | None = None,
    max_frames: int = 600,
    mode: str = 'any',
) -> dict[str, Any]:
    """Run frame by frame until any (mode='any') or all (mode='all') conditions hold, or max_frames."""
    if not until:
        raise CoreError('Give at least one condition in `until`.')
    reader = Reader(core, until)
    start = reader.read()
    test = all if mode == 'all' else any
    n = 0
    values = start
    met = False
    for m in frames(buttons, inputs, max_frames):
        core.run(1, m)
        n += 1
        values = reader.read()
        if test(_check(c, v, s) for c, v, s in zip(until, values, start, strict=True)):
            met = True
            break
    return {
        'met': met,
        'frames': n,
        'frames_run': core.frames_run,
        'start': dict(zip(reader.names, start, strict=True)),
        'values': dict(zip(reader.names, values, strict=True)),
    }


def watch(
    core: Core,
    items: list[dict[str, Any]],
    frames_: int = 60,
    buttons: str = '',
    inputs: list[Any] | None = None,
    every: int = 1,
    limit: int = 400,
) -> dict[str, Any]:
    """Run `frames_` frames, sampling every `every` frames, and log each change as
    {frame, name, old, new}. Stops logging (but keeps running) after `limit` events."""
    reader = Reader(core, items)
    last = reader.read()
    start = list(last)
    events: list[dict[str, Any]] = []
    truncated = False
    every = max(1, every)
    n = 0
    for m in frames(buttons, inputs, frames_):
        core.run(1, m)
        n += 1
        if n % every:
            continue
        now = reader.read()
        for name, old, new in zip(reader.names, last, now, strict=True):
            if old != new:
                if len(events) < limit:
                    events.append({'frame': n, 'name': name, 'old': old, 'new': new})
                else:
                    truncated = True
        last = now
    return {
        'frames': n,
        'frames_run': core.frames_run,
        'start': dict(zip(reader.names, start, strict=True)),
        'end': dict(zip(reader.names, reader.read(), strict=True)),
        'changes': events,
        'truncated': truncated,
    }
