"""Schematic content of the C128DCR SRAM module: symbols, sheet layout,
notes and title.  The generic writer is common/sch_writer.py."""
import design as D
from sch_writer import box_symbol, two_pin

TITLE = ("C128DCR 256KB SRAM module", "2026-10-11", "1.0",
         "Replaces U38-U41 (4x 41464/41254 64Kx4 DRAM) in the Commodore 128DCR",
         "IS61C5128AS-25 SRAM, 74AHCT573 row/column/bank latches, RC-timed /CAS decode; BANK_HI from MMU riser")


DRAM_PIN_NAMES = {1: "~{OE}", 2: "DQ1", 3: "DQ2", 4: "~{W}", 5: "~{RAS}", 6: "A6",
                  7: "A5", 8: "A4", 9: "VCC", 10: "A7", 11: "A3", 12: "A2", 13: "A1",
                  14: "A0", 15: "DQ3", 16: "~{CAS}", 17: "DQ4", 18: "VSS"}


def build_symbols():
    syms = {}
    dram = DRAM_PIN_NAMES
    left = [(str(k), dram[k], "passive") for k in range(1, 10)]
    right = [(str(k), dram[k], "passive") for k in range(18, 9, -1)]
    syms["socket_u38"] = box_symbol("DRAM_Socket_41464", "P", left, right, width=12.7,
                                    descr="Plug into original 41464/41254 64Kx4 DRAM socket")
    keep = D.P4_PINS
    left = [(str(k), dram[k], "passive") if k in keep else None for k in range(1, 10)]
    right = [(str(k), dram[k], "passive") if k in keep else None for k in range(18, 9, -1)]
    syms["socket_u41"] = box_symbol("DRAM_Socket_41464_Partial", "P", left, right, width=12.7,
                                    descr="Partial plug (%d pins) into original DRAM socket" % len(keep))
    sp = D.SRAM_PIN_NAMES
    addr_order = [12, 11, 10, 9, 8, 7, 6, 5, 27, 26, 23, 25, 4, 28, 3, 31, 2, 1]
    left = [(str(p), sp[p], "input") for p in addr_order]
    # pin 30 (A18, tied to +5V) sits inline in this signal-pin list rather
    # than a dedicated top=/bottom= slot (those are already used for the
    # SRAM's real VCC/VSS) -- a power pin's value-text offset (3.81mm) is
    # wider than this list's 2.54mm row pitch, so without a gap it lands on
    # the very next row (confirmed: "+5V" overlapping pin 24's "OE_N").
    right = [(str(p), sp[p], "bidirectional") for p in (13, 14, 15, 17, 18, 19, 20, 21)]
    right += [None, ("22", sp[22], "input"), ("30", sp[30], "input"), None,
              ("24", sp[24], "input"), ("29", sp[29], "input")]
    syms["sram"] = box_symbol("IS61C5128AS", "U", left, right,
                              top=[("32", "VCC", "power_in")], bottom=[("16", "VSS", "power_in")],
                              width=17.78, descr="512K x 8 static RAM (IS61C5128AS-25QLI, SOP-32)")
    left = [(str(2 + k), "D%d" % k, "input") for k in range(8)] + [None, ("11", "LE", "input"),
                                                                   ("1", "~{OE}", "input")]
    right = [(str(19 - k), "Q%d" % k, "tri_state") for k in range(8)]
    syms["573"] = box_symbol("74AHCT573", "U", left, right, top=[("20", "VCC", "power_in")],
                             bottom=[("10", "GND", "power_in")], width=12.7,
                             descr="Octal D-type transparent latch, 3-state")
    left = [("1", "1A", "input"), ("2", "1B", "input"), None, ("4", "2A", "input"),
            ("5", "2B", "input"), None, ("9", "3A", "input"), ("10", "3B", "input"), None,
            ("12", "4A", "input"), ("13", "4B", "input")]
    right = [None, ("3", "1Y", "output"), None, None, ("6", "2Y", "output"), None, None,
             ("8", "3Y", "output"), None, None, ("11", "4Y", "output")]
    syms["08"] = box_symbol("74AHCT08", "U", left, right, top=[("14", "VCC", "power_in")],
                            bottom=[("7", "GND", "power_in")], width=10.16,
                            descr="Quad 2-input AND")
    syms["1g32"] = box_symbol("74AHCT1G32", "U", [("1", "A", "input"), ("2", "B", "input")],
                              [None, ("4", "Y", "output")], top=[("5", "VCC", "power_in")],
                              bottom=[("3", "GND", "power_in")], width=10.16,
                              descr="Single 2-input OR")
    syms["1g17"] = box_symbol("74LVC1G17", "U", [("1", "NC", "no_connect"), ("2", "A", "input")],
                              [None, ("4", "Y", "output")], top=[("5", "VCC", "power_in")],
                              bottom=[("3", "GND", "power_in")], width=10.16,
                              descr="Single Schmitt-trigger buffer")
    syms["1g04"] = box_symbol("74AHCT1G04", "U", [("1", "NC", "no_connect"), ("2", "A", "input")],
                              [None, ("4", "Y", "output")], top=[("5", "VCC", "power_in")],
                              bottom=[("3", "GND", "power_in")], width=10.16,
                              descr="Single inverter")
    syms["tp"] = box_symbol("TestPoint", "TP", [("1", "1", "passive")], [], width=2.54,
                            descr="test point")
    syms["1g08"] = box_symbol("74AHCT1G08", "U", [("1", "A", "input"), ("2", "B", "input")],
                              [None, ("4", "Y", "output")], top=[("5", "VCC", "power_in")],
                              bottom=[("3", "GND", "power_in")], width=10.16,
                              descr="Single 2-input AND")
    syms["jst2"] = box_symbol("Conn_JST_PH_2", "J", [("1", "Pin_1", "passive"), ("2", "Pin_2", "passive")],
                              [], width=5.08, descr="JST PH 2-pin header")
    syms["r"] = two_pin("R", "R", "R")
    syms["c"] = two_pin("C", "C", "C")
    return syms


