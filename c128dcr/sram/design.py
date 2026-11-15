"""C128DCR 256KB SRAM module -- single source of truth for the design.

Board coordinate system: mm, y down, origin = top-left pin of the U38
plug (which is DRAM pin 10).  The board is drawn as seen from above with
the C128DCR in its normal orientation (ROMs "up", front edge "down").
U38..U41 sit in a row left->right, all with pin 1 at the BOTTOM-RIGHT
(notch towards the front), on a common pitch of CHIP_PITCH -- as seen on
ytmytm's 128DCR photos (square pads / notches of his U38/U41 footprints).

CHIP_PITCH is caliper-measured off a real board: U38 pin 1 -> U41 pin 1
= 3 x CHIP_PITCH = 0.6875" x 3.
"""
import uuid
from board import Part, P
from footprints import dip_plug, sop32_450, tssop, sot23_5, chip_named, jst_ph2_horizontal, testpoint

CHIP_PITCH = 0.6875 * 25.4        # U38->U39->U40->U41 centre spacing (mm), caliper-measured
ROW = 7.62                         # 0.3" DIP row spacing

# Set True during a big structural churn (e.g. swapping a part's whole
# footprint, which moves everything around it) to skip the router's
# expensive DFM-polish passes -- the same-footprint neighbour-pad check and
# the wide multi-radius stub-via search -- while placement is still
# settling.  Real electrical safety (different-net clearance, hole-to-hole)
# is never part of this and stays enforced either way.  Flip back to False
# and do one full clean rebuild (0 DRC errors/warnings) before trusting the
# board again -- this only speeds up iteration, it does not replace the
# final verified build.
DRAFT_MODE = False

# Board outline (board coords).  Bottom edge (larger y, the C128's front
# edge): the top edge is permanently jammed against the JiffyDOS EPROM and
# Kernal ROM, but there's room to grow down here if a layout crunch needs
# it.  It doesn't right now -- the lowest part (C3) only reaches y=21.95
# -- so this stays at the original 25.32, not the +3mm it briefly needed
# when C4 shared this column too (see git history); shrink further if a
# future layout change frees up more room, just re-verify the router can
# still place CA3-CA6/A17, which currently dip down near y=25 to escape.
BOARD_Y1 = 25.32
BOARD = (-3.0, -6.0, 3 * CHIP_PITCH + ROW + 3.0, BOARD_Y1)
# Area the router may use; also fixes its grid origin, so the routing cache
# stays valid if the outline grows.  The top 1 mm strip of BOARD is margin
# for silkscreen and parts near the edge, deliberately left out of it.
ROUTE_AREA = (-3.0, -5.0, 3 * CHIP_PITCH + ROW + 3.0, BOARD_Y1)

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
# 4-layer stackup: GND and +5V each get a dedicated inner-layer plane
# (In1.Cu, In2.Cu) instead of competing with signals for F.Cu/B.Cu trace
# space.  Added specifically to free up the routing corridor near U41 so
# U7 can move without breaking GND/+5V connectivity (see git history: on
# 2 layers, relocating U7 off U41's gap stalled GND/+5V routing hard,
# because that gap doubled as their only path across the board).  Every
# SMD pad of a planed net gets a short stub + via straight to its plane
# instead of being routed hop-by-hop; through-hole pads (P1/P4) connect
# to the planes automatically (their pads already span every layer).
PLANE_POWER = True
# Let JLC assemble every SMD IC it can (extended-part fee applies) instead
# of hand-soldering all of them -- confirmed worth the ~$20 difference vs.
# passives-only after a test order (U38-U41's three TSSOP-20 latches plus
# the SOT-23-5 parts are fiddly enough by hand that it's worth paying JLC
# to do it right first time).  U1 is still always hand-soldered regardless
# of this flag -- LCSC has no stock for it at all (see its own Part.dnp in
# build_parts()).  Through-hole parts (P1, P4, J1) still can't go through
# JLC's standard SMT assembly regardless of this flag.
FULL_ASSEMBLY = True

