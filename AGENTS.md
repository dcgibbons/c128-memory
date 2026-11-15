# Notes for agents working in this repo

Open-hardware boards for Commodore 128 family main memory (C128, C128D, and
C128DCR), generated entirely from Python. See `README.md` for the boards and
`*/*/README.md` for each board's theory of operation.

## Layout

- `common/`: shared generator: footprints, router, DRC, KiCad writers,
  `buildlib.py` (build steps), `partcheck.py` (catalog check).
- `c128dcr/sram/`, `c128dcr/mmu-riser/`: complete C128DCR boards (4x 4464 DRAM
  replacement and U7 MMU riser). Each board folder contains `design.py`
  (parts, placement, netlist), `sch_content.py` (schematic symbols, layout,
  notes), `build.py` (silkscreen, rails, BOM notes) and a README.
- `c128/sram/`, `c128/mmu-riser/`: C128 flat / C128D boards (16x 4164 DRAM
  replacement and low-profile U7 MMU riser). In development / planned;
  currently contain initial design constraints and target specifications.
- `tools/memtest/`: 32 KB `U36` Internal Function ROM qualification and
  March-C- burn-in suite (`build.py`, `asm6502.py`, `test_memtest.py`; binary
  output in git-ignored `tools/memtest/fab/`).
- `kicad/`, `docs/`, `fab/` and `route_cache.pkl` inside each board folder
  are **build outputs**. They are git-ignored; never edit them by hand.
  Change the generator and rebuild.

## Build and verify

```
cd c128dcr/sram  # or cd c128dcr/mmu-riser
python3 build.py            # full route (minutes), then everything below
python3 build.py --reuse    # reuse route_cache.pkl (silkscreen/text-only changes)
python3 build.py --offline  # skip the catalog part check (explicitly)
python3 build.py --no-kicad # skip KiCad: removes fab/ and exits non-zero
```

Each build runs, and must pass, all of:
1. **Part check:** every LCSC number / MPN exists in the JLC/LCSC catalog
   and matches. Low-stock warnings are informational.
2. **Own DRC:** 0 clearance/edge errors, 0 unconnected nets.
3. **KiCad ERC/DRC** (with schematic parity and silkscreen checks): clean.
   Needs KiCad 9 (`pcbnew` python module + `kicad-cli`); a build without it
   is never reported as passing.
4. **GAL check** (riser): `c128dcr/mmu-riser/gal/check_gal.py` simulates
   `riser.jed`'s fuse map against the intended remap for all 512 inputs.
   After editing `riser.pld`, re-assemble with GALasm
   (https://github.com/daveho/GALasm) and update `intended()` to match.

Regression tests for the shared code: `python3 common/test_drc.py`.

A change to placement, netlist or footprints needs a full route; `--reuse`
is only valid when copper is unchanged. The router's grid origin is
`ROUTE_AREA`, so growing `BOARD` alone keeps the cache valid; changing
`ROUTE_AREA` does not. After a refactor of shared code, rebuild each board
with `--reuse` and compare outputs (KiCad save UUIDs and Gerber dates
differ every run).

Render to look at the result:
`kicad-cli pcb render --side top|bottom -o out.png <board>/kicad/<name>.kicad_pcb`.
Check silkscreen against pads and vias by eye. Fix collisions with
per-part `ref_at` in `design.py` or text positions in `build.py`.

## Rules

- **Never write a part number from memory or pattern.** Look up every IC in
  the catalog before proposing it, and record its LCSC number in
  `design.py`. Many classic 74xx functions do not exist in the AHCT/ACT/HCT
  families (74AHCT21 and 74AHCT253 were both proposed here by mistake and
  don't exist in those families). The JLC parts API used by `partcheck.py`
  works for manual searches too.
- **Logic levels:** C128 signals are TTL/NMOS (VOH as low as 2.4–2.7 V).
  Anything they drive at 5 V must have TTL-level inputs: AHCT, ACT, HCT or
  F. Not HC, AC or LVC (VIH 3.5 V at 5 V). LVC is fine where our own CMOS
  output drives it (e.g. the SRAM board's Schmitt buffer U5 on the RC node).
- **Timing ordering must be guaranteed, not typical.** Datasheet *minimum*
  gate delays are ~1 ns, so a chain of gates proves nothing. Any "X happens
  after Y" margin (bank-latch setup, write start after address settle, latch
  re-open after write end) needs a guaranteed delay (the SRAM board's RC +
  Schmitt `DLY`) or a causal chain from the same edge. Work the numbers from
  datasheet min/max and record them in the board README.
- **Timing-critical paths** (/CAS → /CE, end of write, bank latch) use
  AHCT/ACT/F; HCT is fine only where there is 100 ns-class slack.
- **JLC assembly** places passives (all JLC basic parts, no extended fees) by
  default; ICs are hand-soldered unless a board has specifically opted into
  `FULL_ASSEMBLY` after confirming the extended-part fee is worth it (the
  SRAM board has, for every IC except U1, which LCSC doesn't stock at all).
  Set `p.hand` accordingly.
- **SRAM substitutes** must have VIH ≤ 2.4 V at 5 V (many 2.7–5.5 V parts are
  0.7 × VCC). Don't claim a part "also fits" without checking its datasheet.
- **Second MMU (riser):** connect its inputs only (plus /CAS1 and the shared
  data bus); leave its pure outputs open. Pin directions: Programmer's
  Reference Guide Table 16-9.
- **Unverified physical assumptions** (do not present as facts): MMU riser
  fit around U7 (comes from photos, not a built board yet). C128DCR SRAM
  chip pitch (`CHIP_PITCH` = 0.6875″), U38–U41 pin-1 orientation, and
  128 KB solo hardware fit/operation are verified on a built v1.0 board in a
  real C128DCR. The 1:1 `fab/*-fit-check-1to1.pdf` is the way to check what's
  still unverified.
- **Commits:** commit only generator sources (plus `gal/riser.jed`, which
  needs GALasm to rebuild); tag released board revisions (`sram-v1.0`,
  `mmu-riser-v1.0`) once they are built and verified.
