# C128DCR 256 KB SRAM module, v1.0

A drop-on replacement for the four main-memory DRAMs (U38–U41, 41464/41254
64K×4) of the Commodore 128DCR, built around one IS61C5128AS-25QLI static RAM
(512K×8, 256 KB of it used).

**C128DCR only.** The flat C128 and the plastic C128D have a different RAM
layout, and in the flat C128 the keyboard leaves no room for a board at this
height.

* Stock C128: uses banks 0/1 (128 KB), exactly like the original DRAM.
* With the [MMU riser](../mmu-riser/) (second 8722 MMU) plugged into J1 with a
  JST-PH 2-pin cable, all 256 KB are usable. No other wiring and no
  motherboard modifications.

**Status: v1.0 built and verified in 128 KB solo mode on a real C128DCR**
(C64 Diag, DesTestMAX, C128 BASIC, and [`tools/memtest/`](../../tools/memtest/)
full 1 MHz + 2 MHz March-C-, MMU P0/P1 relocation, Common RAM, and ROM
write-through burn-in). 256 KB operation with the [MMU riser](../mmu-riser/)
and the scope measurements under "Qualification" remain to be checked once J1
is populated.

Only the generator is kept in git. `kicad/`, `docs/` and `fab/` are build
outputs: run `pip install -r ../../requirements.txt` and then `python3 build.py`
in this folder to create them (see "Rebuilding").

```
build.py, design.py, sch_content.py   this board (shared code is in ../../common)
kicad/            KiCad 9 project (zones filled; ERC/DRC clean)
docs/             previews (pcb_*.png, schematic_preview.png) and bom.csv
fab/              Gerbers + drill (zip), jlc-bom.csv / jlc-cpl.csv, schematic PDF,
                  1:1 fit-check PDF, KiCad erc.rpt / drc.rpt
```

## How it works

The C128 drives the DRAM with a multiplexed address: `MA0-7` carries the row
address when `/RAS` falls and the column address when `/CAS` falls. Bank 0 is
selected by `/CAS0` (U38/U39) and bank 1 by `/CAS1` (U40/U41). The module turns
that back into a flat 18-bit address:

| Part | Function |
|------|----------|
| U4 74AHCT08 | `CE_PRE = CAS0 & CAS1`: the SRAM's `/CE` (one gate). `CE_N` = CE_PRE + 2 gates (column-latch LE). `ROWLE = RAS & DLY` (row-latch LE) |
| R4, C7, U5 74LVC1G17 | `DLY` = CE_PRE through 680 Ω / 33 pF into a Schmitt buffer: both edges lag CE_PRE by a delay set by the RC and U5's datasheet thresholds, not by gate speeds |
| U7 74AHCT573 | **bank latch** (2 of 8 channels): `A16 = /CAS0` (high = bank 1), `A17 = BANK_HI` (low = banks 2/3), LE = `DLY`. TTL-level inputs take `/CAS0` (74F32 output) and `BANK_HI` (8722 NMOS output) directly |
| U2 74AHCT573 | **row latch**, LE = `RAS & DLY`: closes when `/RAS` falls and stays closed until after the SRAM is deselected |
| U3 74AHCT573 | **column latch**, LE = `CE_N` |
| U9 74AHCT1G32 | SRAM `/WE = DWE \| DLY`: a write can only start once A16/A17 have settled |
| U10 74AHCT1G04 | SRAM `/OE = NOT DWE`: the SRAM drives the bus only on reads |
| J1 + R1 | `BANK_HI` from the MMU riser (its second MMU's `/CAS1`). R1 pulls it high: with nothing plugged in the board is a plain 128 KB module |
| U1 IS61C5128AS-25 | 512K×8 SRAM, 256K used (pin 1 = A17, pin 30 = A18 held high) |
| TP1–TP7 | test points: CE_PRE (`/CE`), DLY, `/WE`, A16, A17, GND, +5V |

RAS-only refresh never asserts a `/CAS`, so the SRAM ignores refresh cycles.

Address and data lines are deliberately permuted between the latches and the
SRAM to make the routing clean. A static RAM doesn't care which pin is which
address or data bit, as long as the mapping never changes.

### Timing, from datasheet limits

The bank bit A16 comes from `/CAS0` itself, so it changes at the same moment
the cycle starts. Two things must happen in order, with guaranteed margins
rather than typical gate delays:

* the bank latch must close only after `/CAS0` has reached it (setup 3.5 ns);
* a write must start only after A16 has reached the SRAM (latch D→Q up to
  ≈10 ns).

Both use `DLY`. With τ = 680 Ω × (33 pF + input capacitance), R ±1 %, C ±5 %
(τ ≈ 21–29 ns), and U5's thresholds at 4.5–5.5 V (falling 1.51–2.4 V, rising
2.19–3.33 V, TI SN74LVC1G17 datasheet):

