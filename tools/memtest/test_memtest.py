#!/usr/bin/env python3
"""Automated hardware-fault-injection tests for c128-memtest-u36.bin.

Simulates the MOS 8502 CPU, MOS 8722 MMU (128 KB solo and 256 KB dual-MMU
riser), static Color RAM ($D800-$DBFF), VIC-IIe registers ($D000-$D030), and
U36 Internal Function ROM ($8000-$FFFF) to verify both clean execution and
exact screen diagnostics across 10 hardware fault scenarios.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from build import (
    COL_BASE,
    FAULT_ACT,
    FAULT_ADDR_HI,
    FAULT_ADDR_LO,
    FAULT_BANK,
    FAULT_EXP,
    FAULT_SPEED,
    FAULT_STAGE,
    FAULT_SUB,
    SCR_BASE,
    ZP_GREEN,
    ZP_NUM_BANKS,
    ZP_SPEED,
    ZP_STAGE,
    build_rom,
)


def screencode_to_ascii(sc: int) -> str:
    c = sc & 0x7F
    if c == 0x00:
        return "@"
    if 0x01 <= c <= 0x1A:
        return chr(0x40 + c)
    if c == 0x1B:
        return "["
    if c == 0x1D:
        return "]"
    if 0x20 <= c <= 0x3F:
        return chr(c)
    if c == 0x5D:
        return "|"
    return "?"


class C128Sim:
    """Cycle-functional MOS 8502 + MOS 8722 MMU simulator for U36 ROM testing."""

    def __init__(self, rom32: bytes, num_banks: int = 2, break_p0h_bank2: bool = False):
        assert len(rom32) == 32768
        assert num_banks in (2, 4)
        self.rom = rom32
        self.num_banks = num_banks
        self.break_p0h_bank2 = break_p0h_bank2

        self.ram = [bytearray(65536) for _ in range(num_banks)]
        self.colram = bytearray(1024)
        self.vic = bytearray(0x40)
        self.mmu = bytearray(0x0C)
        self.mmu[0x0B] = 0x20  # MMU version register

        self.cpu_ddr = 0x00
        self.cpu_port = 0x00

        # Shadowed P0H/P1H latches (committed on P0L/P1L write, like real 8722)
        self._p0h_latch = 0
        self._p1h_latch = 0
        self.p0l = 0x00
        self.p0h = 0x00
        self.p1l = 0x01
        self.p1h = 0x00
        # Second MMU's P0L when simulating the 1999 Makela bit-6 remap bug
        self.u2_p0l = 0x00

        # Optional fault hooks: fn(sim, bank, phys_addr, value, is_write) -> value
        self.read_hook = None
        self.write_hook = None

        # 6502 registers
        self.a = 0
        self.x = 0
        self.y = 0
        self.sp = 0xFF
        self.pc = 0x8000  # Kernal POLL enters U36 at $8000 with CR=$16
        self.p = 0x24     # I=1
        self.mmu[0x00] = 0x16
        self.halted = False
        self.steps = 0

    def screen_row(self, row: int) -> str:
        start = SCR_BASE + row * 40
        return "".join(screencode_to_ascii(self.ram[0][start + i]) for i in range(40))

    def screen_text(self) -> str:
        return "\n".join(self.screen_row(r) for r in range(25))

    def _phys_bank(self, logical_bank: int) -> int:
        if self.num_banks == 2:
            return logical_bank & 0x01  # Stock 8722 ignores CR bit 7 in 128K mode
        return logical_bank & 0x03

    def _translate_ram(self, addr: int) -> tuple[int, int]:
        cr = self.mmu[0x00]
        rcr = self.mmu[0x06]
        bank = (cr >> 6) & 0x03
        page = (addr >> 8) & 0xFF
        off = addr & 0xFF

        # Page 0 / Page 1 relocation (MOS 8722, verified against VICE x128)
        reloc_bank = bank
        reloc_page = page
        if page == 0x00:
            reloc_page = self.p0l
            reloc_bank = (self.p0h & 0x01) if self.break_p0h_bank2 else self.p0h
        elif page == self.p0l and bank == self.p0h:
            reloc_page = 0x00
            reloc_bank = self.p0h
        elif page == 0x01:
            reloc_page = self.p1l
            reloc_bank = self.p1h
        elif page == self.p1l and bank == self.p1h:
            reloc_page = 0x01
            reloc_bank = self.p1h

        # Common RAM override (RCR $D506 bits 3..0 checks untranslated CPU page)
        sz_code = rcr & 0x03
        sz_pages = (4, 16, 32, 64)[sz_code]  # 1K, 4K, 8K, 16K
        if (rcr & 0x04) and page < sz_pages:
            reloc_bank = 0
        if (rcr & 0x08) and page >= (256 - sz_pages):
            reloc_bank = 0

        return self._phys_bank(reloc_bank), (reloc_page << 8) | off

    def _write_mmu(self, reg: int, val: int) -> None:
        val &= 0xFF
        if reg == 0x0B:
            return
        self.mmu[reg] = val
        if reg == 0x08:
            self._p0h_latch = val & 0x03
        elif reg == 0x07:
            self.p0l = val
            self.p0h = self._p0h_latch
            # With Makela bug, U2's D6 received D7 instead of D6 on $D507 write
            self.u2_p0l = (val & 0xBF) | (((val >> 7) & 1) << 6)
        elif reg == 0x0A:
            self._p1h_latch = val & 0x03
        elif reg == 0x09:
            self.p1l = val
            self.p1h = self._p1h_latch

    def read(self, addr: int) -> int:
        addr &= 0xFFFF
        if addr == 0x0000:
            return self.cpu_ddr
        if addr == 0x0001:
            return self.cpu_port
        if 0xFF00 <= addr <= 0xFF04:
            return self.mmu[addr - 0xFF00]

        cr = self.mmu[0x00]
        io_on = (cr & 0x01) == 0

        if io_on and 0xD000 <= addr <= 0xDFFF:
            if 0xD000 <= addr <= 0xD03F:
                return self.vic[addr - 0xD000]
            if 0xD500 <= addr <= 0xD50B:
                return self.mmu[addr - 0xD500]
            if 0xD800 <= addr <= 0xDBFF:
                return 0xF0 | (self.colram[addr - 0xD800] & 0x0F)
            return 0xFF

        if 0x8000 <= addr <= 0xBFFF:
            sel = (cr >> 2) & 0x03
            if sel == 0x01:  # U36 Low
                return self.rom[addr - 0x8000]
            if sel != 0x03:
                return 0xFF
        elif 0xC000 <= addr <= 0xFFFF:
            sel = (cr >> 4) & 0x03
            if sel == 0x01:  # U36 High
                return self.rom[0x4000 + (addr - 0xC000)]
            if sel != 0x03:
                return 0xFF
        elif 0x4000 <= addr <= 0x7FFF:
            if (cr & 0x02) == 0:
                return 0xFF

        b, paddr = self._translate_ram(addr)
        val = self.ram[b][paddr]
        if self.read_hook is not None:
            val = self.read_hook(self, b, paddr, val) & 0xFF
        return val

    def write(self, addr: int, val: int) -> None:
        addr &= 0xFFFF
        val &= 0xFF
        if addr == 0x0000:
            self.cpu_ddr = val
            return
        if addr == 0x0001:
            self.cpu_port = val
            return
        if addr == 0xFF00:
            self.mmu[0x00] = val
            return
        if 0xFF01 <= addr <= 0xFF04:
            # LCRA..LCRD copy PCRA..PCRD into CR without using val!
            self.mmu[0x00] = self.mmu[addr - 0xFF00]
            return

        cr = self.mmu[0x00]
        io_on = (cr & 0x01) == 0
        if io_on and 0xD000 <= addr <= 0xDFFF:
            if 0xD000 <= addr <= 0xD03F:
                self.vic[addr - 0xD000] = val
            elif 0xD500 <= addr <= 0xD50B:
                self._write_mmu(addr - 0xD500, val)
            elif 0xD800 <= addr <= 0xDBFF:
                self.colram[addr - 0xD800] = val & 0x0F
            return

        # Note: C128 PLA writes through to underlying RAM even when ROM is mapped!
        b, paddr = self._translate_ram(addr)
        if self.write_hook is not None:
            val = self.write_hook(self, b, paddr, val)
            if val is None:
                return
            val &= 0xFF
        self.ram[b][paddr] = val

    def _zn(self, v: int) -> int:
        v &= 0xFF
        self.p = (self.p & ~0x82) | (0x02 if v == 0 else 0) | (v & 0x80)
        return v

    def _cmp(self, reg: int, v: int) -> None:
        d = (reg - (v & 0xFF)) & 0x1FF
        self.p = (self.p & ~0x83) | (0x01 if reg >= (v & 0xFF) else 0)
        self._zn(d & 0xFF)

    def _push(self, v: int) -> None:
        self.write(0x0100 | self.sp, v)
        self.sp = (self.sp - 1) & 0xFF

    def _pull(self) -> int:
        self.sp = (self.sp + 1) & 0xFF
        return self.read(0x0100 | self.sp)

    def step(self) -> None:
        pc = self.pc
        op = self.read(pc)
        self.pc = (pc + 1) & 0xFFFF
        self.steps += 1

        def imm() -> int:
            v = self.read(self.pc)
            self.pc = (self.pc + 1) & 0xFFFF
            return v

        def zp() -> int:
            return imm()

        def zpx() -> int:
            return (imm() + self.x) & 0xFF

        def absu() -> int:
            lo = imm()
            hi = imm()
            return lo | (hi << 8)

        def absx() -> int:
            return (absu() + self.x) & 0xFFFF

        def absy() -> int:
            return (absu() + self.y) & 0xFFFF

        def indy() -> int:
            z = imm()
            lo = self.read(z)
            hi = self.read((z + 1) & 0xFF)
            return ((lo | (hi << 8)) + self.y) & 0xFFFF

        def branch(cond: bool) -> None:
            rel = imm()
            if cond:
                if rel & 0x80:
                    rel -= 256
                self.pc = (self.pc + rel) & 0xFFFF

        if op == 0x78: self.p |= 0x04                             # SEI
        elif op == 0x58: self.p &= ~0x04                          # CLI
        elif op == 0xD8: self.p &= ~0x08                          # CLD
        elif op == 0x18: self.p &= ~0x01                          # CLC
        elif op == 0x38: self.p |= 0x01                           # SEC
        elif op == 0xEA: pass                                     # NOP
        elif op == 0xE8: self.x = self._zn(self.x + 1)            # INX
        elif op == 0xCA: self.x = self._zn(self.x - 1)            # DEX
        elif op == 0xC8: self.y = self._zn(self.y + 1)            # INY
        elif op == 0x88: self.y = self._zn(self.y - 1)            # DEY
        elif op == 0xAA: self.x = self._zn(self.a)                # TAX
        elif op == 0x8A: self.a = self._zn(self.x)                # TXA
        elif op == 0xA8: self.y = self._zn(self.a)                # TAY
        elif op == 0x98: self.a = self._zn(self.y)                # TYA
        elif op == 0x9A: self.sp = self.x                         # TXS
        elif op == 0xBA: self.x = self._zn(self.sp)               # TSX
        elif op == 0x48: self._push(self.a)                       # PHA
        elif op == 0x68: self.a = self._zn(self._pull())          # PLA
        elif op == 0x0A:                                          # ASL A
            self.p = (self.p & ~0x01) | ((self.a >> 7) & 1)
            self.a = self._zn(self.a << 1)
        elif op == 0x4A:                                          # LSR A
            self.p = (self.p & ~0x01) | (self.a & 1)
            self.a = self._zn(self.a >> 1)
        elif op == 0xA9: self.a = self._zn(imm())                 # LDA #
        elif op == 0xA2: self.x = self._zn(imm())                 # LDX #
        elif op == 0xA0: self.y = self._zn(imm())                 # LDY #
        elif op == 0x29: self.a = self._zn(self.a & imm())        # AND #
        elif op == 0x09: self.a = self._zn(self.a | imm())        # ORA #
        elif op == 0x49: self.a = self._zn(self.a ^ imm())        # EOR #
        elif op == 0x69 or op == 0x6D or op == 0x65:              # ADC
            v = imm() if op == 0x69 else self.read(absu() if op == 0x6D else zp())
            s = self.a + v + (self.p & 1)
            self.p = (self.p & ~0x41) | (0x01 if s > 0xFF else 0) | (
                0x40 if (~(self.a ^ v) & (self.a ^ s) & 0x80) else 0
            )
            self.a = self._zn(s)
        elif op == 0xC9: self._cmp(self.a, imm())                 # CMP #
        elif op == 0xE0: self._cmp(self.x, imm())                 # CPX #
        elif op == 0xC0: self._cmp(self.y, imm())                 # CPY #
        elif op == 0xA5: self.a = self._zn(self.read(zp()))       # LDA zp
        elif op == 0x85: self.write(zp(), self.a)                 # STA zp
        elif op == 0xA6: self.x = self._zn(self.read(zp()))       # LDX zp
        elif op == 0x86: self.write(zp(), self.x)                 # STX zp
        elif op == 0xA4: self.y = self._zn(self.read(zp()))       # LDY zp
        elif op == 0x84: self.write(zp(), self.y)                 # STY zp
        elif op == 0xB5: self.a = self._zn(self.read(zpx()))      # LDA zp,X
        elif op == 0x95: self.write(zpx(), self.a)                # STA zp,X
        elif op == 0xC5: self._cmp(self.a, self.read(zp()))       # CMP zp
        elif op == 0xE4: self._cmp(self.x, self.read(zp()))       # CPX zp
        elif op == 0xC4: self._cmp(self.y, self.read(zp()))       # CPY zp
        elif op == 0x45: self.a = self._zn(self.a ^ self.read(zp()))  # EOR zp
        elif op == 0x05: self.a = self._zn(self.a | self.read(zp()))  # ORA zp
        elif op == 0x25: self.a = self._zn(self.a & self.read(zp()))  # AND zp
        elif op == 0xE6:                                          # INC zp
            z = zp(); self.write(z, self._zn(self.read(z) + 1))
        elif op == 0xC6:                                          # DEC zp
            z = zp(); self.write(z, self._zn(self.read(z) - 1))
        elif op == 0xB1: self.a = self._zn(self.read(indy()))     # LDA (zp),Y
        elif op == 0x91: self.write(indy(), self.a)               # STA (zp),Y
        elif op == 0xD1: self._cmp(self.a, self.read(indy()))     # CMP (zp),Y
        elif op == 0xAD: self.a = self._zn(self.read(absu()))     # LDA abs
        elif op == 0x8D: self.write(absu(), self.a)               # STA abs
        elif op == 0xAE: self.x = self._zn(self.read(absu()))     # LDX abs
        elif op == 0x8E: self.write(absu(), self.x)               # STX abs
        elif op == 0xAC: self.y = self._zn(self.read(absu()))     # LDY abs
        elif op == 0x8C: self.write(absu(), self.y)               # STY abs
        elif op == 0xCD: self._cmp(self.a, self.read(absu()))     # CMP abs
        elif op == 0xEC: self._cmp(self.x, self.read(absu()))     # CPX abs
        elif op == 0xCC: self._cmp(self.y, self.read(absu()))     # CPY abs
        elif op == 0x4D: self.a = self._zn(self.a ^ self.read(absu()))
        elif op == 0x2D: self.a = self._zn(self.a & self.read(absu()))
        elif op == 0x0D: self.a = self._zn(self.a | self.read(absu()))
        elif op == 0xBD: self.a = self._zn(self.read(absx()))     # LDA abs,X
        elif op == 0x9D: self.write(absx(), self.a)               # STA abs,X
        elif op == 0xDD: self._cmp(self.a, self.read(absx()))     # CMP abs,X
        elif op == 0x5D: self.a = self._zn(self.a ^ self.read(absx()))  # EOR abs,X
        elif op == 0xB9: self.a = self._zn(self.read(absy()))     # LDA abs,Y
        elif op == 0x99: self.write(absy(), self.a)               # STA abs,Y
        elif op == 0xD9: self._cmp(self.a, self.read(absy()))     # CMP abs,Y
        elif op == 0x59: self.a = self._zn(self.a ^ self.read(absy()))  # EOR abs,Y
        elif op == 0x4C:                                          # JMP abs
            tgt = absu()
            if tgt == pc:
                self.halted = True
            self.pc = tgt
        elif op == 0x20:                                          # JSR abs
            tgt = absu()
            ret = (self.pc - 1) & 0xFFFF
            self._push((ret >> 8) & 0xFF)
            self._push(ret & 0xFF)
            self.pc = tgt
        elif op == 0x60:                                          # RTS
            lo = self._pull()
            hi = self._pull()
            self.pc = ((lo | (hi << 8)) + 1) & 0xFFFF
        elif op == 0x40:                                          # RTI
            self.p = self._pull() | 0x20
            lo = self._pull()
            hi = self._pull()
            self.pc = lo | (hi << 8)
        elif op == 0x10: branch((self.p & 0x80) == 0)             # BPL
        elif op == 0x30: branch((self.p & 0x80) != 0)             # BMI
        elif op == 0x90: branch((self.p & 0x01) == 0)             # BCC
        elif op == 0xB0: branch((self.p & 0x01) != 0)             # BCS
        elif op == 0xD0: branch((self.p & 0x02) == 0)             # BNE
        elif op == 0xF0: branch((self.p & 0x02) != 0)             # BEQ
        else:
            raise RuntimeError(f"Unhandled opcode {op:#04x} at PC={pc:#06x}")

    def run_until(self, stop_pcs: set[int] | None = None, max_steps: int = 5_000_000) -> str:
        for _ in range(max_steps):
            if self.halted:
                return "halted"
            if stop_pcs and self.pc in stop_pcs:
                return "stop_pc"
            self.step()
        raise TimeoutError(f"Exceeded {max_steps} steps at PC={self.pc:#06x}")


class MemtestFaultSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rom, cls.syms = build_rom()

    def test_01_boot_autodetect_128k_and_stages_1_2_7(self):
        sim = C128Sim(self.rom, num_banks=2)
        # Run boot + Stage 1 + Stage 2 (stops right at Stage 3 full march)
        res = sim.run_until({self.syms["run_full_march_stage"]})
        self.assertEqual(res, "stop_pc")
        self.assertEqual(sim.ram[0][ZP_NUM_BANKS], 2)
        self.assertIn("128 KB (BANKS 0-1, SOLO)", sim.screen_row(2))
        self.assertIn("BANK 0 : [++.....]    BANK 1 : [++.....]", sim.screen_row(9))

        # Now run Stage 7 MMU directly at 2 MHz!
        sim.ram[0][ZP_SPEED] = 1
        sim.ram[0][ZP_STAGE] = 7
        sim.pc = self.syms["run_stage7_mmu"]
        # Push fake return sentinel $FFF8 (RTI/stop)
        sim._push(0xFF)
        sim._push(0xEF)
        res = sim.run_until({0xFFF0})
        self.assertEqual(res, "stop_pc")
        self.assertEqual(sim.vic[0x20], 0x0E)  # No fault (light-blue border)

    def test_02_boot_autodetect_256k_and_stages_1_2_7(self):
        sim = C128Sim(self.rom, num_banks=4)
        res = sim.run_until({self.syms["run_full_march_stage"]})
        self.assertEqual(res, "stop_pc")
        self.assertEqual(sim.ram[0][ZP_NUM_BANKS], 4)
        self.assertIn("256 KB (BANKS 0-3, MMU RISER)", sim.screen_row(2))
        self.assertIn("BANK 2 : [++.....]    BANK 3 : [++.....]", sim.screen_row(10))

        sim.ram[0][ZP_SPEED] = 1
        sim.ram[0][ZP_STAGE] = 7
        sim.pc = self.syms["run_stage7_mmu"]
        sim._push(0xFF)
        sim._push(0xEF)
        res = sim.run_until({0xFFF0})
        self.assertEqual(res, "stop_pc")

    def test_03_stuck_data_bit_d3_bank0(self):
        sim = C128Sim(self.rom, num_banks=2)
        sim.read_hook = lambda s, b, a, v: (v | 0x08) if b == 0 else v
        res = sim.run_until(max_steps=500_000)
        self.assertEqual(res, "halted")
        self.assertEqual(sim.vic[0x20], 0x02)  # Red border
        scr = sim.screen_text()
        self.assertIn("* FAULT HALT *", scr)
        self.assertIn("BANK 0  ADDR $1800", scr)
        self.assertIn("XOR $08  [76543210: ....3...]", scr)

    def test_04_data_bridge_d0_d2_bank0(self):
        """Bridge between D0 and D2 (invisible to $00/$FF/$55/$AA/$B0/$B2, caught by Stage 1 BUS!)."""
        sim = C128Sim(self.rom, num_banks=2)

        def bridge(s, b, a, v):
            if b == 0 and a >= 0x0800 and ((v & 0x01) or (v & 0x04)):
                return v | 0x05
            return v

        sim.read_hook = bridge
        res = sim.run_until(max_steps=500_000)
        self.assertEqual(res, "halted")
        scr = sim.screen_text()
        self.assertIn("WHERE  : BANK 0  ADDR $1800", scr)
        self.assertIn("STEP BUS", scr)
        self.assertIn(".....2..", scr)

    def test_05_address_pin_a5_open_or_short(self):
        """Column address bit A5 stuck low in Bank 1 (MA5 bodge fault)."""
        sim = C128Sim(self.rom, num_banks=2)
        sim.write_hook = lambda s, b, a, v: (
            s.ram[b].__setitem__(a & ~0x0020, v) if b == 1 else v
        )
        sim.read_hook = lambda s, b, a, v: s.ram[b][a & ~0x0020] if b == 1 else v
        res = sim.run_until(max_steps=500_000)
        self.assertEqual(res, "halted")
        scr = sim.screen_text()
        self.assertIn("1/7 DATA BUS + A0-A15 PIN WALK", scr)
        self.assertIn("BANK 1  ADDR $18", scr)
        self.assertIn("STEP ADR", scr)

    def test_06_high_address_a15_alias_mid_ram(self):
        """A15 alias ($9800 aliases $1800) caught in Stage 1 via Upper ROM helper."""
        sim = C128Sim(self.rom, num_banks=2)
        sim.write_hook = lambda s, b, a, v: (
            s.ram[b].__setitem__(a & 0x7FFF, v) if b == 0 and a >= 0x0800 else v
        )
        sim.read_hook = lambda s, b, a, v: (
            s.ram[b][a & 0x7FFF] if b == 0 and a >= 0x0800 else v
        )
        res = sim.run_until(max_steps=500_000)
        self.assertEqual(res, "halted")
        scr = sim.screen_text()
        self.assertIn("STEP ADR", scr)
        self.assertIn("BANK 0", scr)

    def test_07_zero_page_and_stack_fault_zero_ram_mailbox(self):
        """Fault in Bank 0 Hardware Stack ($0142) caught by Stage 2 without crashing!"""
        sim = C128Sim(self.rom, num_banks=2)
        sim.read_hook = lambda s, b, a, v: (v ^ 0x40) if (b == 0 and a == 0x0142) else v
        res = sim.run_until(max_steps=1_000_000)
        self.assertEqual(res, "halted")
        scr = sim.screen_text()
        self.assertIn("2/7 ZP/STACK/SCREEN MARCH-C", scr)
        self.assertIn("WHERE  : BANK 0  ADDR $0142", scr)
        self.assertIn("XOR $40  [76543210: .6......]", scr)

    def test_08_under_io_d456_ram_fault(self):
        """Fault in RAM underneath SID/I/O at Bank 1 $D456 caught by March-C-!"""
        sim = C128Sim(self.rom, num_banks=2)
        res = sim.run_until({self.syms["run_full_march_stage"]})
        self.assertEqual(res, "stop_pc")
        sim.read_hook = lambda s, b, a, v: (v ^ 0x01) if (b == 1 and a == 0xD456) else v
        res = sim.run_until(max_steps=4_500_000)
        self.assertEqual(res, "halted")
        scr = sim.screen_text()
        self.assertIn("WHERE  : BANK 1  ADDR $D456", scr)
        self.assertIn("XOR $01  [76543210: .......0]", scr)

    def test_09_cross_bank_write_bleed_stage6(self):
        """Writing Bank 1 $2345 subtly flips bit 7 in Bank 0 $2345 (caught by Stage 6!)."""
        sim = C128Sim(self.rom, num_banks=2)
        res = sim.run_until({self.syms["run_full_march_stage"]})
        self.assertEqual(res, "stop_pc")

        def bleed_write(s, b, a, v):
            if b == 1 and a == 0x2345:
                s.ram[0][0x2345] ^= 0x80
            return v

        sim.write_hook = bleed_write
        sim.ram[0][ZP_SPEED] = 1
        sim.ram[0][ZP_STAGE] = 6
        sim.pc = self.syms["run_stage6_xbank"]
        res = sim.run_until(max_steps=4_000_000)
        self.assertEqual(res, "halted")
        scr = sim.screen_text()
        self.assertIn("6/7 ALL-BANK STAMP + VERIFY", scr)
        self.assertIn("WHERE  : BANK 0  ADDR $2345  AT 2 MHZ", scr)
        self.assertIn("XOR $80  [76543210: 7.......]", scr)

    def test_10_mmu_riser_p0h_high_bank_fault_caught_in_stage7(self):
        """Simulates broken D0X remap on 256K MMU riser (P0H fails to reach Banks 2/3)."""
        sim = C128Sim(self.rom, num_banks=4, break_p0h_bank2=True)
        sim.ram[0][ZP_NUM_BANKS] = 4
        sim.ram[0][ZP_SPEED] = 0
        sim.ram[0][ZP_STAGE] = 7
        sim.pc = self.syms["run_stage7_mmu"]
        sim._push(0xFF)
        sim._push(0xEF)
        res = sim.run_until({0xFFF0}, max_steps=500_000)
        self.assertEqual(res, "halted")
        scr = sim.screen_text()
        self.assertIn("7/7 MMU P0/P1 + RCR + WR-THRU", scr)
        self.assertIn("ADDR $4080", scr)
        self.assertIn("EXP $70  ACT $72    STEP P0", scr)


if __name__ == "__main__":
    unittest.main()
