"""A route bot: beam search over short input chunks with savestates, scored from RAM.

Generalised from the Skyglass play_bot. RAM is only read to score progress, never written,
so a found route is a real controller-input route. The spec says what to read:

    {"progress": {"address": "$94", "size": 2},            # maximise this
     "goal":    [{"address": "$1493", "op": "!=", "value": 0}],   # any true -> done
     "fail":    [{"address": "$71", "op": "==", "value": 9}],     # any true -> prune
     "penalty": [{"address": "$19", "op": "==", "value": 0, "points": 400}],
     "dedupe":  [{"address": "$94", "size": 2, "div": 6}],        # bucket key for the beam
     "actions": "platformer-right" | {"name": ["RIGHT+B", ...per frame]},
     "chunk": 8}"""

from __future__ import annotations

import operator
import time
from typing import Any

from .core import Core, CoreError, mask, wram_offset

OPS = {
    '==': operator.eq,
    '!=': operator.ne,
    '>': operator.gt,
    '<': operator.lt,
    '>=': operator.ge,
    '<=': operator.le,
    '&': lambda a, b: bool(a & b),
}


def _platformer(chunk: int) -> dict[str, list[int]]:
    def held(b: str) -> list[int]:
        return [mask(b)] * chunk

    def tap(b: str) -> list[int]:  # release B for one frame so a new jump starts
        return [mask(b.replace('+B', ''))] + [mask(b)] * (chunk - 1)

    return {
        'run': held('Y+RIGHT'),
        'runjump': held('Y+RIGHT+B'),
        'runtapjump': tap('Y+RIGHT+B'),
        'walk': held('RIGHT'),
        'walkjump': held('RIGHT+B'),
        'tapjump': tap('RIGHT+B'),
        'spin': held('RIGHT+A'),
        'idle': held(''),
        'back': held('LEFT'),
        'backjump': held('LEFT+B'),
        'duck': held('DOWN'),
    }


PRESETS = {'platformer-right': _platformer}


def actions(spec: dict[str, Any]) -> tuple[dict[str, list[int]], int]:
    chunk = int(spec.get('chunk', 8))
    a = spec.get('actions', 'platformer-right')
    if isinstance(a, str):
        if a not in PRESETS:
            raise CoreError(f'Unknown action preset {a!r}; have {", ".join(PRESETS)}.')
        return PRESETS[a](chunk), chunk
    out = {}
    for name, frames in a.items():
        seq = [mask(f) for f in frames]
        out[name] = (seq * chunk)[:chunk] if len(seq) < chunk else seq[:chunk]
    return out, chunk


def _value(ram: Any, item: dict[str, Any]) -> int:
    at = wram_offset(item['address'])
    v = int(ram[at])
    if int(item.get('size', 1)) == 2:
        v |= int(ram[at + 1]) << 8
    return v // int(item.get('div', 1))


def _any(ram: Any, conds: list[dict[str, Any]]) -> bool:
    return any(OPS[c.get('op', '!=')](_value(ram, c), int(c.get('value', 0))) for c in conds)


def _score(ram: Any, spec: dict[str, Any]) -> int:
    s = _value(ram, spec['progress']) * 10
    for p in spec.get('penalty', []):
        if OPS[p.get('op', '==')](_value(ram, p), int(p.get('value', 0))):
            s -= int(p.get('points', 100))
    return s


def search(
    core: Core, spec: dict[str, Any], beam: int = 40, max_steps: int = 900, time_limit: float = 600
) -> dict[str, Any]:
    """From the core's current state, find action chunks that satisfy spec['goal']."""
    acts, chunk = actions(spec)
    ram = core.ram
    root = core.save_state()
    nodes: list[tuple[int, list[str], bytes]] = [(0, [], root)]
    best, t0, step = 0, time.time(), 0

    def play(name: str) -> None:
        for m in acts[name]:
            core.run(1, m)

    try:
        for step in range(max_steps):
            if time.time() - t0 > time_limit:
                return {
                    'found': False,
                    'reason': 'time limit',
                    'steps': step,
                    'best_progress': best // 10,
                    'actions': nodes[0][1],
                    'chunk': chunk,
                    'seconds': round(time.time() - t0, 1),
                }
            cand: dict[tuple[int, ...], tuple[int, list[str], bytes, str]] = {}
            for _, seq, snap in nodes:
                for name in acts:
                    core.load_state(snap)
                    play(name)
                    if _any(ram, spec.get('goal', [])):
                        return {
                            'found': True,
                            'actions': seq + [name],
                            'chunk': chunk,
                            'steps': step + 1,
                            'seconds': round(time.time() - t0, 1),
                        }
                    if _any(ram, spec.get('fail', [])):
                        continue
                    sc = _score(ram, spec)
                    key = tuple(_value(ram, d) for d in spec.get('dedupe', [spec['progress']]))
                    if key not in cand or cand[key][0] < sc:
                        cand[key] = (sc, seq + [name], snap, name)
            if not cand:
                return {
                    'found': False,
                    'reason': 'every branch failed',
                    'steps': step,
                    'best_progress': best // 10,
                    'actions': nodes[0][1],
                    'chunk': chunk,
                    'seconds': round(time.time() - t0, 1),
                }
            top = sorted(cand.values(), key=lambda c: -c[0])[:beam]
            nodes = []
            for sc, seq, parent, name in top:
                core.load_state(parent)
                play(name)
                nodes.append((sc, seq, core.save_state()))
            best = max(best, top[0][0])
        return {
            'found': False,
            'reason': 'step limit',
            'steps': step + 1,
            'best_progress': best // 10,
            'actions': nodes[0][1],
            'chunk': chunk,
            'seconds': round(time.time() - t0, 1),
        }
    finally:
        core.load_state(root)  # leave the session where the search started


def replay(core: Core, spec: dict[str, Any], seq: list[str], chunk: int) -> dict[str, Any]:
    """Play a found route from the current state with controller inputs only and report what happened."""
    acts, _ = actions({**spec, 'chunk': chunk})
    ram = core.ram
    for i, name in enumerate(seq):
        if name not in acts:
            raise CoreError(f'Route uses unknown action {name!r}.')
        for m in acts[name]:
            core.run(1, m)
        if _any(ram, spec.get('goal', [])):
            return {'goal': True, 'at_chunk': i, 'progress': _value(ram, spec['progress'])}
        if _any(ram, spec.get('fail', [])):
            return {'goal': False, 'failed_at_chunk': i, 'progress': _value(ram, spec['progress'])}
    return {'goal': False, 'ran_out_at_chunk': len(seq), 'progress': _value(ram, spec['progress'])}