def sym_key(part):
    return {"P1": "socket_u38", "P4": "socket_u41"}.get(part.ref, part.sym)


LAYOUT = {   # ref -> (x, y) on A3 sheet, all on 2.54 grid
    "P1": (50.8, 71.12), "P4": (50.8, 137.16),
    "U2": (144.78, 71.12), "U3": (144.78, 152.4),
    "U1": (248.92, 124.46), "U4": (342.9, 66.04),
    "R4": (325.12, 121.92), "C7": (340.36, 129.54), "U5": (365.76, 119.38),
    "U9": (203.2, 205.74), "U10": (248.92, 205.74), "U7": (284.48, 210.82),
    "J1": (342.9, 165.1), "R1": (365.76, 162.56),
    # pitch widened from 12.7 to 15.24mm (and the row shifted left to still
    # fit TP7 on the sheet): at 12.7 each test point's own net-name label
    # ran into the box of the test point to its left (confirmed by
    # rendering -- e.g. "WE_N"/"DLY" visually merging).
    "TP1": (320.04, 190.5), "TP2": (335.28, 190.5), "TP3": (350.52, 190.5),
    "TP4": (365.76, 190.5), "TP5": (381.0, 190.5), "TP6": (396.24, 190.5),
    "TP7": (411.48, 190.5),
    "C1": (124.46, 238.76), "C2": (139.7, 238.76), "C3": (154.94, 238.76),
    "C4": (170.18, 238.76), "C5": (185.42, 238.76), "C6": (200.66, 238.76),
    "C8": (215.9, 238.76), "C9": (231.14, 238.76), "C10": (246.38, 238.76),
}


NOTES = """C128DCR 256 KB SRAM MODULE v1.0 - replaces U38..U41 (4x 41464/41254 64Kx4 DRAM)

HOW IT WORKS
 - The DRAM interface is multiplexed: MA0-7 carry the row address while ~RAS falls,
   then the column address while ~CAS falls.  Bank 0 uses ~CAS0 (U38/U39), bank 1 ~CAS1 (U40/U41).
 - U4: CE_PRE = CAS0 & CAS1 = SRAM ~CE (one gate: fast reads, write ends 1 gate after ~CAS rises).
   CE_N = CE_PRE + 2 gates closes the column latch U3.  ROWLE = RAS & DLY drives the row latch U2.
 - DLY = CE_PRE delayed by R4/C7 (680R x 33pF) and the Schmitt buffer U5.  The delay is set by the RC
   and U5's datasheet thresholds, not gate speeds: >= ~13 ns (up to ~37 ns) on each edge.
 - U7 bank latch: A16 = ~CAS0 (high = bank 1), A17 = BANK_HI (low = banks 2/3), LE = DLY.
   It closes >= ~15 ns after ~CAS falls (setup 3.5 ns) and re-opens only after ~CE has risen.
 - U9: ~WE = DWE | DLY, so a write starts only after A16/A17 have settled; it ends when ~CE rises.
 - U10: ~OE = NOT DWE, so the SRAM drives the bus only on reads.
 - RAS-only refresh cycles never assert ~CAS, so the SRAM simply ignores them.
 - Address/data pin order on U1/U2/U3 is deliberately scrambled for routing - any permutation of
   SRAM address or data lines is transparent to the computer.
 - TP1-TP7: CE_PRE (~CE), DLY, ~WE, A16, A17, GND, +5V for timing measurements and power checks.

STOCK C128 = 128 KB (banks 0/1).  Banks 2/3 need the MMU riser (second 8722): its BANK_HI
(second MMU's ~CAS1) arrives on J1.  Leave J1 open otherwise - R1 pulls BANK_HI high.

SRAM: IS61C5128AS-25QLI (512Kx8, TTL-level inputs, VIH 2.2 V min).  Pin 1 = our A17, pin 30 = +5V
(its A18: upper 256K used).  Substitutes must have VIH <= 2.4 V at 5 V and the same pinout.

PHYSICAL: P1 plugs into the U38 socket, P4 into U41 (machined round-pin strips).
DCR DRAMs have pin 1 bottom-right (notch to the front); the plug footprints match that.
U39/U40 are spanned but not used - only U38 (D0-3, ~CAS0) and U41 (D4-7, ~CAS1) carry needed signals.
Chip pitch is caliper-measured: 0.6875 in between U38-U39-U40-U41 pin-1s."""


def decorate(sh):
    # power flags (flag and power port on the same point).  Below the
    # decoupling caption instead of nested between C8/C9 -- the old spot put
    # the +5V flag right on top of C8's "100nF" value text and the GND flag
    # on top of C9's own reference label (confirmed by rendering); a spot
    # merely past C10 turned out to sit on an existing wire (ERC: U10's Y
    # output shorted into the GND flag) -- below everything else on the
    # sheet is the one place guaranteed clear of both pads and wires.
    sh.flag(124.46, 264.16, 180)
    sh.power("+5V", 124.46, 264.16)
    sh.flag(124.46, 274.32, 0)
    sh.power("GND", 124.46, 274.32)
    sh.text(NOTES, 182.88, 15.0, 1.27)
    sh.text("Decoupling: C1 U1, C2 U2, C3 U3, C4 U4, C6 U7, C8 U5, C9 U9, C10 U10, C5 bulk.  JLC places the passives and every IC except U1 (no LCSC stock); U1 is always hand-soldered.", 124.46, 254.0)