# Test points (labelled on the silkscreen), in a row below the SRAM
TP_NETS = ["CE_PRE", "DLY", "WE_N", "A16", "A17", "GND", "+5V"]
TP_X0, TP_Y, TP_PITCH = 28.6, 20.5, 2.8

# Project identity (file names, KiCad library name, UUID namespaces)
PROJECT = "c128dcr-sram"
LIB = "c128sram"
LIB_DESCR = "C128DCR SRAM module"
SCH_NS = uuid.UUID("0d7a2f8c-3c55-4b0e-8a64-c128d5a0d002")
PCB_NS = uuid.UUID("6b1f3c2e-1d2a-4c8e-9b54-c128d5a0d001")
PCB_TITLE = ("C128DCR 256KB SRAM module", "1.0", "Replaces U38-U41 (4x 41464/41254 64Kx4 DRAM)")

# ---------------------------------------------------------------------------
# Parts
# ---------------------------------------------------------------------------
ALL18 = set(range(1, 19))
# 9 and 10 (the +5V pin and its unused neighbour) would otherwise be the
# only pads at their end of each column -- a lone pin has nothing to key
# a machined pin strip against and is fiddly to hold straight while
# soldering.  8 and 11 (also unused) give each one a same-column neighbour
# to solder as a short 2-pin strip instead; no-connect, mechanical only.
P4_PINS = {1, 2, 3, 8, 9, 10, 11, 15, 16, 17, 18}


