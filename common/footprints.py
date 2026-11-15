"""Footprint definitions shared by the C128DCR boards.

Each footprint is a dict with:
  name   : footprint name (written to each board's project library)
  pads   : list of Pad
  graphics: list of (layer, kind, data) for silk/fab/courtyard
All coordinates in mm, footprint-local, KiCad convention (y down).
"""
from dataclasses import dataclass, field


@dataclass
class Pad:
    num: str
    kind: str          # "smd" or "thru"
    shape: str         # "rect", "roundrect", "circle", "oval"
    x: float
    y: float
    w: float
    h: float
    drill: float = 0.0


@dataclass
class Footprint:
    name: str
    pads: list
    graphics: list = field(default_factory=list)
    descr: str = ""
    ref_pos: tuple = (0.0, 0.0)
    val_pos: tuple = (0.0, 0.0)
    smd: bool = True
    ref_hide: bool = False
    val_hide: bool = False
    board_only: bool = False


def _box(x0, y0, x1, y1, layer, width):
    return [(layer, "line", (x0, y0, x1, y0, width)),
            (layer, "line", (x1, y0, x1, y1, width)),
            (layer, "line", (x1, y1, x0, y1, width)),
            (layer, "line", (x0, y1, x0, y0, width))]


def dip(n, row=15.24, pad=1.6, drill=1.0, body_len=None, body_w=None, descr=None):
    """Standard through-hole DIP-n IC package (real plated pads, not
    dip_plug()'s machined-pin-into-socket style) -- for a chip meant to be
    soldered (or socketed) directly onto this board, not plugged into an
    existing DRAM socket.  Pin 1 at (0,0), same numbering convention as
    dip_plug(): pins 1..n/2 down the left column, n/2+1..n up the right.
    Default body_len/body_w are standard 600mil-DIP-ish dimensions scaled
    off the pin count if not given explicitly -- close enough for
    silk/courtyard, not load-bearing (the pads are what matters
    electrically, and row/pad/drill above are)."""
    half = n // 2
    pads = []
    for p in range(1, n + 1):
        x, y = (0.0, (p - 1) * 2.54) if p <= half else (row, (n - p) * 2.54)
        shape = "rect" if p == 1 else "circle"
        pads.append(Pad(str(p), "thru", shape, x, y, pad, pad, drill))
    ln = (half - 1) * 2.54
    bl = body_len if body_len is not None else ln + 3.3
    bw = body_w if body_w is not None else row - 1.3
    bx, by = bw / 2, (bl - ln) / 2
    g = []
    g += _box(row / 2 - bx, -by, row / 2 + bx, ln + by, "F.Fab", 0.1)
    g += _box(row / 2 - bx - 0.1, -by - 0.1, row / 2 + bx + 0.1, ln + by + 0.1, "F.SilkS", 0.12)
    g.append(("F.SilkS", "arc", (row / 2 - 1.2, -by - 0.1, row / 2, -by - 0.1 + 1.2,
                                  row / 2 + 1.2, -by - 0.1, 0.12)))   # pin-1 notch
    g += _box(-1.5, -by - 1.5, row + 1.5, ln + by + 1.5, "F.CrtYd", 0.05)
    return Footprint("DIP-%d_W%.2fmm" % (n, row), pads, g,
                     descr=descr or "DIP-%d, %.0fmil row" % (n, round(row / 25.4 * 1000)),
                     ref_pos=(row / 2, -by - 2.2), val_pos=(row / 2, ln + by + 2.2))