| Event | Min | Max | Needs |
|---|---|---|---|
| DLY falls after CE_PRE falls | ≈13 ns | ≈37 ns (+ U5 delay) | — |
| bank latch closes after `/CAS` falls | ≈15 ns | ≈50 ns | ≥ 3.5 ns setup |
| write starts (`/WE` falls) after `/CAS` falls | ≈16 ns | ≈55 ns | ≥ A16 settled (≈10 ns) |
| latches re-open after `/CE` rises | ≈11 ns | ≈46 ns | after the write ends; before the next `/RAS` falls |

* **Reads:** `/CE` is one AHCT gate after `/CAS` (≤ ≈8.5 ns), so data is valid
  about ≤ 35 ns after `/CAS` falls (address via the latches + the SRAM's 25 ns
  access time), assuming that access time applies at the motherboard bus load.
* **Write end:** a write ends when `/CE` rises, one gate (≤ ≈8.5 ns) after
  `/CAS` rises. The 8502 holds write data ≥ 40 ns after φ0 falls (Programmer's
  Reference Guide, Table 16-5), so `/CAS` must rise no later than ≈30 ns after
  φ0 falls.
* **Write pulse:** a late DLY shortens the write. The old ≥ 45 ns `/WE`-low /
  ≈100 ns `/CAS`-low figures were sized for the retired 55 ns AS6C4008-55SIN.
  The IS61C5128AS-25QLI's write-cycle table (ISSI datasheet) gives its 25 ns
  grade a tWP (write pulse width) minimum of 15 ns, vs. the old part's 45 ns
  -- keeping the same margin the old figures left beyond tWP for DLY's own
  delay (≈100 − 45 = 55 ns), `/CAS` needs to stay low ≥ ≈70 ns in a write.
  Measure this directly in the "Qualification" pass below regardless.
* **Column latch:** closes 3 gates after `/CAS` (≈3–25 ns). This relies on MA
  holding the column address that long after `/CAS` falls, which DRAM timing
  requires anyway.

### Qualification (to do on real hardware)

Scope these in C128 (1 and 2 MHz), C64 and CP/M (Z80) mode, using TP1–TP7 and
the socket pins:
* φ0, socket `/CAS`, `/CE` (TP1), DLY (TP2), `/WE` (TP3), A16/A17 (TP4/5) and a
  data line: write-end vs data hold, `/CAS` low time, read data vs the CPU/VIC
  sampling point.
* `/CAS` rising to the next `/RAS` falling (must exceed the latch re-open time).
* Data-bus high level against the SRAM's 2.4 V input threshold: the 8502's
  guaranteed output high is also 2.4 V. The original 41464 DRAMs had the same
  2.4 V input spec, so this is no worse than stock, but measure it.
* BANK_HI at the SRAM board with the riser cable fitted (edges, ringing).

## SRAM choice

The BOM uses the **IS61C5128AS-25QLI** (ISSI, 512K×8, 5 V, TTL-level inputs:
VIH 2.2 V min). Its pin 1 (A17) is driven by our A17 directly and pin 30
(A18) is held high, so the upper 256 KB are used. Switched to this from the
original AS6C4008-55SIN when that part became hard to source (long lead
times/restricted availability at major distributors) -- same JEDEC-standard
SOP-32 package and pin-for-pin identical pinout, so this was a drop-in swap,
not a board redesign.

Substitutes must have the same JEDEC SOP-32 pinout, ≤ 55 ns access and
**VIH ≤ 2.4 V at 5 V** (TTL-compatible inputs). Several 2.7–5.5 V SRAMs
specify VIH as 0.7 × VCC (3.5 V at 5 V) and will not read the C128's bus
reliably. Check the datasheet before substituting. The AS6C2008A in particular
is not a supported substitute.

## ⚠ Before ordering boards

1. **Check orientation.** The design assumes U38→U41 in a row (computer in
   normal orientation, ROMs behind them), with **pin 1 at the bottom-right /
   notch toward the front**, on a caliper-measured **0.6875″ (17.4625 mm)**
   pitch. U38 pin 1 → U41 pin 1 is 52.3875 mm. Print
   `fab/c128dcr-sram-fit-check-1to1.pdf` at 100 % (the outline must measure
   66.0 × 31.3 mm) and lay it over the RAM row to confirm before ordering. If
   your board differs, set `CHIP_PITCH` in `design.py` and rebuild.
