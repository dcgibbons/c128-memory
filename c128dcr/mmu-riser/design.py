"""C128DCR MMU riser -- single source of truth for the design.

Plugs into the 8722 MMU socket (U7) and carries the stock 8722 plus a second
8722 that tracks the 256K bank bits.  The second MMU's /CAS1 output says
"banks 2/3"; it leaves on J1 as BANK_HI for the SRAM module, which does the
rest of the bank decoding.  Nothing else is wired to the motherboard.

Based on ytmytm's c128-mmu-256k-exp (after Marko Makela and Pekka Pessi):
the three DIP-48 footprints are interleaved on 6.35 mm offsets exactly as
on his board, which is proven to fit between the SID and the power socket
of a C128DCR.  Board coordinates: mm, y down, origin = top-left corner of
the board; his board's origin was (80.01, 25.4).

Second-MMU data remap: one ATF16V8B GAL (gal/riser.pld, checked by
gal/check_gal.py), as on ytmytm's board but with two changes:
  D6 into MMU2 = CPU D6 at $xxx5 (MCR), $xxx7 (P0L), $xxx9 (P1L), else D7
  D0 into MMU2 = CPU D1 at $xxx8/$xxxA (P0H/P1H), else D0
P0L/P1L are passed unremapped (they hold page numbers, which must match the
stock MMU), and the GAL drives D0/D6 only during writes (OE = /R/W).
MMU2's D1-D5/D7 share the bus with the stock MMU; both chips hold the same
bits there and return the same values on register reads.  Which of MMU2's
remaining pins are safe to tie to the stock MMU and which must be left
floating is checked against the 8722's own pin description (not just the
hobbyist schematic above) -- see the comment at MMU2_NETS below.
"""
import uuid
from board import Part, P
from footprints import dip_plug, tssop, chip_named, jst_ph2_horizontal

# --- board -------------------------------------------------------------------
BOARD = (0.0, 0.0, 39.37, 75.0)
ROUTE_AREA = BOARD

# Design rules (mm)
CLEARANCE = 0.15
W_SIG = 0.2
W_PWR = 0.4
VIA_D = 0.6                        # 0.15mm annular ring. Tried 0.7 (0.2mm ring) for
VIA_DRILL = 0.3                    # JLC's DFM "Good" rating: it nearly tripled the
# "silkscreen to pad/hole" Danger count instead (bigger via copper = less
# clearance to nearby silkscreen on this dense a board) -- net worse, reverted.
EDGE_CLR = 0.35

POWER_NETS = {"GND", "+5V"}

# Project identity (file names, KiCad library name, UUID namespaces)
PROJECT = "c128dcr-mmu-riser"
LIB = "c128mmu"
LIB_DESCR = "C128DCR MMU riser"
SCH_NS = uuid.UUID("4f6a1c2d-7e3b-4a59-9c21-c128d5a0d003")
PCB_NS = uuid.UUID("9b2e5d71-0c4f-4e86-a3d7-c128d5a0d004")
PCB_TITLE = ("C128DCR MMU riser", "1.0", "Stock 8722 + second 8722 for 256K banks; BANK_HI to the SRAM module")

# --- the three DIP-48 positions (pin 1 of each, rotated 180: pins 1-24 run up
# the right column, 25-48 down the left column) ----------------------------
ROW48 = 15.24
PIN1_Y = 60.95
PLUG_X = 24.135        # P1: machined pins into the motherboard's U7 socket
MMU1_X = 17.785        # U1: socket for the stock 8722
MMU2_X = 36.835        # U2: socket for the second 8722

MMU_PIN_NAMES = {
    1: "VDD", 2: "~{RESET}", 3: "TA15", 4: "TA14", 5: "TA13", 6: "TA12", 7: "TA11",
    8: "TA10", 9: "TA9", 10: "TA8", 11: "~{CAS1}", 12: "~{CAS0}", 13: "MS2/~{IOSEL}",
    14: "MS1", 15: "MS0", 16: "AEC", 17: "MUX", 18: "A0", 19: "A1", 20: "A2", 21: "A3",
    22: "A5/A4", 23: "A7/A6", 24: "A8", 25: "A9", 26: "A10", 27: "A11", 28: "A12",
    29: "A13", 30: "A14", 31: "A15", 32: "R/~{W}", 33: "PHI_IN", 34: "VSS", 35: "D0",
    36: "D1", 37: "D2", 38: "D3", 39: "D4", 40: "D5", 41: "D6", 42: "D7",
    43: "~{Z80EN}", 44: "~{FSDIR}", 45: "~{GAME}", 46: "~{EXROM}", 47: "MS3", 48: "40/~{80}"}