def dip_plug(name, pins_used, n=18, row=7.62, pad=1.6, drill=1.0,
             label="", silk=True, descr=None, notch=1.0):
    """DIP-n plug made from machined pin strips.  Pin 1 at (0,0), pins
    1..n/2 down the left column, n/2+1..n up the right column (top view).
    pins_used: set of pin numbers that get a pad (others omitted)."""
    half = n // 2
    pads = []
    for p in range(1, n + 1):
        if p not in pins_used:
            continue
        if p <= half:
            x, y = 0.0, (p - 1) * 2.54
        else:
            x, y = row, (n - p) * 2.54
        shape = "rect" if p == 1 else "circle"
        pads.append(Pad(str(p), "thru", shape, x, y, pad, pad, drill))
    ln = (half - 1) * 2.54
    g = []
    # outline of the original DRAM package (fab) + silk outline with notch
    if silk:
        g += _box(-1.27, -1.27, row + 1.27, ln + 1.27, "F.SilkS", 0.12)
        g.append(("F.SilkS", "arc", (row / 2 - notch, -1.27, row / 2, -1.27 + notch,
                                      row / 2 + notch, -1.27, 0.12)))
    # no courtyard: the plug's "body" is the DRAM socket under the board,
    # and other parts are deliberately placed inside its outline
    g += _box(-0.9, -1.27, row + 0.9, ln + 1.27, "F.Fab", 0.1)
    if label:
        g.append(("F.SilkS", "text", (row / 2, ln / 2, label, 1.0, 90)))
    return Footprint(name, pads, g, descr=descr or "DIP-%d plug (machined pin strip) "
                     "into original 41464/41254 DRAM socket" % n,
                     ref_pos=(row / 2, -2.6), val_pos=(row / 2, ln + 2.6),
                     smd=False, ref_hide=True)


def sop32_450():
    """SOP-32, 450 mil body (11.3 x 20.5 mm), 1.27 mm pitch.
    Pin 1 top-left, pins 1-16 down the left side."""
    pads = []
    for i in range(16):
        y = -9.525 + i * 1.27
        pads.append(Pad(str(i + 1), "smd", "roundrect", -6.7, y, 1.9, 0.6))
        pads.append(Pad(str(32 - i), "smd", "roundrect", 6.7, y, 1.9, 0.6))
    g = []
    g += _box(-5.65, -10.25, 5.65, 10.25, "F.Fab", 0.1)
    g += [("F.SilkS", "line", (-5.76, -10.36, 5.76, -10.36, 0.12)),
          ("F.SilkS", "line", (-5.76, 10.36, 5.76, 10.36, 0.12)),
          # pin-1 bracket: corner moved from y=-9.95 to -10.1 -- pad 1's own
          # top edge is at y=-9.525-0.3=-9.825, so the old corner left only
          # 0.065mm to the pad (a confirmed JLC "silkscreen to pad" Danger,
          # since the horizontal arm runs almost the full pad width at that
          # y). -10.1 gives ~0.335mm, clear of JLC's 0.06mm floor with room
          # to spare.
          ("F.SilkS", "line", (-5.76, -10.36, -5.76, -10.1, 0.12)),
          ("F.SilkS", "line", (-5.76, -10.1, -7.65, -10.1, 0.12)),
          ("F.SilkS", "circle", (-4.8, -9.3, 0.25, 0.12))]
    g += _box(-7.9, -10.5, 7.9, 10.5, "F.CrtYd", 0.05)
    return Footprint("SOP-32_11.3x20.5mm_P1.27mm", pads, g,
                     descr="SOP-32 450mil, 1.27mm pitch (11.3 x 20.5mm body)",
                     ref_pos=(0, 0), val_pos=(0, 2.0))


def tssop(n):
    """TSSOP-n, 4.4 mm body, 0.65 mm pitch. Pin 1 top-left."""
    half = n // 2
    pads = []
    y0 = -(half - 1) * 0.65 / 2
    for i in range(half):
        y = y0 + i * 0.65
        pads.append(Pad(str(i + 1), "smd", "roundrect", -2.8625, y, 1.475, 0.4))
        pads.append(Pad(str(n - i), "smd", "roundrect", 2.8625, y, 1.475, 0.4))
    blen = {14: 5.0, 16: 5.0, 20: 6.5}[n]
    g = []
    g += _box(-2.2, -blen / 2, 2.2, blen / 2, "F.Fab", 0.1)
    # end-line half-width: was 2.31 (slightly past the F.Fab body at 2.2),
    # which reached past the corner pads' own inner edge (2.8625-1.475/2 =
    # 2.125), overlapping them in X at every end and giving JLC's "silk to
    # pad" check only ~0.155-0.175mm (a confirmed Warning on all 14/16/20
    # pin variants). 2.0 sits inside the pad's inner edge, so there's no
    # overlap left to measure.
    g += [("F.SilkS", "line", (-2.0, -blen / 2 - 0.11, 2.0, -blen / 2 - 0.11, 0.12)),
          ("F.SilkS", "line", (-2.0, blen / 2 + 0.11, 2.0, blen / 2 + 0.11, 0.12)),
          ("F.SilkS", "circle", (-1.5, y0, 0.2, 0.12))]
    g += _box(-3.85, -blen / 2 - 0.25, 3.85, blen / 2 + 0.25, "F.CrtYd", 0.05)
    return Footprint("TSSOP-%d_4.4x%gmm_P0.65mm" % (n, blen), pads, g,
                     descr="TSSOP-%d 4.4mm body" % n,
                     ref_pos=(0, 0), val_pos=(0, 1.2))


