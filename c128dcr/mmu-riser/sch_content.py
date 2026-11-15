"""Schematic content of the C128DCR MMU riser: symbols, sheet layout, notes
and title.  The generic writer is common/sch_writer.py."""
import design as D
from sch_writer import box_symbol, two_pin

TITLE = ("C128DCR MMU riser", "2026-10-11", "1.0",
         "Plugs into U7: stock 8722 + second 8722; BANK_HI to the SRAM module",
         "After ytmytm/c128-mmu-256k-exp (Makela/Pessi 256K expansion); ATF16V8B remap")


def build_symbols():
    syms = {}
    n = D.MMU_PIN_NAMES
    # MMU pins are all "passive": two MMUs legitimately share nets (they read
    # back identical register bits), which ERC would flag as output conflicts.
    left = [(str(k), n[k], "passive") for k in [2, 16, 17] + list(range(18, 34))]
    left += [None] + [(str(k), n[k], "passive") for k in range(43, 49)]
    right = [(str(k), n[k], "passive") for k in range(3, 16)]
    right += [None] + [(str(k), n[k], "passive") for k in range(35, 43)]
    syms["mmu"] = box_symbol("MOS_8722_MMU", "U", left, right,
                             top=[("1", "VDD", "power_in")], bottom=[("34", "VSS", "power_in")],
                             width=20.32, descr="MOS 8722 memory management unit")
    # Pins 1 (VDD) and 34 (VSS) are power pins sitting inline in this flat
    # 48-pin pass-through list instead of a dedicated top/bottom slot (the
    # only place in either board's symbols that does that -- everywhere
    # else a power pin gets box_symbol's top=/bottom= treatment instead,
    # which spaces it clear of other rows).  A power symbol's value text
    # sits 3.81mm off its pin (matching KiCad's own standard GND/+5V symbol
    # library defaults), more than this list's 2.54mm row pitch, so VDD's
    # and VSS's labels land on the very next row (confirmed: "5V" on pin
    # 1 overlapping pin 2's "RESET", "GND" on pin 34 overlapping pin 33's
    # "PHI").  A None gap -- the same device used elsewhere in this file to
    # space out unrelated pin groups -- is cheaper and safer than changing
    # the shared offset used by every other power pin on both boards.
    left = [(str(k), n[k], "passive") for k in range(1, 2)] + [None]
    left += [(str(k), n[k], "passive") for k in range(2, 25)]
    right = [(str(k), n[k], "passive") for k in range(48, 34, -1)]
    right += [("34", n[34], "passive"), None]
    right += [(str(k), n[k], "passive") for k in range(33, 24, -1)]
    syms["plug48"] = box_symbol("Plug_DIP48_MMU_Socket", "P", left, right, width=20.32,
                                descr="Machined pins into the C128DCR MMU socket U7")
    left = [("1", "A0", "input"), ("2", "A1", "input"), ("3", "A2", "input"), ("4", "A3", "input"),
            ("5", "R/~{W}", "input"), None, ("6", "D0", "input"), ("7", "D1", "input"),
            ("8", "D6", "input"), ("9", "D7", "input"), None, ("11", "I9", "input")]
    # IO7/IO6/IO5/IO2/IO1/IO0 (pins 19/18/17/14/13/12) are unused in Complex
    # Mode (riser.jed: SYN=1, AC0=1) with their output enable permanently
    # off, so they're programmed as pure inputs -- "input" here (like pin
    # 11/I9 above), not "tri_state", so ERC doesn't read tying them to GND
    # as a driver shorted to a power rail.  D6X/D0X (16/15) are real
    # outputs, driven only on writes, and stay tri_state.
    right = [("19", "IO7", "input"), ("18", "IO6", "input"), ("17", "IO5", "input"),
             ("16", "D6X", "tri_state"), ("15", "D0X", "tri_state"), ("14", "IO2", "input"),
             ("13", "IO1", "input"), ("12", "IO0", "input")]
    syms["gal"] = box_symbol("ATF16V8B", "U", left, right, top=[("20", "VCC", "power_in")],
                             bottom=[("10", "GND", "power_in")], width=12.7,
                             descr="SPLD (GAL16V8 compatible), programmed with gal/riser.jed")
    syms["jst2"] = box_symbol("Conn_JST_PH_2", "J", [("1", "Pin_1", "passive"), ("2", "Pin_2", "passive")],
                              [], width=5.08, descr="JST PH 2-pin header")
    syms["r"] = two_pin("R", "R", "R")
    syms["c"] = two_pin("C", "C", "C")
    return syms


def sym_key(part):
    return part.sym


LAYOUT = {   # ref -> (x, y) on A3 sheet, all on 2.54 grid
    "P1": (45.72, 106.68), "U1": (134.62, 106.68), "U2": (223.52, 106.68),
    "U3": (322.58, 71.12), "R1": (350.52, 132.08), "J1": (378.46, 134.62),
    "C1": (304.8, 236.22), "C2": (320.04, 236.22), "C3": (335.28, 236.22), "C4": (350.52, 236.22),
}

NOTES = """C128DCR MMU RISER v1.0 - plugs into the 8722 MMU socket U7; adds a second 8722 for 256 KB

HOW IT WORKS
 - P1 plugs into U7.  U1 holds the computer's own 8722, every pin passed straight through.
 - U2 is a second 8722 on the same bus.  Its data bits D0/D6 come from the GAL U3 so that its
   bank bit follows the CPU's 256K bank bit; its ~CAS1 output then means "banks 2/3".
   That output, BANK_HI, goes through R1 and J1 to the SRAM module, which does the rest of
   the bank decoding.  Nothing else is wired to the motherboard.
 - U3 (ATF16V8B, gal/riser.pld, checked by gal/check_gal.py against all 512 input states):
     D6X = CPU D6 at $xxx5 (MCR), $xxx7 (P0L), $xxx9 (P1L); CPU D7 everywhere else
     D0X = CPU D1 at $xxx8/$xxxA (P0H/P1H bank pointers); CPU D0 everywhere else
     both driven only while R/~W is low, so U2 never fights them on register reads.
 - U2's D1-D5/D7 share the bus with U1: both chips hold the same bits there and return
   the same values.  U2's other outputs (TA8-15, ~CAS0, MS0-3, ~Z80EN, ~FSDIR) are open.
 - After ytmytm/c128-mmu-256k-exp, with P0L/P1L passed unremapped (page numbers must
   match the stock MMU) and 3-state remap outputs.

INSTALL: program U3 with gal/riser.jed (TL866: device ATF16V8B, no encryption).
Solder P1's machined pins first, then the DIP-48 and DIP-20 sockets on top.
Move the 8722 from the motherboard's U7 to U1; fit the second 8722 in U2.
Cable J1 to the SRAM module's J1 (JST-PH 2-pin, pin 1 = BANK_HI, pin 2 = GND)."""


def decorate(sh):
    # power flags (flag and power port on the same point)
    sh.flag(375.92, 236.22, 180)
    sh.power("+5V", 375.92, 236.22)
    sh.flag(391.16, 231.14, 0)
    sh.power("GND", 391.16, 231.14)
    sh.text(NOTES, 30.48, 205.74, 1.27)
    sh.text("Decoupling: C1 U1, C2 U2, C3 U3, C4 bulk.  JLC places the passives; ICs and "
            "sockets are hand-soldered.", 254.0, 254.0)