# motherboard net on each MMU pin
MMU_NETS = {
    1: "+5V", 2: "RESET", 3: "TA15", 4: "TA14", 5: "TA13", 6: "TA12", 7: "TA11", 8: "TA10",
    9: "TA9", 10: "TA8", 11: "GCAS1", 12: "GCAS0", 13: "MS2", 14: "MS1", 15: "MS0",
    16: "AEC", 17: "MUX", 18: "A0", 19: "A1", 20: "A2", 21: "A3", 22: "A5A4", 23: "A7A6",
    24: "A8", 25: "A9", 26: "A10", 27: "A11", 28: "A12", 29: "A13", 30: "A14", 31: "A15",
    32: "RW", 33: "PHI", 34: "GND", 35: "D0", 36: "D1", 37: "D2", 38: "D3", 39: "D4",
    40: "D5", 41: "D6", 42: "D7", 43: "Z80EN", 44: "FSDIR", 45: "GAME", 46: "EXROM",
    47: "MS3", 48: "4080"}

# Second MMU: only /CAS1 is used (-> CAS1_HI below); every other MMU2 pin is
# either left tied to the stock MMU (confirmed safe to share) or floated
# (confirmed to be a genuine driven output, so tying it would risk bus
# contention between the two chips).  Source: the MOS 8722's own pin
# description, not the hobbyist schematic this board is otherwise based on --
# Commodore 128 Programmer's Reference Guide (Bantam Books, 1986), Chapter 16
# "C128 Hardware Specifications", Table 16-9 "MMU Pin Requirements" + the
# signal-by-signal Pin Description + Figure 16-9.
#
# Left tied to the stock MMU (pins not touched below):
#   AEC (16), MUX (17): the datasheet lists both as pure inputs, so both
#     MMUs just read the same motherboard signal -- no contention possible.
#   GAME (45), EXROM (46), 40/80 (48): the datasheet describes these as
#     bidirectional with "a passive depletion mode pull-up on input;
#     external hardware can pull this line down, but not up" -- textbook
#     open-drain/wired-AND.  Two MMUs pulling the same net low is safe by
#     design, so tying these is fine.
MMU2_NETS = dict(MMU_NETS)

# Floated: the datasheet lists these as true driven outputs, with no
# open-drain/pull-up language, so tying two MMUs' copies together would be
# genuine contention against the stock MMU:
#   TA15-TA8 (3-10): "Translated address outputs"; TA12-15 have only a weak
#     internal pull-up, TA8-11 go tri-state during VIC time -- real bus
#     drivers either way.
#   /CAS0 (12), MS2/IOSEL (13), MS1 (14), MS0 (15): driven outputs to the
#     PLA (ROM-bank/IO-bank selects).
for _p in (3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15):
    MMU2_NETS[_p] = None

# /Z80EN (43) and MS3/C128-mode (47): also listed under "MMU output
# signals" in the datasheet, but *without* the open-drain/pull-up language
# given to GAME/EXROM/40-80 above -- nothing in the datasheet says tying
# these between two chips is safe.  (The hobbyist reference board this
# design is based on ties them anyway, relying on its GAL keeping both
# MMUs' Mode Configuration Registers in lockstep so the two chips never
# actually disagree -- a software guarantee, not a hardware one.)  Floated
# here instead, since nothing on this board reads MMU2's copies.
#
# /FSDIR (44): the datasheet groups this with GAME/EXROM/40-80 (same
# open-drain/pull-up language), so tying it would also be safe -- floated
# anyway since it's unused and floating costs nothing.
for _p in (43, 44, 47):
    MMU2_NETS[_p] = None

# The one MMU2 output this board actually uses: instead of tying /CAS1 to
# the stock MMU's own /CAS1 (net "GCAS1" above -- genuine contention, it's
# a true driven output, not open-drain), give it its own net so it can be
# read out independently as "banks 2/3".
MMU2_NETS[11] = "CAS1_HI"
# D0/D6 are remapped by the GAL instead of carrying the real bus value.
MMU2_NETS[35] = "D0X"
MMU2_NETS[41] = "D6X"

ALL48 = set(range(1, 49))

# ATF16V8B (DIP-20, socketed) in the strip below the MMUs: pin 1 position
GAL_X, GAL_Y = 9.6, 73.12