def soic(n):
    """SOIC-n, 3.9 mm body, 1.27 mm pitch (KiCad SOIC-n_3.9x..mm_P1.27mm). Pin 1 top-left."""
    half = n // 2
    pads = []
    y0 = -(half - 1) * 1.27 / 2
    for i in range(half):
        y = y0 + i * 1.27
        pads.append(Pad(str(i + 1), "smd", "roundrect", -2.475, y, 1.95, 0.6))
        pads.append(Pad(str(n - i), "smd", "roundrect", 2.475, y, 1.95, 0.6))
    blen = {14: 8.65}[n]
    g = []
    g += _box(-1.95, -blen / 2, 1.95, blen / 2, "F.Fab", 0.1)
    g += [("F.SilkS", "line", (-2.06, -blen / 2 - 0.11, 2.06, -blen / 2 - 0.11, 0.12)),
          ("F.SilkS", "line", (-2.06, blen / 2 + 0.11, 2.06, blen / 2 + 0.11, 0.12)),
          ("F.SilkS", "circle", (-1.3, y0, 0.2, 0.12))]
    g += _box(-3.7, -blen / 2 - 0.25, 3.7, blen / 2 + 0.25, "F.CrtYd", 0.05)
    return Footprint("SOIC-%d_3.9x%gmm_P1.27mm" % (n, blen), pads, g,
                     descr="SOIC-%d 3.9mm body" % n, ref_pos=(0, 0), val_pos=(0, 1.2))


def sot23_5():
    pads = [Pad("1", "smd", "roundrect", -1.1375, -0.95, 1.325, 0.6),
            Pad("2", "smd", "roundrect", -1.1375, 0.0, 1.325, 0.6),
            Pad("3", "smd", "roundrect", -1.1375, 0.95, 1.325, 0.6),
            Pad("4", "smd", "roundrect", 1.1375, 0.95, 1.325, 0.6),
            Pad("5", "smd", "roundrect", 1.1375, -0.95, 1.325, 0.6)]
    g = _box(-0.8, -1.45, 0.8, 1.45, "F.Fab", 0.1)
    g += [("F.SilkS", "line", (-0.15, -1.56, 0.15, -1.56, 0.12)),
          ("F.SilkS", "line", (-0.15, 1.56, 0.15, 1.56, 0.12)),
          # pin-1 dot: moved from x=-0.15 (0.155mm from pad 1, a JLC
          # "silkscreen to pad" Warning) to x=0.0, dead centre -- equally
          # close to pads 1/3 and 4/5 but with real margin from both.
          ("F.SilkS", "circle", (0.0, -1.0, 0.12, 0.1))]
    g += _box(-2.05, -1.7, 2.05, 1.7, "F.CrtYd", 0.05)
    return Footprint("SOT-23-5", pads, g, descr="SOT-23-5, 0.95mm pitch",
                     ref_pos=(0, -2.4), val_pos=(0, 2.4))


def chip(size):
    """0603 / 0805 two-terminal SMD."""
    if size == "0603":
        dx, w, h, bx, by = 0.775, 0.9, 0.95, 0.8, 0.4
    else:
        dx, w, h, bx, by = 0.95, 1.0, 1.45, 1.0, 0.625
    pads = [Pad("1", "smd", "roundrect", -dx, 0, w, h),
            Pad("2", "smd", "roundrect", dx, 0, w, h)]
    g = _box(-bx, -by, bx, by, "F.Fab", 0.1)
    cx, cy = dx + w / 2 + 0.25, h / 2 + 0.25
    g += _box(-cx, -cy, cx, cy, "F.CrtYd", 0.05)
    # ref/value clearance from the pad: a fixed offset (was -1.4/1.4 for
    # both 0603 and 0805) put the 0805's taller pad (h=1.45) right under
    # the reference text; scale with h so both sizes keep the same margin.
    ry = h / 2 + 0.95
    return Footprint("%s_%s" % ("C" if size else "R", size), pads, g,
                     descr="%s chip" % size, ref_pos=(0, -ry),
                     val_pos=(0, ry))