def build_parts():
    cp = CHIP_PITCH
    parts = []

    # --- plugs into the original DRAM sockets --------------------------------
    # Only U38 and U41 carry signals we need (U38: D0-D3 + bank-0 /CAS,
    # U41: D4-D7 + bank-1 /CAS).  U39/U40 are spanned but not plugged.
    parts.append(Part("P1", "U38 plug", dip_plug("DIP18_Plug_U38", ALL18),
                      ROW, 20.32, 180, "dip18", P({
                          1: "GND", 2: "D0", 3: "D1", 4: "DWE", 5: "RAS",
                          6: "MA6", 7: "MA5", 8: "MA4", 9: "+5V", 10: "MA7",
                          11: "MA3", 12: "MA2", 13: "MA1", 14: "MA0", 15: "D2",
                          16: "CAS0", 17: "D3", 18: "GND"}),
                      descr="to U38 socket (bank 0, D0-D3)"))
    parts.append(Part("P4", "U41 plug", dip_plug("DIP18_Plug_U41", P4_PINS),
                      3 * cp + ROW, 20.32, 180, "dip18", P({
                          1: "GND", 2: "D4", 3: "D5", 8: None, 9: "+5V", 10: None,
                          11: None, 15: "D6", 16: "CAS1", 17: "D7", 18: "GND"}),
                      descr="to U41 socket (bank 1, D4-D7)"))

    # --- SRAM -----------------------------------------------------------------
    # Rotated 90 deg: pin 1 at bottom-left, pins 1..16 along the bottom edge
    # (left->right), 17..32 along the top edge (right->left).
    # IS61C5128AS-25QLI (512Kx8, ISSI): pin 1 = A17 directly (no relabeling
    # needed -- ISSI's own pin names match our address bus exactly, unlike
    # the original AS6C4008 which called this pin "A18" internally), pin 30
    # = A18 = tied to +5V (selects the chip's upper half, same role the
    # AS6C4008's pin 30 played).  Confirmed pin-for-pin identical to the
    # AS6C4008 on every other pin (same JEDEC-standard SOP-32 package) by
    # reading ISSI's actual datasheet -- this was a drop-in swap, not a
    # redesign.  Picked after AS6C4008 became hard to source: same spec
    # class (5V, TTL-compatible inputs, VIH 2.2V min, single CE), Active
    # production, real stock at Mouser/RS/Digikey, cheaper than the part
    # it replaces.  /OE = NOT DWE: the SRAM drives the bus only on reads.
    sram = {1: "A17", 2: "A16", 16: "GND", 22: "CE_PRE", 24: "OE_N",
            29: "WE_N", 30: "+5V", 32: "+5V"}
    # A0..A15 and DQ0..DQ7 are assigned later (pin swapping, see assign()).
    parts.append(Part("U1", "IS61C5128AS-25QLI", sop32_450(), 37.0, 10.16, 90,
                      "sram", P(sram), mpn="IS61C5128AS-25QLI",
                      descr="512K x 8 SRAM (256K used), 25ns, TTL-level inputs, SOP-32 450mil"))

    # --- address latches --------------------------------------------------------
    # U2 (row): rot 90 -> D row (pins 1..10) along the bottom, Q row on top.
    # U3 (col): rot 270 -> D row along the top, Q row along the bottom.
    # Same x so U2 pin p sits exactly above U3 pin 11-p.  x=20.5 splits the
    # U38-to-U1 run evenly: ~2.45mm clear of U4 (gap1) on one side, ~2.5mm
    # clear of U1 on the other.
    parts.append(Part("U2", "74AHCT573", tssop(20), 20.5, 4.6, 90, "573",
                      P({1: "GND", 10: "GND", 11: "ROWLE", 20: "+5V"}),
                      mpn="SN74AHCT573PWR",
                      descr="row address latch, LE = RAS & DLY"))
    parts.append(Part("U3", "74AHCT573", tssop(20), 20.5, 17.7, 270, "573",
                      P({1: "GND", 10: "GND", 11: "CE_N", 20: "+5V"}),
                      mpn="SN74AHCT573PWR",
                      descr="column address latch, LE = CE_N"))

    # --- /CAS decode, guaranteed delay, write and output enables -------------
    # CE_PRE (either /CAS low) is the SRAM /CE: one gate, fast reads, and the
    # write ends one gate after /CAS rises (CPU data hold).
    # DLY = CE_PRE through R4/C7 (tau 22 ns) and a Schmitt buffer: its edges
    # lag CE_PRE by >= ~16 ns, guaranteed by the RC and the threshold limits
    # rather than by gate speeds.  The bank latch closes on DLY (setup >> 3.5
    # ns) and a write can only start after DLY (A16 settled): /WE = DWE | DLY.
    # Every latch re-opens only after DLY rises, after /CE has gone high.
    # In gap1 (the U38-U39 socket gap): the only open space on the board big
    # enough for a TSSOP-14 once you count not just its own courtyard but
    # where its GND/+5V pins' plane-vias land (PLANE_POWER) -- U41's gap,
    # the U5/U9 gap, and the board's own top edge all ran out of room for
    # that once checked properly.  x=12.3, not dead-centre: P1 has no
    # courtyard (its "body" is the DRAM socket under the board, see
    # footprints.py), so that side can give up some of its 1.3mm margin to
    # grow U4's gap to U2 -- the only spot wide enough for C2 to sit at
    # U2's own +5V pin (20) instead of settling for its GND pins.
    parts.append(Part("U4", "74AHCT08", tssop(14), 12.3, 7.8, 90, "08",
                      P({1: "CAS0", 2: "CAS1", 3: "CE_PRE", 4: "CE_PRE", 5: "CE_PRE",
                         6: "CE_D", 7: "GND", 8: "CE_N", 9: "CE_D", 10: "CE_D",
                         11: "ROWLE", 12: "RAS", 13: "DLY", 14: "+5V"}),
                      mpn="SN74AHCT08PWR",
                      descr="CE_PRE = CAS0 & CAS1; CE_N = CE_PRE + 2 gates; ROWLE = RAS & DLY"))
    # U5 stays at y=-1.8, clear of U2 below it.  U9/U10 sit lower, at
    # y=-3.5, close to the top edge: that leaves C9/C10 (their decoupling,
    # below them) a real gap to U1 for their plane-vias to escape through,
    # rather than crowding U1's own pads.  x=29.0 for U9 (not centred in
    # its own gap) leaves 3.1mm to U7, which sits in the same y band.
    parts.append(Part("U5", "74LVC1G17", sot23_5(), 19.5, -1.8, 0, "1g17",
                      P({1: None, 2: "DLY_RC", 3: "GND", 4: "DLY", 5: "+5V"}),
                      mpn="SN74LVC1G17DBVR",
                      descr="Schmitt buffer: DLY = CE_PRE delayed by the RC (CMOS-driven input)"))
    parts.append(Part("U9", "74AHCT1G32", sot23_5(), 29.0, -3.5, 0, "1g32",
                      P({1: "DWE", 2: "DLY", 3: "GND", 4: "WE_N", 5: "+5V"}),
                      mpn="74AHCT1G32W5-7",
                      descr="SRAM /WE = DWE | DLY: a write starts only after the bank latch has settled"))
    # x=47.0, not 46.0: its old spot put U10's GND stub-via only 0.57mm from
    # C6's (same net, but still too close for drill-to-drill spacing), so
    # neither could find a clear spot and both ended up overlapping their
    # own pad (the JLC/MacroFab "pad spacing"/"via to pad" dangers).  +1mm
    # buys real separation.  Checked against pads only the first time and
    # missed that P4's own SILKSCREEN outline box (not just its pads) runs
    # the full height from y=-1.33 to 21.65 at x=48.20 -- +2mm put U10's own
    # body marks right on that line; +1mm clears it with real margin.
    parts.append(Part("U10", "74AHCT1G04", sot23_5(), 47.0, -3.5, 0, "1g04",
                      P({1: None, 2: "DWE", 3: "GND", 4: "OE_N", 5: "+5V"}),
                      mpn="SN74AHCT1G04DBVR",
                      descr="SRAM /OE = NOT DWE: outputs off during writes"))
    # Bank latch: transparent while DLY is high, holds A16/A17 while the SRAM
    # is selected.  A16 = which bank /CAS fired (CAS0 high = bank 1), A17 =
    # BANK_HI from the MMU riser (low = banks 2/3; pulled high without it).
    # A '573 because its TTL-level inputs take /CAS0 (74F32 output) and
    # BANK_HI (8722 NMOS output) directly.  In the top margin strip: this
    # is the biggest part up here (TSSOP-20), so it gets boxed in on every
    # side (board edge, U1, U9, U10) with no spare room for its own
    # decoupling cap beside it -- see C6 below.  y=-1.7 splits the ~0.9mm
    # of slack between the edge and U1 evenly, ~0.45mm each side.
    parts.append(Part("U7", "74AHCT573", tssop(20), 38.0, -1.7, 0, "573",
                      P({1: "GND", 2: "CAS0", 3: "BANK_HI", 4: "GND", 5: "GND", 6: "GND",
                         7: "GND", 8: "GND", 9: "GND", 10: "GND", 11: "DLY",
                         12: None, 13: None, 14: None, 15: None, 16: None, 17: None,
                         18: "A17", 19: "A16", 20: "+5V"}),
                      mpn="SN74AHCT573PWR",
                      descr="bank latch: A16 = /CAS0, A17 = BANK_HI, LE = DLY"))

    # --- test points for the timing measurements --------------------------------
    for k, net in enumerate(TP_NETS):
        parts.append(Part("TP%d" % (k + 1), net, testpoint(), TP_X0 + k * TP_PITCH, TP_Y, 0, "tp",
                          P({1: net}), descr="test point"))

    # --- BANK_HI from the MMU riser + pull-up ----------------------------------
    # Horizontal (side-entry), pointing off the RIGHT edge, past U41: the top
    # edge has zero clearance on a real C128DCR (jammed against the JiffyDOS
    # EPROM and Kernal ROM) the whole way across -- confirmed with the v1.0
    # fit-check template, so growing the board that direction isn't an
    # option either.  Nested inside the U41 plug's silkscreen outline
    # instead of past it -- dip_plug() has no courtyard there on purpose
    # (its "body" is the DRAM socket under the board) -- so its cable tip
    # just reaches the original board edge and J1 needs no extra width.
    # y=8.62 centers its courtyard between P4's pin 8/11 row (y=2.54) and
    # pin 15 (y=12.7), the only two things in that gap to clear.
    parts.append(Part("J1", "BANK_HI", jst_ph2_horizontal(), 3 * cp + 2.47, 8.62, 90, "jst2",
                      P({1: "BANK_HI", 2: "GND"}), mpn="S2B-PH-K-S",
                      descr="from the MMU riser (leave open for 128K; JST PH 2-pin, side-entry)"))

    # --- decoupling: one 100nF touching (or as close as the courtyard/
    # clearance rules allow) each IC's own body, plus the bulk cap and the
    # DLY_RC/BANK_HI passives next to whatever they're electrically
    # closest to.  Every IC that used to be "boxed in with no room" (U1,
    # U5, U7) gets a dedicated pocket instead of settling for the nearest
    # open gap -- gap1's lower column now only holds U4's own RC/decoupling
    # pair and the bulk cap, since C6/C1 have real homes next to U7/U1.
    # In the U4-U2 gap, right at U2's own +5V pin (20, its top-left corner)
    # rather than down in the U2-U3 gap near its GND pins (1/10) instead:
    # rotated 90 so its short 1.45mm courtyard dimension (not the full
    # 2.95mm body) spans the gap -- U4 moved 0.6mm off-centre (see its own
    # comment) specifically to make this gap wide enough.
    parts.append(Part("C2", "100nF", chip_named("C", "0603"), 16.0, 2.3, 90, "c",
                      P({1: "+5V", 2: "GND"}), descr="decoupling (U2), at its +5V pin (20)"))
    # Below U3, right at its own +5V pin (20, its bottom-right corner) --
    # open board between U3's right edge (24.03) and TP1 (27.67); rotated
    # 90 for clearance the same way C1/C2 needed.
    parts.append(Part("C3", "100nF", chip_named("C", "0603"), 25.2, 20.0, 90, "c",
                      P({1: "+5V", 2: "GND"}), descr="decoupling (U3), at its +5V pin (20)"))
    parts.append(Part("C9", "100nF", chip_named("C", "0603"), 29.0, -0.5, 0, "c",
                      P({1: "+5V", 2: "GND"}), descr="decoupling (U9), below it, clear of U1's pads"))
    # x=46.5: P4's pin column itself is at x=52.39, with its silkscreen
    # OUTLINE box running the full height at x=51.06 (not just its pads) --
    # C10's own ref text and its GND pad both reached past that line at
    # 47.0 when P4's pin column was still at the old (pre-caliper-measured)
    # x=49.53, close enough back then that the silkscreen would print right
    # over the pad's copper.  46.5 clears both with real margin either way.
    parts.append(Part("C10", "100nF", chip_named("C", "0603"), 46.5, -0.5, 0, "c",
                      P({1: "+5V", 2: "GND"}), descr="decoupling (U10), below it, clear of U1's pads"))
    # Directly above U5, in the gap to the board's top edge -- the only
    # side of U5 not already claimed by U2 (below) or U4 (left, different
    # y band entirely).
    parts.append(Part("C8", "100nF", chip_named("C", "0603"), 19.5, -4.5, 0, "c",
                      P({1: "+5V", 2: "GND"}), descr="decoupling (U5), directly above it"))
    parts.append(Part("C7", "33pF", chip_named("C", "0603"), 12.3, 1.0, 0, "c",
                      P({1: "DLY_RC", 2: "GND"}), descr="delay RC, C0G, gap1 next to U4"))
    # Wedged directly beside U7, in the U7-U10 gap, rotated 90 so its short
    # 1.45mm dimension (not the full 2.95mm body) faces across the gap.
    parts.append(Part("C6", "100nF", chip_named("C", "0603"), 42.9, -1.1, 90, "c",
                      P({1: "+5V", 2: "GND"}), descr="decoupling (U7), directly beside it"))
    # In the U1-U2 gap, right at U1's own +5V pins (30/32, its top-left
    # corner) rather than merely beside the chip body: the right edge
    # (old spot) is close to pin 16 (GND) but nowhere near the actual
    # +5V pins, which sit here instead.  This is the board's densest
    # routing region (RA0-7/MA1-7/CAS0-1/etc all cross it), and the gap
    # (U2's right edge 24.03 to U1's left edge 26.47, 2.44mm) is too
    # narrow for an unrotated 0603 -- rotated 90 so its short 0.8mm
    # dimension (not the 1.6mm body) spans the gap, same trick as C6.
    parts.append(Part("C1", "100nF", chip_named("C", "0603"), 25.25, 3.46, 90, "c",
                      P({1: "+5V", 2: "GND"}), descr="decoupling (U1), at its +5V pins (30/32)"))
    # Between P1 and C7, right at U4's own +5V pin (14, its top-left
    # corner) rather than down past its GND pins (7) instead -- rotated 90
    # for the same gap-width reason as C1/C2.  y=1.3, not 2.0: pin 14's own
    # +5V stub-via lands almost straight down from the pin (10.35,3.50),
    # only 0.85mm from C4's pad at y=2.0 -- just over the drill-to-drill
    # minimum, leaving no room for C4's own via to land anywhere nearby.
    # y=1.3 opens that back up to ~1.5mm.
    parts.append(Part("C4", "100nF", chip_named("C", "0603"), 9.9, 1.3, 90, "c",
                      P({1: "+5V", 2: "GND"}), descr="decoupling (U4), at its +5V pin (14)"))
    parts.append(Part("R4", "680", chip_named("R", "0603"), 12.3, 15.3, 0, "r",
                      P({1: "DLY_RC", 2: "CE_PRE"}), descr="delay RC (680R x 33pF = 22 ns), gap1"))
    parts.append(Part("C5", "10uF", chip_named("C", "0805"), 12.3, 18.3, 0, "c",
                      P({1: "+5V", 2: "GND"}), descr="bulk, gap1"))
    # P4's trough (the same unused-pin gap U7 used to occupy): clear of
    # P4's own pads above (pin 15, y=12.7) and below (pin 1/18, y=20.32).
    # 3*cp + ROW/2 centers it between P4's two pin columns (3*cp and 3*cp+ROW).
    parts.append(Part("R1", "10k", chip_named("R", "0603"), 3 * cp + ROW / 2, 15.0, 0, "r",
                      P({1: "BANK_HI", 2: "+5V"}), descr="BANK_HI pull-up, below J1"))

    # LCSC part numbers.  JLC assembles the passives (all JLC "basic" parts, so
    # no extended-part fees there) plus every IC except U1 (FULL_ASSEMBLY,
    # extended-part fee); the through-hole parts are always hand-soldered.
    # The IC numbers are kept for ordering from LCSC even where DNP, so U1 --
    # the one chip LCSC has no stock for at all -- can still be bought
    # elsewhere and hand-fit the same way the rest of the board used to be.
    lcsc = {"U2": "C141311", "U3": "C141311", "U7": "C141311", "U4": "C155175",
            "U5": "C7836", "U9": "C842290", "U10": "C30326", "J1": "C173752",
            "R1": "C25804", "R4": "C23228", "C7": "C1663", "C5": "C15850"}
    # Manufacturer part numbers for the passives (the ICs and J1 already got
    # theirs via Part(..., mpn=...) above).  These are the actual MPN behind
    # each LCSC number -- not just any equivalent -- confirmed against
    # lcsc.com/jlcpcb.com, so a non-LCSC assembler (MacroFab etc., which only
    # auto-matches a BOM line that carries a real MPN) can source the same
    # physical part instead of being stuck on an LCSC-only catalog number.
    passive_mpn = {"R1": "0603WAF1002T5E", "R4": "0603WAF6800T5E",
                   "C7": "CL10C330JB8NNNC", "C5": "CL21A106KAYNNNE"}
    # Manufacturer names, for non-LCSC assemblers (MacroFab etc.) that key
    # off Manufacturer + Part Number rather than an LCSC catalog number.
    # P1/P4 have none -- their BOM note already says generic-or-Mill-Max,
    # buyer's choice, not one fixed part.
    manufacturer = {"U1": "ISSI", "U2": "Texas Instruments",
                     "U3": "Texas Instruments", "U7": "Texas Instruments",
                     "U4": "Texas Instruments", "U5": "Texas Instruments",
                     "U9": "Diodes Incorporated", "U10": "Texas Instruments",
                     "J1": "JST", "R1": "UNI-ROYAL", "R4": "UNI-ROYAL",
                     "C5": "Samsung Electro-Mechanics", "C7": "Samsung Electro-Mechanics"}
    for p in parts:
        p.lcsc = lcsc.get(p.ref, "C14663" if p.value == "100nF" else "")
        if not p.mpn:
            p.mpn = passive_mpn.get(p.ref, "CC0603KRX7R9BB104" if p.value == "100nF" else p.mpn)
        p.manufacturer = manufacturer.get(p.ref, "Yageo" if p.value == "100nF" else "")
        # U1 is always DNP: LCSC has no stock for it at all, so it's always
        # hand-soldered (buy from Mouser/DigiKey) regardless of FULL_ASSEMBLY.
        # Every other IC is JLC-assembled by default now -- confirmed worth
        # the extended-part fee after a test order (see FULL_ASSEMBLY above).
        p.dnp = p.ref == "U1"
        p.hand = p.ref[0] in "UPJ"
    # silkscreen reference positions (footprint-local) where the default
    # (above the part) collides with a neighbour's pad or body.  U9/U10 sit
    # close to the top edge, so their default ref runs off the board.
    # C9/C10 sit right below U9/U10 with little vertical room either way,
    # so their ref is moved to the side instead of above/below -- C10 to
    # the left, since P4's own silkscreen outline is immediately to its
    # right.  C2/C3 sit under U2/U3's own near-edge pads, so theirs is
    # moved toward U1's side, the only direction in that gap with clear
    # board.
    # gap1's lower pocket (R4, C4, C6, C1, C5): each part's default ref
    # sits *above* it and its value *below* it, both offset ~1.4-1.7mm
    # (more for C5's bigger 0805 body) from its own centre.  R4/C5 now sit
    # well clear of U4 (which moved away long ago) so they just use their
    # own default "above" position split apart -- R4 stays above, C5
    # pushed to below instead -- which also moves both well clear of P1's
    # silkscreen outline on their left.
    ref_at = {"U9": (-3.0, 0.0), "U10": (3.5, 0.0),
              "C9": (2.2, 0.0), "C10": (0.0, 1.4),
              "C5": (0.0, 1.9), "R4": (2.2, 0.0),
              "C8": (2.2, 0.0), "C6": (-2.4, 0.0), "C1": (2.4, 0.0),
              "C3": (-0.21, 1.39), "C4": (2.3, 0.0),
              "J1": (3.5, -2.55), "U5": (-3.0, 0.0)}
    for p in parts:
        if p.ref in ref_at:
            p.ref_at = ref_at[p.ref]
    return parts