2. Only **U38** (D0–D3, `/CAS0`) and **U41** (D4–D7, `/CAS1`) carry signals the
   module needs. The board spans all four positions but plugs into those two
   sockets only; U39/U40 stay empty.
3. Plug pins: **machined round-pin male strips** into **machined sockets** at
   U38 and U41. P1 uses all 18 pins; P4 uses 11 (1, 2, 3, 8, 9, 10, 11, 15, 16,
   17, 18).
4. Check clearance to the ROMs above the RAM row and the caps (C38–C41)
   below. The board is 66.0 × 31.3 mm and extends 6 mm above the top row of
   plug pins.

## Bill of materials

Generated to `docs/bom.csv` (and `fab/jlc-bom.csv` / `fab/macrofab-bom.csv`) by `build.py`. Summary:

| Qty | Ref | Part | LCSC |
|---|---|---|---|
| 1 | U1 | IS61C5128AS-25QLI (SOP-32 450 mil) | Mouser/DigiKey |
| 3 | U2, U3, U7 | SN74AHCT573PWR (TSSOP-20) | C141311 |
| 1 | U4 | SN74AHCT08PWR (TSSOP-14) | C155175 |
| 1 | U5 | SN74LVC1G17DBVR (SOT-23-5) | C7836 |
| 1 | U9 | 74AHCT1G32W5-7 (SOT-23-5) | C842290 |
| 1 | U10 | SN74AHCT1G04DBVR (SOT-23-5) | C30326 |
| 8 | C1–C4, C6, C8–C10 | 100 nF 0603 X7R (one per IC) | C14663 |
| 1 | C5 | 10 µF 0805 X5R | C15850 |
| 1 | C7 | 33 pF 0603 C0G | C1663 |
| 1 | R1 | 10 k 0603 | C25804 |
| 1 | R4 | 680 Ω 0603 1 % | C23228 |
| 1 | J1 | JST PH 2-pin horizontal, S2B-PH-K-S (optional, for the MMU riser) | C173752 |
| 29 pins | P1, P4 | machined round-pin male strip (generic is fine) | |
| | TP1–TP7 | bare test-point pads, no part | |

### Ordering from JLCPCB

JLC places the passives (all JLC *basic* parts, no extended-part fees there)
plus every IC except the SRAM itself (U1), which LCSC doesn't stock at all.
The other ICs are extended parts (about $3 per part type per order, roughly
$20 total) -- worth it over hand-soldering three TSSOP-20 latches plus the
SOT-23-5 parts yourself. U1 still has to be bought separately (Mouser/DigiKey)
and hand-soldered; it's a straightforward SOP-32, a quick pass with the iron
and some flux.

Upload `fab/c128dcr-sram-gerbers.zip`, enable assembly (top side), then upload
`fab/jlc-bom.csv` and `fab/jlc-cpl.csv`. In JLC's placement preview, check the
parts sit on their pads.

## Installation

1. Remove U38–U41 and fit machined 18-pin sockets at U38 and U41.
2. Solder U1 (pin 1 at its silkscreen dot; JLC assembles the rest), J1 if
   wanted, and the plug pins. Check for shorts between +5 V and GND.
3. Plug in with the square pads / silk notches matching the original chips
   (pin 1 bottom-right, toward the front of the machine). The SRAM side faces up.
4. Stock machine: leave J1 open. With the MMU riser: connect its J1 to this
   J1 with a JST-PH 2-pin cable (pin 1 to pin 1: `BANK_HI`, pin 2: GND).

## Rebuilding / changing the design

```
cd c128dcr/sram
python3 build.py            # place → route (≈5 min) → checks → write all files
python3 build.py --reuse    # regenerate files from the cached routing
```

The build runs the catalog part check (`--offline` to skip), its own DRC, and
KiCad 9's zone fill, ERC and DRC with schematic parity, then writes `fab/`. It
exits non-zero if any check fails or cannot run (`--no-kicad` skips KiCad,
removes `fab/` and also exits non-zero). See `../../AGENTS.md`.

## Sources

* C128DCR schematic (sheet 4: U38–U41 wiring):
  [zimmers.net c128 schematics](http://www.zimmers.net/anonftp/pub/cbm/schematics/computers/c128/)
* IS61C5128AS-25QLI datasheet (pin 1 = A17, pin 30 = A18; VIH 2.2 V min):
  [ISSI](https://www.issi.com/)
* SN74LVC1G17 datasheet (Schmitt thresholds): [TI](https://www.ti.com/product/SN74LVC1G17)
* Commodore 128 Programmer's Reference Guide, chapter 16 (8502 timing, MMU pins)
* Reference/inspiration: [ytmytm/c128-mmu-256k-exp](https://github.com/ytmytm/c128-mmu-256k-exp)
