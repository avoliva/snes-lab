"""A 65C816 disassembler and call-site finder that work straight from the ROM, for games with no disassembly.

The 65816 cannot be decoded without knowing the M (accumulator) and X (index) flags: `LDA #imm` is 2 or
3 bytes depending on M. disassemble() starts from the flags you give and follows REP/SEP, and every line
shows the flags it assumed, so a wrong guess is visible rather than silent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import rom as R

_TABLE = """BRK s;ORA dxi;COP s;ORA sr;TSB d;ORA d;ASL d;ORA dil;PHP i;ORA m;ASL A;PHD i;TSB a;ORA a;ASL a;ORA al
BPL r;ORA diy;ORA di;ORA sriy;TRB d;ORA dx;ASL dx;ORA dily;CLC i;ORA ay;INC A;TCS i;TRB a;ORA ax;ASL ax;ORA alx
JSR a;AND dxi;JSL al;AND sr;BIT d;AND d;ROL d;AND dil;PLP i;AND m;ROL A;PLD i;BIT a;AND a;ROL a;AND al
BMI r;AND diy;AND di;AND sriy;BIT dx;AND dx;ROL dx;AND dily;SEC i;AND ay;DEC A;TSC i;BIT ax;AND ax;ROL ax;AND alx
RTI i;EOR dxi;WDM s;EOR sr;MVP bm;EOR d;LSR d;EOR dil;PHA i;EOR m;LSR A;PHK i;JMP a;EOR a;LSR a;EOR al
BVC r;EOR diy;EOR di;EOR sriy;MVN bm;EOR dx;LSR dx;EOR dily;CLI i;EOR ay;PHY i;TCD i;JML al;EOR ax;LSR ax;EOR alx
RTS i;ADC dxi;PER rl;ADC sr;STZ d;ADC d;ROR d;ADC dil;PLA i;ADC m;ROR A;RTL i;JMP ai;ADC a;ROR a;ADC al
BVS r;ADC diy;ADC di;ADC sriy;STZ dx;ADC dx;ROR dx;ADC dily;SEI i;ADC ay;PLY i;TDC i;JMP axi;ADC ax;ROR ax;ADC alx
BRA r;STA dxi;BRL rl;STA sr;STY d;STA d;STX d;STA dil;DEY i;BIT m;TXA i;PHB i;STY a;STA a;STX a;STA al
BCC r;STA diy;STA di;STA sriy;STY dx;STA dx;STX dy;STA dily;TYA i;STA ay;TXS i;TXY i;STZ a;STA ax;STZ ax;STA alx
LDY x;LDA dxi;LDX x;LDA sr;LDY d;LDA d;LDX d;LDA dil;TAY i;LDA m;TAX i;PLB i;LDY a;LDA a;LDX a;LDA al
BCS r;LDA diy;LDA di;LDA sriy;LDY dx;LDA dx;LDX dy;LDA dily;CLV i;LDA ay;TSX i;TYX i;LDY ax;LDA ax;LDX ay;LDA alx
CPY x;CMP dxi;REP s;CMP sr;CPY d;CMP d;DEC d;CMP dil;INY i;CMP m;DEX i;WAI i;CPY a;CMP a;DEC a;CMP al
BNE r;CMP diy;CMP di;CMP sriy;PEI d;CMP dx;DEC dx;CMP dily;CLD i;CMP ay;PHX i;STP i;JML ail;CMP ax;DEC ax;CMP alx
CPX x;SBC dxi;SEP s;SBC sr;CPX d;SBC d;INC d;SBC dil;INX i;SBC m;NOP i;XBA i;CPX a;SBC a;INC a;SBC al
BEQ r;SBC diy;SBC di;SBC sriy;PEA a;SBC dx;INC dx;SBC dily;SED i;SBC ay;PLX i;XCE i;JSR axi;SBC ax;INC ax;SBC alx"""

OPCODES: list[tuple[str, str]] = [
    (e.split()[0], e.split()[1]) for row in _TABLE.split('\n') for e in row.split(';')
]  # fmt: skip
assert len(OPCODES) == 256

_LEN = {'i': 0, 'A': 0, 's': 1, 'd': 1, 'dx': 1, 'dy': 1, 'dxi': 1, 'di': 1, 'diy': 1, 'dil': 1, 'dily': 1,
        'sr': 1, 'sriy': 1, 'r': 1, 'a': 2, 'ax': 2, 'ay': 2, 'ai': 2, 'axi': 2, 'ail': 2, 'rl': 2, 'bm': 2,
        'al': 3, 'alx': 3}  # fmt: skip
_FMT = {'i': '', 'A': 'A', 's': '#${:02X}', 'd': '${:02X}', 'dx': '${:02X},x', 'dy': '${:02X},y',
        'dxi': '(${:02X},x)', 'di': '(${:02X})', 'diy': '(${:02X}),y', 'dil': '[${:02X}]', 'dily': '[${:02X}],y',
        'sr': '${:02X},s', 'sriy': '(${:02X},s),y', 'a': '${:04X}', 'ax': '${:04X},x', 'ay': '${:04X},y',
        'ai': '(${:04X})', 'axi': '(${:04X},x)', 'ail': '[${:04X}]', 'al': '${:06X}', 'alx': '${:06X},x'}  # fmt: skip
ENDS = {'RTS', 'RTL', 'RTI', 'STP', 'JMP', 'JML'}  # not BRA/BRL: usually an if/else inside the routine
CALL_OPS = {0x22: 'JSL', 0x5C: 'JML', 0x20: 'JSR', 0x4C: 'JMP'}
LONG_DATA_OPS = {op for op, (_, mode) in enumerate(OPCODES) if mode in ('al', 'alx') and op not in CALL_OPS}


@dataclass
class Insn:
    address: int
    raw: bytes
    mnemonic: str
    operand: str
    m8: bool
    x8: bool
    target: int | None = None  # where a branch, jump or call goes
    seen: str | None = None  # with a code/data log: 'code' (flags from the log), 'data' or 'unseen'

    def text(self) -> str:
        flags = f'{"M8" if self.m8 else "M16"} {"X8" if self.x8 else "X16"}'
        line = f'${self.address >> 16:02X}:{self.address & 0xFFFF:04X}  {self.raw.hex(" ").upper():<12} {flags:<8} '
        note = {'data': '  ; data in the log', 'unseen': '  ; not run in the log'}.get(self.seen or '', '')
        return (line + f'{self.mnemonic} {self.operand}').rstrip() + note

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            'address': f'${self.address >> 16:02X}:{self.address & 0xFFFF:04X}',
            'bytes': self.raw.hex(' ').upper(),
            'asm': f'{self.mnemonic} {self.operand}'.rstrip(),
        }
        if self.target is not None:
            d['target'] = f'${self.target >> 16:02X}:{self.target & 0xFFFF:04X}'
        return d


def decode(code: bytes, address: int, m8: bool, x8: bool) -> Insn:
    """Decode one instruction from `code` (which starts at the opcode) at CPU `address`."""
    op = code[0]
    mnemonic, mode = OPCODES[op]
    if mode == 'm':
        n = 1 if m8 else 2
    elif mode == 'x':
        n = 1 if x8 else 2
    else:
        n = _LEN[mode]
    if len(code) < 1 + n:
        raise R.RomError(f'Instruction at ${address:06X} runs past the end of the ROM.')
    v = int.from_bytes(code[1 : 1 + n], 'little')
    bank, pc = address & 0xFF0000, address & 0xFFFF
    target = None
    if mode in ('m', 'x'):
        operand = f'#${v:0{n * 2}X}'
    elif mode == 'r':
        target = bank | (pc + 2 + (v - 256 if v > 127 else v)) & 0xFFFF
        operand = f'${target & 0xFFFF:04X}'
    elif mode == 'rl':
        target = bank | (pc + 3 + (v - 65536 if v > 32767 else v)) & 0xFFFF
        operand = f'${target & 0xFFFF:04X}'
    elif mode == 'bm':  # bytes are dest, src; written "MVN src,dest"
        operand = f'${v >> 8:02X},${v & 0xFF:02X}'
    else:
        operand = _FMT[mode].format(v)
        if mnemonic in ('JSR', 'JMP') and mode == 'a':
            target = bank | v
        elif mnemonic in ('JSL', 'JML') and mode == 'al':
            target = v
    return Insn(address, bytes(code[: 1 + n]), mnemonic, operand, m8, x8, target)


def disassemble(
    rom: bytes,
    address: int | str,
    count: int = 40,
    m8: bool = False,
    x8: bool = False,
    mapping: str | None = None,
    stop_at_return: bool = False,
    cdl: bytes | None = None,
) -> list[Insn]:
    """Linear disassembly from `address`, following REP/SEP for the M/X flags. Stops after `count`
    instructions, or (stop_at_return) after the first RTS/RTL/RTI/STP/JMP/JML.

    With `cdl` (a Mesen code/data log, one flag byte per ROM byte), an opcode the game ran takes its
    M/X flags from the log instead of the guess, and every line says whether the log saw it run."""
    mapping = mapping or R.code_mapping(rom)
    a = R.parse_address(address)
    out: list[Insn] = []
    for _ in range(count):
        try:
            off = R.to_offset(a, mapping)
        except R.RomError:
            if not out:
                raise  # the start address itself is not ROM
            break  # ran off the end of the bank (wrapped to $0000): stop the listing there
        if off >= len(rom):
            break
        seen = None
        if cdl is not None:
            flags = cdl[off] if off < len(cdl) else 0
            if flags & 0x01:
                m8, x8, seen = bool(flags & 0x20), bool(flags & 0x10), 'code'
            else:
                seen = 'data' if flags & 0x02 else 'unseen'
        try:
            ins = decode(rom[off : off + 4], a, m8, x8)
        except R.RomError:
            if not out:
                raise
            break  # the last instruction would run past the end of the ROM
        ins.seen = seen
        out.append(ins)
        if ins.mnemonic in ('REP', 'SEP'):
            v = ins.raw[1]
            if v & 0x20:
                m8 = ins.mnemonic == 'SEP'
            if v & 0x10:
                x8 = ins.mnemonic == 'SEP'
        elif ins.mnemonic == 'XCE':
            pass  # emulation-mode switches are rare in game code; flags are left as they are
        if stop_at_return and ins.mnemonic in ENDS:
            break
        a = (a & 0xFF0000) | ((a + len(ins.raw)) & 0xFFFF)
    return out


def _same(target_off: int, address: int, mapping: str) -> bool:
    try:
        return R.to_offset(address, mapping) == target_off
    except R.RomError:
        return False


def xrefs(
    rom: bytes, target: int | str, mapping: str | None = None, kinds: tuple[str, ...] = ('call',), limit: int = 200
) -> dict[str, Any]:
    """Find instructions that call or jump to `target` (kinds 'call': JSL/JML long, JSR/JMP absolute in the
    same bank) and, with 'long', long-address loads and stores of it (LDA $7E1234 etc.).

    It is a byte-pattern scan, so data that happens to look like an instruction also matches; each hit
    carries the instruction before it so a reader can judge. Addresses are compared as ROM file offsets,
    so every mirror ($00:8000 vs $80:8000) counts. RAM targets match long operands only."""
    mapping = mapping or R.code_mapping(rom)
    t = R.parse_address(target)
    try:
        t_off: int | None = R.to_offset(t, mapping)
    except R.RomError:
        t_off = None  # RAM or I/O: compare the address itself
    want_long = 'long' in kinds
    hits: list[dict[str, Any]] = []
    truncated = False
    for off in range(len(rom) - 3):
        op = rom[off]
        kind = CALL_OPS.get(op)
        if kind is None and not (want_long and op in LONG_DATA_OPS):
            continue
        if kind in ('JSL', 'JML') or kind is None:
            operand = rom[off + 1] | rom[off + 2] << 8 | rom[off + 3] << 16
            match = _same(t_off, operand, mapping) if t_off is not None else operand == t
            if match and kind is None:
                kind = OPCODES[op][0]
        else:
            if t_off is None:
                continue
            operand = rom[off + 1] | rom[off + 2] << 8
            here = R.to_address(off, mapping)
            match = _same(t_off, (here & 0xFF0000) | operand, mapping)
        if not match or kind is None or ('call' not in kinds and kind in CALL_OPS.values()):
            continue
        if len(hits) >= limit:
            truncated = True
            break
        here = R.to_address(off, mapping)
        hits.append({'address': f'${here >> 16:02X}:{here & 0xFFFF:04X}', 'file_offset': f'{off:#x}', 'kind': kind})
    return {'target': f'${t >> 16:02X}:{t & 0xFFFF:04X}', 'mapping': mapping, 'hits': hits, 'truncated': truncated}