def build_parts():
    parts = []
    plug = dip_plug("DIP48_Plug_U7", ALL48, n=48, row=ROW48, pad=1.5, drill=0.9, silk=False,
                    descr="DIP-48 plug (machined pin strips) into the C128DCR MMU socket U7")
    # small notch: on the interleaved layout P1's pin 48 sits right beside U1's notch
    sock = dip_plug("DIP48_Socket", ALL48, n=48, row=ROW48, pad=1.5, drill=0.9,
                    descr="DIP-48 600mil socket", notch=0.45)
    parts.append(Part("P1", "to U7 socket", plug, PLUG_X, PIN1_Y, 180, "plug48",
                      P(MMU_NETS), descr="machined pins into the motherboard MMU socket U7"))
    parts.append(Part("U1", "8722 (stock)", sock, MMU1_X, PIN1_Y, 180, "mmu",
                      P(MMU_NETS), mpn="MOS 8722", descr="the computer's own MMU, moved from U7"))
    parts.append(Part("U2", "8722 (second)", sock, MMU2_X, PIN1_Y, 180, "mmu",
                      P(MMU2_NETS), mpn="MOS 8722",
                      descr="second MMU: tracks the 256K bank bits, /CAS1 = banks 2/3"))

    # --- second-MMU data remap: one ATF16V8B (equations in gal/riser.pld) ----
    # Socketed DIP-20 so it can be reprogrammed.  Rotated 90: pins 1-10 along
    # the bottom row (pin 1 left), pins 20-11 along the top row.
    gal = dip_plug("DIP20_Socket_GAL", set(range(1, 21)), n=20, row=7.62, pad=1.6, drill=0.9,
                   descr="DIP-20 300mil socket for the ATF16V8B")
    # Pins 11-14/17-19: unused in Complex Mode (riser.jed: SYN=1, AC0=1) --
    # pin 11 is the dedicated input I9, and 12-14/17-19 are I/O macrocells
    # with their output enable permanently off (OE product term all zero),
    # so every one of them is a floating CMOS input as programmed. Tied to
    # GND per Microchip's datasheet guidance for unused ATF16V8B/GAL16V8
    # inputs (noise immunity, avoids a bias current with no defined logic
    # level). Declaring them "input" rather than "tri_state" on the
    # schematic symbol (sch_content.py) is what makes this ERC-clean: ERC
    # has no way to know their output drivers are permanently disabled, so
    # as "tri_state" it reads tying them to GND as a driver shorted to a
    # power rail (tried that first, 6 warnings, failed the build's own
    # ERC-clean gate) -- "input" matches what they actually are once
    # programmed, same as pin 11/I9 already was.
    parts.append(Part("U3", "ATF16V8B", gal, GAL_X, GAL_Y, 90, "gal",
                      P({1: "A0", 2: "A1", 3: "A2", 4: "A3", 5: "RW", 6: "D0", 7: "D1",
                         8: "D6", 9: "D7", 10: "GND", 11: "GND", 12: "GND", 13: "GND",
                         14: "GND", 15: "D0X", 16: "D6X", 17: "GND", 18: "GND", 19: "GND",
                         20: "+5V"}),
                      mpn="ATF16V8B-15PU",
                      descr="programmed with gal/riser.jed: D6X/D0X remap, driven only on writes"))

    # --- BANK_HI out ------------------------------------------------------------
    parts.append(Part("R1", "100", chip_named("R", "0603"), 35.8, 65.0, 0, "r",
                      P({1: "BANK_HI", 2: "CAS1_HI"}), descr="series resistor for the cable"))
    # Horizontal (side-entry): shroud points off the left edge so the cable
    # lies flat instead of standing up -- lower profile and self-keyed.
    parts.append(Part("J1", "BANK_HI", jst_ph2_horizontal(), 3.2, 69.8, 270, "jst2",
                      P({1: "BANK_HI", 2: "GND"}), mpn="S2B-PH-K-S",
                      descr="to the SRAM module J1 (JST PH 2-pin cable, side-entry)"))

    # --- decoupling ---------------------------------------------------------
    caps = [("C1", 17.785, 63.45, 0, "100nF"),    # U1 (stock MMU)
            ("C2", 35.8, 63.45, 0, "100nF"),      # U2 (second MMU)
            ("C3", 5.5, 63.45, 0, "100nF"),       # U3 (GAL), next to its VCC pin 20
            ("C4", 35.8, 67.4, 0, "10uF")]        # bulk
    for ref, x, y, r, v in caps:
        parts.append(Part(ref, v, chip_named("C", "0805" if v == "10uF" else "0603"),
                          x, y, r, "c", P({1: "+5V", 2: "GND"}), descr="decoupling"))

    lcsc = {"U3": "C6509", "J1": "C173752", "R1": "C22775", "C4": "C15850"}
    for p in parts:
        p.lcsc = lcsc.get(p.ref, "C14663" if p.value == "100nF" else "")
        p.dnp = False
        p.hand = p.ref[0] in "UPJ"
    # silkscreen reference positions (footprint-local) where the default collides
    ref_at = {"C1": (-2.2, 0.0), "C2": (-2.2, 0.0), "C3": (-2.2, 0.0),
              "R1": (2.2, 0.0), "C4": (2.4, 0.0)}
    for p in parts:
        if p.ref in ref_at:
            p.ref_at = ref_at[p.ref]
    return parts


def get_design():
    parts = build_parts()
    nets = {}
    for p in parts:
        for pin, net in p.pins.items():
            if net:
                nets.setdefault(net, []).append((p.ref, pin))
    return parts, nets