def chip_named(prefix, size):
    f = chip(size)
    f.name = "%s_%s" % (prefix, size)
    f.descr = "%s %s chip" % ("Capacitor" if prefix == "C" else "Resistor", size)
    return f


def solder_jumper3():
    """3-pad solder jumper, all open; bridge 1-2 or 2-3 with solder."""
    pads = [Pad("1", "smd", "rect", -1.2, 0, 0.9, 1.4),
            Pad("2", "smd", "rect", 0.0, 0, 0.9, 1.4),
            Pad("3", "smd", "rect", 1.2, 0, 0.9, 1.4)]
    g = _box(-1.9, -1.0, 1.9, 1.0, "F.SilkS", 0.12)
    g += [("F.SilkS", "text", (-1.2, 1.9, "1", 0.8, 0))]
    g += _box(-2.1, -1.2, 2.1, 1.2, "F.CrtYd", 0.05)
    return Footprint("SolderJumper-3_Open", pads, g,
                     descr="3-way solder jumper, open by default",
                     ref_pos=(1.0, 1.9), val_pos=(0, 2.3))


def testpoint():
    """Bare SMD test pad (1.3 mm, top side) for a scope probe tip."""
    pads = [Pad("1", "smd", "circle", 0, 0, 1.3, 1.3)]
    g = _box(-0.9, -0.9, 0.9, 0.9, "F.CrtYd", 0.05)
    # Both ref and value are hidden: build.py draws its own short silkscreen
    # label under each test point (e.g. "CE" for CE_PRE) instead of KiCad's
    # full net name, which at TP_PITCH doesn't have room for names like
    # "CE_PRE" or "WE_N" without running into its neighbours.
    return Footprint("TestPoint_Pad_D1.3mm", pads, g, descr="test point (SMD pad)",
                     ref_pos=(0, -1.6), val_pos=(0, 1.6), ref_hide=True, val_hide=True)


def jst_ph2():
    """JST PH 2-pin vertical through-hole header (B2B-PH-K-S), 2.0 mm pitch.
    Geometry from KiCad's JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical."""
    pads = [Pad("1", "thru", "rect", 0, 0, 1.2, 1.75, 0.75),
            Pad("2", "thru", "oval", 2.0, 0, 1.2, 1.75, 0.75)]
    g = _box(-1.95, -1.7, 3.95, 2.8, "F.Fab", 0.1)
    g += _box(-2.06, -1.81, 4.06, 2.91, "F.SilkS", 0.12)
    g += [("F.SilkS", "line", (-2.36, -2.11, -2.36, -0.9, 0.12)),     # pin-1 corner mark
          ("F.SilkS", "line", (-2.36, -2.11, -1.15, -2.11, 0.12))]
    g += _box(-2.45, -2.2, 4.45, 3.3, "F.CrtYd", 0.05)
    return Footprint("JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical", pads, g,
                     descr="JST PH 2-pin vertical, 2.0mm (B2B-PH-K-S)",
                     ref_pos=(-3.3, 0), val_pos=(1.0, 4.0), smd=False)


def jst_ph2_horizontal():
    """JST PH 2-pin side-entry (horizontal) through-hole header (S2B-PH-K),
    2.0 mm pitch.  Same pads as jst_ph2() -- the shroud just lies along +y
    instead of surrounding the pins, so the cable exits parallel to the
    board instead of standing up off it.  Geometry from KiCad's
    JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal."""
    pads = [Pad("1", "thru", "rect", 0, 0, 1.2, 1.75, 0.75),
            Pad("2", "thru", "oval", 2.0, 0, 1.2, 1.75, 0.75)]
    g = _box(-1.95, -1.35, 3.95, 6.25, "F.Fab", 0.1)
    # Silkscreen only marks the base, next to the pins: this connector is
    # meant to be placed with the shroud hanging off the board edge (that's
    # the point of a side-entry part), and silk out past the edge just gets
    # clipped by the fab, so there is no point drawing the full outline.
    g += [("F.SilkS", "line", (-2.06, -1.46, -2.06, 0.14, 0.12)),
          ("F.SilkS", "line", (-2.06, -1.46, 3.14, -1.46, 0.12)),
          ("F.SilkS", "line", (3.14, -1.46, 3.14, 0.14, 0.12)),
          ("F.SilkS", "line", (-2.06, 0.14, -0.86, 0.14, 0.12)),      # pin-1 corner mark
          ("F.SilkS", "line", (3.14, 0.14, 2.86, 0.14, 0.12))]
    g += _box(-2.45, -1.85, 4.45, 6.75, "F.CrtYd", 0.05)
    return Footprint("JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal", pads, g,
                     descr="JST PH 2-pin horizontal (side-entry), 2.0mm (S2B-PH-K)",
                     ref_pos=(1.0, -2.55), val_pos=(1.0, 7.45), smd=False)


