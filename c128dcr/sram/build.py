#!/usr/bin/env python3
"""Build the C128DCR SRAM module: route, check, write KiCad files and outputs.

    python3 build.py            # route (~5 min) + write everything
    python3 build.py --reuse    # reuse cached routing (route_cache.pkl)
    python3 build.py --offline  # skip the part-number check (no network)
    python3 build.py --no-kicad # skip KiCad (zone fill, ERC/DRC, fab/): exits non-zero

Outputs go to kicad/ (KiCad project), docs/ (previews, BOM) and, if KiCad is
installed, fab/ (Gerbers, drill, JLC files, schematic PDF, ERC/DRC reports).
Shared code lives in ../../common.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(ROOT, "common")]

import buildlib as BL  # noqa: E402
import design as D  # noqa: E402
import sch_content  # noqa: E402
import sch_writer as SW  # noqa: E402
from footprints import oshw_logo  # noqa: E402

SW.use(sch_content)


def bom_note(refs, fp, hand, dnp=False):
    if fp.startswith("DIP18"):
        return ("machined round-pin male strip 2.54mm (generic LCSC/AliExpress is fine; "
                "Mill-Max 350 / Preci-Dip 310 if you prefer), %d pins"
                % (18 if "P1" in refs else len(D.P4_PINS)))
    if refs == ["U1"]:
        # write_bom() already prepends "HAND-SOLDER (not vendor-assembled)"
        # for any dnp part (U1 is the only one) -- don't repeat it here.
        return "buy from Mouser/DigiKey (no LCSC stock). Substitutes need VIH <= 2.4 V at 5 V"
    if fp.startswith("TestPoint"):
        return "bare pad, no part"
    if hand and fp.startswith(("SOP", "TSSOP", "SOIC", "SOT")):
        if dnp:
            return "buyer hand-solders their own"
        if getattr(D, "FULL_ASSEMBLY", False):
            return "JLC-assembled (extended-part fee)"
        return "hand-solder (not in JLC assembly files)"
    return ""


def main():
    reuse = "--reuse" in sys.argv
    paths = BL.Paths(HERE)
    parts, nets = D.get_design()
    parts_ok = BL.check_parts(parts, offline="--offline" in sys.argv)
    r, left = BL.route(paths, parts, nets, reuse)
    segs, vias = r.items()
    ok = BL.check(parts, segs, vias, left)

    # Title/J1 caption sit below the decoupling row now, as a footer right
    # above the bottom edge, instead of between the row and the rest of the
    # circuit -- reads more naturally that way.  y=24.0 keeps the same
    # ~1.3mm margin to BOARD_Y1 that y=27.0 had before the edge moved back
    # in to 25.32 (see BOARD_Y1's own comment).
    texts = [("C128DCR 256K SRAM v1.0", -2.0, 24.0, 0.9, "F.SilkS", "left"),
             ("U38", 3.81, -2.3, 1.0, "F.SilkS", ""),
             ("U41", 3 * D.CHIP_PITCH + D.ROW / 2, -2.3, 1.0, "F.SilkS", ""),
             # right-justified at BOARD's right edge minus 1.0mm, the same
             # margin the title gets from the left edge (-2.0 vs -3.0) --
             # balances the footer row.
             ("J1: BANK_HI from MMU riser", D.BOARD[2] - 1.0, 24.0, 0.9, "F.SilkS", "right")]
    # test point labels, under each test point.  testpoint()'s own value
    # (the full net name, e.g. "CE_PRE") is hidden -- at TP_PITCH there's
    # no room for names that long without running into their neighbours --
    # these short labels on F.SilkS are what actually identifies each pin.
    labels = {"CE_PRE": "CE", "DLY": "DLY", "WE_N": "WE", "A16": "A16", "A17": "A17", "GND": "GND",
              "+5V": "5V"}
    texts += [(labels[n], D.TP_X0 + k * D.TP_PITCH, D.TP_Y + 1.55, 0.8, "F.SilkS", "")
              for k, n in enumerate(D.TP_NETS)]
    # Bottom-side legend, mirrored so it reads correctly from below.  Seen from
    # below, the U38 end (small x) is on the right and the U41 end on the left.
    # "left mirror" text grows toward smaller x; "right mirror" toward larger x.
    B = "B.SilkS"
    texts += [("C128DCR 256K SRAM module v1.0", -1.5, -4.2, 1.0, B, "right mirror"),
              ("https://github.com/dcgibbons/c128-memory", -1.5, -2.6, 0.8, B, "right mirror")]
    # Fixed column x's instead of space-padded single strings: KiCad's stroke
    # font is proportional, not monospace, so padding with spaces doesn't
    # actually line up a second column -- it drifts with each row's own
    # length (confirmed against KiCad's own GetTextBox() for every entry).
    # Column x's chosen so the widest entry that ever lands in the column
    # to its left still clears it by ~1mm: col1's longest row is
    # "U1 IS61C5128AS-25QLI" (16.53mm from x=47.0 -> left edge 30.47), col2's
    # longest-in-row4 is "C5 10uF" (5.90mm from x=29.4 -> left edge 23.50).
    PARTS_COLS = (47.0, 29.4, 22.5)
    parts_rows = [("U1 IS61C5128AS-25QLI", "U4 74AHCT08"),
                  ("U2,U3,U7 74AHCT573", "U5 74LVC1G17"),
                  ("U9 74AHCT1G32", "U10 74AHCT1G04"),
                  ("C1-4,6,8-10 100nF", "C5 10uF", "C7 33pF"),
                  ("R1 10k", "R4 680", "J1 S2B-PH-K-S")]
    texts += [(cell, PARTS_COLS[c], 6.8 + k * 1.6, 0.9, B, "left mirror")
              for k, row in enumerate(parts_rows) for c, cell in enumerate(row)]
    howto = ["Replaces U38-U41 (4x 41464 64Kx4 DRAM)",
             "Remove U38-U41, sockets at U38 + U41",
             "Square pads (pin 1) face front of C128",
             "J1: BANK_HI from MMU riser (open=128K)"]
    texts += [(line, 47.0, 18.6 + k * 1.6, 0.9, B, "left mirror")
              for k, line in enumerate(howto)]
    logo = os.environ.get("OSHW_LOGO", "oshw_logo2")
    logos = []
    # Bottom-side OSHW logo just past (mirrored: larger x reads further left)
    # the end of the title/github lines, vertically centered on the two of
    # them; mirrored to read from below.
    blogo = oshw_logo(4.6, logo)
    bx, by = 30.3, -3.46
    polys = [("B.SilkS", [(bx - x, by + y) for x, y in pts]) for _, _, (pts, _) in blogo.graphics]
    BL.write_kicad(paths, parts, segs, vias, texts, logos, polys)
    BL.write_bom(paths, parts, bom_note)
    BL.write_previews(paths, parts, segs, vias, "C128DCR 256K SRAM - top (red) + bottom (blue)")
    ok = BL.kicad_finish(paths, parts, skip="--no-kicad" in sys.argv) and ok and parts_ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
