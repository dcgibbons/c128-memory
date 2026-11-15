#!/usr/bin/env python3
"""Build the C128 SRAM & MMU qualification Option ROM (U36, 32 KB for OneROM/27256).

Generates:
  fab/c128-memtest-u36.bin   32 KB C128 Internal Function ROM (socket U36)

Features:
  * Immediate C128 Kernal POLL autostart ($8006 = $01, "CBM" at $8007-$8009):
    starts BEFORE Kernal IOINIT/RAMTAS/CINT so broken upper RAM or Bank 1
    cannot crash the machine before diagnostics appear.
  * Auto-detects 128 KB solo mode (Banks 0-1) vs 256 KB MMU riser mode
    (Banks 0-3), catching Bank 1 mirroring or partial riser faults at boot.
  * Tests 65,521 / 65,536 bytes (99.98%) of every detected 64 KB bank:
      - $0002-$07FF + $FF05-$FFF7 (Zero Page, Stack, Pages 2-3, Screen, Top)
        via a zero-RAM register-only 6-pass March-C- engine with Common RAM off
      - $0800-$7FFF (30 KB low/BASIC program RAM)
      - $8000-$BFFF (16 KB mid RAM, tested from U36 upper-half $C100 helper)
      - $C000-$FEFF (15.75 KB high RAM, including $D000-$DFFF underneath I/O)
  * 7 qualification stages run at BOTH 1 MHz (SLOW, with active VIC-IIe DMA)
    and 2 MHz (FAST, $D030 = $01) on every pass:
      1. Data-bus walking 1/0 + A0-A15 single-bit address uniqueness ($1800)
      2. Zero-Page / Stack / Screen / Page-$FF 6-pass March-C- ($00/$FF, $55/$AA)
      3. Full-bank 6-pass March-C- ($00 / $FF)
      4. Full-bank 6-pass March-C- ($55 / $AA)
      5. Full-bank 6-pass March-C- ($33 / $CC and $0F / $F0 alternating)
      6. Simultaneous cross-bank non-aliasing stamp & verify across all banks
      7. MOS 8722 + Riser P0L/P0H ($18, $40), P1L/P1H ($41), RCR Common RAM,
         and U36 ROM write-through ($8800, $C800)
  * Zero-RAM fault handler (stores fault mailbox in VIC-II sprite coordinate
    registers $D000-$D007 via STA $FF01 LCRA switch) displays exact failing
    Bank, Address, Speed, Stage/Step, Expected, Actual, XOR mask, and decoded
    failing data bit positions [76543210].
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from asm6502 import Asm6502, ascii_to_screencode, scr_bytes

# --- Hardware Constants ---
CR_UI = 0x16          # %00_01_01_1_0: Bank 0, U36 Lo+Hi, RAM $0000-$7FFF, I/O ON
SCR_BASE = 0x0400
COL_BASE = 0xD800
SAVE_NIB_BASE = 0xDBE8  # 24 unused 4-bit nibbles in static Color RAM ($DBE8-$DBFF)

# VIC-II sprite coordinate registers (sprites off) used as zero-RAM fault mailbox
FAULT_ACT = 0xD000
FAULT_EXP = 0xD001
FAULT_ADDR_LO = 0xD002
FAULT_ADDR_HI = 0xD003
FAULT_BANK = 0xD004
FAULT_STAGE = 0xD005
FAULT_SUB = 0xD006
FAULT_SPEED = 0xD007

# Zero-page workspace ($10-$2B in Bank 0 when RCR=$04)
ZP_PTR = 0x10        # $10-$11
ZP_START_HI = 0x12
ZP_END_HI = 0x13
ZP_PAT0 = 0x14
ZP_PAT1 = 0x15
ZP_BANK = 0x16
ZP_STAGE = 0x17
ZP_SUB = 0x18
ZP_TAG = 0x19
ZP_TMP = 0x1A
ZP_TMP2 = 0x1B
ZP_SAVED_SP = 0x1C

# Persistent state saved across Stage 2 in Color RAM nibbles ($DBE8..$DBF9 = 9 bytes)
ZP_PERSIST_START = 0x20
ZP_NUM_BANKS = 0x20  # 2 or 4
ZP_D0 = 0x21         # pass ones (0..9)
ZP_D1 = 0x22         # pass tens (0..9)
ZP_D2 = 0x23         # pass hundreds (0..9)
ZP_D3 = 0x24         # pass thousands (0..9)
ZP_SPEED = 0x25      # 0 = 1 MHz, 1 = 2 MHz
ZP_PAIR_ALT = 0x26   # 0 = $33/$CC, 1 = $0F/$F0
ZP_MMU_VER = 0x27    # $D50B snapshot
ZP_GREEN = 0x28      # 1 once >= 1 full 1M+2M pass succeeded
ZP_PERSIST_COUNT = 9

# Sub-step IDs (index into 3-char STEP_NAMES table)
SUB_M0 = 0
SUB_M1 = 1
SUB_M2 = 2
SUB_M3 = 3
SUB_M4 = 4
SUB_M5 = 5
SUB_BUS = 6
SUB_ADR = 7
SUB_WR = 8
SUB_VFY = 9
SUB_P0 = 10
SUB_P1 = 11
SUB_RCR = 12
SUB_ROM = 13
SUB_DET = 14

STEP_NAMES = [
    "M0 ", "M1 ", "M2 ", "M3 ", "M4 ", "M5 ",
    "BUS", "ADR", "WR ", "VFY", "P0 ", "P1 ", "RCR", "ROM", "DET",
]

STAGE_TITLES = [
    "0/7 BANK AUTO-DETECT          ",  # 30 chars
    "1/7 DATA BUS + A0-A15 PIN WALK",
    "2/7 ZP/STACK/SCREEN MARCH-C   ",
    "3/7 FULL MARCH-C $00 / $FF    ",
    "4/7 FULL MARCH-C $55 / $AA    ",
    "5/7 FULL MARCH-C BIT-PAIRS    ",
    "6/7 ALL-BANK STAMP + VERIFY   ",
    "7/7 MMU P0/P1 + RCR + WR-THRU ",
]
assert all(len(s) == 30 for s in STAGE_TITLES)

# 25 rows x 40 cols static UI frame
SCREEN_ROWS = [
    ("  C128 SRAM + MMU QUALIFICATION  V1.0   ", True, 0x01),   # 0: rev white
    ("----------------------------------------", False, 0x03),  # 1: cyan
    ("CONFIG : DETECTING...                   ", False, 0x01),  # 2: white
    ("MMU ID : $--    STATUS : STARTING       ", False, 0x01),  # 3
    ("PASSES : 0000   SPEED  : 1 MHZ (SLOW)   ", False, 0x01),  # 4
    ("----------------------------------------", False, 0x03),  # 5
    ("STAGE  : 0/7 BANK AUTO-DETECT           ", False, 0x07),  # 6: yellow
    ("WINDOW : BANK 0  $0000..$FFFF   STEP DET", False, 0x07),  # 7
    ("                                        ", False, 0x01),  # 8
    ("BANK 0 : [.......]    BANK 1 : [.......]", False, 0x0D),  # 9: light green
    ("BANK 2 : [-------]    BANK 3 : [-------]", False, 0x0C),  # 10: grey until 256K
    ("                                        ", False, 0x01),  # 11
    ("1:PIN+BUS  2:ZP+STK+SCR  3:MARCH $00/$FF", False, 0x0F),  # 12: light grey
    ("4:M-$55/AA 5:M-$33/$0F   6:X-BANK STAMP ", False, 0x0F),  # 13
    ("7:MMU P0/P1 + COMMON RAM + ROM WR-THRU  ", False, 0x0F),  # 14
    ("----------------------------------------", False, 0x03),  # 15
    ("RESULT : NO ERRORS                      ", False, 0x0D),  # 16
    ("WHERE  : -                              ", False, 0x0F),  # 17
    ("DATA   : -                              ", False, 0x0F),  # 18
    ("BITS   : -                              ", False, 0x0F),  # 19
    ("----------------------------------------", False, 0x03),  # 20
    ("COVERAGE: $0002-$FFF7 PER BANK (99.98%) ", False, 0x03),  # 21
    ("NOTE: 2 MHZ FAST MODE BLANKS 40-COL VIC ", False, 0x0C),  # 22
    ("      DURING EACH STAGE BURST (NORMAL). ", False, 0x0C),  # 23
    ("      BORDER GREEN = FULL 1M+2M PASSED! ", False, 0x0D),  # 24
]
assert len(SCREEN_ROWS) == 25
assert all(len(r[0]) == 40 for r in SCREEN_ROWS)


def scr_pos(row: int, col: int) -> int:
    return SCR_BASE + row * 40 + col


def col_pos(row: int, col: int) -> int:
    return COL_BASE + row * 40 + col


def make_scramble_table() -> bytes:
    """256-byte bijective nonlinear permutation for Stage 6 cross-bank hash."""
    tbl = []
    for i in range(256):
        x = ((i * 167) + 0x5D) & 0xFF
        x ^= (x >> 4)
        x = (x * 29) & 0xFF
        x ^= (x >> 5)
        tbl.append(x)
    assert len(set(tbl)) == 256, "Scramble table must be a bijection!"
    return bytes(tbl)


def bne_far(a: Asm6502, target: str) -> None:
    skip = a.unique_label("bne_skip")
    a.beq(skip)
    a.jmp(target)
    a.label(skip)


def beq_far(a: Asm6502, target: str) -> None:
    skip = a.unique_label("beq_skip")
    a.bne(skip)
    a.jmp(target)
    a.label(skip)


def bcc_far(a: Asm6502, target: str) -> None:
    skip = a.unique_label("bcc_skip")
    a.bcs(skip)
    a.jmp(target)
    a.label(skip)


def emit_march6_body(
    a: Asm6502,
    prefix: str,
    set_cr_fn,
    on_mismatch_fn,
    update_step_fn,
) -> None:
    """Emit a 6-element March-C- engine over pages [ZP_START_HI .. ZP_END_HI].

    Uses ZP_PTR ($10/$11), ZP_PAT0 ($14), ZP_PAT1 ($15), ZP_SUB ($18).
    """
    # M0: Ascending w(B0)
    a.lda_imm(SUB_M0)
    a.sta_zp(ZP_SUB)
    update_step_fn(a)
    set_cr_fn(a)
    a.lda_imm(0x00)
    a.sta_zp(ZP_PTR)
    a.lda_zp(ZP_START_HI)
    a.sta_zp(ZP_PTR + 1)
    l_m0_pg = a.label(f"{prefix}_m0_pg")
    a.lda_zp(ZP_PAT0)
    a.ldy_imm(0x00)
    l_m0_by = a.label(f"{prefix}_m0_by")
    a.sta_indy(ZP_PTR)
    a.iny()
    a.bne(l_m0_by)
    a.lda_zp(ZP_PTR + 1)
    a.cmp_zp(ZP_END_HI)
    l_m0_done = f"{prefix}_m0_done"
    a.beq(l_m0_done)
    a.inc_zp(ZP_PTR + 1)
    a.jmp(l_m0_pg)
    a.label(l_m0_done)

    # M1 (up r0,w1) and M2 (up r1,w0)
    for step_idx, sub_id, exp_zp, wr_zp in (
        (1, SUB_M1, ZP_PAT0, ZP_PAT1),
        (2, SUB_M2, ZP_PAT1, ZP_PAT0),
    ):
        a.lda_imm(sub_id)
        a.sta_zp(ZP_SUB)
        update_step_fn(a)
        set_cr_fn(a)
        a.lda_imm(0x00)
        a.sta_zp(ZP_PTR)
        a.lda_zp(ZP_START_HI)
        a.sta_zp(ZP_PTR + 1)
        l_pg = a.label(f"{prefix}_m{step_idx}_pg")
        a.ldx_zp(wr_zp)
        a.ldy_imm(0x00)
        l_by = a.label(f"{prefix}_m{step_idx}_by")
        a.lda_indy(ZP_PTR)
        a.cmp_zp(exp_zp)
        l_ok = a.unique_label(f"{prefix}_m{step_idx}_ok")
        a.beq(l_ok)
        on_mismatch_fn(a, exp_zp)
        a.label(l_ok)
        a.txa()
        a.sta_indy(ZP_PTR)
        a.iny()
        a.bne(l_by)
        a.lda_zp(ZP_PTR + 1)
        a.cmp_zp(ZP_END_HI)
        l_done = f"{prefix}_m{step_idx}_done"
        a.beq(l_done)
        a.inc_zp(ZP_PTR + 1)
        a.jmp(l_pg)
        a.label(l_done)

    # M3 (down r0,w1) and M4 (down r1,w0)
    for step_idx, sub_id, exp_zp, wr_zp in (
        (3, SUB_M3, ZP_PAT0, ZP_PAT1),
        (4, SUB_M4, ZP_PAT1, ZP_PAT0),
    ):
        a.lda_imm(sub_id)
        a.sta_zp(ZP_SUB)
        update_step_fn(a)
        set_cr_fn(a)
        a.lda_imm(0x00)
        a.sta_zp(ZP_PTR)
        a.lda_zp(ZP_END_HI)
        a.sta_zp(ZP_PTR + 1)
        l_pg = a.label(f"{prefix}_m{step_idx}_pg")
        a.ldx_zp(wr_zp)
        a.ldy_imm(0xFF)
        l_by = a.label(f"{prefix}_m{step_idx}_by")
        a.lda_indy(ZP_PTR)
        a.cmp_zp(exp_zp)
        l_ok = a.unique_label(f"{prefix}_m{step_idx}_ok")
        a.beq(l_ok)
        on_mismatch_fn(a, exp_zp)
        a.label(l_ok)
        a.txa()
        a.sta_indy(ZP_PTR)
        a.dey()
        a.cpy_imm(0xFF)
        a.bne(l_by)
        a.lda_zp(ZP_PTR + 1)
        a.cmp_zp(ZP_START_HI)
        l_done = f"{prefix}_m{step_idx}_done"
        a.beq(l_done)
        a.dec_zp(ZP_PTR + 1)
        a.jmp(l_pg)
        a.label(l_done)

    # M5: Ascending r(B0)
    a.lda_imm(SUB_M5)
    a.sta_zp(ZP_SUB)
    update_step_fn(a)
    set_cr_fn(a)
    a.lda_imm(0x00)
    a.sta_zp(ZP_PTR)
    a.lda_zp(ZP_START_HI)
    a.sta_zp(ZP_PTR + 1)
    l_m5_pg = a.label(f"{prefix}_m5_pg")
    a.ldy_imm(0x00)
    l_m5_by = a.label(f"{prefix}_m5_by")
    a.lda_indy(ZP_PTR)
    a.cmp_zp(ZP_PAT0)
    l_m5_ok = a.unique_label(f"{prefix}_m5_ok")
    a.beq(l_m5_ok)
    on_mismatch_fn(a, ZP_PAT0)
    a.label(l_m5_ok)
    a.iny()
    a.bne(l_m5_by)
    a.lda_zp(ZP_PTR + 1)
    a.cmp_zp(ZP_END_HI)
    l_m5_done = f"{prefix}_m5_done"
    a.beq(l_m5_done)
    a.inc_zp(ZP_PTR + 1)
    a.jmp(l_m5_pg)
    a.label(l_m5_done)


def build_rom() -> tuple[bytes, dict[str, int]]:
    lo = Asm6502(origin=0x8000, max_size=0x4000)
    hi = Asm6502(origin=0xC000, max_size=0x4000)

    # =========================================================================
    # UPPER 16 KB ($C000-$FFFF in U36): Helper routines for $8000-$BFFF RAM
    # Note: $C000-$C0FF is $00 (no "CBM" signature at $C007 so POLL starts $8000)
    # =========================================================================
    hi.org_pad(0xC100, fill=0x00)

    # --- Helper: set CR = (ZP_BANK << 6) | $1F (Mid RAM $0000-$BFFF + U36 Hi) ---
    hi.label("hi_set_cr_mid")
    hi.ldx_zp(ZP_BANK)
    hi.lda_absx("hi_cr_mid_tbl")
    hi.sta_abs(0xFF00)
    hi.rts()

    hi.label("hi_cr_mid_tbl")
    hi.emit(0x1F, 0x5F, 0x9F, 0xDF)

    # --- Helper: Stage 1 Data Bus + A0-A15 Pin Walk around $1800 ---
    # With CR = (B<<6) | $1F, $0800..$9800 are ALL flat RAM simultaneously!
    hi.label("upper_stage1_bank")
    hi.lda_imm(SUB_BUS)
    hi.sta_zp(ZP_SUB)
    hi.jsr("hi_set_cr_mid")

    # 1A. Data bus walking-1 and walking-0 at $1800 & $1801 (inverts adjacent bus load)
    hi.ldy_imm(0)
    l_bus_lp = hi.label("hi_bus_lp")
    hi.lda_absy("hi_walk_tbl")
    hi.sta_abs(0x1800)
    hi.eor_imm(0xFF)
    hi.sta_abs(0x1801)          # bus Discharge / opposite pattern before readback!
    hi.lda_abs(0x1800)
    hi.cmp_absy("hi_walk_tbl")
    l_bus_ok1 = hi.unique_label("bus_ok1")
    hi.beq(l_bus_ok1)
    # Fault at $1800!
    hi.sta_zp(ZP_TMP)           # Bank 0 ZP via Common RAM ($D506=$04)
    hi.lda_imm(CR_UI)
    hi.sta_abs(0xFF00)
    hi.lda_zp(ZP_TMP)
    hi.sta_abs(FAULT_ACT)
    hi.lda_absy("hi_walk_tbl")
    hi.sta_abs(FAULT_EXP)
    hi.lda_imm(0x00)
    hi.sta_abs(FAULT_ADDR_LO)
    hi.lda_imm(0x18)
    hi.sta_abs(FAULT_ADDR_HI)
    hi.lda_zp(ZP_BANK)
    hi.sta_abs(FAULT_BANK)
    hi.lda_zp(ZP_STAGE)
    hi.sta_abs(FAULT_STAGE)
    hi.lda_imm(SUB_BUS)
    hi.sta_abs(FAULT_SUB)
    hi.jmp("fatal_halt")
    hi.label(l_bus_ok1)
    hi.iny()
    hi.cpy_imm(18)
    hi.bne(l_bus_lp)

    # 1B. Address-pin A0..A15 single-bit uniqueness around $1800 (17 addresses)
    hi.lda_imm(SUB_ADR)
    hi.sta_zp(ZP_SUB)
    # Fill all 17 addresses with $55
    hi.ldx_imm(0)
    l_adr_init = hi.label("hi_adr_init")
    hi.lda_absx("hi_pin_lo")
    hi.sta_zp(ZP_PTR)
    hi.lda_absx("hi_pin_hi")
    hi.sta_zp(ZP_PTR + 1)
    hi.ldy_imm(0)
    hi.lda_imm(0x55)
    hi.sta_indy(ZP_PTR)
    hi.inx()
    hi.cpx_imm(17)
    hi.bne(l_adr_init)

    # Walk $AA through each of the 17 addresses one at a time, checking all 17!
    hi.ldx_imm(0)               # X = target index 0..16
    l_adr_outer = hi.label("hi_adr_outer")
    hi.stx_zp(ZP_TMP2)
    hi.lda_absx("hi_pin_lo")
    hi.sta_zp(ZP_PTR)
    hi.lda_absx("hi_pin_hi")
    hi.sta_zp(ZP_PTR + 1)
    hi.ldy_imm(0)
    hi.lda_imm(0xAA)
    hi.sta_indy(ZP_PTR)

    # Verify all 17 slots: slot == ZP_TMP2 must be $AA, all others $55
    hi.ldx_imm(0)
    l_adr_chk = hi.label("hi_adr_chk")
    hi.lda_absx("hi_pin_lo")
    hi.sta_zp(ZP_PTR)
    hi.lda_absx("hi_pin_hi")
    hi.sta_zp(ZP_PTR + 1)
    hi.lda_imm(0x55)
    hi.cpx_zp(ZP_TMP2)
    l_not_self = hi.unique_label("not_self")
    hi.bne(l_not_self)
    hi.lda_imm(0xAA)
    hi.label(l_not_self)
    hi.sta_zp(ZP_PAT0)          # expected for this slot
    hi.ldy_imm(0)
    hi.lda_indy(ZP_PTR)
    hi.cmp_zp(ZP_PAT0)
    l_adr_ok = hi.unique_label("adr_ok")
    hi.beq(l_adr_ok)
    # Address alias fault!
    hi.sta_zp(ZP_TMP)
    hi.lda_imm(CR_UI)
    hi.sta_abs(0xFF00)
    hi.lda_zp(ZP_TMP)
    hi.sta_abs(FAULT_ACT)
    hi.lda_zp(ZP_PAT0)
    hi.sta_abs(FAULT_EXP)
    hi.lda_zp(ZP_PTR)
    hi.sta_abs(FAULT_ADDR_LO)
    hi.lda_zp(ZP_PTR + 1)
    hi.sta_abs(FAULT_ADDR_HI)
    hi.lda_zp(ZP_BANK)
    hi.sta_abs(FAULT_BANK)
    hi.lda_zp(ZP_STAGE)
    hi.sta_abs(FAULT_STAGE)
    hi.lda_imm(SUB_ADR)
    hi.sta_abs(FAULT_SUB)
    hi.jmp("fatal_halt")
    hi.label(l_adr_ok)
    hi.inx()
    hi.cpx_imm(17)
    hi.bne(l_adr_chk)

    # Restore target slot back to $55
    hi.ldx_zp(ZP_TMP2)
    hi.lda_absx("hi_pin_lo")
    hi.sta_zp(ZP_PTR)
    hi.lda_absx("hi_pin_hi")
    hi.sta_zp(ZP_PTR + 1)
    hi.ldy_imm(0)
    hi.lda_imm(0x55)
    hi.sta_indy(ZP_PTR)
    hi.inx()
    hi.cpx_imm(17)
    hi.bne(l_adr_outer)

    hi.lda_imm(CR_UI)
    hi.sta_abs(0xFF00)
    hi.rts()

    # --- Helper: 6-pass March-C- on $8000-$BFFF ---
    def hi_set_cr(a: Asm6502):
        a.jsr("enter_test_speed_hi")
        a.jsr("hi_set_cr_mid")

    def hi_mismatch(a: Asm6502, exp_zp: int):
        a.sta_zp(ZP_TMP)
        a.lda_imm(CR_UI)
        a.sta_abs(0xFF00)
        a.lda_zp(ZP_TMP)
        a.sta_abs(FAULT_ACT)
        a.lda_zp(exp_zp)
        a.sta_abs(FAULT_EXP)
        a.sty_abs(FAULT_ADDR_LO)
        a.lda_zp(ZP_PTR + 1)
        a.sta_abs(FAULT_ADDR_HI)
        a.lda_zp(ZP_BANK)
        a.sta_abs(FAULT_BANK)
        a.lda_zp(ZP_STAGE)
        a.sta_abs(FAULT_STAGE)
        a.lda_zp(ZP_SUB)
        a.sta_abs(FAULT_SUB)
        a.jmp("fatal_halt")

    def hi_update_step(a: Asm6502):
        a.lda_imm(CR_UI)
        a.sta_abs(0xFF00)
        a.jsr("ui_draw_step")

    hi.label("enter_test_speed_hi")
    hi.lda_imm(CR_UI)
    hi.sta_abs(0xFF00)
    hi.lda_zp(ZP_SPEED)
    hi.sta_abs(0xD030)
    hi.rts()

    hi.label("upper_march_mid")
    hi.lda_imm(0x80)
    hi.sta_zp(ZP_START_HI)
    hi.lda_imm(0xBF)
    hi.sta_zp(ZP_END_HI)
    emit_march6_body(hi, "hi_mc", hi_set_cr, hi_mismatch, hi_update_step)
    hi.lda_imm(CR_UI)
    hi.sta_abs(0xFF00)
    hi.rts()

    # --- Helper: Stage 6 Stamp Write & Verify on $8000-$BFFF ---
    hi.label("upper_stamp_write_mid")
    hi.jsr("enter_test_speed_hi")
    hi.jsr("hi_set_cr_mid")
    hi.lda_imm(0x00)
    hi.sta_zp(ZP_PTR)
    hi.lda_imm(0x80)
    hi.sta_zp(ZP_PTR + 1)
    l_sw_pg = hi.label("hi_sw_pg")
    hi.ldx_zp(ZP_PTR + 1)
    hi.lda_absx("hi_scramble_tbl")
    hi.ldx_zp(ZP_BANK)
    hi.eor_absx("hi_bank_salt")
    hi.eor_zp(ZP_D0)
    hi.sta_zp(ZP_TAG)
    hi.ldy_imm(0x00)
    l_sw_by = hi.label("hi_sw_by")
    hi.lda_absy("hi_scramble_tbl")
    hi.eor_zp(ZP_TAG)
    hi.sta_indy(ZP_PTR)
    hi.iny()
    hi.bne(l_sw_by)
    hi.lda_zp(ZP_PTR + 1)
    hi.cmp_imm(0xBF)
    l_sw_dn = "hi_sw_dn"
    hi.beq(l_sw_dn)
    hi.inc_zp(ZP_PTR + 1)
    hi.jmp(l_sw_pg)
    hi.label(l_sw_dn)
    hi.lda_imm(CR_UI)
    hi.sta_abs(0xFF00)
    hi.rts()

    hi.label("upper_stamp_verify_mid")
    hi.jsr("enter_test_speed_hi")
    hi.jsr("hi_set_cr_mid")
    hi.lda_imm(0x00)
    hi.sta_zp(ZP_PTR)
    hi.lda_imm(0x80)
    hi.sta_zp(ZP_PTR + 1)
    l_sv_pg = hi.label("hi_sv_pg")
    hi.ldx_zp(ZP_PTR + 1)
    hi.lda_absx("hi_scramble_tbl")
    hi.ldx_zp(ZP_BANK)
    hi.eor_absx("hi_bank_salt")
    hi.eor_zp(ZP_D0)
    hi.sta_zp(ZP_TAG)
    hi.ldy_imm(0x00)
    l_sv_by = hi.label("hi_sv_by")
    hi.lda_absy("hi_scramble_tbl")
    hi.eor_zp(ZP_TAG)
    hi.sta_zp(ZP_PAT0)
    hi.lda_indy(ZP_PTR)
    hi.cmp_zp(ZP_PAT0)
    l_sv_ok = hi.unique_label("hi_sv_ok")
    hi.beq(l_sv_ok)
    hi_mismatch(hi, ZP_PAT0)
    hi.label(l_sv_ok)
    hi.iny()
    hi.bne(l_sv_by)
    hi.lda_zp(ZP_PTR + 1)
    hi.cmp_imm(0xBF)
    l_sv_dn = "hi_sv_dn"
    hi.beq(l_sv_dn)
    hi.inc_zp(ZP_PTR + 1)
    hi.jmp(l_sv_pg)
    hi.label(l_sv_dn)
    hi.lda_imm(CR_UI)
    hi.sta_abs(0xFF00)
    hi.rts()

    # --- Helper: Read $8800 from RAM Bank ZP_BANK (for Stage 7D ROM write-through) ---
    hi.label("upper_read_8800")
    hi.jsr("hi_set_cr_mid")
    hi.lda_abs(0x8800)
    hi.ldx_imm(CR_UI)
    hi.stx_abs(0xFF00)
    hi.rts()

    # Tables in Upper ROM
    hi.label("hi_walk_tbl")
    hi.emit(
        0x00, 0xFF,
        0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80,
        0xFE, 0xFD, 0xFB, 0xF7, 0xEF, 0xDF, 0xBF, 0x7F,
    )
    pin_addrs = [0x1800] + [0x1800 ^ (1 << k) for k in range(16)]
    hi.label("hi_pin_lo")
    hi.emit(*(addr & 0xFF for addr in pin_addrs))
    hi.label("hi_pin_hi")
    hi.emit(*((addr >> 8) & 0xFF for addr in pin_addrs))
    hi.label("hi_bank_salt")
    hi.emit(0x00, 0x6B, 0xB5, 0xDE)
    hi.label("hi_scramble_tbl")
    hi.emit_bytes(make_scramble_table())

    # Hardware vectors at $FFEF-$FFFF in U36 High
    hi.org_pad(0xFFEF, fill=0xFF)
    hi.label("hi_rti")
    hi.rti()
    hi.label("hi_reset")
    hi.sei()
    hi.lda_imm(CR_UI)
    hi.sta_abs(0xFF00)
    hi.jmp("cold_start")
    hi.org_pad(0xFFFA, fill=0xFF)
    hi.word("hi_rti")     # $FFFA NMI
    hi.word("hi_reset")   # $FFFC RESET
    hi.word("hi_rti")     # $FFFE IRQ/BRK

    # =========================================================================
    # LOWER 16 KB ($8000-$BFFF in U36): Autostart Header + Main Test Suite
    # =========================================================================
    lo.jmp("cold_start")  # $8000-$8002
    lo.jmp("cold_start")  # $8003-$8005
    lo.emit(0x01)         # $8006: $01 = Immediate Autostart in Kernal POLL!
    lo.emit_bytes(b"CBM") # $8007-$8009: C128 Function ROM signature

    lo.label("cold_start")
    lo.sei()
    lo.cld()
    lo.ldx_imm(0xFF)
    lo.txs()

    # Map Bank 0 + U36 Lo/Hi + I/O ON ($16)
    lo.lda_imm(CR_UI)
    lo.sta_abs(0xFF00)

    # Configure MMU registers $D501..$D50A cleanly
    lo.sta_abs(0xD501)    # PCRA ($FF01) = $16 (CR_UI for zero-RAM STA $FF01!)
    lo.lda_imm(0x37)
    lo.sta_abs(0xD502)
    lo.lda_imm(0x1F)
    lo.sta_abs(0xD503)
    lo.lda_imm(0xBF)
    lo.sta_abs(0xD505)    # MCR: C128 mode, 8502 active
    lo.lda_imm(0x04)
    lo.sta_abs(0xD506)    # RCR: VIC bank 0, 1 KB Bottom Common RAM
    lo.lda_imm(0x00)
    lo.sta_abs(0xD508)    # P0H = 0 (must precede P0L)
    lo.sta_abs(0xD507)    # P0L = 0
    lo.sta_abs(0xD50A)    # P1H = 0 (must precede P1L)
    lo.lda_imm(0x01)
    lo.sta_abs(0xD509)    # P1L = 1

    # Disable CIA1/CIA2 interrupts and ack pending
    lo.lda_imm(0x7F)
    lo.sta_abs(0xDC0D)
    lo.sta_abs(0xDD0D)
    lo.lda_abs(0xDC0D)
    lo.lda_abs(0xDD0D)
    lo.lda_imm(0x08)
    lo.sta_abs(0xDC0E)
    lo.sta_abs(0xDD0E)
    lo.sta_abs(0xDC0F)
    lo.sta_abs(0xDD0F)

    # Configure CIA2 VIC bank ($0000-$3FFF) and 8502 CPU port ($00/$01)
    # Matches C128 Kernal IOINIT ($E12E-$E13E): $01=$E3 (CHAREN=0 -> Char ROM for VIC,
    # both CPU & VIC on same Color RAM bank), $00=$2F.
    lo.lda_imm(0x07)
    lo.sta_abs(0xDD00)
    lo.lda_imm(0x3F)
    lo.sta_abs(0xDD02)
    lo.lda_imm(0xE3)
    lo.sta_zp(0x01)
    lo.lda_imm(0x2F)
    lo.sta_zp(0x00)

    # Silence SID & configure VIC-IIe 40-column text display
    lo.lda_imm(0x00)
    lo.sta_abs(0xD418)    # SID volume off
    lo.sta_abs(0xD015)    # Sprites off ($D000-$D007 free as fault mailbox!)
    lo.sta_abs(0xD01A)    # VIC IRQs off
    lo.sta_abs(0xD030)    # 1 MHz SLOW mode
    lo.sta_abs(FAULT_SPEED)
    lo.lda_imm(0xFF)
    lo.sta_abs(0xD019)    # Ack VIC IRQ flags
    lo.lda_imm(0x1B)
    lo.sta_abs(0xD011)    # 25 rows, screen enabled
    lo.lda_imm(0x08)
    lo.sta_abs(0xD016)    # 40 columns
    lo.lda_imm(0x14)
    lo.sta_abs(0xD018)    # Screen at $0400, Char ROM at $1000
    lo.lda_imm(0x0E)
    lo.sta_abs(0xD020)    # Light-blue border while Pass 1 in progress
    lo.lda_imm(0x06)
    lo.sta_abs(0xD021)    # Blue background

    # Draw static UI template onto $0400 + $D800 INLINE (zero JSR/RTS stack usage
    # so even if Bank 0 SRAM has a stuck data bit, RTS cannot crash!)
    for pg_i in range(4):
        lo.ldx_imm(0)
        l_boot_cp = lo.here_label("boot_cp")
        lo.lda_absx(f"tpl_scr_{pg_i}")
        lo.sta_absx(SCR_BASE + pg_i * 256)
        lo.lda_absx(f"tpl_col_{pg_i}")
        lo.sta_absx(COL_BASE + pg_i * 256)
        lo.inx()
        if pg_i < 3:
            lo.bne(l_boot_cp)
        else:
            lo.cpx_imm(232)
            lo.bne(l_boot_cp)

    # =========================================================================
    # BOOT BANK AUTO-DETECT (uses registers + $1800 before relying on ZP!)
    # =========================================================================
    # 1. Verify Bank 0 $1800 can hold $55 and $AA
    for pat in (0x55, 0xAA):
        lo.lda_imm(pat)
        lo.sta_abs(0x1800)
        lo.eor_imm(0xFF)
        lo.sta_abs(0x1801)
        lo.lda_abs(0x1800)
        lo.cmp_imm(pat)
        l_ok = lo.unique_label("b0_det_ok")
        lo.beq(l_ok)
        lo.sta_abs(FAULT_ACT)
        lo.lda_imm(pat)
        lo.sta_abs(FAULT_EXP)
        lo.lda_imm(0x00)
        lo.sta_abs(FAULT_ADDR_LO)
        lo.lda_imm(0x18)
        lo.sta_abs(FAULT_ADDR_HI)
        lo.lda_imm(0)
        lo.sta_abs(FAULT_BANK)
        lo.sta_abs(FAULT_STAGE)
        lo.lda_imm(SUB_DET)
        lo.sta_abs(FAULT_SUB)
        lo.jmp("fatal_halt")
        lo.label(l_ok)

    # 2. Stamp Banks 3, 2, 1, 0 in descending order ($B3, $B2, $B1, $B0) at $1800
    for b in (3, 2, 1, 0):
        lo.lda_imm((b << 6) | CR_UI)
        lo.sta_abs(0xFF00)
        lo.lda_imm(0xB0 | b)
        lo.sta_abs(0x1800)
    lo.lda_imm(CR_UI)
    lo.sta_abs(0xFF00)

    # Check Bank 0 == $B0 (not overwritten if Bank 1 mirrored onto Bank 0? Wait:
    # descending order 3->2->1->0 wrote $B0 last; check ascending 0->1 too!)
    lo.lda_imm(0xB0)
    lo.sta_abs(0x1800)
    lo.lda_imm(0x40 | CR_UI)  # Bank 1
    lo.sta_abs(0xFF00)
    lo.lda_imm(0xB1)
    lo.sta_abs(0x1800)
    lo.lda_imm(CR_UI)         # Back to Bank 0: must still be $B0!
    lo.sta_abs(0xFF00)
    lo.lda_abs(0x1800)
    lo.cmp_imm(0xB0)
    l_b01_ok = lo.unique_label("b01_ok")
    lo.beq(l_b01_ok)
    # Bank 1 write overwrote Bank 0!
    lo.sta_abs(FAULT_ACT)
    lo.lda_imm(0xB0)
    lo.sta_abs(FAULT_EXP)
    lo.lda_imm(0x00)
    lo.sta_abs(FAULT_ADDR_LO)
    lo.lda_imm(0x18)
    lo.sta_abs(FAULT_ADDR_HI)
    lo.lda_imm(1)
    lo.sta_abs(FAULT_BANK)
    lo.lda_imm(0)
    lo.sta_abs(FAULT_STAGE)
    lo.lda_imm(SUB_DET)
    lo.sta_abs(FAULT_SUB)
    lo.jmp("fatal_halt")
    lo.label(l_b01_ok)

    # Also verify Bank 1 reads back $B1
    lo.lda_imm(0x40 | CR_UI)
    lo.sta_abs(0xFF00)
    lo.lda_abs(0x1800)
    lo.ldx_imm(CR_UI)
    lo.stx_abs(0xFF00)
    lo.cmp_imm(0xB1)
    l_b1_rd_ok = lo.unique_label("b1_rd_ok")
    lo.beq(l_b1_rd_ok)
    lo.sta_abs(FAULT_ACT)
    lo.lda_imm(0xB1)
    lo.sta_abs(FAULT_EXP)
    lo.lda_imm(0x00)
    lo.sta_abs(FAULT_ADDR_LO)
    lo.lda_imm(0x18)
    lo.sta_abs(FAULT_ADDR_HI)
    lo.lda_imm(1)
    lo.sta_abs(FAULT_BANK)
    lo.lda_imm(0)
    lo.sta_abs(FAULT_STAGE)
    lo.lda_imm(SUB_DET)
    lo.sta_abs(FAULT_SUB)
    lo.jmp("fatal_halt")
    lo.label(l_b1_rd_ok)

    # Initialize persistent ZP variables in Bank 0
    lo.lda_imm(0)
    lo.sta_zp(ZP_D0)
    lo.sta_zp(ZP_D1)
    lo.sta_zp(ZP_D2)
    lo.sta_zp(ZP_D3)
    lo.sta_zp(ZP_SPEED)
    lo.sta_zp(ZP_PAIR_ALT)
    lo.sta_zp(ZP_GREEN)
    lo.lda_abs(0xD50B)
    lo.sta_zp(ZP_MMU_VER)

    # Now distinguish 128 KB (Bank 2 mirrors Bank 0, Bank 3 mirrors Bank 1)
    # vs 256 KB (Banks 0, 1, 2, 3 all independent)
    for b in (0, 1, 2, 3):
        lo.lda_imm((b << 6) | CR_UI)
        lo.sta_abs(0xFF00)
        lo.lda_imm(0xB0 | b)
        lo.sta_abs(0x1800)
    lo.lda_imm(CR_UI)
    lo.sta_abs(0xFF00)
    lo.lda_abs(0x1800)        # In 128K solo, Bank 2 ($B2) overwrote Bank 0!
    lo.cmp_imm(0xB2)
    l_check_256 = lo.unique_label("chk_256")
    lo.bne(l_check_256)

    # Bank 0 saw $B2 -> verify Bank 1 also saw $B3 (clean 128 KB mirror)
    lo.lda_imm(0x40 | CR_UI)
    lo.sta_abs(0xFF00)
    lo.lda_abs(0x1800)
    lo.ldx_imm(CR_UI)
    lo.stx_abs(0xFF00)
    lo.cmp_imm(0xB3)
    bne_far(lo, "det_partial_riser_b3")
    lo.lda_imm(2)
    lo.sta_zp(ZP_NUM_BANKS)
    lo.jmp("det_done")

    lo.label(l_check_256)
    # Must be $B0 in Bank 0, $B1 in Bank 1, $B2 in Bank 2, $B3 in Bank 3!
    for b in (0, 1, 2, 3):
        lo.lda_imm((b << 6) | CR_UI)
        lo.sta_abs(0xFF00)
        lo.lda_abs(0x1800)
        lo.ldx_imm(CR_UI)
        lo.stx_abs(0xFF00)
        lo.cmp_imm(0xB0 | b)
        l_ok256 = lo.unique_label(f"ok256_{b}")
        lo.beq(l_ok256)
        lo.sta_abs(FAULT_ACT)
        lo.lda_imm(0xB0 | b)
        lo.sta_abs(FAULT_EXP)
        lo.lda_imm(0x00)
        lo.sta_abs(FAULT_ADDR_LO)
        lo.lda_imm(0x18)
        lo.sta_abs(FAULT_ADDR_HI)
        lo.lda_imm(b)
        lo.sta_abs(FAULT_BANK)
        lo.lda_imm(0)
        lo.sta_abs(FAULT_STAGE)
        lo.lda_imm(SUB_DET)
        lo.sta_abs(FAULT_SUB)
        lo.jmp("fatal_halt")
        lo.label(l_ok256)
    lo.lda_imm(4)
    lo.sta_zp(ZP_NUM_BANKS)
    lo.jmp("det_done")

    lo.label("det_partial_riser_b3")
    lo.sta_abs(FAULT_ACT)
    lo.lda_imm(0xB3)
    lo.sta_abs(FAULT_EXP)
    lo.lda_imm(0x00)
    lo.sta_abs(FAULT_ADDR_LO)
    lo.lda_imm(0x18)
    lo.sta_abs(FAULT_ADDR_HI)
    lo.lda_imm(3)
    lo.sta_abs(FAULT_BANK)
    lo.lda_imm(0)
    lo.sta_abs(FAULT_STAGE)
    lo.lda_imm(SUB_DET)
    lo.sta_abs(FAULT_SUB)
    lo.jmp("fatal_halt")

    lo.label("det_done")
    # Install safe NMI/IRQ RTI stub at $FFF8-$FFFF in every detected bank
    lo.jsr("install_nmi_stubs")
    lo.jsr("ui_refresh_header")

    # =========================================================================
    # MAIN INFINITE QUALIFICATION / BURN-IN LOOP (1 MHz pass + 2 MHz pass)
    # Kept flat (no JSR open across Stage 2) so Stage 2 can March-test $0100-$01FF!
    # =========================================================================
    lo.label("pass_loop")
    lo.lda_imm(0)
    lo.sta_zp(ZP_SPEED)

    lo.label("speed_half_loop")
    lo.ldx_imm(0xFF)
    lo.txs()
    lo.jsr("ui_set_speed_and_reset_grid")

    # --- STAGE 1: Data Bus + A0-A15 Pin Walk ---
    lo.lda_imm(1)
    lo.sta_zp(ZP_STAGE)
    lo.jsr("ui_draw_stage")
    lo.lda_imm(0)
    lo.sta_zp(ZP_BANK)
    l_s1_bk = lo.label("s1_bk")
    lo.jsr("ui_mark_bank_active")
    lo.ldx_imm(0x18)
    lo.ldy_imm(0x98)
    lo.lda_imm(SUB_ADR)
    lo.sta_zp(ZP_SUB)
    lo.jsr("ui_draw_window")
    lo.jsr("enter_test_speed")
    lo.jsr("upper_stage1_bank")
    lo.jsr("ui_begin")
    lo.jsr("ui_mark_bank_done")
    lo.inc_zp(ZP_BANK)
    lo.lda_zp(ZP_BANK)
    lo.cmp_zp(ZP_NUM_BANKS)
    lo.bcc(l_s1_bk)

    # --- STAGE 2: Low RAM ($0002-$07FF) + Top Page ($FF05-$FFF7) March-C- ---
    lo.lda_imm(2)
    lo.sta_zp(ZP_STAGE)
    lo.sta_abs(FAULT_STAGE)
    lo.lda_zp(ZP_SPEED)
    lo.sta_abs(FAULT_SPEED)
    lo.lda_imm(SUB_M1)
    lo.sta_zp(ZP_SUB)
    lo.jsr("ui_draw_stage")
    lo.lda_imm(0)
    lo.sta_zp(ZP_BANK)
    lo.ldx_imm(0x00)
    lo.ldy_imm(0x07)
    lo.jsr("ui_draw_window")
    lo.jsr("save_persist_to_colram")

    # Pre-compute stage-2 loop limit (num_banks * 2) into $DBF8..$DBF9? We already
    # saved ZP_NUM_BANKS at SAVE_NIB_BASE+0/+1!
    lo.jsr("enter_test_speed")
    lo.jmp("stage2_entry")
    lo.label("stage2_return")
    lo.lda_imm(CR_UI)
    lo.sta_abs(0xFF00)
    lo.lda_imm(0x00)
    lo.sta_abs(0xD508)
    lo.sta_abs(0xD507)
    lo.sta_abs(0xD50A)
    lo.lda_imm(0x01)
    lo.sta_abs(0xD509)
    lo.lda_imm(0x04)
    lo.sta_abs(0xD506)        # Restore 1 KB Bottom Common RAM
    lo.lda_imm(0x00)
    lo.sta_abs(0xD030)        # 1 MHz for screen redraw
    lo.ldx_imm(0xFF)
    lo.txs()
    lo.jsr("restore_persist_from_colram")
    # Bank 0 $0400-$07FF now holds $55 from M5 -> redraw template & restore bars!
    lo.jsr("draw_full_template_no_zp")
    lo.jsr("ui_refresh_header")
    lo.jsr("ui_restore_grid_after_stage2")

    # --- STAGE 3: Full-Bank March-C- $00 / $FF ---
    lo.lda_imm(3)
    lo.sta_zp(ZP_STAGE)
    lo.lda_imm(0x00)
    lo.sta_zp(ZP_PAT0)
    lo.lda_imm(0xFF)
    lo.sta_zp(ZP_PAT1)
    lo.jsr("run_full_march_stage")

    # --- STAGE 4: Full-Bank March-C- $55 / $AA ---
    lo.lda_imm(4)
    lo.sta_zp(ZP_STAGE)
    lo.lda_imm(0x55)
    lo.sta_zp(ZP_PAT0)
    lo.lda_imm(0xAA)
    lo.sta_zp(ZP_PAT1)
    lo.jsr("run_full_march_stage")

    # --- STAGE 5: Full-Bank March-C- Bit-Pair ($33/$CC or $0F/$F0) ---
    lo.lda_imm(5)
    lo.sta_zp(ZP_STAGE)
    lo.lda_zp(ZP_PAIR_ALT)
    l_s5_0f = lo.unique_label("s5_0f")
    lo.bne(l_s5_0f)
    lo.lda_imm(0x33)
    lo.sta_zp(ZP_PAT0)
    lo.lda_imm(0xCC)
    lo.sta_zp(ZP_PAT1)
    lo.jmp("s5_go")
    lo.label(l_s5_0f)
    lo.lda_imm(0x0F)
    lo.sta_zp(ZP_PAT0)
    lo.lda_imm(0xF0)
    lo.sta_zp(ZP_PAT1)
    lo.label("s5_go")
    lo.jsr("run_full_march_stage")

    # --- STAGE 6: Simultaneous All-Bank Non-Aliasing Stamp & Verify ---
    lo.lda_imm(6)
    lo.sta_zp(ZP_STAGE)
    lo.jsr("run_stage6_xbank")

    # --- STAGE 7: MMU P0/P1 Relocation + Common RAM + U36 ROM Write-Through ---
    lo.lda_imm(7)
    lo.sta_zp(ZP_STAGE)
    lo.jsr("run_stage7_mmu")

    # If ZP_SPEED == 0 (just finished 1 MHz), run 2 MHz half-pass next!
    lo.lda_zp(ZP_SPEED)
    l_finished_2m = lo.unique_label("fin_2m")
    lo.bne(l_finished_2m)
    lo.lda_imm(1)
    lo.sta_zp(ZP_SPEED)
    lo.jmp("speed_half_loop")

    # Both 1 MHz and 2 MHz completed with 0 errors! Increment pass counter!
    lo.label(l_finished_2m)
    lo.jsr("inc_pass_counter")
    lo.lda_imm(1)
    lo.sta_zp(ZP_GREEN)
    lo.lda_zp(ZP_PAIR_ALT)
    lo.eor_imm(0x01)
    lo.sta_zp(ZP_PAIR_ALT)
    lo.jsr("ui_refresh_header")
    lo.jmp("pass_loop")

    # =========================================================================
    # FULL-BANK MARCH-C- ORCHESTRATOR ($0800-$7FFF, $8000-$BFFF, $C000-$FEFF)
    # =========================================================================
    lo.label("run_full_march_stage")
    lo.jsr("ui_draw_stage")
    lo.lda_imm(0)
    lo.sta_zp(ZP_BANK)
    l_fm_bk = lo.label("fm_bk")
    lo.jsr("ui_mark_bank_active")

    # Window 1: $0800-$7FFF
    lo.ldx_imm(0x08)
    lo.ldy_imm(0x7F)
    lo.jsr("ui_draw_window")
    lo.lda_imm(0x08)
    lo.sta_zp(ZP_START_HI)
    lo.lda_imm(0x7F)
    lo.sta_zp(ZP_END_HI)
    lo.jsr("lo_march_range")

    # Window 2: $8000-$BFFF (via U36 High helper)
    lo.ldx_imm(0x80)
    lo.ldy_imm(0xBF)
    lo.jsr("ui_draw_window")
    lo.jsr("upper_march_mid")
    lo.jsr("ui_begin")

    # Window 3: $C000-$FEFF (includes $D000-$DFFF underneath I/O!)
    lo.ldx_imm(0xC0)
    lo.ldy_imm(0xFE)
    lo.jsr("ui_draw_window")
    lo.lda_imm(0xC0)
    lo.sta_zp(ZP_START_HI)
    lo.lda_imm(0xFE)
    lo.sta_zp(ZP_END_HI)
    lo.jsr("lo_march_range")

    lo.jsr("ui_begin")
    lo.jsr("ui_mark_bank_done")
    lo.inc_zp(ZP_BANK)
    lo.lda_zp(ZP_BANK)
    lo.cmp_zp(ZP_NUM_BANKS)
    lo.bcc(l_fm_bk)
    lo.rts()

    # --- Lower-ROM 6-pass March-C- subroutine for $0800-$7FFF and $C000-$FEFF ---
    def lo_set_cr(a: Asm6502):
        a.jsr("enter_test_speed")
        a.ldx_zp(ZP_BANK)
        a.lda_absx("lo_cr_ram_tbl")
        a.sta_abs(0xFF00)

    def lo_mismatch(a: Asm6502, exp_zp: int):
        a.sta_zp(ZP_TMP)
        a.lda_imm(CR_UI)
        a.sta_abs(0xFF00)
        a.lda_zp(ZP_TMP)
        a.sta_abs(FAULT_ACT)
        a.lda_zp(exp_zp)
        a.sta_abs(FAULT_EXP)
        a.sty_abs(FAULT_ADDR_LO)
        a.lda_zp(ZP_PTR + 1)
        a.sta_abs(FAULT_ADDR_HI)
        a.lda_zp(ZP_BANK)
        a.sta_abs(FAULT_BANK)
        a.lda_zp(ZP_STAGE)
        a.sta_abs(FAULT_STAGE)
        a.lda_zp(ZP_SUB)
        a.sta_abs(FAULT_SUB)
        a.jmp("fatal_halt")

    def lo_update_step(a: Asm6502):
        a.jsr("ui_begin")
        a.jsr("ui_draw_step")

    lo.label("lo_march_range")
    emit_march6_body(lo, "lo_mc", lo_set_cr, lo_mismatch, lo_update_step)
    lo.jsr("ui_begin")
    lo.rts()

    # =========================================================================
    # STAGE 6: SIMULTANEOUS CROSS-BANK STAMP & VERIFY ($0800-$FEFF ALL BANKS)
    # =========================================================================
    lo.label("run_stage6_xbank")
    lo.jsr("ui_draw_stage")

    # Pass A: Write unique hash across ALL banks before verifying any bank!
    lo.lda_imm(SUB_WR)
    lo.sta_zp(ZP_SUB)
    lo.lda_imm(0)
    lo.sta_zp(ZP_BANK)
    l_s6w_bk = lo.label("s6w_bk")
    lo.jsr("ui_mark_bank_active")
    lo.ldx_imm(0x08)
    lo.ldy_imm(0xFE)
    lo.jsr("ui_draw_window")
    lo.lda_imm(0x08)
    lo.sta_zp(ZP_START_HI)
    lo.lda_imm(0x7F)
    lo.sta_zp(ZP_END_HI)
    lo.jsr("lo_stamp_write_range")
    lo.jsr("upper_stamp_write_mid")
    lo.lda_imm(0xC0)
    lo.sta_zp(ZP_START_HI)
    lo.lda_imm(0xFE)
    lo.sta_zp(ZP_END_HI)
    lo.jsr("lo_stamp_write_range")
    lo.jsr("ui_begin")
    lo.inc_zp(ZP_BANK)
    lo.lda_zp(ZP_BANK)
    lo.cmp_zp(ZP_NUM_BANKS)
    lo.bcc(l_s6w_bk)

    # Pass B: Read back & verify ALL banks!
    lo.lda_imm(SUB_VFY)
    lo.sta_zp(ZP_SUB)
    lo.lda_imm(0)
    lo.sta_zp(ZP_BANK)
    l_s6v_bk = lo.label("s6v_bk")
    lo.ldx_imm(0x08)
    lo.ldy_imm(0xFE)
    lo.jsr("ui_draw_window")
    lo.lda_imm(0x08)
    lo.sta_zp(ZP_START_HI)
    lo.lda_imm(0x7F)
    lo.sta_zp(ZP_END_HI)
    lo.jsr("lo_stamp_verify_range")
    lo.jsr("upper_stamp_verify_mid")
    lo.lda_imm(0xC0)
    lo.sta_zp(ZP_START_HI)
    lo.lda_imm(0xFE)
    lo.sta_zp(ZP_END_HI)
    lo.jsr("lo_stamp_verify_range")
    lo.jsr("ui_begin")
    lo.jsr("ui_mark_bank_done")
    lo.inc_zp(ZP_BANK)
    lo.lda_zp(ZP_BANK)
    lo.cmp_zp(ZP_NUM_BANKS)
    lo.bcc(l_s6v_bk)
    lo.rts()

    lo.label("lo_stamp_write_range")
    lo.jsr("enter_test_speed")
    lo.ldx_zp(ZP_BANK)
    lo.lda_absx("lo_cr_ram_tbl")
    lo.sta_abs(0xFF00)
    lo.lda_imm(0x00)
    lo.sta_zp(ZP_PTR)
    lo.lda_zp(ZP_START_HI)
    lo.sta_zp(ZP_PTR + 1)
    l_lsw_pg = lo.label("lsw_pg")
    lo.ldx_zp(ZP_PTR + 1)
    lo.lda_absx("lo_scramble_tbl")
    lo.ldx_zp(ZP_BANK)
    lo.eor_absx("lo_bank_salt")
    lo.eor_zp(ZP_D0)
    lo.sta_zp(ZP_TAG)
    lo.ldy_imm(0x00)
    l_lsw_by = lo.label("lsw_by")
    lo.lda_absy("lo_scramble_tbl")
    lo.eor_zp(ZP_TAG)
    lo.sta_indy(ZP_PTR)
    lo.iny()
    lo.bne(l_lsw_by)
    lo.lda_zp(ZP_PTR + 1)
    lo.cmp_zp(ZP_END_HI)
    l_lsw_dn = "lsw_dn"
    lo.beq(l_lsw_dn)
    lo.inc_zp(ZP_PTR + 1)
    lo.jmp(l_lsw_pg)
    lo.label(l_lsw_dn)
    lo.lda_imm(CR_UI)
    lo.sta_abs(0xFF00)
    lo.rts()

    lo.label("lo_stamp_verify_range")
    lo.jsr("enter_test_speed")
    lo.ldx_zp(ZP_BANK)
    lo.lda_absx("lo_cr_ram_tbl")
    lo.sta_abs(0xFF00)
    lo.lda_imm(0x00)
    lo.sta_zp(ZP_PTR)
    lo.lda_zp(ZP_START_HI)
    lo.sta_zp(ZP_PTR + 1)
    l_lsv_pg = lo.label("lsv_pg")
    lo.ldx_zp(ZP_PTR + 1)
    lo.lda_absx("lo_scramble_tbl")
    lo.ldx_zp(ZP_BANK)
    lo.eor_absx("lo_bank_salt")
    lo.eor_zp(ZP_D0)
    lo.sta_zp(ZP_TAG)
    lo.ldy_imm(0x00)
    l_lsv_by = lo.label("lsv_by")
    lo.lda_absy("lo_scramble_tbl")
    lo.eor_zp(ZP_TAG)
    lo.sta_zp(ZP_PAT0)
    lo.lda_indy(ZP_PTR)
    lo.cmp_zp(ZP_PAT0)
    l_lsv_ok = lo.unique_label("lsv_ok")
    lo.beq(l_lsv_ok)
    lo_mismatch(lo, ZP_PAT0)
    lo.label(l_lsv_ok)
    lo.iny()
    lo.bne(l_lsv_by)
    lo.lda_zp(ZP_PTR + 1)
    lo.cmp_zp(ZP_END_HI)
    l_lsv_dn = "lsv_dn"
    lo.beq(l_lsv_dn)
    lo.inc_zp(ZP_PTR + 1)
    lo.jmp(l_lsv_pg)
    lo.label(l_lsv_dn)
    lo.lda_imm(CR_UI)
    lo.sta_abs(0xFF00)
    lo.rts()

    # =========================================================================
    # STAGE 7: MMU P0/P1 RELOCATION ($18, $40, $41) + RCR + ROM WRITE-THROUGH
    # =========================================================================
    lo.label("run_stage7_mmu")
    lo.jsr("ui_draw_stage")
    lo.lda_imm(7)
    lo.sta_abs(FAULT_STAGE)
    lo.lda_zp(ZP_SPEED)
    lo.sta_abs(FAULT_SPEED)

    # Mark all banks active
    lo.lda_imm(0)
    lo.sta_zp(ZP_BANK)
    l_s7_act = lo.label("s7_act")
    lo.jsr("ui_mark_bank_active")
    lo.inc_zp(ZP_BANK)
    lo.lda_zp(ZP_BANK)
    lo.cmp_zp(ZP_NUM_BANKS)
    lo.bcc(l_s7_act)

    lo.lda_imm(0)
    lo.sta_zp(ZP_BANK)
    lo.lda_imm(SUB_P0)
    lo.sta_zp(ZP_SUB)
    lo.ldx_imm(0x40)
    lo.ldy_imm(0x41)
    lo.jsr("ui_draw_window")

    # Save ZP_NUM_BANKS in Y-safe Color RAM nibbles, though P0L=0 whenever we
    # aren't inside the 6-instruction P0L window! Still, disable Common RAM
    # ($D506=$00) cleanly during 7A/7B/7C and use CPU registers inside relocated ops.
    lo.lda_zp(ZP_NUM_BANKS)
    lo.sta_zp(ZP_TMP2)        # Bank 0 ZP $1B (accessible whenever P0L=0, CR=Bank 0)
    lo.jsr("enter_test_speed")

    # --- 7A: P0L/P0H relocation to $4080 (bit 6 != bit 7!) & $1880 across banks ---
    for reloc_pg, tag_base in ((0x40, 0x40), (0x18, 0x20)):
        # Pre-stamp target addr (reloc_pg << 8 | $80) AND physical (Bank Y, $0080)
        lo.lda_imm(0x00)
        lo.sta_abs(0xD506)    # Common RAM OFF
        lo.ldy_imm(0)
        l_p0_pre = lo.here_label("p0_pre")
        lo.sty_abs(0xD508)    # P0H = Y (on 8722, Page 0 ALWAYS uses P0H!)
        lo.lda_imm(0x00)
        lo.sta_abs(0xD507)    # P0L = 0 -> $0080 maps to physical (Bank Y, $0080)!
        lo.tya()
        lo.ora_imm(0x70)
        lo.sta_zp(0x80)
        lo.lda_imm(0x00)
        lo.sta_abs(0xD508)
        lo.sta_abs(0xD507)    # Restore P0H=0, P0L=0 before touching Bank 0 ZP!
        lo.lda_absy("lo_cr_io_tbl")
        lo.sta_abs(0xFF00)
        lo.tya()
        lo.ora_imm(tag_base)
        lo.sta_abs((reloc_pg << 8) | 0x80)
        lo.iny()
        lo.ldx_imm(CR_UI)
        lo.stx_abs(0xFF00)    # Bank 0 to read ZP_TMP2
        lo.cpy_zp(ZP_TMP2)
        lo.bcc(l_p0_pre)
        lo.lda_imm(0x04)
        lo.sta_abs(0xD506)

        # Now test relocating ZP ($0080) <-> (Bank Y, reloc_pg:$80) with Common RAM OFF!
        lo.ldy_imm(0)
        l_p0_tst = lo.here_label("p0_tst")
        lo.lda_imm(0x00)
        lo.sta_abs(0xD506)    # Common RAM OFF so P0H selects target bank!
        lo.sty_abs(0xD508)    # P0H = Y (target bank 0..3; written BEFORE P0L!)
        lo.lda_imm(reloc_pg)
        lo.sta_abs(0xD507)    # P0L = reloc_pg ($40 or $18)

        # Zero-page read LDA $80 MUST read from (Bank Y, reloc_pg:$80)!
        lo.lda_zp(0x80)
        lo.tax()              # Save read value in X
        # Write inverted value via zero-page STA $80!
        lo.tya()
        lo.ora_imm(tag_base)
        lo.eor_imm(0xFF)
        lo.sta_zp(0x80)

        # Reverse-swap check while P0L=reloc_pg is active:
        # Switch CR to Bank Y (matching P0H = Y) -> CPU access to (reloc_pg:$80)
        # swaps to physical (Bank Y, $0080) = ($70 | Y)!
        lo.lda_absy("lo_cr_io_tbl")
        lo.sta_abs(0xFF00)
        lo.lda_abs((reloc_pg << 8) | 0x80)
        lo.pha()              # Push swapped read? Wait: Common RAM is OFF, SP is in P1H=0!
        lo.lda_imm(CR_UI)
        lo.sta_abs(0xFF00)
        lo.pla()
        lo.sta_abs(0xD008)    # Save swapped read of reloc_pg:$80 in VIC scratch reg

        # Read back $D507 (P0L register) while I/O is mapped: verifies 8722
        # register latch AND verifies MMU riser GAL 3-states cleanly on reads!
        lo.lda_imm(CR_UI)
        lo.sta_abs(0xFF00)
        lo.lda_abs(0xD507)
        lo.sta_abs(0xD009)

        # Restore P0H=0, P0L=0, RCR=$04 immediately!
        lo.lda_imm(0x00)
        lo.sta_abs(0xD508)
        lo.sta_abs(0xD507)
        lo.lda_imm(0x04)
        lo.sta_abs(0xD506)

        # Check 1: value read via ZP ($80) in X must equal (tag_base | Y)
        lo.tya()
        lo.ora_imm(tag_base)
        lo.sta_zp(ZP_PAT0)
        lo.txa()
        lo.cmp_zp(ZP_PAT0)
        l_p0_ok1 = lo.unique_label("p0_ok1")
        lo.beq(l_p0_ok1)
        lo.sta_abs(FAULT_ACT)
        lo.lda_zp(ZP_PAT0)
        lo.sta_abs(FAULT_EXP)
        lo.lda_imm(0x80)
        lo.sta_abs(FAULT_ADDR_LO)
        lo.lda_imm(reloc_pg)
        lo.sta_abs(FAULT_ADDR_HI)
        lo.sty_abs(FAULT_BANK)
        lo.lda_imm(SUB_P0)
        lo.sta_abs(FAULT_SUB)
        lo.jmp("fatal_halt")
        lo.label(l_p0_ok1)

        # Check 2: physical read of (Bank Y, reloc_pg:$80) must equal ~(tag_base | Y)
        lo.lda_absy("lo_cr_io_tbl")
        lo.sta_abs(0xFF00)
        lo.lda_abs((reloc_pg << 8) | 0x80)
        lo.ldx_imm(CR_UI)
        lo.stx_abs(0xFF00)
        lo.sta_zp(ZP_TMP)
        lo.lda_zp(ZP_PAT0)
        lo.eor_imm(0xFF)
        lo.cmp_zp(ZP_TMP)
        l_p0_ok2 = lo.unique_label("p0_ok2")
        lo.beq(l_p0_ok2)
        lo.lda_zp(ZP_TMP)
        lo.sta_abs(FAULT_ACT)
        lo.lda_zp(ZP_PAT0)
        lo.eor_imm(0xFF)
        lo.sta_abs(FAULT_EXP)
        lo.lda_imm(0x80)
        lo.sta_abs(FAULT_ADDR_LO)
        lo.lda_imm(reloc_pg)
        lo.sta_abs(FAULT_ADDR_HI)
        lo.sty_abs(FAULT_BANK)
        lo.lda_imm(SUB_P0)
        lo.sta_abs(FAULT_SUB)
        lo.jmp("fatal_halt")
        lo.label(l_p0_ok2)

        # Check 3: reverse-swap read stored in $D008 must equal ($70 | Y) if 8722
        # swaps reloc_pg -> (P0H, $00) from Bank 0!
        lo.tya()
        lo.ora_imm(0x70)
        lo.sta_zp(ZP_PAT0)
        lo.lda_abs(0xD008)
        lo.cmp_zp(ZP_PAT0)
        l_p0_ok3 = lo.unique_label("p0_ok3")
        lo.beq(l_p0_ok3)
        lo.sta_abs(FAULT_ACT)
        lo.lda_zp(ZP_PAT0)
        lo.sta_abs(FAULT_EXP)
        lo.lda_imm(0x80)
        lo.sta_abs(FAULT_ADDR_LO)
        lo.lda_imm(reloc_pg)
        lo.sta_abs(FAULT_ADDR_HI)
        lo.sty_abs(FAULT_BANK)
        lo.lda_imm(SUB_P0)
        lo.sta_abs(FAULT_SUB)
        lo.jmp("fatal_halt")
        lo.label(l_p0_ok3)

        # Check 4: $D009 ($D507 P0L register readback) must equal reloc_pg!
        lo.lda_abs(0xD009)
        lo.cmp_imm(reloc_pg)
        l_p0_ok4 = lo.unique_label("p0_ok4")
        lo.beq(l_p0_ok4)
        lo.sta_abs(FAULT_ACT)
        lo.lda_imm(reloc_pg)
        lo.sta_abs(FAULT_EXP)
        lo.lda_imm(0x07)
        lo.sta_abs(FAULT_ADDR_LO)
        lo.lda_imm(0xD5)
        lo.sta_abs(FAULT_ADDR_HI)
        lo.sty_abs(FAULT_BANK)
        lo.lda_imm(SUB_P0)
        lo.sta_abs(FAULT_SUB)
        lo.jmp("fatal_halt")
        lo.label(l_p0_ok4)

        lo.iny()
        lo.cpy_zp(ZP_NUM_BANKS)
        bcc_far(lo, l_p0_tst)

    # --- 7B: P1L/P1H Hardware Stack Relocation ($41F0 across all banks) ---
    lo.ldy_imm(0)
    l_p1_clr = lo.here_label("p1_clr")
    lo.lda_absy("lo_cr_io_tbl")
    lo.sta_abs(0xFF00)
    lo.lda_imm(0x00)
    lo.sta_abs(0x41F0)
    lo.iny()
    lo.cpy_zp(ZP_NUM_BANKS)
    lo.bcc(l_p1_clr)
    lo.lda_imm(CR_UI)
    lo.sta_abs(0xFF00)

    lo.tsx()
    lo.stx_zp(ZP_SAVED_SP)
    lo.ldy_imm(0)
    l_p1_tst = lo.here_label("p1_tst")
    lo.lda_imm(0x00)
    lo.sta_abs(0xD506)        # Common RAM OFF
    lo.ldx_imm(0xF0)
    lo.txs()
    lo.sty_abs(0xD50A)        # P1H = Y (before P1L!)
    lo.lda_imm(0x41)
    lo.sta_abs(0xD509)        # P1L = $41
    lo.tya()
    lo.ora_imm(0x90)
    lo.pha()                  # Hardware stack push -> Bank Y, $41F0!
    lo.lda_imm(0x00)
    lo.pla()                  # Hardware stack pull <- Bank Y, $41F0!
    lo.tax()                  # Save pulled byte in X
    # Restore P1H=0, P1L=1, SP=ZP_SAVED_SP, RCR=$04
    lo.lda_imm(0x00)
    lo.sta_abs(0xD50A)
    lo.lda_imm(0x01)
    lo.sta_abs(0xD509)
    lo.lda_imm(0x04)
    lo.sta_abs(0xD506)
    lo.sty_zp(ZP_BANK)
    lo.stx_zp(ZP_TMP)
    lo.ldx_zp(ZP_SAVED_SP)
    lo.txs()

    lo.tya()
    lo.ora_imm(0x90)
    lo.sta_zp(ZP_PAT0)
    lo.lda_zp(ZP_TMP)
    lo.cmp_zp(ZP_PAT0)
    l_p1_ok1 = lo.unique_label("p1_ok1")
    lo.beq(l_p1_ok1)
    lo.sta_abs(FAULT_ACT)
    lo.lda_zp(ZP_PAT0)
    lo.sta_abs(FAULT_EXP)
    lo.lda_imm(0xF0)
    lo.sta_abs(FAULT_ADDR_LO)
    lo.lda_imm(0x41)
    lo.sta_abs(FAULT_ADDR_HI)
    lo.sty_abs(FAULT_BANK)
    lo.lda_imm(SUB_P1)
    lo.sta_abs(FAULT_SUB)
    lo.jmp("fatal_halt")
    lo.label(l_p1_ok1)

    # Also verify physical $41F0 in Bank Y holds ($90 | Y)
    lo.lda_absy("lo_cr_io_tbl")
    lo.sta_abs(0xFF00)
    lo.lda_abs(0x41F0)
    lo.ldx_imm(CR_UI)
    lo.stx_abs(0xFF00)
    lo.cmp_zp(ZP_PAT0)
    l_p1_ok2 = lo.unique_label("p1_ok2")
    lo.beq(l_p1_ok2)
    lo.sta_abs(FAULT_ACT)
    lo.lda_zp(ZP_PAT0)
    lo.sta_abs(FAULT_EXP)
    lo.lda_imm(0xF0)
    lo.sta_abs(FAULT_ADDR_LO)
    lo.lda_imm(0x41)
    lo.sta_abs(FAULT_ADDR_HI)
    lo.sty_abs(FAULT_BANK)
    lo.lda_imm(SUB_P1)
    lo.sta_abs(FAULT_SUB)
    lo.jmp("fatal_halt")
    lo.label(l_p1_ok2)

    lo.iny()
    lo.cpy_zp(ZP_NUM_BANKS)
    bcc_far(lo, l_p1_tst)

    # --- 7C: RCR ($D506) Common RAM Gating Check ($0380 Bank 0 vs Bank 1) ---
    lo.lda_imm(0x00)
    lo.sta_abs(0xD506)        # Common RAM OFF
    lo.lda_imm(CR_UI)         # Bank 0
    lo.sta_abs(0xFF00)
    lo.lda_imm(0xC0)
    lo.sta_abs(0x0380)
    lo.lda_imm(0x40 | CR_UI)  # Bank 1 (unshared)
    lo.sta_abs(0xFF00)
    lo.lda_imm(0xC1)
    lo.sta_abs(0x0380)
    # Enable 1 KB Bottom Common RAM ($04): Bank 1 read of $0380 MUST see $C0!
    lo.lda_imm(0x04)
    lo.sta_abs(0xD506)
    lo.lda_abs(0x0380)
    lo.ldx_imm(CR_UI)
    lo.stx_abs(0xFF00)
    lo.cmp_imm(0xC0)
    l_rcr_ok1 = lo.unique_label("rcr_ok1")
    lo.beq(l_rcr_ok1)
    lo.sta_abs(FAULT_ACT)
    lo.lda_imm(0xC0)
    lo.sta_abs(FAULT_EXP)
    lo.lda_imm(0x80)
    lo.sta_abs(FAULT_ADDR_LO)
    lo.lda_imm(0x03)
    lo.sta_abs(FAULT_ADDR_HI)
    lo.lda_imm(1)
    lo.sta_abs(FAULT_BANK)
    lo.lda_imm(SUB_RCR)
    lo.sta_abs(FAULT_SUB)
    lo.jmp("fatal_halt")
    lo.label(l_rcr_ok1)

    # And with Common RAM OFF again, Bank 1 $0380 must STILL be $C1!
    lo.lda_imm(0x00)
    lo.sta_abs(0xD506)
    lo.lda_imm(0x40 | CR_UI)
    lo.sta_abs(0xFF00)
    lo.lda_abs(0x0380)
    lo.ldx_imm(CR_UI)
    lo.stx_abs(0xFF00)
    lo.ldx_imm(0x04)
    lo.stx_abs(0xD506)
    lo.cmp_imm(0xC1)
    l_rcr_ok2 = lo.unique_label("rcr_ok2")
    lo.beq(l_rcr_ok2)
    lo.sta_abs(FAULT_ACT)
    lo.lda_imm(0xC1)
    lo.sta_abs(FAULT_EXP)
    lo.lda_imm(0x80)
    lo.sta_abs(FAULT_ADDR_LO)
    lo.lda_imm(0x03)
    lo.sta_abs(FAULT_ADDR_HI)
    lo.lda_imm(1)
    lo.sta_abs(FAULT_BANK)
    lo.lda_imm(SUB_RCR)
    lo.sta_abs(FAULT_SUB)
    lo.jmp("fatal_halt")
    lo.label(l_rcr_ok2)

    # --- 7D: U36 Function ROM Write-Through ($8800 & $C800 across all banks) ---
    lo.lda_imm(0)
    lo.sta_zp(ZP_BANK)
    l_wt_bk = lo.label("wt_bk")
    lo.ldx_zp(ZP_BANK)
    lo.lda_absx("lo_cr_io_tbl")   # (B<<6) | $16 -> U36 ROM mapped at $8000-$FFFF!
    lo.sta_abs(0xFF00)
    lo.lda_zp(ZP_BANK)
    lo.ora_imm(0x60)
    lo.sta_abs(0x8800)            # Write-through under U36 Low!
    lo.lda_zp(ZP_BANK)
    lo.ora_imm(0xA0)
    lo.sta_abs(0xC800)            # Write-through under U36 High!
    lo.lda_imm(CR_UI)
    lo.sta_abs(0xFF00)

    # Verify $8800 in RAM via Upper helper
    lo.jsr("upper_read_8800")
    lo.sta_zp(ZP_TMP)
    lo.lda_zp(ZP_BANK)
    lo.ora_imm(0x60)
    lo.cmp_zp(ZP_TMP)
    l_wt_ok1 = lo.unique_label("wt_ok1")
    lo.beq(l_wt_ok1)
    lo.lda_zp(ZP_TMP)
    lo.sta_abs(FAULT_ACT)
    lo.lda_zp(ZP_BANK)
    lo.ora_imm(0x60)
    lo.sta_abs(FAULT_EXP)
    lo.lda_imm(0x00)
    lo.sta_abs(FAULT_ADDR_LO)
    lo.lda_imm(0x88)
    lo.sta_abs(FAULT_ADDR_HI)
    lo.lda_zp(ZP_BANK)
    lo.sta_abs(FAULT_BANK)
    lo.lda_imm(SUB_ROM)
    lo.sta_abs(FAULT_SUB)
    lo.jmp("fatal_halt")
    lo.label(l_wt_ok1)

    # Verify $C800 in RAM via CR = (B<<6) | $37
    lo.ldx_zp(ZP_BANK)
    lo.lda_absx("lo_cr_ram_tbl")
    lo.sta_abs(0xFF00)
    lo.lda_abs(0xC800)
    lo.ldx_imm(CR_UI)
    lo.stx_abs(0xFF00)
    lo.sta_zp(ZP_TMP)
    lo.lda_zp(ZP_BANK)
    lo.ora_imm(0xA0)
    lo.cmp_zp(ZP_TMP)
    l_wt_ok2 = lo.unique_label("wt_ok2")
    lo.beq(l_wt_ok2)
    lo.lda_zp(ZP_TMP)
    lo.sta_abs(FAULT_ACT)
    lo.lda_zp(ZP_BANK)
    lo.ora_imm(0xA0)
    lo.sta_abs(FAULT_EXP)
    lo.lda_imm(0x00)
    lo.sta_abs(FAULT_ADDR_LO)
    lo.lda_imm(0xC8)
    lo.sta_abs(FAULT_ADDR_HI)
    lo.lda_zp(ZP_BANK)
    lo.sta_abs(FAULT_BANK)
    lo.lda_imm(SUB_ROM)
    lo.sta_abs(FAULT_SUB)
    lo.jmp("fatal_halt")
    lo.label(l_wt_ok2)

    lo.inc_zp(ZP_BANK)
    lo.lda_zp(ZP_BANK)
    lo.cmp_zp(ZP_NUM_BANKS)
    bcc_far(lo, l_wt_bk)

    # Mark Stage 7 done on all banks!
    lo.jsr("ui_begin")
    lo.lda_imm(0)
    lo.sta_zp(ZP_BANK)
    l_s7_dn = lo.label("s7_dn")
    lo.jsr("ui_mark_bank_done")
    lo.inc_zp(ZP_BANK)
    lo.lda_zp(ZP_BANK)
    lo.cmp_zp(ZP_NUM_BANKS)
    lo.bcc(l_s7_dn)
    lo.rts()

    # =========================================================================
    # STAGE 2: REGISTER-ONLY ZERO-RAM MARCH-C- ($0002-$07FF + $FF05-$FFF7)
    # Uses ONLY A, X, Y! Y = (bank << 1) | pat_idx (0..3 for 128K, 0..7 for 256K).
    # Common RAM is OFF ($D506 = $00). Any mismatch hits STA $FF01 -> $D000-$D006!
    # =========================================================================
    stg2_pages = [
        (0x00, 0x02, 0xFF),  # Page 0: $0002-$00FF (skips 8502 internal port $00/$01)
        (0x01, 0x00, 0xFF),  # Page 1: $0100-$01FF (Hardware Stack)
        (0x02, 0x00, 0xFF),  # Page 2: $0200-$02FF
        (0x03, 0x00, 0xFF),  # Page 3: $0300-$03FF
        (0x04, 0x00, 0xFF),  # Page 4: $0400-$04FF (Screen RAM)
        (0x05, 0x00, 0xFF),  # Page 5: $0500-$05FF
        (0x06, 0x00, 0xFF),  # Page 6: $0600-$06FF
        (0x07, 0x00, 0xFF),  # Page 7: $0700-$07FF
        (0xFF, 0x05, 0xF7),  # Page $FF: $FF05-$FFF7 (skips MMU $FF00-$FF04 & NMI stub)
    ]

    lo.label("stage2_entry")
    lo.lda_imm(0x00)
    lo.sta_abs(0xD506)        # Common RAM OFF!
    lo.ldy_imm(0x00)          # Y = 0 .. (num_banks*2 - 1)

    l_s2_loop = lo.label("s2_loop")
    # On MOS 8722, Page 0 & Page 1 ALWAYS route to P0H & P1H (not CR bank)!
    # Set P0H = P1H = (Y >> 1) while CR_UI ($16, I/O ON) is active so Stage 2
    # genuinely March-tests physical $0002-$01FF of EVERY bank 0..3!
    lo.lda_imm(0x00)
    lo.sta_abs(0xD506)        # Common RAM OFF!
    lo.tya()
    lo.lsr_a()
    lo.sta_abs(0xD508)        # P0H = bank
    lo.sta_abs(0xD50A)        # P1H = bank
    lo.lda_imm(0x00)
    lo.sta_abs(0xD507)        # P0L = $00
    lo.lda_imm(0x01)
    lo.sta_abs(0xD509)        # P1L = $01
    lo.lda_absy("stg2_cr_tbl")
    lo.sta_abs(0xFF00)        # Map target Bank + RAM ($37/$77/$B7/$F7)

    def emit_stg2_fail(pg_hi: int, sub_id: int, exp_tbl: str):
        lo.sta_abs(0xFF01)    # Loads PCRA ($16 = CR_UI) WITHOUT clobbering A, X, Y!
        lo.sta_abs(FAULT_ACT)
        lo.stx_abs(FAULT_ADDR_LO)
        lo.lda_absy(exp_tbl)
        lo.sta_abs(FAULT_EXP)
        lo.lda_imm(pg_hi)
        lo.sta_abs(FAULT_ADDR_HI)
        lo.tya()
        lo.lsr_a()
        lo.sta_abs(FAULT_BANK)
        lo.lda_imm(sub_id)
        lo.sta_abs(FAULT_SUB)
        lo.jmp("fatal_halt")

    # M0: Ascending w(B0)
    lo.lda_absy("stg2_p0_tbl")
    for pg, x_lo, x_hi in stg2_pages:
        base = pg << 8
        lo.ldx_imm(x_lo)
        l_lp = lo.here_label(f"s2_m0_{pg:02x}")
        lo.sta_absx(base)
        if x_hi == 0xFF:
            lo.inx()
            lo.bne(l_lp)
        else:
            lo.inx()
            lo.cpx_imm(x_hi + 1)
            lo.bne(l_lp)

    # M1 (up r0,w1) and M2 (up r1,w0)
    for sub_id, exp_tbl, wr_tbl in (
        (SUB_M1, "stg2_p0_tbl", "stg2_p1_tbl"),
        (SUB_M2, "stg2_p1_tbl", "stg2_p0_tbl"),
    ):
        for pg, x_lo, x_hi in stg2_pages:
            base = pg << 8
            lo.ldx_imm(x_lo)
            l_lp = lo.here_label(f"s2_u{sub_id}_{pg:02x}")
            lo.lda_absx(base)
            lo.cmp_absy(exp_tbl)
            l_ok = lo.unique_label("s2_ok")
            lo.beq(l_ok)
            emit_stg2_fail(pg, sub_id, exp_tbl)
            lo.label(l_ok)
            lo.lda_absy(wr_tbl)
            lo.sta_absx(base)
            if x_hi == 0xFF:
                lo.inx()
                lo.bne(l_lp)
            else:
                lo.inx()
                lo.cpx_imm(x_hi + 1)
                lo.bne(l_lp)

    # M3 (down r0,w1) and M4 (down r1,w0)
    for sub_id, exp_tbl, wr_tbl in (
        (SUB_M3, "stg2_p0_tbl", "stg2_p1_tbl"),
        (SUB_M4, "stg2_p1_tbl", "stg2_p0_tbl"),
    ):
        for pg, x_lo, x_hi in reversed(stg2_pages):
            base = pg << 8
            lo.ldx_imm(x_hi)
            l_lp = lo.here_label(f"s2_d{sub_id}_{pg:02x}")
            lo.lda_absx(base)
            lo.cmp_absy(exp_tbl)
            l_ok = lo.unique_label("s2_ok")
            lo.beq(l_ok)
            emit_stg2_fail(pg, sub_id, exp_tbl)
            lo.label(l_ok)
            lo.lda_absy(wr_tbl)
            lo.sta_absx(base)
            lo.dex()
            lo.cpx_imm((x_lo - 1) & 0xFF)
            lo.bne(l_lp)

    # M5: Ascending r(B0)
    for pg, x_lo, x_hi in stg2_pages:
        base = pg << 8
        lo.ldx_imm(x_lo)
        l_lp = lo.here_label(f"s2_m5_{pg:02x}")
        lo.lda_absx(base)
        lo.cmp_absy("stg2_p0_tbl")
        l_ok = lo.unique_label("s2_ok")
        lo.beq(l_ok)
        emit_stg2_fail(pg, SUB_M5, "stg2_p0_tbl")
        lo.label(l_ok)
        if x_hi == 0xFF:
            lo.inx()
            lo.bne(l_lp)
        else:
            lo.inx()
            lo.cpx_imm(x_hi + 1)
            lo.bne(l_lp)

    # Advance Y and compare against (num_banks * 2) read from Color RAM $DBE8!
    lo.sta_abs(0xFF01)        # Restore CR_UI ($16) so $DBE8 Color RAM is readable!
    lo.iny()
    lo.lda_abs(SAVE_NIB_BASE) # Low nibble of ZP_NUM_BANKS (2 or 4)
    lo.and_imm(0x0F)
    lo.asl_a()                # * 2 -> 4 or 8
    lo.sta_abs(0xD008)        # Scratch VIC M4X sprite reg (zero RAM!)
    lo.cpy_abs(0xD008)
    bne_far(lo, l_s2_loop)
    lo.jmp("stage2_return")

    # =========================================================================
    # ZERO-RAM FATAL FAULT HANDLER (reads $D000-$D007 ONLY; needs NO ZP or Stack!)
    # =========================================================================
    lo.label("fatal_halt")
    lo.sei()
    lo.lda_imm(CR_UI)
    lo.sta_abs(0xFF00)
    lo.lda_imm(0x04)
    lo.sta_abs(0xD506)
    lo.lda_imm(0x00)
    lo.sta_abs(0xD508)
    lo.sta_abs(0xD507)
    lo.sta_abs(0xD50A)
    lo.lda_imm(0x01)
    lo.sta_abs(0xD509)
    lo.lda_imm(0x00)
    lo.sta_abs(0xD030)        # Force 1 MHz so VIC-II displays steadily!
    lo.lda_imm(0x1B)
    lo.sta_abs(0xD011)
    lo.lda_imm(0x02)          # RED BORDER!
    lo.sta_abs(0xD020)

    # Redraw clean template if Stage 2 left $0400-$07FF filled with pattern!
    # Check if top-left banner char at $0402 is 'C' ($83 reverse): if not, redraw!
    lo.lda_abs(SCR_BASE + 2)
    lo.cmp_imm(ascii_to_screencode("C", reverse=True))
    l_scr_intact = lo.unique_label("scr_intact")
    lo.beq(l_scr_intact)
    # Inline 4-page template copy (no JSR/stack needed!)
    for pg_i in range(4):
        lo.ldx_imm(0)
        l_cp = lo.here_label("fh_cp")
        lo.lda_absx(f"tpl_scr_{pg_i}")
        lo.sta_absx(SCR_BASE + pg_i * 256)
        lo.lda_absx(f"tpl_col_{pg_i}")
        lo.sta_absx(COL_BASE + pg_i * 256)
        lo.inx()
        if pg_i < 3:
            lo.bne(l_cp)
        else:
            lo.cpx_imm(232)
            lo.bne(l_cp)
    lo.label(l_scr_intact)

    # Row 3 col 25..39: "** FAULT HALT *" in reverse red/yellow
    lo.ldx_imm(0)
    l_fh_st = lo.here_label("fh_st")
    lo.lda_absx("str_fault_status")
    lo.sta_absx(scr_pos(3, 25))
    lo.lda_imm(0x07)          # yellow
    lo.sta_absx(col_pos(3, 25))
    lo.inx()
    lo.cpx_imm(15)
    lo.bne(l_fh_st)

    # Row 6: STAGE title for FAULT_STAGE ($D005), 32-byte padded table
    lo.lda_abs(FAULT_STAGE)
    lo.and_imm(0x07)
    lo.asl_a()
    lo.asl_a()
    lo.asl_a()
    lo.asl_a()
    lo.asl_a()                # A = stage * 32 (0..224 fits in u8!)
    lo.tax()
    lo.ldy_imm(0)
    l_fh_stg = lo.here_label("fh_stg")
    lo.lda_absx("stage_title_tbl32")
    lo.sta_absy(scr_pos(6, 9))
    lo.inx()
    lo.iny()
    lo.cpy_imm(30)
    lo.bne(l_fh_stg)

    # Row 16: "RESULT : ** MEMORY FAULT DETECTED **    "
    # Row 17: "WHERE  : BANK B  ADDR $XXXX  AT X MHZ   "
    # Row 18: "DATA   : EXP $XX  ACT $XX    STEP XXX   "
    # Row 19: "BITS   : XOR $XX  [76543210: ........]  "
    for r_idx, (lbl_name, col_c) in enumerate((
        ("str_err_r16", 0x07),
        ("str_err_r17", 0x01),
        ("str_err_r18", 0x01),
        ("str_err_r19", 0x07),
    )):
        row_n = 16 + r_idx
        lo.ldx_imm(0)
        l_er = lo.here_label("fh_er")
        lo.lda_absx(lbl_name)
        lo.sta_absx(scr_pos(row_n, 0))
        lo.lda_imm(col_c)
        lo.sta_absx(col_pos(row_n, 0))
        lo.inx()
        lo.cpx_imm(40)
        lo.bne(l_er)

    # Fill Row 17 dynamic fields: BANK (col 14), ADDR (col 23..26), SPEED (col 32)
    lo.lda_abs(FAULT_BANK)
    lo.and_imm(0x03)
    lo.ora_imm(0x30)
    lo.sta_abs(scr_pos(17, 14))

    def emit_hex_inline(src_reg: int, row: int, col: int):
        lo.lda_abs(src_reg)
        lo.lsr_a()
        lo.lsr_a()
        lo.lsr_a()
        lo.lsr_a()
        lo.tax()
        lo.lda_absx("hex_scr_tbl")
        lo.sta_abs(scr_pos(row, col))
        lo.lda_abs(src_reg)
        lo.and_imm(0x0F)
        lo.tax()
        lo.lda_absx("hex_scr_tbl")
        lo.sta_abs(scr_pos(row, col + 1))

    emit_hex_inline(FAULT_ADDR_HI, 17, 23)
    emit_hex_inline(FAULT_ADDR_LO, 17, 25)

    lo.lda_abs(FAULT_SPEED)
    lo.and_imm(0x01)
    lo.clc()
    lo.adc_imm(0x31)          # '1' or '2'
    lo.sta_abs(scr_pos(17, 32))

    # Fill Row 18 dynamic fields: EXP (col 14..15), ACT (col 23..24), STEP (col 34..36)
    emit_hex_inline(FAULT_EXP, 18, 14)
    emit_hex_inline(FAULT_ACT, 18, 23)

    lo.lda_abs(FAULT_SUB)
    lo.asl_a()
    lo.clc()
    lo.adc_abs(FAULT_SUB)     # * 3
    lo.tax()
    for k in range(3):
        lo.lda_absx("step_names_tbl")
        lo.sta_abs(scr_pos(18, 34 + k))
        if k < 2:
            lo.inx()

    # Fill Row 19 dynamic fields: XOR = EXP ^ ACT (stored in $D008), plus [76543210]
    lo.lda_abs(FAULT_EXP)
    lo.eor_abs(FAULT_ACT)
    lo.sta_abs(0xD008)
    emit_hex_inline(0xD008, 19, 14)

    # Decode individual bits 7..0 at Row 19 col 29..36
    for bit in range(8):
        col_k = 29 + (7 - bit)
        lo.lda_abs(0xD008)
        lo.and_imm(1 << bit)
        l_zero = lo.unique_label("bit_z")
        lo.beq(l_zero)
        lo.lda_imm(ascii_to_screencode(str(bit), reverse=True))
        lo.sta_abs(scr_pos(19, col_k))
        lo.lda_imm(0x0A)      # light red highlight on bad bit!
        lo.sta_abs(col_pos(19, col_k))
        l_done_b = lo.unique_label("bit_d")
        lo.jmp(l_done_b)
        lo.label(l_zero)
        lo.lda_imm(ascii_to_screencode("."))
        lo.sta_abs(scr_pos(19, col_k))
        lo.label(l_done_b)

    lo.label("fatal_spin")
    lo.jmp("fatal_spin")

    # =========================================================================
    # UI & UTILITY SUBROUTINES (Lower ROM)
    # =========================================================================
    lo.label("ui_begin")
    lo.lda_imm(CR_UI)
    lo.sta_abs(0xFF00)
    lo.lda_imm(0x00)
    lo.sta_abs(0xD030)
    lo.rts()

    lo.label("enter_test_speed")
    lo.lda_imm(CR_UI)
    lo.sta_abs(0xFF00)
    lo.lda_zp(ZP_STAGE)
    lo.sta_abs(FAULT_STAGE)
    lo.lda_zp(ZP_SPEED)
    lo.sta_abs(FAULT_SPEED)
    lo.sta_abs(0xD030)
    lo.rts()

    lo.label("draw_full_template_no_zp")
    for pg_i in range(4):
        lo.ldx_imm(0)
        l_cp = lo.here_label("tpl_cp")
        lo.lda_absx(f"tpl_scr_{pg_i}")
        lo.sta_absx(SCR_BASE + pg_i * 256)
        lo.lda_absx(f"tpl_col_{pg_i}")
        lo.sta_absx(COL_BASE + pg_i * 256)
        lo.inx()
        if pg_i < 3:
            lo.bne(l_cp)
        else:
            lo.cpx_imm(232)
            lo.bne(l_cp)
    lo.rts()

    lo.label("install_nmi_stubs")
    lo.ldy_imm(0)
    l_nmi_bk = lo.label("nmi_bk")
    lo.lda_absy("lo_cr_ram_tbl")
    lo.sta_abs(0xFF00)
    lo.lda_imm(0x40)          # RTI at $FFF8
    lo.sta_abs(0xFFF8)
    lo.lda_imm(0xF8)
    lo.sta_abs(0xFFFA)
    lo.sta_abs(0xFFFC)
    lo.sta_abs(0xFFFE)
    lo.lda_imm(0xFF)
    lo.sta_abs(0xFFFB)
    lo.sta_abs(0xFFFD)
    lo.sta_abs(0xFFFF)
    lo.iny()
    lo.cpy_zp(ZP_NUM_BANKS)
    lo.bcc(l_nmi_bk)
    lo.lda_imm(CR_UI)
    lo.sta_abs(0xFF00)
    lo.rts()

    lo.label("save_persist_to_colram")
    lo.ldx_imm(0)
    lo.ldy_imm(0)
    l_sp_lp = lo.label("sp_lp")
    lo.lda_zpx(ZP_PERSIST_START)
    lo.sta_absy(SAVE_NIB_BASE)
    lo.lsr_a()
    lo.lsr_a()
    lo.lsr_a()
    lo.lsr_a()
    lo.iny()
    lo.sta_absy(SAVE_NIB_BASE)
    lo.iny()
    lo.inx()
    lo.cpx_imm(ZP_PERSIST_COUNT)
    lo.bne(l_sp_lp)
    lo.rts()

    lo.label("restore_persist_from_colram")
    lo.ldx_imm(0)
    lo.ldy_imm(0)
    l_rp_lp = lo.label("rp_lp")
    lo.lda_absy(SAVE_NIB_BASE)
    lo.and_imm(0x0F)
    lo.sta_zp(ZP_TMP)
    lo.iny()
    lo.lda_absy(SAVE_NIB_BASE)
    lo.and_imm(0x0F)
    lo.asl_a()
    lo.asl_a()
    lo.asl_a()
    lo.asl_a()
    lo.ora_zp(ZP_TMP)
    lo.sta_zpx(ZP_PERSIST_START)
    lo.iny()
    lo.inx()
    lo.cpx_imm(ZP_PERSIST_COUNT)
    lo.bne(l_rp_lp)
    lo.rts()

    lo.label("inc_pass_counter")
    lo.ldx_imm(0)             # ZP_D0..ZP_D3
    l_inc_d = lo.label("inc_d")
    lo.lda_zpx(ZP_D0)
    lo.clc()
    lo.adc_imm(1)
    lo.cmp_imm(10)
    l_no_cy = lo.unique_label("no_cy")
    lo.bcc(l_no_cy)
    lo.lda_imm(0)
    lo.sta_zpx(ZP_D0)
    lo.inx()
    lo.cpx_imm(4)
    lo.bcc(l_inc_d)
    lo.rts()
    lo.label(l_no_cy)
    lo.sta_zpx(ZP_D0)
    lo.rts()

    lo.label("ui_refresh_header")
    lo.jsr("ui_begin")
    # CONFIG line (Row 2 col 9..38)
    lo.ldx_imm(0)
    lo.lda_zp(ZP_NUM_BANKS)
    lo.cmp_imm(4)
    l_cfg_256 = lo.unique_label("cfg_256")
    lo.beq(l_cfg_256)
    l_cfg_128_lp = lo.here_label("cfg128")
    lo.lda_absx("str_cfg_128")
    lo.sta_absx(scr_pos(2, 9))
    lo.inx()
    lo.cpx_imm(30)
    lo.bne(l_cfg_128_lp)
    lo.jmp("cfg_done")
    lo.label(l_cfg_256)
    l_cfg_256_lp = lo.here_label("cfg256")
    lo.lda_absx("str_cfg_256")
    lo.sta_absx(scr_pos(2, 9))
    lo.inx()
    lo.cpx_imm(30)
    lo.bne(l_cfg_256_lp)
    # Light up Bank 2/3 row in green for 256K!
    lo.ldx_imm(0)
    l_b23_col = lo.here_label("b23_col")
    lo.lda_imm(0x0D)
    lo.sta_absx(col_pos(10, 0))
    lo.inx()
    lo.cpx_imm(40)
    lo.bne(l_b23_col)
    lo.label("cfg_done")

    # MMU ID ($D50B hex at Row 3 col 10..11)
    lo.lda_zp(ZP_MMU_VER)
    lo.lsr_a()
    lo.lsr_a()
    lo.lsr_a()
    lo.lsr_a()
    lo.tax()
    lo.lda_absx("hex_scr_tbl")
    lo.sta_abs(scr_pos(3, 10))
    lo.lda_zp(ZP_MMU_VER)
    lo.and_imm(0x0F)
    lo.tax()
    lo.lda_absx("hex_scr_tbl")
    lo.sta_abs(scr_pos(3, 11))

    # STATUS + Border color + Result line
    lo.lda_zp(ZP_GREEN)
    l_not_grn = lo.unique_label("not_grn")
    lo.beq(l_not_grn)
    lo.lda_imm(0x05)          # GREEN BORDER!
    lo.sta_abs(0xD020)
    lo.ldx_imm(0)
    l_st_grn = lo.here_label("st_grn")
    lo.lda_absx("str_status_burnin")
    lo.sta_absx(scr_pos(3, 25))
    lo.lda_imm(0x0D)
    lo.sta_absx(col_pos(3, 25))
    lo.inx()
    lo.cpx_imm(15)
    lo.bne(l_st_grn)
    lo.ldx_imm(0)
    l_res_grn = lo.here_label("res_grn")
    lo.lda_absx("str_res_pass")
    lo.sta_absx(scr_pos(16, 9))
    lo.inx()
    lo.cpx_imm(31)
    lo.bne(l_res_grn)
    lo.jmp("st_done")

    lo.label(l_not_grn)
    lo.ldx_imm(0)
    l_st_run = lo.here_label("st_run")
    lo.lda_absx("str_status_testing")
    lo.sta_absx(scr_pos(3, 25))
    lo.inx()
    lo.cpx_imm(15)
    lo.bne(l_st_run)
    lo.label("st_done")

    # PASSES 4-digit counter at Row 4 col 9..12
    lo.lda_zp(ZP_D3)
    lo.ora_imm(0x30)
    lo.sta_abs(scr_pos(4, 9))
    lo.lda_zp(ZP_D2)
    lo.ora_imm(0x30)
    lo.sta_abs(scr_pos(4, 10))
    lo.lda_zp(ZP_D1)
    lo.ora_imm(0x30)
    lo.sta_abs(scr_pos(4, 11))
    lo.lda_zp(ZP_D0)
    lo.ora_imm(0x30)
    lo.sta_abs(scr_pos(4, 12))

    # SPEED at Row 4 col 25..38
    lo.ldx_imm(0)
    lo.lda_zp(ZP_SPEED)
    l_sp_fast = lo.unique_label("sp_fast")
    lo.bne(l_sp_fast)
    l_sp_s = lo.here_label("sp_s")
    lo.lda_absx("str_spd_1m")
    lo.sta_absx(scr_pos(4, 25))
    lo.inx()
    lo.cpx_imm(14)
    lo.bne(l_sp_s)
    lo.rts()
    lo.label(l_sp_fast)
    l_sp_f = lo.here_label("sp_f")
    lo.lda_absx("str_spd_2m")
    lo.sta_absx(scr_pos(4, 25))
    lo.inx()
    lo.cpx_imm(14)
    lo.bne(l_sp_f)
    lo.rts()

    lo.label("ui_set_speed_and_reset_grid")
    lo.jsr("ui_refresh_header")
    # Reset [.......] for Bank 0 and Bank 1
    lo.ldx_imm(0)
    l_rg01 = lo.here_label("rg01")
    lo.lda_imm(ascii_to_screencode("."))
    lo.sta_absx(scr_pos(9, 10))
    lo.sta_absx(scr_pos(9, 32))
    lo.inx()
    lo.cpx_imm(7)
    lo.bne(l_rg01)
    lo.lda_zp(ZP_NUM_BANKS)
    lo.cmp_imm(4)
    l_rg_done = lo.unique_label("rg_done")
    lo.bne(l_rg_done)
    lo.ldx_imm(0)
    l_rg23 = lo.here_label("rg23")
    lo.lda_imm(ascii_to_screencode("."))
    lo.sta_absx(scr_pos(10, 10))
    lo.sta_absx(scr_pos(10, 32))
    lo.inx()
    lo.cpx_imm(7)
    lo.bne(l_rg23)
    lo.label(l_rg_done)
    lo.rts()

    lo.label("ui_restore_grid_after_stage2")
    lo.jsr("ui_set_speed_and_reset_grid")
    # Stages 1 and 2 are now '+' on all active banks!
    lo.lda_imm(ascii_to_screencode("+"))
    lo.sta_abs(scr_pos(9, 10))
    lo.sta_abs(scr_pos(9, 11))
    lo.sta_abs(scr_pos(9, 32))
    lo.sta_abs(scr_pos(9, 33))
    lo.lda_zp(ZP_NUM_BANKS)
    lo.cmp_imm(4)
    l_rgs2_dn = lo.unique_label("rgs2_dn")
    lo.bne(l_rgs2_dn)
    lo.lda_imm(ascii_to_screencode("+"))
    lo.sta_abs(scr_pos(10, 10))
    lo.sta_abs(scr_pos(10, 11))
    lo.sta_abs(scr_pos(10, 32))
    lo.sta_abs(scr_pos(10, 33))
    lo.label(l_rgs2_dn)
    lo.rts()

    # Mark current (ZP_BANK, ZP_STAGE 1..7) as '*' or '+'
    lo.label("ui_mark_bank_active")
    lo.lda_imm(ascii_to_screencode("*"))
    lo.jmp("ui_put_grid_char")
    lo.label("ui_mark_bank_done")
    lo.lda_imm(ascii_to_screencode("+"))
    lo.label("ui_put_grid_char")
    lo.sta_zp(ZP_TMP)
    lo.ldx_zp(ZP_BANK)
    lo.lda_absx("grid_base_lo")
    lo.clc()
    lo.adc_zp(ZP_STAGE)       # base points 1 before slot 1
    lo.sta_zp(ZP_PTR)
    lo.lda_absx("grid_base_hi")
    lo.adc_imm(0)
    lo.sta_zp(ZP_PTR + 1)
    lo.ldy_imm(0)
    lo.lda_zp(ZP_TMP)
    lo.sta_indy(ZP_PTR)
    lo.rts()

    lo.label("ui_draw_stage")
    lo.jsr("ui_begin")
    lo.lda_zp(ZP_STAGE)
    lo.and_imm(0x07)
    lo.asl_a()
    lo.asl_a()
    lo.asl_a()
    lo.asl_a()
    lo.asl_a()                # * 32
    lo.tax()
    lo.ldy_imm(0)
    l_ds_lp = lo.here_label("ds_lp")
    lo.lda_absx("stage_title_tbl32")
    lo.sta_absy(scr_pos(6, 9))
    lo.inx()
    lo.iny()
    lo.cpy_imm(30)
    lo.bne(l_ds_lp)
    lo.rts()

    # X = start_hi, Y = end_hi
    lo.label("ui_draw_window")
    lo.stx_zp(ZP_TMP)
    lo.sty_zp(ZP_TMP2)
    lo.jsr("ui_begin")
    lo.lda_zp(ZP_BANK)
    lo.ora_imm(0x30)
    lo.sta_abs(scr_pos(7, 14))
    # Start page hex + "00"
    lo.lda_zp(ZP_TMP)
    lo.lsr_a()
    lo.lsr_a()
    lo.lsr_a()
    lo.lsr_a()
    lo.tax()
    lo.lda_absx("hex_scr_tbl")
    lo.sta_abs(scr_pos(7, 18))
    lo.lda_zp(ZP_TMP)
    lo.and_imm(0x0F)
    lo.tax()
    lo.lda_absx("hex_scr_tbl")
    lo.sta_abs(scr_pos(7, 19))
    lo.lda_imm(0x30)
    lo.sta_abs(scr_pos(7, 20))
    lo.sta_abs(scr_pos(7, 21))
    # End page hex + "FF"
    lo.lda_zp(ZP_TMP2)
    lo.lsr_a()
    lo.lsr_a()
    lo.lsr_a()
    lo.lsr_a()
    lo.tax()
    lo.lda_absx("hex_scr_tbl")
    lo.sta_abs(scr_pos(7, 25))
    lo.lda_zp(ZP_TMP2)
    lo.and_imm(0x0F)
    lo.tax()
    lo.lda_absx("hex_scr_tbl")
    lo.sta_abs(scr_pos(7, 26))
    lo.lda_imm(ascii_to_screencode("F"))
    lo.sta_abs(scr_pos(7, 27))
    lo.sta_abs(scr_pos(7, 28))
    lo.jsr("ui_draw_step")
    lo.rts()

    lo.label("ui_draw_step")
    lo.lda_zp(ZP_SUB)
    lo.asl_a()
    lo.clc()
    lo.adc_zp(ZP_SUB)
    lo.tax()
    lo.lda_absx("step_names_tbl")
    lo.sta_abs(scr_pos(7, 37))
    lo.inx()
    lo.lda_absx("step_names_tbl")
    lo.sta_abs(scr_pos(7, 38))
    lo.inx()
    lo.lda_absx("step_names_tbl")
    lo.sta_abs(scr_pos(7, 39))
    lo.rts()

    # =========================================================================
    # CONSTANT TABLES & SCREEN TEMPLATES (Lower ROM)
    # =========================================================================
    lo.label("lo_cr_ram_tbl")
    lo.emit(0x37, 0x77, 0xB7, 0xF7)
    lo.label("lo_cr_io_tbl")
    lo.emit(0x16, 0x56, 0x96, 0xD6)

    lo.label("stg2_cr_tbl")
    lo.emit(0x37, 0x37, 0x77, 0x77, 0xB7, 0xB7, 0xF7, 0xF7)
    lo.label("stg2_p0_tbl")
    lo.emit(0x00, 0x55, 0x00, 0x55, 0x00, 0x55, 0x00, 0x55)
    lo.label("stg2_p1_tbl")
    lo.emit(0xFF, 0xAA, 0xFF, 0xAA, 0xFF, 0xAA, 0xFF, 0xAA)

    lo.label("hex_scr_tbl")
    lo.emit_scr("0123456789ABCDEF")

    lo.label("step_names_tbl")
    for nm in STEP_NAMES:
        assert len(nm) == 3
        lo.emit_scr(nm)

    lo.label("stage_title_tbl32")
    for st in STAGE_TITLES:
        lo.emit_scr(st + "  ")  # 32 bytes per entry

    grid_bases = [
        scr_pos(9, 9),    # Bank 0: slot 1 at +1 = col 10
        scr_pos(9, 31),   # Bank 1: slot 1 at +1 = col 32
        scr_pos(10, 9),   # Bank 2: slot 1 at +1 = col 10
        scr_pos(10, 31),  # Bank 3: slot 1 at +1 = col 32
    ]
    lo.label("grid_base_lo")
    lo.emit(*(g & 0xFF for g in grid_bases))
    lo.label("grid_base_hi")
    lo.emit(*((g >> 8) & 0xFF for g in grid_bases))

    lo.label("str_cfg_128")
    lo.emit_scr("128 KB (BANKS 0-1, SOLO)      ")
    lo.label("str_cfg_256")
    lo.emit_scr("256 KB (BANKS 0-3, MMU RISER) ")
    lo.label("str_status_testing")
    lo.emit_scr("TESTING...     ")
    lo.label("str_status_burnin")
    lo.emit_scr("PASS + BURN-IN ")
    lo.label("str_fault_status")
    lo.emit_scr("* FAULT HALT * ", reverse=True)
    lo.label("str_spd_1m")
    lo.emit_scr("1 MHZ (SLOW)  ")
    lo.label("str_spd_2m")
    lo.emit_scr("2 MHZ (FAST)  ")
    lo.label("str_res_pass")
    lo.emit_scr("ALL 1M+2M TESTS PASSED (0 ERR) ")

    lo.label("str_err_r16")
    lo.emit_scr("RESULT : ** MEMORY FAULT DETECTED **    ")
    lo.label("str_err_r17")
    lo.emit_scr("WHERE  : BANK 0  ADDR $0000  AT 1 MHZ   ")
    lo.label("str_err_r18")
    lo.emit_scr("DATA   : EXP $00  ACT $00    STEP DET   ")
    lo.label("str_err_r19")
    lo.emit_scr("BITS   : XOR $00  [76543210: ........]  ")

    lo.label("lo_bank_salt")
    lo.emit(0x00, 0x6B, 0xB5, 0xDE)
    lo.label("lo_scramble_tbl")
    lo.emit_bytes(make_scramble_table())

    # Build 1000-byte screen + color templates
    full_scr = bytearray()
    full_col = bytearray()
    for txt, rev, color in SCREEN_ROWS:
        full_scr.extend(scr_bytes(txt, reverse=rev))
        full_col.extend([color] * 40)
    assert len(full_scr) == 1000 and len(full_col) == 1000

    for pg_i in range(4):
        s_chunk = full_scr[pg_i * 256 : min((pg_i + 1) * 256, 1000)]
        c_chunk = full_col[pg_i * 256 : min((pg_i + 1) * 256, 1000)]
        lo.label(f"tpl_scr_{pg_i}")
        lo.emit_bytes(s_chunk)
        lo.label(f"tpl_col_{pg_i}")
        lo.emit_bytes(c_chunk)

    # Link cross-referenced symbols between Lower ($8000) and Upper ($C000) halves
    all_syms = dict(lo.labels)
    for k, v in hi.labels.items():
        if k in all_syms:
            raise ValueError(f"Duplicate symbol across halves: {k}")
        all_syms[k] = v

    lo_bin = lo.link(all_syms)
    hi_bin = hi.link(all_syms)
    rom32 = lo_bin.ljust(0x4000, b"\xFF") + hi_bin.ljust(0x4000, b"\xFF")
    assert len(rom32) == 32768
    return rom32, all_syms


def main() -> int:
    rom32, syms = build_rom()
    fab_dir = os.path.join(HERE, "fab")
    os.makedirs(fab_dir, exist_ok=True)
    out_u36 = os.path.join(fab_dir, "c128-memtest-u36.bin")
    with open(out_u36, "wb") as f:
        f.write(rom32)
    lo_used = max(a for a in syms.values() if 0x8000 <= a < 0xC000) - 0x8000
    print(f"Wrote {out_u36} ({len(rom32)} bytes; lower code+data ~{lo_used + 256} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
