"""Write KiCad 7-format .kicad_pcb and .kicad_mod files (opens in 7/8/9)."""
import uuid
import design as D
import sch_writer as SW

OX, OY = 100.0, 80.0          # board-coords -> KiCad page coords
NS = D.PCB_NS


def uid(*key):
    return str(uuid.uuid5(NS, "/".join(str(k) for k in key)))


def f(v):
    s = ("%.4f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def pcb_net_name(net):
    return net if net in D.POWER_NETS else "/" + net


def _layers_block(four_layer):
    inner = '    (1 "In1.Cu" signal)\n    (2 "In2.Cu" signal)\n' if four_layer else ""
    return LAYERS_TMPL % inner


LAYERS_TMPL = """  (layers
    (0 "F.Cu" signal)
%s    (31 "B.Cu" signal)
    (32 "B.Adhes" user "B.Adhesive")
    (33 "F.Adhes" user "F.Adhesive")
    (34 "B.Paste" user)
    (35 "F.Paste" user)
    (36 "B.SilkS" user "B.Silkscreen")
    (37 "F.SilkS" user "F.Silkscreen")
    (38 "B.Mask" user)
    (39 "F.Mask" user)
    (40 "Dwgs.User" user "User.Drawings")
    (41 "Cmts.User" user "User.Comments")
    (42 "Eco1.User" user "User.Eco1")
    (43 "Eco2.User" user "User.Eco2")
    (44 "Edge.Cuts" user)
    (45 "Margin" user)
    (46 "B.CrtYd" user "B.Courtyard")
    (47 "F.CrtYd" user "F.Courtyard")
    (48 "B.Fab" user)
    (49 "F.Fab" user)
  )
"""


def _blayer(layer, mirror):
    return "B." + layer[2:] if mirror and layer.startswith("F.") else layer


def _graphics(fp, key, ind="    ", mirror=False):
    out = []
    mx = (lambda x: -x) if mirror else (lambda x: x)
    for n, (layer, kind, d) in enumerate(fp.graphics):
        ts = uid(key, "g", n)
        layer = _blayer(layer, mirror)
        if kind == "line":
            x1, y1, x2, y2, w = d
            out.append('%s(fp_line (start %s %s) (end %s %s) (stroke (width %s) (type solid)) '
                       '(layer "%s") (tstamp %s))' % (ind, f(mx(x1)), f(y1), f(mx(x2)), f(y2), f(w), layer, ts))
        elif kind == "circle":
            cx, cy, r, w = d
            out.append('%s(fp_circle (center %s %s) (end %s %s) (stroke (width %s) (type solid)) '
                       '(fill none) (layer "%s") (tstamp %s))' % (ind, f(mx(cx)), f(cy), f(mx(cx + r)), f(cy), f(w), layer, ts))
        elif kind == "arc":
            x1, y1, xm, ym, x2, y2, w = d
            out.append('%s(fp_arc (start %s %s) (mid %s %s) (end %s %s) (stroke (width %s) (type solid)) '
                       '(layer "%s") (tstamp %s))' % (ind, f(mx(x1)), f(y1), f(mx(xm)), f(ym), f(mx(x2)), f(y2), f(w), layer, ts))
        elif kind == "poly":
            pts, w = d
            xy = " ".join("(xy %s %s)" % (f(mx(x)), f(y)) for x, y in pts)
            out.append('%s(fp_poly (pts %s) (stroke (width %s) (type solid)) (fill solid) '
                       '(layer "%s") (tstamp %s))' % (ind, xy, f(w), layer, ts))
        elif kind == "text":
            x, y, txt, sz, ang = d
            # stroke width proportional to text size.  Tried flooring this at
            # JLC's 0.15mm line-width minimum, but it didn't actually clear
            # their "line width" warning (needs more than 0.15mm) while the
            # thicker ink made the "silkscreen to pad" Danger count worse
            # board-wide -- reverted, net negative trade.
            just = " (justify mirror)" if mirror else ""
            out.append('%s(fp_text user "%s" (at %s %s %s) (layer "%s") (tstamp %s)\n%s  (effects (font (size %s %s) (thickness %s))%s))'
                       % (ind, txt, f(mx(x)), f(y), f(ang), layer, ts, ind, f(sz), f(sz), f(sz * 0.15), just))
    return out


def _pad(pd, rot, netstr, key, ind="    ", mirror=False):
    ang = (rot) % 360
    px = -pd.x if mirror else pd.x
    if pd.kind == "smd":
        layers = '"B.Cu" "B.Paste" "B.Mask"' if mirror else '"F.Cu" "F.Paste" "F.Mask"'
        typ = "smd"
    else:
        layers = '"*.Cu" "*.Mask"'
        typ = "thru_hole"
    s = '%s(pad "%s" %s %s (at %s %s %s) (size %s %s)' % (
        ind, pd.num, typ, pd.shape, f(px), f(pd.y), f(ang), f(pd.w), f(pd.h))
    if pd.drill:
        s += " (drill %s)" % f(pd.drill)
    s += " (layers %s)" % layers
    if pd.shape == "roundrect":
        s += " (roundrect_rratio 0.25)"
    if netstr:
        s += " " + netstr
    s += " (tstamp %s))" % uid(key, "pad", pd.num)
    return s


def fp_attr(fp):
    if fp.board_only:
        return "board_only exclude_from_pos_files exclude_from_bom"
    return "smd" if fp.smd else "through_hole"


_PIN_NAMES = {}


def unconnected_name(part, num):
    """KiCad's name for the net of a schematic pin left unconnected."""
    if not _PIN_NAMES:
        for key, sym in SW.CONTENT.build_symbols().items():
            _PIN_NAMES[key] = {pin[0]: pin[1] for pin in sym.pins}
    key = SW.CONTENT.sym_key(part)
    name = _PIN_NAMES.get(key, {}).get(num, num).replace("/", "{slash}")   # KiCad's escaping
    return "unconnected-(%s-%s-Pad%s)" % (part.ref, name, num)


def footprint_text(part, netidx):
    fp = part.fp
    key = part.ref
    mirror = getattr(part, "side", "F") == "B"
    # Part.dnp means "skip this in the vendor's automated assembly" (used
    # only for U1: no LCSC stock, buyer hand-solders it) -- never "leave
    # this position empty".  KiCad's own (dnp yes) attribute means the
    # latter and visibly crosses the part out in the schematic/3D viewer,
    # which is wrong for a required, populated part; the JLC/MacroFab
    # exports already key off Part.dnp directly in buildlib.py, not off
    # this file's own attribute, so leaving it out here costs nothing.
    attr = fp_attr(fp)
    rot = part.rot
    L = ['  (footprint "%s:%s" (layer "%s")' % (D.LIB, fp.name, "B.Cu" if mirror else "F.Cu"),
         '    (tstamp %s)' % uid(key, "fp"),
         '    (at %s %s%s)' % (f(part.x + OX), f(part.y + OY), (" " + f(rot)) if rot else ""),
         '    (descr "%s")' % fp.descr,
         '    (property "Sheetfile" "%s.kicad_sch")' % D.PROJECT,
         '    (attr %s)' % attr]
    if not fp.board_only:
        L.insert(-1, '    (path "/%s")' % SW.uid("sym", part.ref))
    rx, ry = getattr(part, "ref_at", None) or fp.ref_pos
    vx, vy = fp.val_pos
    mx = (lambda x: -x) if mirror else (lambda x: x)
    just = " (justify mirror)" if mirror else ""
    L.append('    (fp_text reference "%s" (at %s %s %s) (layer "%s")%s (tstamp %s)\n'
             '      (effects (font (size 0.8 0.8) (thickness 0.12))%s))'
             % (part.ref, f(mx(rx)), f(ry), f(rot), _blayer("F.SilkS", mirror),
                " hide" if fp.ref_hide else "", uid(key, "ref"), just))
    L.append('    (fp_text value "%s" (at %s %s %s) (layer "%s")%s (tstamp %s)\n'
             '      (effects (font (size 0.8 0.8) (thickness 0.12))%s))'
             % (part.value, f(mx(vx)), f(vy), f(rot), _blayer("F.Fab", mirror),
                " hide" if fp.val_hide else "", uid(key, "val"), just))
    L += _graphics(fp, key, mirror=mirror)
    for pd in fp.pads:
        net = part.pins.get(pd.num)
        nn = pcb_net_name(net) if net else unconnected_name(part, pd.num)
        ns = '(net %d "%s")' % (netidx[nn], nn)
        L.append(_pad(pd, rot, ns, key, mirror=mirror))
    L.append("  )")
    return "\n".join(L)


def write_pcb(path, parts, segs, vias, title_texts=(), logos=(), polys=()):
    """logos: board-only graphic footprints (design.Part, no pins)
    polys: board-level filled polygons [(layer, [(x, y), ...]), ...]"""
    names = sorted({pcb_net_name(n) for p in parts for n in p.pins.values() if n})
    names += sorted({unconnected_name(p, pd.num) for p in parts for pd in p.fp.pads
                     if not p.pins.get(pd.num)})
    netidx = {n: k + 1 for k, n in enumerate(names)}
    out = ['(kicad_pcb (version 20221018) (generator pcbnew)',
           '  (general (thickness 1.6))',
           '  (paper "USLetter")',
           '  (title_block (title "%s") (rev "%s") (company "") (comment 1 "%s"))' % D.PCB_TITLE,
           _layers_block(getattr(D, "PLANE_POWER", False)).rstrip(),
           # 0.05mm positive mask expansion: standard practice for a 1:1
           # soldermask opening (0 left JLC's own AMRoundRect corner-inset
           # math ~0.04mm tighter than the unioned copper on every 0603
           # pad -- 22 "negative soldermask expansion" Warnings).
           '  (setup (pad_to_mask_clearance 0.05)',
           '    (pcbplotparams (layerselection 0x00010fc_ffffffff) (plot_on_all_layers_selection 0x0000000_00000000)',
           '      (disableapertmacros false) (usegerberextensions false) (usegerberattributes true)',
           '      (usegerberadvancedattributes true) (creategerberjobfile true) (dashed_line_dash_ratio 12.000000)',
           '      (dashed_line_gap_ratio 3.000000) (svgprecision 4) (plotframeref false) (viasonmask false)',
           '      (mode 1) (useauxorigin false) (hpglpennumber 1) (hpglpenspeed 20) (hpglpendiameter 15.000000)',
           '      (dxfpolygonmode true) (dxfimperialunits true) (dxfusepcbnewfont true) (psnegative false)',
           '      (psa4output false) (plotreference true) (plotvalue true) (plotinvisibletext false)',
           '      (sketchpadsonfab false) (subtractmaskfromsilk false) (outputformat 1) (mirror false)',
           '      (drillshape 0) (scaleselection 1) (outputdirectory "gerbers/")))',
           '  (net 0 "")']
    for n in names:
        out.append('  (net %d "%s")' % (netidx[n], n))
    for p in list(parts) + list(logos):
        out.append(footprint_text(p, netidx))
    x0, y0, x1, y1 = D.BOARD
    out.append('  (gr_rect (start %s %s) (end %s %s) (stroke (width 0.1) (type default)) (fill none) '
               '(layer "Edge.Cuts") (tstamp %s))' % (f(x0 + OX), f(y0 + OY), f(x1 + OX), f(y1 + OY), uid("edge")))
    for k, t in enumerate(title_texts):
        txt, x, y, sz, layer, just = t[:6]
        ang = t[6] if len(t) > 6 else 0          # optional rotation (degrees)
        out.append('  (gr_text "%s" (at %s %s%s) (layer "%s") (tstamp %s)\n'
                   '    (effects (font (size %s %s) (thickness %s))%s))'
                   % (txt, f(x + OX), f(y + OY), (" " + f(ang)) if ang else "", layer, uid("txt", k),
                      f(sz), f(sz), f(max(0.15, sz * 0.15)), (" (justify %s)" % just) if just else ""))
    for k, (layer, pts) in enumerate(polys):
        xy = " ".join("(xy %s %s)" % (f(x + OX), f(y + OY)) for x, y in pts)
        out.append('  (gr_poly (pts %s) (stroke (width 0.01) (type solid)) (fill solid) '
                   '(layer "%s") (tstamp %s))' % (xy, layer, uid("poly", k)))
    for k, s in enumerate(segs):
        nn = pcb_net_name(s.net)
        out.append('  (segment (start %s %s) (end %s %s) (width %s) (layer "%s") (net %d) (tstamp %s))'
                   % (f(s.x + OX), f(s.y + OY), f(s.x2 + OX), f(s.y2 + OY), f(s.w),
                      "F.Cu" if s.layers[0] == 0 else "B.Cu", netidx[nn], uid("seg", k)))
    for k, v in enumerate(vias):
        nn = pcb_net_name(v.net)
        out.append('  (via (at %s %s) (size %s) (drill %s) (layers "F.Cu" "B.Cu") (net %d) (tstamp %s))'
                   % (f(v.x + OX), f(v.y + OY), f(v.w), f(v.drill), netidx[nn], uid("via", k)))
    def _zone(net, layer):
        ni = netidx[pcb_net_name(net)]
        out.append('  (zone (net %d) (net_name "%s") (layer "%s") (tstamp %s) (hatch edge 0.5)'
                   % (ni, pcb_net_name(net), layer, uid("zone", layer)))
        out.append('    (priority 0)')
        out.append('    (connect_pads yes (clearance 0.25))')
        out.append('    (min_thickness 0.25) (filled_areas_thickness no)')
        out.append('    (fill yes (thermal_gap 0.3) (thermal_bridge_width 0.4) (island_removal_mode 0))')
        m = 0.4
        out.append('    (polygon (pts (xy %s %s) (xy %s %s) (xy %s %s) (xy %s %s))))' % (
            f(x0 + m + OX), f(y0 + m + OY), f(x1 - m + OX), f(y0 + m + OY),
            f(x1 - m + OX), f(y1 - m + OY), f(x0 + m + OX), f(y1 - m + OY)))

    if getattr(D, "PLANE_POWER", False):
        # Dedicated inner-layer planes: F.Cu/B.Cu stay free for signal
        # routing entirely, GND and +5V each get their own flood layer.
        _zone("GND", "In1.Cu")
        _zone("+5V", "In2.Cu")
    else:
        # 2-layer: GND pours on both outer layers (refill with 'B' in
        # KiCad) as a backstop behind the explicitly routed copper; +5V
        # has no pour and relies entirely on routed traces.
        _zone("GND", "F.Cu")
        _zone("GND", "B.Cu")
    out.append(")")
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")


def write_mod(path, fp):
    attr = fp_attr(fp)
    L = ['(footprint "%s" (version 20221018) (generator pcbnew)' % fp.name,
         '  (layer "F.Cu")',
         '  (descr "%s")' % fp.descr,
         '  (attr %s)' % attr,
         '  (fp_text reference "REF**" (at %s %s) (layer "F.SilkS")\n'
         '    (effects (font (size 0.8 0.8) (thickness 0.12))))' % (f(fp.ref_pos[0]), f(fp.ref_pos[1])),
         '  (fp_text value "%s" (at %s %s) (layer "F.Fab")\n'
         '    (effects (font (size 0.8 0.8) (thickness 0.12))))' % (fp.name, f(fp.val_pos[0]), f(fp.val_pos[1]))]
    L += [s.replace("(tstamp %s)" % uid("lib", fp.name, "g", n), "") for n, s in
          enumerate(_graphics(fp, ("lib", fp.name), ind="  "))]
    for pd in fp.pads:
        L.append(_pad(pd, 0, "", ("lib", fp.name), ind="  "))
    L.append(")")
    with open(path, "w") as fh:
        fh.write("\n".join(L) + "\n")
