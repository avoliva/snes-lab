# Design

The choices behind snes-lab, and what was rejected.

## Shape

**An MCP server, in Python, over stdio.** Built on the official `mcp` SDK. Expected errors are
raised as `ToolError` so the client sees the message instead of a generic failure.

**A generic core with Super Mario World as one module.** ROM tools, BPS, disassembly, emulator
sessions, experiments and the route bot work for any SNES game; `smw.py` holds the level format,
RAM map and recipes. Another game would add its own module. The generic parts cost little extra
over an SMW-only tool.

**Byte-level code in Rust (`crates/snes-kit`), used from Python.** Address maths for LoROM, HiROM,
ExHiROM and SA-1, header detection and checksums, BPS, HAL compression and SMW level encoding are
written once, with the overflow and bank-wrap cases tested in `cargo test`, and exposed to Python
through a PyO3 extension (`crates/snes-kit-py`, one abi3 wheel for Python 3.11+). `rom.py` keeps
its own public names and delegates to it. The emulator harness, disassembler and route bot stay
Python: the emulator core does the heavy work there.

## Emulation

**One child process per emulator session.** libretro callbacks are process-global, the core must
stay on one thread, and Snes9x aborts the whole process on some misuse. The MCP SDK runs tools on a
thread pool, so an in-process core would be called from random threads. A child process per session
fixes all three and allows several sessions at once. Rejected: a single worker thread in the server
(no isolation from aborts, one session only).

**Memory watches run inside the worker, frame by frame.** One call answers "run until HP changes"
or "what changed while I pressed B" instead of a client-side loop of `emu_run` and `emu_ram_read`.

**Levels are rendered by the emulator, not by a static tile renderer.** `smw_level_sheet` copies a
level into Yoshi's Island 1's slot, enters it and glides the camera, so it shows exactly what the
game draws (palettes, tilesets, sprites) with no Map16 or graphics decoder. Limits: horizontal
levels only, and a level's own entrance position is kept.

**The route bot reads RAM, never writes it.** A found route is a pure controller-input route, and
`route_replay` proves it from a saved start state. Tours do write RAM and are labelled as layout
checks, not proof a level can be beaten.

**VRAM tiles in grey by default.** The libretro core doesn't expose CGRAM; a palette can be read
from the game's own buffer in WRAM.

## Code without a disassembly

**Our own 65816 disassembler, in Python.** One opcode table, no install, and it prints the M/X flags
it assumed on every line, which is the way a 65816 disassembly goes wrong. Rejected: shipping a C
disassembler (another build step).

**Caller search is a byte scan, compared as file offsets.** It finds JSL/JML from any mirror and
JSR/JMP within the bank; it matches SMWDisX exactly for `FinishOAMWrite` (32 calls). Rejected:
tracing code flow from the vectors (misses code reached through pointer tables, much larger).

**Asar runs as a subprocess on a temporary copy, with `--no-title-check`.** snes-lab strips copier
headers first, which is what that check guards against. The tool warns on numeric branch targets:
Asar 1.91 encodes `BRA $1FF010` as a raw offset.

## The debugger backend

**MesenCE run headless with `--testrunner`, one run per tool call.** The Snes9x libretro API has no
breakpoints or SA-1 I-RAM, so `mesen_trace` and `mesen_cdl` use Mesen. The job is a Lua table;
`mesen_job.lua` plays the inputs, records watch hits, the code/data log and a savestate, and writes
tab-separated results. Batch tools fit the MCP style (one call answers "who writes $19 in these 600
frames") and nothing stays running. Rejected: an interactive Mesen session API (a second emulator
stack with its own lifetimes; Snes9x stays the fast one for play).

**Portable mode, with a controller in port 1.** Without a `settings.json` beside the binary, Mesen
opens its first-run window and `--testrunner` waits forever; and only that window plugs a controller
into port 1, so a hand-written settings file needs `"Port1": {"Type": "SnesController"}` or every
input is silently ignored. `mesen.binary()` refuses a settings file without both.

**Writer recovery for read/write hits.** Mesen reports the CPU's program counter after the
instruction, so the tool tries the 2-4 byte instructions ending there with the hit's M/X flags and
keeps the one load or store that fits; `pc` is null when none or several fit (DMA, code in RAM).

**`rom_disasm(cdl=...)` takes M/X from the code/data log.** Mesen's log stores, per ROM byte,
whether it ran as code and with which 8-bit A/X flags. That removes the disassembler's main guess
for any code the run reached, and lines it never saw run are marked.

## Testing across many games

**A sweep script for the slow checks, cheap checks in pytest.** `scripts/romset_sweep.py` boots
every game in a folder of ROM dumps in Snes9x and samples screenshots; `tests/test_romset.py` checks
the header and reset-vector disassembly of every game (both skip without `SNES_CORPUS`). No ROM is
ever stored in the repo. Two bugs it found: `rom.header()` raised a bare `IndexError` on files
smaller than the header (8 KiB adapter carts), and `disassemble()` failed when a listing ran past
`$FFFF`; both now return clear errors or stop at the bank end. SuperFX games draw nothing in the
Snes9x libretro core, so their screenshots go through Mesen.
