#!/usr/bin/env python3
"""Build the C128DCR MMU riser: route, check, write KiCad files and outputs.

    python3 build.py            # route + write everything
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
sys.path.insert(0, os.path.join(HERE, "gal"))
import check_gal  # noqa: E402
from footprints import oshw_logo  # noqa: E402

SW.use(sch_content)


def bom_note(refs, fp, hand, dnp=False):
    if fp.startswith("DIP48_Plug"):
        return "machined round-pin male strips 2.54mm, 2x 24 pins (solder these first)"
    if fp.startswith("DIP20_Socket"):
        return "ATF16V8B in a 20-pin 300mil DIP socket; program with gal/riser.jed first"
    if fp.startswith("DIP48_Socket"):
        return "48-pin 600mil DIP socket (machined or open-frame; centre must clear P1's pins)"
    if refs[0] in ("U1", "U2"):
        return "MOS 8722 MMU: U1 = the computer's own, U2 = a second one"
    if hand and fp.startswith(("TSSOP", "SOIC", "SOT")):
        return "hand-solder (not in JLC assembly files)"
    return ""


def main():
    reuse = "--reuse" in sys.argv
    paths = BL.Paths(HERE)
    parts, nets = D.get_design()
    parts_ok = BL.check_parts(parts, offline="--offline" in sys.argv)
    gal_bad = check_gal.check()
    print("GAL check:", "OK (512 input combinations)" if not gal_bad else "%d MISMATCHES" % len(gal_bad))
    r, left = BL.route(paths, parts, nets, reuse)
    segs, vias = r.items()
    ok = BL.check(parts, segs, vias, left)

    F, B = "F.SilkS", "B.SilkS"
    mid_y = D.PIN1_Y - 11.5 * 2.54           # centre of the DIP-48 rows
    texts = [
        # top: which MMU goes where, in the clear gaps between pin columns
        ("U1: 8722 from U7 (stock)", (D.PLUG_X - D.ROW48 + D.MMU1_X) / 2, mid_y, 1.0, F, "", 90),
        ("U2: second 8722", (D.PLUG_X + D.MMU2_X) / 2, mid_y, 1.0, F, "", 90),
        ("BANK_HI", 4.2, 65.0, 0.8, F, ""),
        ("ATF16V8B: gal/riser.jed", D.GAL_X + 4.5 * 2.54, D.GAL_Y - 3.81, 0.8, F, ""),
    ]
    # bottom legend, mirrored so it reads correctly from below
    legend = ["C128DCR MMU riser v1.0",
              "https://github.com/dcgibbons/c128-memory",
              "U1: 8722 from U7   U2: second 8722",
              "U3: ATF16V8B, program gal/riser.jed",
              "J1: BANK_HI to SRAM module J1",
              "Fit P1 pins first, then the sockets"]
    # runs along the clear gap between P1's and U2's pins-1-24 columns, below the logo
    gap_x = (D.PLUG_X + D.MMU2_X) / 2
    texts += [(line, gap_x - 3.75 + k * 1.5, 12.0, 0.8, B, "left mirror", 90)
              for k, line in enumerate(legend)]
    # Bottom-side OSHW logo in the clear gap between P1's pins 1-24 column and
    # U2's pins 1-24 column, near the top edge; mirrored to read from below.
    blogo = oshw_logo(6.0, "oshw_logo2")
    ys = [y for _, _, (pts, _) in blogo.graphics for _, y in pts]
    bx, by = (D.PLUG_X + D.MMU2_X) / 2, 3.0 - min(ys)
    polys = [(B, [(bx - x, by + y) for x, y in pts]) for _, _, (pts, _) in blogo.graphics]
    BL.write_kicad(paths, parts, segs, vias, texts, polys=polys)
    BL.write_bom(paths, parts, bom_note)
    BL.write_previews(paths, parts, segs, vias, "C128DCR MMU riser - top (red) + bottom (blue)")
    ok = BL.kicad_finish(paths, parts, skip="--no-kicad" in sys.argv) and ok and parts_ok and not gal_bad
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
