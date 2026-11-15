# C128 SRAM & MMU Qualification Option ROM (`U36`)

Native MOS 8502 hardware qualification and burn-in ROM for the Commodore 128
family (128 KB stock / solo SRAM mode and 256 KB MMU riser mode).

Designed for a **OneROM** or **27C256 (32 KB)** EPROM/EEPROM in the C128's
internal Function ROM socket (**U36**).

## Building & Testing

```sh
cd tools/memtest
python3 build.py          # writes fab/c128-memtest-u36.bin (32,768 bytes)
python3 test_memtest.py   # runs 10-scenario simulator + fault-injection suite
```

`fab/` is git-ignored; only the Python generator and test suite are tracked.

## Why `U36` Internal Function ROM?

* **Starts before Kernal `IOINIT`/`RAMTAS`:** Uses header flag `$8006 = $01`
  (`"CBM"` at `$8007–$8009`), so C128 Kernal `POLL` jumps to `$8000` before
  touching Bank 1 or initializing BASIC. Even dead upper RAM, a dead Bank 0
  stack, or a dead Bank 1 cannot crash the machine before diagnostics appear.
* **99.98% RAM coverage (`65,521 / 65,536` bytes per bank):** Because the test
  executes out of `U36` Low (`$8000–$BFFF`) with a companion helper in `U36`
  High (`$C100`), it March-tests `$0002–$FFF7` of every detected 64 KB bank—
  including Zero Page (`$0002–$00FF`), Hardware Stack (`$0100–$01FF`), Screen
  RAM (`$0400–$07FF`), Mid RAM (`$8000–$BFFF`), RAM underneath `$D000–$DFFF`
  I/O, and Kernal-shadow RAM (`$E000–$FFF7`). (Only `$0000–$0001` internal 8502
  port, `$FF00–$FF04` MMU registers, and an 8-byte `$FFF8–$FFFF` NMI safety
  stub are excluded.)
* **Both 1 MHz and 2 MHz every pass:** Every pass runs all 7 stages at **1 MHz
  (`SLOW`, with active VIC-IIe DMA contention)** followed by **2 MHz (`FAST`,
  `$D030 = $01`)**. Note that 2 MHz mode briefly blanks/bars the 40-column
  VIC-IIe screen during each burst—this is normal C128 hardware behavior.

## Stages (`1/7` – `7/7`)

1. **`1/7 DATA BUS + A0-A15 PIN WALK`** — Walking-1 / walking-0 data bus check
   (catches adjacent/non-adjacent data bridges invisible to `$55/$AA`) plus
   single-bit address uniqueness across all 16 address lines `A0–A15` around
   `$1800` (`$0800..$9800`).
2. **`2/7 ZP/STACK/SCREEN MARCH-C`** — Register-only (zero-RAM) 6-element
   March-C- (`$00/$FF` and `$55/$AA`) across `$0002–$07FF` and `$FF05–$FFF7`
   with Common RAM disabled and `P0H`/`P1H` stepped across every bank `0..N-1`.
3. **`3/7 FULL MARCH-C $00 / $FF`** — 6-element March-C- (`⇑w0, ⇑r0w1, ⇑r1w0,
   ⇓r0w1, ⇓r1w0, ⇑r0`) across `$0800–$7FFF`, `$8000–$BFFF`, and `$C000–$FEFF`.
4. **`4/7 FULL MARCH-C $55 / $AA`** — Checkerboard 6-element March-C- across
   all banks.
5. **`5/7 FULL MARCH-C BIT-PAIRS`** — Alternates `$33/$CC` and `$0F/$F0` across
   passes to complete pairwise bit-coupling coverage across `D0–D7`.
6. **`6/7 ALL-BANK STAMP + VERIFY`** — Writes a bijective `(bank, page, byte,
   pass)` hash to **all** banks before reading any bank back, catching any
   cross-bank `/CAS` or `A16`/`A17` bank-latch write bleed.
7. **`7/7 MMU P0/P1 + RCR + WR-THRU`** — Exercises MOS 8722 `P0L/P0H` (`$40`
   and `$18`, including reverse-swap and `$D507` readback), `P1L/P1H` (`$41`
   via `PHA`/`PLA`), `RCR` (`$D506`) Common RAM gating, and C128 PLA
   write-through under `U36` ROM (`$8800` & `$C800`).

## Screen & Border Status

* **Light-blue border:** Pass 1 in progress (`0` errors so far).
* **Green border (`PASSES : 0001+`):** Full 1 MHz + 2 MHz qualification passed;
  continues looping indefinitely for thermal/burn-in testing.
* **Red border (`* FAULT HALT *`):** Halts immediately at 1 MHz displaying the
  exact `BANK`, `ADDR`, `SPEED`, `STAGE`/`STEP`, `EXP`, `ACT`, `XOR` hex mask,
  and highlighted `[76543210]` data bit(s).
