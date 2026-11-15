# C128DCR MMU riser, v1.0

Plugs into the C128DCR's MMU socket (U7) and adds a second MOS 8722 MMU, so
the [SRAM module](../sram/) can use all 256 KB. The only connection to the
SRAM module is one JST-PH 2-pin cable carrying `BANK_HI`. There is no other
wiring and nothing to modify on the motherboard.

**Status: not yet built or tested in a machine.**

It is based on ytmytm's
[c128-mmu-256k-exp](https://github.com/ytmytm/c128-mmu-256k-exp), itself
after Marko Mäkelä and Pekka Pessi's [256K expansion on Zimmers.net](https://www.zimmers.net/anonftp/pub/cbm/documents/projects/memory/c128/1028/1028.html). The three DIP-48
footprints use his interleaved placement, which is proven to fit between the
SID and the power socket. What changed:

| | ytmytm's GAL board | This riser |
|---|---|---|
| Motherboard work | lift R29/R30, 4 wires to U9 and the RAM | none |
| Link to the RAM | `/CAS2`, `/CAS3` (2 wires) | `BANK_HI` + GND, one keyed JST-PH cable |
| Logic | GAL22V10 | ATF16V8B (socketed DIP-20) |
| Bank decoding | on the riser (splits `/CAS0`/`/CAS1`) | on the SRAM module |
| P0L/P1L (`$D507`/`$D509`) | bit 6 remapped like the bank bits | passed unchanged (fix, see below) |
| Remap outputs | always driven | driven only during writes |
| Second MMU's /Z80EN, /FSDIR, MS3 | connected to the stock MMU's outputs | left open |

## How it works

* **P1** (machined pins) plugs into U7. **U1** holds the computer's own 8722,
  with every pin passed straight through.
* **U2** is a second 8722 on the same bus. Two of its data bits come from the
  GAL, so that its bank bit follows the CPU's 256K bank bit. Its `/CAS1`
  output then means "banks 2/3". That is `BANK_HI`, sent through R1 (100 Ω)
  to J1.
* **U3, ATF16V8B** ([`gal/riser.pld`](gal/riser.pld)):
  * D6 into U2 = CPU D6 at `$xxx5` (MCR), `$xxx7` (P0L) and `$xxx9` (P1L),
    otherwise CPU D7.
  * D0 into U2 = CPU D1 at `$xxx8`/`$xxxA` (P0H/P1H bank pointers), otherwise
    CPU D0.
  * Both outputs drive only while R/W is low, so U2 never fights them when
    its registers are read.
* **The P0L/P1L fix:** ytmytm's equations (from the original 1999 design)
  remap bit 6 at every address except `$xxx5`. P0L/P1L hold the zero-page and
  stack *page numbers*, so the second MMU stored a different page whenever
  bit 6 ≠ bit 7. For example, zero page relocated to `$40` became `$00`
  there. Accesses to that page could then pick the wrong bank pair. Here they
  are passed unchanged.
* U2's D1–D5/D7 share the bus with U1. Both chips hold the same bits there and
  return the same values on register reads, as on ytmytm's board. U2's other
  outputs (TA8–TA15, `/CAS0`, MS0–MS3, `/Z80EN`, `/FSDIR`) are not connected.
  Its inputs (`/GAME`, `/EXROM`, 40/80, AEC, MUX, ...) are.
  * **`$D50B` (version register):** this one is read-only hardware, not a
    value either MMU was written with. If U1 and U2 are different 8722
    silicon revisions, D1–D5/D7 can read back differently on `$D50B` and the
    two chips will fight. **Use two 8722s of the same revision/date code**
    (and check a clean read of `$D50B` right after power-up, before either
    MMU is reset by the CPU) to avoid this.

The SRAM module latches `BANK_HI` as A17 alongside A16 (`/CAS0`). The second
MMU switches its bank enables at the same time as the original, before the
VIC's `/CAS` falls. Measure this with the SRAM board's test points
(TP5 = A17).

## The GAL

* **Source:** [`gal/riser.pld`](gal/riser.pld), in
  [GALasm](https://github.com/daveho/GALasm) syntax.
* **Programming file:** [`gal/riser.jed`](gal/riser.jed), assembled with
  `galasm riser.pld`.
* **Check:** [`gal/check_gal.py`](gal/check_gal.py) simulates the JEDEC fuse
  map, independently of GALasm, and compares both outputs against the
  intended behaviour for all 512 input combinations. The build runs it.
* **Programming:** use a TL866II+/T48 with device ATF16V8B. Turn encryption
  (security fuse) off.
* **Genuine parts:** buy Microchip ATF16V8B from an authorized distributor
  (Microchip, Mouser, DigiKey). Marketplace "GAL16V8/22V10" chips are often
  old parts, sanded and re-marked.

## Bill of materials

| Qty | Ref | Part | LCSC |
|---|---|---|---|
| 2 | U1, U2 | MOS 8722 MMU: U1 is the computer's own, U2 a second one | salvage |
| 2 | (U1, U2) | 48-pin 600 mil DIP socket (open centre) | |
| 1 | U3 | ATF16V8B-15PU (DIP-20), programmed | C6509 (or Mouser/DigiKey) |
| 1 | (U3) | 20-pin 300 mil DIP socket | |
| 48 pins | P1 | machined round-pin male strips | |
| 1 | R1 | 100 Ω 0603 | C22775 |
| 3 | C1–C3 | 100 nF 0603 (one per IC) | C14663 |
| 1 | C4 | 10 µF 0805 | C15850 |
| 1 | J1 | JST PH 2-pin horizontal, S2B-PH-K-S | C173752 |
| 1 | | JST-PH 2-pin cable, crimped both ends, about 20 cm (check it is wired pin 1 to pin 1) | |

JLC can place the passives (all basic parts). The sockets, pins, GAL and J1
are hand-soldered.

## Assembly and installation

1. Program U3 with `gal/riser.jed`.
2. Solder P1's machined pins first (from the bottom), then the two DIP-48
   sockets and the DIP-20 socket on top. The DIP-48 sockets sit over P1's
   pins, so their centres must be open.
3. Solder R1, C1–C4 and J1.
4. Move the 8722 from the motherboard's U7 into U1, fit the second 8722 in U2
   and the GAL in U3, all with pin 1 matching the silkscreen.
5. Plug the riser into U7, checking that no pin is offset.
6. Connect J1 to the SRAM module's J1 (pin 1 `BANK_HI` to pin 1).

Before ordering: print `fab/c128dcr-mmu-riser-fit-check-1to1.pdf` at 100 % and
check it around U7 (SID, power connector, anything tall nearby).

## Rebuilding

```
cd c128dcr/mmu-riser
python3 build.py            # route, check (parts, GAL, DRC, KiCad), write kicad/ docs/ fab/
python3 build.py --reuse    # regenerate from the cached routing
```

To change the GAL logic, edit `gal/riser.pld`, re-assemble it with GALasm,
and update `intended()` in `gal/check_gal.py` to match.