def assign(parts):
    """Pin swapping: address bits and data bits of a static RAM are fully
    interchangeable, and a '573 is 8 identical channels.  Assign them by
    geometry so the buses nest instead of crossing.  Mutates pin maps."""
    pm = {p.ref: p for p in parts}
    u1, u2, u3 = pm["U1"], pm["U2"], pm["U3"]

    def px(part, num):
        for pd in part.fp.pads:
            if pd.num == str(num):
                return part.pad_xy(pd)[0]
        raise KeyError(num)

    # MA lines in the order they are expected to stack (top->bottom) in the
    # corridor between U2 and U3; top line -> right-most column.
    ma_tb = ["MA7", "MA4", "MA3", "MA5", "MA2", "MA6", "MA1", "MA0"]
    u2d = sorted(range(2, 10), key=lambda p: -px(u2, p))      # right->left
    for p, ma in zip(u2d, ma_tb):
        u2.pins[str(p)] = ma
        u3.pins[str(11 - p)] = ma                                 # same column

    # Row latch U2 (Q row on top): two right-most Q pins dive into the SRAM
    # body to bottom-row pins 3,4; the other six nest over the top to the
    # six top-row address pins (left-most Q -> right-most SRAM pin).
    q2 = sorted(range(12, 20), key=lambda p: px(u2, p))       # left->right
    top_addr = sorted([31, 28, 27, 26, 25, 23], key=lambda p: -px(u1, p))
    tg2 = dict(zip(q2[:6], top_addr))
    tg2[q2[6]] = 4
    tg2[q2[7]] = 3
    for q, t in tg2.items():
        ch = 19 - q
        net = "RA%d" % ch
        u2.pins[str(q)] = net
        u1.pins[str(t)] = net

    # Column latch U3 (Q row at bottom) -> bottom-row pins 5..12.
    q3 = sorted(range(12, 20), key=lambda p: -px(u3, p))      # right->left
    bot_addr = sorted(range(5, 13), key=lambda p: px(u1, p))  # left->right
    for q, t in zip(q3, bot_addr):
        ch = 19 - q
        net = "CA%d" % ch
        u3.pins[str(q)] = net
        u1.pins[str(t)] = net

    # Data.  D4..D7 come from the lower half of U41 (right) -> the DQ pins
    # at the bottom-right corner of the SRAM (+ pin 17 just above them).
    for pin, d in ((17, "D6"), (15, "D7"), (14, "D4"), (13, "D5")):
        u1.pins[str(pin)] = d
    # D0..D3 come from U38 (left) -> remaining DQ pins on the top edge.
    for pin, d in ((21, "D2"), (20, "D1"), (19, "D3"), (18, "D0")):
        u1.pins[str(pin)] = d
    return parts


