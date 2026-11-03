"""The experiment runner: "vary X across Y, start, observe, group the screenshots".

Each variant is a set of ROM and/or RAM
writes; every variant starts the same way, runs the same inputs, and is photographed. Variants with
identical pictures (or identical crops) land in one group, so you only eyeball one image per group."""

from __future__ import annotations

from typing import Any

from . import rom as R
from . import smw
from .image import Frame, sheet
from .sessions import Session, SessionError


def parse_writes(items: list[dict[str, Any]] | None) -> list[tuple[Any, bytes, bool]]:
    out = []
    for w in items or []:
        if 'hex' not in w or 'address' not in w:
            raise ValueError(f'Each write needs "address" and "hex": {w}')
        out.append((w['address'], bytes.fromhex(str(w['hex']).replace(' ', '')), bool(w.get('offset'))))
    return out


def build_rom(base: bytes, writes: list[tuple[Any, bytes, bool]], fix: bool = True) -> bytes:
    """Apply CPU-address (or file-offset) writes and, by default, fix the checksum."""
    if not writes:
        return base
    mapping = R.detect_mapping(base)
    at = [(R.parse_address(a) if off else R.to_offset(R.parse_address(a), mapping), d) for a, d, off in writes]
    patched = R.patch(base, at)
    return R.fix_checksum(patched) if fix else patched


def start(session: Session, base: bytes, writes: list[tuple[Any, bytes, bool]], how: str) -> None:
    """how: 'power_on:<frames>', 'smw_level:<hex level>' or 'state:<path>'."""
    kind, _, arg = how.partition(':')
    if kind == 'power_on':
        session.call('load', rom=build_rom(base, writes))
        if arg and int(arg):
            session.call('run', frames=int(arg))
    elif kind == 'smw_level':
        level = int(arg, 16) if arg else None
        target = smw.preview_rom(base, level) if level is not None else base
        session.call('smw_enter', base=base, rom=build_rom(target, writes))
    elif kind == 'state':
        session.call('load', rom=build_rom(base, writes))
        session.call('load_state', path=arg)
    else:
        raise ValueError(f'Unknown start {how!r}; use power_on:<frames>, smw_level:<hex> or state:<path>.')


def run(
    session: Session,
    base: bytes,
    variants: list[dict[str, Any]],
    how: str,
    inputs: list[Any] | None = None,
    settle: int = 30,
    crop: list[int] | None = None,
    cols: int = 4,
) -> dict[str, Any]:
    """Returns per-variant results, groups of variant names with identical pictures, and two sheets."""
    if not variants:
        raise ValueError('Give at least one variant.')
    results: list[dict[str, Any]] = []
    shots: list[tuple[str, str, Frame]] = []
    groups: dict[str, list[str]] = {}
    for i, v in enumerate(variants):
        name = str(v.get('name', f'v{i}'))
        try:
            start(session, base, parse_writes(v.get('rom')), how)
            for addr, data, _ in parse_writes(v.get('ram')):
                session.call('write', address=addr, data=data)
            if inputs:
                session.call('play', inputs=inputs)
            if settle:
                session.call('run', frames=settle)
            data, w, h = session.call('frame')
            whole = Frame(data, w, h)
            digest = (whole.crop(*crop) if crop else whole).digest()
            status = session.call('smw_status') if how.startswith('smw_level') else None
        except (SessionError, R.RomError, ValueError) as e:
            results.append({'name': name, 'error': str(e)})
            continue
        shots.append((name, digest, whole))
        groups.setdefault(digest, []).append(name)
        results.append({'name': name, 'digest': digest, **({'status': status} if status else {})})
    out: dict[str, Any] = {'results': results, 'groups': list(groups.values())}
    if shots:
        firsts = {d: i for i, (_, d, _) in reversed(list(enumerate(shots)))}
        reps = sorted(firsts.values())
        out['sheet_png'] = sheet([f for _, _, f in shots], cols)
        out['sheet_order'] = [n for n, _, _ in shots]
        out['groups_png'] = sheet([shots[i][2] for i in reps], cols)
        out['groups_order'] = [shots[i][0] for i in reps]
    return out
