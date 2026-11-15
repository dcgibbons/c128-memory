"""Write the KiCad 7-format schematic (.kicad_sch) and symbol library.

Connectivity is by net labels: every pin gets a 2.54 mm wire stub ending
in a local label (signals) or a power symbol (+5V / GND)."""
import uuid
import design as D

NS = D.SCH_NS
ROOT = str(uuid.uuid5(NS, "root"))
PROJECT = D.PROJECT
LIB = D.LIB
P = 2.54
CONTENT = None      # the project's schematic content module, set by use()


def use(content):
    """Register the project's schematic content: build_symbols(), sym_key(part),
    LAYOUT, TITLE and decorate(sheet)."""
    global CONTENT
    CONTENT = content


def uid(*k):
    return str(uuid.uuid5(NS, "/".join(str(x) for x in k)))


def f(v):
    s = ("%.4f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


FONT = "(effects (font (size 1.27 1.27)))"
FONT_HIDE = "(effects (font (size 1.27 1.27)) hide)"


# ---------------------------------------------------------------------------
# symbol definitions (lib coords, y up)
# ---------------------------------------------------------------------------
class Sym:
    def __init__(self, name, ref, descr=""):
        self.name, self.ref, self.descr = name, ref, descr
        self.pins = []          # (num, name, type, x, y, angle, hidden)
        self.graphics = []      # raw s-expr strings
        self.ref_at = (0, 0)
        self.val_at = (0, 0)

    def pin_end(self, num):
        for p in self.pins:
            if p[0] == num:
                return p[3], p[4], p[5]
        raise KeyError((self.name, num))


def box_symbol(name, ref, left, right, top=(), bottom=(), width=15.24, descr=""):
    """left/right: lists of (num, pinname, type) or None for a gap."""
    s = Sym(name, ref, descr)
    n = max(len(left), len(right))
    ytop = ((n - 1) // 2 + (n - 1) % 2) * P     # on grid
    hw = width / 2
    for side, lst in ((0, left), (1, right)):
        for k, pin in enumerate(lst):
            if pin is None:
                continue
            num, pname, typ = pin
            y = ytop - k * P
            if side == 0:
                s.pins.append((num, pname, typ, -hw - P, y, 0, False))
            else:
                s.pins.append((num, pname, typ, hw + P, y, 180, False))
    btop = ytop + P
    bbot = ytop - (n - 1) * P - P
    for k, pin in enumerate(top):
        num, pname, typ = pin
        x = (k - (len(top) - 1) / 2) * 2 * P
        s.pins.append((num, pname, typ, x, btop + P, 270, False))
    for k, pin in enumerate(bottom):
        num, pname, typ = pin
        x = (k - (len(bottom) - 1) / 2) * 2 * P
        s.pins.append((num, pname, typ, x, bbot - P, 90, False))
    s.graphics.append('(rectangle (start %s %s) (end %s %s) (stroke (width 0.254) (type default)) '
                      '(fill (type background)))' % (f(-hw), f(btop), f(hw), f(bbot)))
    s.ref_at = (-hw, btop + 1.27 + (P if top else 0))
    s.val_at = (-hw, bbot - 1.27 - (P if bottom else 0))
    return s


def two_pin(name, ref, kind):
    s = Sym(name, ref)
    # resistor: pin 2 on top (pull-ups have +5V on pin 2)
    top, bot = ("2", "1") if kind == "R" else ("1", "2")
    s.pins.append((top, "~", "passive", 0, 3.81, 270, False))
    s.pins.append((bot, "~", "passive", 0, -3.81, 90, False))
    if kind == "R":
        s.graphics.append('(rectangle (start -1.016 2.54) (end 1.016 -2.54) (stroke (width 0.254) '
                          '(type default)) (fill (type none)))')
    else:
        s.graphics.append('(polyline (pts (xy -2.032 0.762) (xy 2.032 0.762)) (stroke (width 0.508) '
                          '(type default)) (fill (type none)))')
        s.graphics.append('(polyline (pts (xy -2.032 -0.762) (xy 2.032 -0.762)) (stroke (width 0.508) '
                          '(type default)) (fill (type none)))')
        s.pins[0] = ("1", "~", "passive", 0, 3.81, 270, False)
    s.ref_at = (2.54, 1.27)
    s.val_at = (2.54, -1.27)
    return s


def jumper3():
    s = Sym("SolderJumper_3_Open", "JP")
    s.pins.append(("1", "A", "passive", -5.08, 0, 0, False))
    s.pins.append(("2", "C", "passive", 0, -3.81, 90, False))
    s.pins.append(("3", "B", "passive", 5.08, 0, 180, False))
    for x0 in (-2.54, -0.635, 1.27):
        s.graphics.append('(rectangle (start %s 0.762) (end %s -0.762) (stroke (width 0.254) '
                          '(type default)) (fill (type none)))' % (f(x0), f(x0 + 1.27)))
    s.ref_at = (-2.54, 2.54)
    s.val_at = (-2.54, 5.08)
    return s


def sym_lib_sexpr(s, libname=LIB):
    full = "%s:%s" % (libname, s.name) if libname else s.name
    L = ['(symbol "%s" (pin_names (offset 1.016)) (in_bom yes) (on_board yes)' % full,
         '  (property "Reference" "%s" (at %s %s 0) (effects (font (size 1.27 1.27)) (justify left)))'
         % (s.ref, f(s.ref_at[0]), f(s.ref_at[1])),
         '  (property "Value" "%s" (at %s %s 0) (effects (font (size 1.27 1.27)) (justify left)))'
         % (s.name, f(s.val_at[0]), f(s.val_at[1])),
         '  (property "Footprint" "" (at 0 0 0) %s)' % FONT_HIDE,
         '  (property "Datasheet" "" (at 0 0 0) %s)' % FONT_HIDE,
         '  (property "ki_description" "%s" (at 0 0 0) %s)' % (s.descr, FONT_HIDE),
         '  (symbol "%s_0_1"' % s.name]
    L += ["    " + g for g in s.graphics]
    L.append("  )")
    L.append('  (symbol "%s_1_1"' % s.name)
    for num, pname, typ, x, y, ang, hid in s.pins:
        L.append('    (pin %s line (at %s %s %d) (length 2.54)%s (name "%s" %s) (number "%s" %s))'
                 % (typ, f(x), f(y), ang, " hide" if hid else "", pname, FONT, num, FONT))
    L.append("  )")
    L.append(")")
    return "\n".join(L)


POWER_LIB = r'''(symbol "LIB:+5V" (power) (pin_names (offset 0)) (in_bom yes) (on_board yes)
  (property "Reference" "#PWR" (at 0 -3.81 0) (effects (font (size 1.27 1.27)) hide))
  (property "Value" "+5V" (at 0 3.556 0) (effects (font (size 1.27 1.27))))
  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (symbol "+5V_0_1"
    (polyline (pts (xy -0.762 1.27) (xy 0 2.54)) (stroke (width 0) (type default)) (fill (type none)))
    (polyline (pts (xy 0 0) (xy 0 2.54)) (stroke (width 0) (type default)) (fill (type none)))
    (polyline (pts (xy 0 2.54) (xy 0.762 1.27)) (stroke (width 0) (type default)) (fill (type none)))
  )
  (symbol "+5V_1_1"
    (pin power_in line (at 0 0 90) (length 0) hide (name "+5V" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
  )
)
(symbol "LIB:GND" (power) (pin_names (offset 0)) (in_bom yes) (on_board yes)
  (property "Reference" "#PWR" (at 0 -6.35 0) (effects (font (size 1.27 1.27)) hide))
  (property "Value" "GND" (at 0 -3.81 0) (effects (font (size 1.27 1.27))))
  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (symbol "GND_0_1"
    (polyline (pts (xy 0 0) (xy 0 -1.27) (xy 1.27 -1.27) (xy 0 -2.54) (xy -1.27 -1.27) (xy 0 -1.27)) (stroke (width 0) (type default)) (fill (type none)))
  )
  (symbol "GND_1_1"
    (pin power_in line (at 0 0 270) (length 0) hide (name "GND" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
  )
)
(symbol "LIB:PWR_FLAG" (power) (pin_numbers hide) (pin_names (offset 0) hide) (in_bom yes) (on_board yes)
  (property "Reference" "#FLG" (at 0 1.905 0) (effects (font (size 1.27 1.27)) hide))
  (property "Value" "PWR_FLAG" (at 0 3.81 0) (effects (font (size 1.27 1.27))))
  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (symbol "PWR_FLAG_0_0"
    (pin power_out line (at 0 0 90) (length 0) (name "pwr" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
  )
  (symbol "PWR_FLAG_0_1"
    (polyline (pts (xy 0 0) (xy 0 1.27) (xy -1.016 1.905) (xy 0 2.54) (xy 1.016 1.905) (xy 0 1.27)) (stroke (width 0) (type default)) (fill (type none)))
  )
)'''.replace('"LIB:', '"' + LIB + ':')


# ---------------------------------------------------------------------------
# schematic
# ---------------------------------------------------------------------------
class Sheet:
    def __init__(self):
        self.draw = []
        self.items = []
        self.npwr = 0
        self.nflg = 0

    def wire(self, x1, y1, x2, y2):
        self.draw.append(("line", x1, y1, x2, y2))
        self.items.append('  (wire (pts (xy %s %s) (xy %s %s)) (stroke (width 0) (type default)) (uuid %s))'
                          % (f(x1), f(y1), f(x2), f(y2), uid("w", len(self.items))))

    def label(self, name, x, y, ang):
        self.draw.append(("label", name, x, y, ang))
        just = {0: "left bottom", 180: "right bottom", 90: "left bottom", 270: "right bottom"}[ang]
        self.items.append('  (label "%s" (at %s %s %d) (fields_autoplaced) (effects (font (size 1.27 1.27)) '
                          '(justify %s)) (uuid %s))' % (name, f(x), f(y), ang, just, uid("l", len(self.items))))

    def noconn(self, x, y):
        self.draw.append(("nc", x, y))
        self.items.append('  (no_connect (at %s %s) (uuid %s))' % (f(x), f(y), uid("nc", len(self.items))))

    def text(self, txt, x, y, size=1.27):
        self.draw.append(("text", txt, x, y))
        txt = txt.replace('"', "'").replace("\n", "\\n")
        self.items.append('  (text "%s" (at %s %s 0) (effects (font (size %s %s)) (justify left top)) (uuid %s))'
                          % (txt, f(x), f(y), f(size), f(size), uid("t", len(self.items))))

    def power(self, net, x, y, rot=0):
        self.draw.append(("pwr", net, x, y, rot))
        self.npwr += 1
        ref = "#PWR%02d" % self.npwr
        lib = LIB + ":" + net
        # value text position: away from the pin, along the symbol's axis
        off = 3.81 if net == "GND" else -3.556
        vx, vy = {0: (x, y + off), 90: (x + off, y), 180: (x, y - off), 270: (x - off, y)}[rot]
        self.items.append(
            '  (symbol (lib_id "%s") (at %s %s %d) (unit 1) (in_bom yes) (on_board yes) (dnp no) (uuid %s)\n'
            '    (property "Reference" "%s" (at %s %s 0) %s)\n'
            '    (property "Value" "%s" (at %s %s 0) %s)\n'
            '    (property "Footprint" "" (at %s %s 0) %s)\n'
            '    (property "Datasheet" "" (at %s %s 0) %s)\n'
            '    (pin "1" (uuid %s))\n'
            '    (instances (project "%s" (path "/%s" (reference "%s") (unit 1)))))'
            % (lib, f(x), f(y), rot, uid("pwr", ref), ref, f(x), f(y), FONT_HIDE, net, f(vx), f(vy), FONT,
               f(x), f(y), FONT_HIDE, f(x), f(y), FONT_HIDE, uid("pwrpin", ref), PROJECT, ROOT, ref))

    def flag(self, x, y, rot=0):
        self.nflg += 1
        ref = "#FLG%02d" % self.nflg
        self.items.append(
            '  (symbol (lib_id "' + LIB + ':PWR_FLAG") (at %s %s %d) (unit 1) (in_bom yes) (on_board yes) (dnp no) (uuid %s)\n'
            '    (property "Reference" "%s" (at %s %s 0) %s)\n'
            '    (property "Value" "PWR_FLAG" (at %s %s 0) %s)\n'
            '    (property "Footprint" "" (at %s %s 0) %s)\n'
            '    (property "Datasheet" "~" (at %s %s 0) %s)\n'
            '    (pin "1" (uuid %s))\n'
            '    (instances (project "%s" (path "/%s" (reference "%s") (unit 1)))))'
            % (f(x), f(y), rot, uid("flg", ref), ref, f(x), f(y - 1.905), FONT_HIDE, f(x), f(y - 3.81 if rot == 0 else y + 3.81), FONT,
               f(x), f(y), FONT_HIDE, f(x), f(y), FONT_HIDE, uid("flgpin", ref), PROJECT, ROOT, ref))

    def symbol(self, part, sym, x, y):
        self.draw.append(("sym", part.ref, part.value, sym, x, y))
        props = [("Reference", part.ref, x + sym.ref_at[0], y - sym.ref_at[1], FONT_LEFT),
                 ("Value", part.value if part.ref[0] not in "UJ" or part.ref == "J1" else part.value,
                  x + sym.val_at[0], y - sym.val_at[1], FONT_LEFT),
                 ("Footprint", LIB + ":" + part.fp.name, x, y, FONT_HIDE),
                 ("Datasheet", "", x, y, FONT_HIDE)]
        if part.mpn:
            props.append(("MPN", part.mpn, x, y, FONT_HIDE))
        if getattr(part, "lcsc", ""):
            props.append(("LCSC", part.lcsc, x, y, FONT_HIDE))
        if part.descr:
            props.append(("Description", part.descr, x, y, FONT_HIDE))
        # Part.dnp ("skip vendor assembly, hand-solder it", used only for
        # U1) is deliberately not mirrored into KiCad's own (dnp yes) here --
        # see pcb_writer.py's footprint_text for why.
        L = ['  (symbol (lib_id "' + LIB + ':%s") (at %s %s 0) (unit 1) (in_bom yes) (on_board yes) (dnp no) (uuid %s)'
             % (sym.name, f(x), f(y), uid("sym", part.ref))]
        for name, val, px, py, eff in props:
            L.append('    (property "%s" "%s" (at %s %s 0) %s)' % (name, val, f(px), f(py), eff))
        for p in sym.pins:
            L.append('    (pin "%s" (uuid %s))' % (p[0], uid("pin", part.ref, p[0])))
        L.append('    (instances (project "%s" (path "/%s" (reference "%s") (unit 1)))))'
                 % (PROJECT, ROOT, part.ref))
        self.items.append("\n".join(L))
        # stubs + labels / power / no-connects
        for num, pname, typ, px, py, ang, hid in sym.pins:
            ex, ey = x + px, y - py
            dx, dy = {0: (-1, 0), 180: (1, 0), 90: (0, 1), 270: (0, -1)}[ang]
            net = part.pins.get(num)
            if net is None:
                self.noconn(ex, ey)
                continue
            if net in D.POWER_NETS:
                # power symbol directly on a stub; +5V points up, GND down
                self.wire(ex, ey, ex + dx * P, ey + dy * P)
                if dy == 0:
                    if net == "GND":
                        rot = 90 if dx > 0 else 270
                    else:
                        rot = 270 if dx > 0 else 90
                else:
                    rot = 0 if (net == "GND") == (dy > 0) else 180
                self.power(net, ex + dx * P, ey + dy * P, rot)
                continue
            lx, ly = ex + dx * 2 * P, ey + dy * 2 * P
            self.wire(ex, ey, lx, ly)
            lang = {(-1, 0): 180, (1, 0): 0, (0, 1): 270, (0, -1): 90}[(dx, dy)]
            self.label(net, lx, ly, lang)


FONT_LEFT = "(effects (font (size 1.27 1.27)) (justify left))"


def write_schematic(path, parts):
    C = CONTENT
    syms = C.build_symbols()
    used = {p.ref: syms[C.sym_key(p)] for p in parts}
    sh = Sheet()
    for p in parts:
        x, y = C.LAYOUT[p.ref]
        sh.symbol(p, used[p.ref], x, y)
    C.decorate(sh)

    libs = []
    seen = set()
    for s in used.values():
        if s.name not in seen:
            seen.add(s.name)
            libs.append(sym_lib_sexpr(s))
    out = ['(kicad_sch (version 20230121) (generator eeschema)',
           '  (uuid %s)' % ROOT,
           '  (paper "A3")',
           '  (title_block (title "%s") (date "%s") (rev "%s")' % C.TITLE[:3]]
    out[-1] += "".join('\n    (comment %d "%s")' % (k + 1, c) for k, c in enumerate(C.TITLE[3:])) + ")"
    out.append('  (lib_symbols')
    for l in libs:
        out.append("\n".join("    " + x for x in l.split("\n")))
    out.append("\n".join("    " + x for x in POWER_LIB.split("\n")))
    out.append("  )")
    out += sh.items
    out.append('  (sheet_instances (path "/" (page "1")))')
    out.append(")")
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")
    write_schematic.sheet = sh
    return syms


def write_symlib(path, syms):
    out = ["(kicad_symbol_lib (version 20220914) (generator kicad_symbol_editor)"]
    for s in syms.values():
        out.append("\n".join("  " + x for x in sym_lib_sexpr(s, libname=None).split("\n")))
    # power symbols live in the project library too, so they never mismatch
    # the stock "power" library of whichever KiCad version opens the project
    out.append("\n".join("  " + x for x in POWER_LIB.replace('"' + LIB + ':', '"').split("\n")))
    out.append(")")
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")