# IS61C5128AS-25QLI datasheet (ISSI, SOP-32): single chip-enable only, no
# dual-function CE2 like some other vendors' same-density SRAMs.  Pin 30
# (A18 in ISSI's own naming) is tied to +5V in `sram` above, which is a
# real address bit (not a chip-enable): that permanently selects the
# chip's upper half, using 256K of its 512K capacity -- not a disable.
SRAM_PIN_NAMES = {1: "A17", 2: "A16", 3: "A14", 4: "A12", 5: "A7", 6: "A6",
                  7: "A5", 8: "A4", 9: "A3", 10: "A2", 11: "A1", 12: "A0",
                  13: "DQ0", 14: "DQ1", 15: "DQ2", 16: "VSS", 17: "DQ3",
                  18: "DQ4", 19: "DQ5", 20: "DQ6", 21: "DQ7", 22: "~{CE}",
                  23: "A10", 24: "~{OE}", 25: "A11", 26: "A9", 27: "A8",
                  28: "A13", 29: "~{WE}", 30: "A18", 31: "A15", 32: "VCC"}


def get_design():
    parts = assign(build_parts())
    nets = {}
    for p in parts:
        for num, n in p.pins.items():
            if n:
                nets.setdefault(n, []).append((p.ref, num))
    return parts, nets