def header_1x2():
    pads = [Pad("1", "thru", "rect", 0, 0, 1.6, 1.6, 1.0),
            Pad("2", "thru", "circle", 2.54, 0, 1.6, 1.6, 1.0)]
    g = _box(-1.33, -1.33, 3.87, 1.33, "F.SilkS", 0.12)
    g += _box(-1.8, -1.8, 4.34, 1.8, "F.CrtYd", 0.05)
    return Footprint("PinHeader_1x02_P2.54mm", pads, g,
                     descr="1x2 pin header, /CAS2 /CAS3 input",
                     ref_pos=(1.27, -2.3), val_pos=(1.27, 2.3), smd=False)


def sot23_6():
    """SOT-23-6 (SN74LVC1G373 DBV): 1-3 left top->bottom, 4-6 right bottom->top."""
    pads = [Pad("1", "smd", "roundrect", -1.1375, -0.95, 1.325, 0.6),
            Pad("2", "smd", "roundrect", -1.1375, 0.0, 1.325, 0.6),
            Pad("3", "smd", "roundrect", -1.1375, 0.95, 1.325, 0.6),
            Pad("4", "smd", "roundrect", 1.1375, 0.95, 1.325, 0.6),
            Pad("5", "smd", "roundrect", 1.1375, 0.0, 1.325, 0.6),
            Pad("6", "smd", "roundrect", 1.1375, -0.95, 1.325, 0.6)]
    g = _box(-0.8, -1.45, 0.8, 1.45, "F.Fab", 0.1)
    g += [("F.SilkS", "line", (-0.15, -1.56, 0.15, -1.56, 0.12)),
          ("F.SilkS", "line", (-0.15, 1.56, 0.15, 1.56, 0.12)),
          ("F.SilkS", "circle", (-0.15, -1.0, 0.12, 0.1))]
    g += _box(-2.05, -1.7, 2.05, 1.7, "F.CrtYd", 0.05)
    return Footprint("SOT-23-6", pads, g, descr="SOT-23-6 (SN74LVC1G373DBV)",
                     ref_pos=(0, 2.4), val_pos=(0, -2.4))


def oshw_logo(height, src="oshw_symbol"):
    """Open Source Hardware logo (silkscreen), scaled to `height` mm.
    Outlines taken from KiCad's Symbol library, vendored as <src>.kicad_mod:
      oshw_symbol: OSHW-Symbol_6.7x6mm  (gear only)
      oshw_logo:   OSHW-Logo_5.7x6mm    (gear + "open source hardware")
      oshw_logo2:  OSHW-Logo2_7.3x6mm   (gear + "open source hardware", v2)"""
    import os
    import re
    text = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             src + ".kicad_mod")).read()
    polys = [[(float(a), float(b)) for a, b in re.findall(r"\(xy ([-\d.]+) ([-\d.]+)\)", p)]
             for p in re.findall(r"\(fp_poly\s*\(pts(.*?)\)\s*\(stroke", text, re.S)]
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    k = height / (max(ys) - min(ys))
    w = (max(xs) - min(xs)) * k
    g = [("F.SilkS", "poly", ([((x - cx) * k, (y - cy) * k) for x, y in p], 0.01))
         for p in polys]
    return Footprint("OSHW-%s_%.1fx%gmm_SilkScreen" % (src.split("_")[1].capitalize(), w, height),
                     [], g, descr="Open Source Hardware logo", ref_hide=True,
                     board_only=True, ref_pos=(0, 0), val_pos=(0, height / 2 + 0.8))
