"""Minimal, self-contained 2-pass NMOS 6502 / MOS 8502 assembler DSL.

Used by build.py to generate the C128 U36 diagnostic Option ROM image
with zero external assembler dependencies.
"""

# C128 uppercase/graphics screen code conversion ($D018 = $14, char ROM $1000)
def ascii_to_screencode(ch: str, reverse: bool = False) -> int:
    c = ord(ch.upper())
    if c == 0x40:  # '@'
        sc = 0x00
    elif 0x41 <= c <= 0x5A:  # 'A'..'Z'
        sc = c - 0x40
    elif c == 0x5B:  # '['
        sc = 0x1B
    elif c == 0x5D:  # ']'
        sc = 0x1D
    elif 0x20 <= c <= 0x3F:  # ' '..'?' (digits, punctuation)
        sc = c
    elif c == 0x7C:  # '|' -> vertical bar graphic
        sc = 0x5D
    else:
        raise ValueError(f"Unsupported screen char: {ch!r}")
    return sc | (0x80 if reverse else 0x00)


def scr_bytes(text: str, reverse: bool = False) -> bytes:
    return bytes(ascii_to_screencode(ch, reverse=reverse) for ch in text)


class Asm6502:
    """Two-pass 6502 segment assembler with explicit ZP vs Absolute addressing."""

    def __init__(self, origin: int, max_size: int):
        self.origin = origin
        self.max_size = max_size
        self.buf = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[str, int, str, int]] = []  # (kind, offset, expr, pc_next)
        self._uid = 0

    @property
    def pc(self) -> int:
        return self.origin + len(self.buf)

    def unique_label(self, prefix: str = "L") -> str:
        self._uid += 1
        return f"_{prefix}_{self._uid}"

    def here_label(self, prefix: str = "L") -> str:
        return self.label(self.unique_label(prefix))

    def label(self, name: str) -> str:
        if name in self.labels:
            raise ValueError(f"Duplicate label: {name}")
        self.labels[name] = self.pc
        return name

    def org_pad(self, addr: int, fill: int = 0xFF) -> None:
        if addr < self.pc:
            raise ValueError(f"org_pad backwards: {addr:#06x} < {self.pc:#06x}")
        self.buf.extend([fill] * (addr - self.pc))

    def emit(self, *vals: int) -> None:
        for v in vals:
            self.buf.append(v & 0xFF)

    def emit_bytes(self, data: bytes | bytearray | list[int]) -> None:
        self.buf.extend(data)

    def emit_scr(self, text: str, reverse: bool = False, null_term: bool = False) -> None:
        self.buf.extend(scr_bytes(text, reverse=reverse))
        if null_term:
            self.buf.append(0xFF if not reverse else 0x7F)

    def word(self, target: int | str) -> None:
        if isinstance(target, int):
            self.emit(target & 0xFF, (target >> 8) & 0xFF)
        else:
            self.fixups.append(("word", len(self.buf), target, self.pc + 2))
            self.emit(0x00, 0x00)

    def _u8_or_ref(self, kind: str, val: int | str) -> None:
        if isinstance(val, int):
            if not (0 <= val <= 0xFF):
                raise ValueError(f"Byte out of range: {val:#x}")
            self.emit(val)
        else:
            self.fixups.append((kind, len(self.buf), val, self.pc + 1))
            self.emit(0x00)

    def _u16_or_ref(self, val: int | str) -> None:
        if isinstance(val, int):
            if not (0 <= val <= 0xFFFF):
                raise ValueError(f"Word out of range: {val:#x}")
            self.emit(val & 0xFF, (val >> 8) & 0xFF)
        else:
            self.fixups.append(("word", len(self.buf), val, self.pc + 2))
            self.emit(0x00, 0x00)

    def _rel(self, opcode: int, target: int | str) -> None:
        self.emit(opcode)
        if isinstance(target, int):
            delta = target - (self.pc + 1)
            if not (-128 <= delta <= 127):
                raise ValueError(f"Branch out of range: {delta}")
            self.emit(delta & 0xFF)
        else:
            self.fixups.append(("rel", len(self.buf), target, self.pc + 1))
            self.emit(0x00)

    # --- Implied / Accumulator ---
    def sei(self): self.emit(0x78)
    def cli(self): self.emit(0x58)
    def cld(self): self.emit(0xD8)
    def clc(self): self.emit(0x18)
    def sec(self): self.emit(0x38)
    def inx(self): self.emit(0xE8)
    def dex(self): self.emit(0xCA)
    def iny(self): self.emit(0xC8)
    def dey(self): self.emit(0x88)
    def tax(self): self.emit(0xAA)
    def txa(self): self.emit(0x8A)
    def tay(self): self.emit(0xA8)
    def tya(self): self.emit(0x98)
    def txs(self): self.emit(0x9A)
    def tsx(self): self.emit(0xBA)
    def pha(self): self.emit(0x48)
    def pla(self): self.emit(0x68)
    def nop(self): self.emit(0xEA)
    def rts(self): self.emit(0x60)
    def rti(self): self.emit(0x40)
    def brk(self): self.emit(0x00)
    def asl_a(self): self.emit(0x0A)
    def lsr_a(self): self.emit(0x4A)
    def rol_a(self): self.emit(0x2A)
    def ror_a(self): self.emit(0x6A)

    # --- Immediate ---
    def lda_imm(self, v: int | str): self.emit(0xA9); self._u8_or_ref("u8", v)
    def ldx_imm(self, v: int | str): self.emit(0xA2); self._u8_or_ref("u8", v)
    def ldy_imm(self, v: int | str): self.emit(0xA0); self._u8_or_ref("u8", v)
    def and_imm(self, v: int | str): self.emit(0x29); self._u8_or_ref("u8", v)
    def ora_imm(self, v: int | str): self.emit(0x09); self._u8_or_ref("u8", v)
    def eor_imm(self, v: int | str): self.emit(0x49); self._u8_or_ref("u8", v)
    def adc_imm(self, v: int | str): self.emit(0x69); self._u8_or_ref("u8", v)
    def sbc_imm(self, v: int | str): self.emit(0xE9); self._u8_or_ref("u8", v)
    def cmp_imm(self, v: int | str): self.emit(0xC9); self._u8_or_ref("u8", v)
    def cpx_imm(self, v: int | str): self.emit(0xE0); self._u8_or_ref("u8", v)
    def cpy_imm(self, v: int | str): self.emit(0xC0); self._u8_or_ref("u8", v)
    def lda_lo(self, sym: str): self.emit(0xA9); self._u8_or_ref("lo", sym)
    def lda_hi(self, sym: str): self.emit(0xA9); self._u8_or_ref("hi", sym)

    # --- Zero Page ---
    def lda_zp(self, z: int): self.emit(0xA5); self._u8_or_ref("u8", z)
    def sta_zp(self, z: int): self.emit(0x85); self._u8_or_ref("u8", z)
    def ldx_zp(self, z: int): self.emit(0xA6); self._u8_or_ref("u8", z)
    def stx_zp(self, z: int): self.emit(0x86); self._u8_or_ref("u8", z)
    def ldy_zp(self, z: int): self.emit(0xA4); self._u8_or_ref("u8", z)
    def sty_zp(self, z: int): self.emit(0x84); self._u8_or_ref("u8", z)
    def cmp_zp(self, z: int): self.emit(0xC5); self._u8_or_ref("u8", z)
    def cpx_zp(self, z: int): self.emit(0xE4); self._u8_or_ref("u8", z)
    def cpy_zp(self, z: int): self.emit(0xC4); self._u8_or_ref("u8", z)
    def eor_zp(self, z: int): self.emit(0x45); self._u8_or_ref("u8", z)
    def ora_zp(self, z: int): self.emit(0x05); self._u8_or_ref("u8", z)
    def and_zp(self, z: int): self.emit(0x25); self._u8_or_ref("u8", z)
    def adc_zp(self, z: int): self.emit(0x65); self._u8_or_ref("u8", z)
    def inc_zp(self, z: int): self.emit(0xE6); self._u8_or_ref("u8", z)
    def dec_zp(self, z: int): self.emit(0xC6); self._u8_or_ref("u8", z)
    def asl_zp(self, z: int): self.emit(0x06); self._u8_or_ref("u8", z)
    def lsr_zp(self, z: int): self.emit(0x46); self._u8_or_ref("u8", z)
    def lda_zpx(self, z: int): self.emit(0xB5); self._u8_or_ref("u8", z)
    def sta_zpx(self, z: int): self.emit(0x95); self._u8_or_ref("u8", z)

    # --- Indirect,Y ---
    def lda_indy(self, z: int): self.emit(0xB1); self._u8_or_ref("u8", z)
    def sta_indy(self, z: int): self.emit(0x91); self._u8_or_ref("u8", z)
    def cmp_indy(self, z: int): self.emit(0xD1); self._u8_or_ref("u8", z)
    def eor_indy(self, z: int): self.emit(0x51); self._u8_or_ref("u8", z)

    # --- Absolute ---
    def lda_abs(self, a: int | str): self.emit(0xAD); self._u16_or_ref(a)
    def sta_abs(self, a: int | str): self.emit(0x8D); self._u16_or_ref(a)
    def ldx_abs(self, a: int | str): self.emit(0xAE); self._u16_or_ref(a)
    def stx_abs(self, a: int | str): self.emit(0x8E); self._u16_or_ref(a)
    def ldy_abs(self, a: int | str): self.emit(0xAC); self._u16_or_ref(a)
    def sty_abs(self, a: int | str): self.emit(0x8C); self._u16_or_ref(a)
    def cmp_abs(self, a: int | str): self.emit(0xCD); self._u16_or_ref(a)
    def cpx_abs(self, a: int | str): self.emit(0xEC); self._u16_or_ref(a)
    def cpy_abs(self, a: int | str): self.emit(0xCC); self._u16_or_ref(a)
    def and_abs(self, a: int | str): self.emit(0x2D); self._u16_or_ref(a)
    def ora_abs(self, a: int | str): self.emit(0x0D); self._u16_or_ref(a)
    def eor_abs(self, a: int | str): self.emit(0x4D); self._u16_or_ref(a)
    def adc_abs(self, a: int | str): self.emit(0x6D); self._u16_or_ref(a)
    def sbc_abs(self, a: int | str): self.emit(0xED); self._u16_or_ref(a)
    def inc_abs(self, a: int | str): self.emit(0xEE); self._u16_or_ref(a)
    def dec_abs(self, a: int | str): self.emit(0xCE); self._u16_or_ref(a)
    def jmp(self, a: int | str): self.emit(0x4C); self._u16_or_ref(a)
    def jsr(self, a: int | str): self.emit(0x20); self._u16_or_ref(a)

    # --- Absolute,X / Absolute,Y ---
    def lda_absx(self, a: int | str): self.emit(0xBD); self._u16_or_ref(a)
    def sta_absx(self, a: int | str): self.emit(0x9D); self._u16_or_ref(a)
    def cmp_absx(self, a: int | str): self.emit(0xDD); self._u16_or_ref(a)
    def eor_absx(self, a: int | str): self.emit(0x5D); self._u16_or_ref(a)
    def lda_absy(self, a: int | str): self.emit(0xB9); self._u16_or_ref(a)
    def sta_absy(self, a: int | str): self.emit(0x99); self._u16_or_ref(a)
    def cmp_absy(self, a: int | str): self.emit(0xD9); self._u16_or_ref(a)
    def eor_absy(self, a: int | str): self.emit(0x59); self._u16_or_ref(a)

    # --- Branches ---
    def bpl(self, t: int | str): self._rel(0x10, t)
    def bmi(self, t: int | str): self._rel(0x30, t)
    def bvc(self, t: int | str): self._rel(0x50, t)
    def bvs(self, t: int | str): self._rel(0x70, t)
    def bcc(self, t: int | str): self._rel(0x90, t)
    def bcs(self, t: int | str): self._rel(0xB0, t)
    def bne(self, t: int | str): self._rel(0xD0, t)
    def beq(self, t: int | str): self._rel(0xF0, t)

    def link(self, extra_symbols: dict[str, int] | None = None) -> bytes:
        if len(self.buf) > self.max_size:
            raise ValueError(
                f"Segment overflow at {self.origin:#06x}: {len(self.buf)} > {self.max_size}"
            )
        syms = dict(self.labels)
        if extra_symbols:
            for k, v in extra_symbols.items():
                if k in syms and syms[k] != v:
                    raise ValueError(f"Conflicting symbol {k}: {syms[k]:#x} vs {v:#x}")
                syms[k] = v

        out = bytearray(self.buf)
        for kind, off, name, pc_next in self.fixups:
            if name not in syms:
                raise KeyError(f"Undefined label: {name}")
            addr = syms[name]
            if kind == "word":
                out[off] = addr & 0xFF
                out[off + 1] = (addr >> 8) & 0xFF
            elif kind == "lo":
                out[off] = addr & 0xFF
            elif kind == "hi":
                out[off] = (addr >> 8) & 0xFF
            elif kind == "u8":
                if not (0 <= addr <= 0xFF):
                    raise ValueError(f"u8 fixup {name}={addr:#x} out of range")
                out[off] = addr
            elif kind == "rel":
                delta = addr - pc_next
                if not (-128 <= delta <= 127):
                    raise ValueError(
                        f"Branch to {name} ({addr:#06x}) from {pc_next - 1:#06x} "
                        f"out of range: {delta}"
                    )
                out[off] = delta & 0xFF
            else:
                raise ValueError(f"Unknown fixup kind: {kind}")
        return bytes(out)
