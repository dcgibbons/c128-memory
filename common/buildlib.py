"""Shared build steps for every board in this repo.

A board's build.py describes the board (parts, power rails, silkscreen) and
calls these helpers to route, check and write the KiCad project, previews,
BOM and fabrication files.  Paths are relative to the board's own folder:
kicad/, docs/ and fab/ next to its build.py.
"""
import csv
import json
import os
import pickle
import subprocess
import zipfile

import design as D
import drc
import partcheck
import pcb_writer as PW
import render
import sch_preview
import sch_writer as SW
try:
    # Rust-accelerated astar (common/router_rs/, see its module docstring):
    # ~30x faster on this board's net list, validated net-for-net against
    # the pure-Python router (same success/failure per net, no path-length
    # discrepancies) before this import was wired in.  Falls back silently
    # to the pure-Python router if the compiled extension isn't present on
    # this machine (e.g. a platform the .so wasn't built for) -- nobody's
    # build breaks for not having a Rust toolchain.
    from router_rs import Router
except ImportError:
    from router import Router


class Paths:
    def __init__(self, board_dir):
        self.root = board_dir
        self.kicad = os.path.join(board_dir, "kicad")
        self.docs = os.path.join(board_dir, "docs")
        self.fab = os.path.join(board_dir, "fab")
        self.cache = os.path.join(board_dir, "route_cache.pkl")
        self.pretty = os.path.join(self.kicad, D.LIB + ".pretty")
        self.pcb = os.path.join(self.kicad, D.PROJECT + ".kicad_pcb")
        self.sch = os.path.join(self.kicad, D.PROJECT + ".kicad_sch")
        for d in (self.kicad, self.pretty, self.docs):
            os.makedirs(d, exist_ok=True)


def route(paths, parts, nets, reuse, add_rails=None, iters=60):
    """Route all nets (power first, then by pin count), caching the result."""
    r = Router(parts, nets, log=lambda s: print(s, flush=True))
    planed = getattr(D, "PLANE_POWER", False)
    if planed:
        # GND/+5V get their own inner-layer plane instead of F.Cu/B.Cu
        # traces: connect every SMD pad straight to it now, so the A*
        # pass below never has to fight them for routing space.
        for net in ("GND", "+5V"):
            if net in nets:
                r.plane_connect(net)
    elif add_rails:
        add_rails(r)
    if reuse and os.path.exists(paths.cache):
        r.paths = pickle.load(open(paths.cache, "rb"))
        return r, set()
    if planed:
        order = sorted([n for n in nets if n not in D.POWER_NETS], key=lambda n: -len(nets[n]))
    else:
        order = [n for n in ("GND", "+5V") if n in nets]
        order += sorted([n for n in nets if n not in D.POWER_NETS], key=lambda n: -len(nets[n]))
    left = r.run(order, iters=iters)
    pickle.dump(r.paths, open(paths.cache, "wb"))
    return r, left


def check_parts(parts, offline=False):
    """Every part number must exist in the JLC/LCSC catalog (see partcheck)."""
    ok, lines = partcheck.run(parts, offline)
    print("\n".join(lines))
    return ok


def check(parts, segs, vias, left):
    errs, unconn = drc.run(parts, segs, vias)
    print("routing leftovers:", sorted(left) or "none")
    print("DRC: %d clearance/edge errors, %d unconnected nets" % (len(errs), len(unconn)))
    for e in errs[:40] + unconn:
        print("  ", e)
    return not errs and not unconn and not left


def write_kicad(paths, parts, segs, vias, texts=(), logos=(), polys=()):
    PW.write_pcb(paths.pcb, parts, segs, vias, texts, logos, polys)
    seen = set()
    for p in list(parts) + list(logos):
        if p.fp.name not in seen:
            seen.add(p.fp.name)
            PW.write_mod(os.path.join(paths.pretty, p.fp.name + ".kicad_mod"), p.fp)
    syms = SW.write_schematic(paths.sch, parts)
    SW.write_symlib(os.path.join(paths.kicad, D.LIB + ".kicad_sym"), syms)
    write_project(paths)


def write_project(paths):
    pro = {
        "board": {
            "design_settings": {
                "defaults": {"board_outline_line_width": 0.1, "copper_line_width": 0.2,
                             "silk_line_width": 0.12, "silk_text_size_h": 0.8,
                             "silk_text_size_v": 0.8, "silk_text_thickness": 0.12},
                "rules": {"min_clearance": 0.15, "min_copper_edge_clearance": 0.3,
                          "min_hole_clearance": 0.25, "min_hole_to_hole": 0.25,
                          "min_through_hole_diameter": 0.3, "min_track_width": 0.15,
                          "min_via_annular_width": 0.1, "min_via_diameter": 0.5,
                          "min_silk_clearance": 0.0, "solder_mask_to_copper_clearance": 0.0,
                          "use_height_for_length_calcs": True},
                # silkscreen checks are on: off the board edge and silk-on-silk
                # overlap are errors (fabs clip/mangle those); silk over copper
                # is only a warning -- with a GND pour covering most of the
                # board, "copper" here is almost always soldermask-covered
                # plane, not an exposed pad, which is normal on real PCBs
                "rule_severities": {"silk_overlap": "error", "silk_over_copper": "warning",
                                    "silk_edge_clearance": "error", "lib_footprint_issues": "ignore",
                                    "lib_footprint_mismatch": "ignore"},
                "track_widths": [0.0, 0.2, 0.4, 0.6],
                "via_dimensions": [{"diameter": 0.0, "drill": 0.0}, {"diameter": 0.6, "drill": 0.3}],
            },
        },
        "boards": [],
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": D.PROJECT + ".kicad_pro", "version": 1},
        "net_settings": {
            "classes": [
                {"name": "Default", "clearance": 0.15, "track_width": 0.2,
                 "via_diameter": 0.6, "via_drill": 0.3, "microvia_diameter": 0.3,
                 "microvia_drill": 0.1, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
                 "diff_pair_width": 0.2, "bus_width": 12, "line_style": 0, "wire_width": 6,
                 "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)"},
                {"name": "Power", "clearance": 0.15, "track_width": 0.4,
                 "via_diameter": 0.6, "via_drill": 0.3, "microvia_diameter": 0.3,
                 "microvia_drill": 0.1, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
                 "diff_pair_width": 0.2, "bus_width": 12, "line_style": 0, "wire_width": 6,
                 "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)"},
            ],
            "meta": {"version": 3},
            "net_colors": None,
            "netclass_assignments": None,
            "netclass_patterns": [{"netclass": "Power", "pattern": "GND"},
                                  {"netclass": "Power", "pattern": "+5V"}],
        },
        "pcbnew": {"last_paths": {}, "page_layout_descr_file": ""},
        "schematic": {"drawing": {"default_line_thickness": 6.0, "default_text_size": 50.0},
                      "legacy_lib_dir": "", "legacy_lib_list": []},
        "sheets": [[SW.ROOT, ""]],
        "text_variables": {},
    }
    with open(os.path.join(paths.kicad, D.PROJECT + ".kicad_pro"), "w") as fh:
        json.dump(pro, fh, indent=2)
    with open(os.path.join(paths.kicad, "fp-lib-table"), "w") as fh:
        fh.write('(fp_lib_table\n  (version 7)\n  (lib (name "%s")(type "KiCad")'
                 '(uri "${KIPRJMOD}/%s.pretty")(options "")(descr "%s"))\n)\n' % (D.LIB, D.LIB, D.LIB_DESCR))
    with open(os.path.join(paths.kicad, "sym-lib-table"), "w") as fh:
        fh.write('(sym_lib_table\n  (version 7)\n  (lib (name "%s")(type "KiCad")'
                 '(uri "${KIPRJMOD}/%s.kicad_sym")(options "")(descr "%s"))\n)\n' % (D.LIB, D.LIB, D.LIB_DESCR))


def write_bom(paths, parts, note=lambda refs, fp, hand, dnp: ""):
    """docs/bom.csv grouped by value/footprint/MPN; note() adds a Notes column.
    A DNP part's group gets "HAND-SOLDER" prepended to its note.  Every use
    of Part.dnp in this codebase (just U1, no LCSC stock) means "skip this
    in the vendor's automated assembly, the buyer fits it by hand" -- never
    "leave this position empty" -- so the BOM note, and KiCad's own (dnp
    yes) schematic/3D-viewer marking, used to literally read "DO NOT
    POPULATE" right next to a note saying to hand-solder the board's single
    most important chip.  The flag itself still has to stay (MacroFab's
    export needs it to keep a no-LCSC, hand-fitted part out of its assembled
    quantity), just not worded as if the part should be left off the board."""
    rows = {}
    dnp = {}
    for p in parts:
        key = (p.value, p.fp.name, p.mpn, p.lcsc, p.hand)
        rows.setdefault(key, []).append(p.ref)
        dnp[key] = dnp.get(key, False) or getattr(p, "dnp", False)
    with open(os.path.join(paths.docs, "bom.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Qty", "References", "Value", "Footprint", "MPN", "LCSC", "Notes"])
        for key, refs in sorted(rows.items(), key=lambda kv: kv[1][0]):
            val, fp, mpn, lcsc, hand = key
            n = note(refs, fp, hand, dnp[key])
            if dnp[key]:
                n = "HAND-SOLDER (not vendor-assembled)" + ("; " + n if n else "")
            w.writerow([len(refs), " ".join(refs), val, fp, mpn, lcsc, n])


def write_previews(paths, parts, segs, vias, title):
    render.render(parts, segs, vias, os.path.join(paths.docs, "pcb_both.png"), title=title)
    render.render(parts, [s for s in segs if s.layers[0] == 0], vias,
                  os.path.join(paths.docs, "pcb_top.png"), layers=(0,), title="F.Cu")
    render.render(parts, [s for s in segs if s.layers[0] == 1], vias,
                  os.path.join(paths.docs, "pcb_bottom.png"), layers=(1,), title="B.Cu")
    sch_preview.render(SW.write_schematic.sheet, os.path.join(paths.docs, "schematic_preview.png"))


def write_jlc(paths, parts):
    """JLCPCB assembly files: BOM (LCSC part numbers) and CPL (placement), for
    the parts JLC places (an LCSC number, SMD, not DNP'd, and -- unless
    FULL_ASSEMBLY opts in to paying JLC's extended-part fee for ICs too --
    not hand-fitted).  Through-hole parts (dip_plug/jst_ph2*, smd=False)
    never go through JLC's standard SMT assembly regardless of any flag.
    Positions come from KiCad's own export so they match the Gerbers."""
    full = getattr(D, "FULL_ASSEMBLY", False)
    rows = {}
    for p in parts:
        if not p.lcsc or not p.fp.smd or getattr(p, "dnp", False):
            continue
        if p.hand and not full:
            continue
        rows.setdefault((p.value, p.fp.name, p.lcsc), []).append(p.ref)
    with open(os.path.join(paths.fab, "jlc-bom.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for (val, fp, lcsc), refs in sorted(rows.items(), key=lambda kv: kv[1][0]):
            w.writerow([val, ",".join(refs), fp, lcsc])
    pos = os.path.join(paths.fab, "pos.csv")
    subprocess.run(["kicad-cli", "pcb", "export", "pos", "--side", "front", "--format", "csv",
                    "--units", "mm", "--smd-only", "-o", pos, paths.pcb],
                   check=True, capture_output=True)
    keep = {r for refs in rows.values() for r in refs}
    with open(pos) as fh, open(os.path.join(paths.fab, "jlc-cpl.csv"), "w", newline="") as out:
        w = csv.writer(out)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for r in csv.DictReader(fh):
            if r["Ref"] in keep:
                w.writerow([r["Ref"], r["PosX"] + "mm", r["PosY"] + "mm", "Top",
                            "%g" % float(r["Rot"])])
    os.remove(pos)


def write_macrofab(paths, parts):
    """MacroFab assembly files: BOM (Designator/Manufacturer/Part Number,
    their required/recommended columns) and CPL.  One row per designator,
    not grouped by value -- MacroFab's own notes ask for each designator to
    be unique, which a comma-joined multi-ref row (the JLC convention) is
    not.  DNP and through-hole parts (which can't go through standard SMT
    assembly regardless of FULL_ASSEMBLY) are still listed, at Quantity 0,
    per MacroFab's own documented convention for marking a part DNP --
    better than silently omitting a board position."""
    full = getattr(D, "FULL_ASSEMBLY", False)
    rows = []   # (ref, qty, manufacturer, mpn, description)
    placed = set()
    for p in parts:
        if p.ref.startswith("TP"):
            continue  # bare test pads, not a real component
        place = p.fp.smd and p.mpn and not getattr(p, "dnp", False) and not (p.hand and not full)
        rows.append((p.ref, 1 if place else 0, getattr(p, "manufacturer", ""), p.mpn, p.value))
        if place:
            placed.add(p.ref)
    with open(os.path.join(paths.fab, "macrofab-bom.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Designator", "Quantity", "Manufacturer", "Part Number", "Description"])
        for ref, qty, mfr, mpn, descr in sorted(rows, key=lambda r: r[0]):
            w.writerow([ref, qty, mfr, mpn, descr])
    pos = os.path.join(paths.fab, "pos.csv")
    subprocess.run(["kicad-cli", "pcb", "export", "pos", "--side", "front", "--format", "csv",
                    "--units", "mm", "--smd-only", "-o", pos, paths.pcb],
                   check=True, capture_output=True)
    with open(pos) as fh, open(os.path.join(paths.fab, "macrofab-cpl.csv"), "w", newline="") as out:
        w = csv.writer(out)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for r in csv.DictReader(fh):
            if r["Ref"] in placed:
                w.writerow([r["Ref"], r["PosX"] + "mm", r["PosY"] + "mm", "Top",
                            "%g" % float(r["Rot"])])
    os.remove(pos)


def kicad_finish(paths, parts, skip=False):
    """Fill the GND pours, run KiCad ERC/DRC and plot the fabrication files
    into fab/.  Returns False if a check fails, or if KiCad is missing: a
    build without KiCad's checks is not a verified build.  With skip=True
    (--no-kicad) it says so, removes stale fab/ outputs and returns False."""
    if not skip:
        try:
            import pcbnew
        except ImportError:
            print("KiCad python module not found")
            skip = True
    if skip:
        if os.path.isdir(paths.fab):
            import shutil
            shutil.rmtree(paths.fab)
        print("KiCad ERC/DRC/Gerbers NOT run; fab/ removed so no stale files remain. "
              "This build is not verified.")
        return False
    b = pcbnew.LoadBoard(paths.pcb)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    b.Save(paths.pcb)
    fab = paths.fab
    gbr = os.path.join(fab, "gerbers")
    os.makedirs(gbr, exist_ok=True)
    for old in os.listdir(gbr):
        os.remove(os.path.join(gbr, old))
    run = lambda *a: subprocess.run(["kicad-cli", *a], check=True, capture_output=True)
    run("sch", "erc", "--severity-all", "-o", os.path.join(fab, "erc.rpt"), paths.sch)
    run("pcb", "drc", "--schematic-parity", "--severity-all", "-o", os.path.join(fab, "drc.rpt"), paths.pcb)
    run("pcb", "export", "gerbers", "--board-plot-params", "-o", gbr + "/", paths.pcb)
    run("pcb", "export", "drill", "--format", "excellon", "--excellon-separate-th",
        "--generate-map", "--map-format", "pdf", "-o", gbr + "/", paths.pcb)
    # 1:1 fit-check template: board outline, pad outlines and package outlines,
    # to lay over the motherboard and check position and orientation.
    run("pcb", "export", "pdf", "--layers", "Edge.Cuts,F.Fab,F.SilkS", "--sp",
        "--black-and-white", "--drill-shape-opt", "2", "-o",
        os.path.join(fab, D.PROJECT + "-fit-check-1to1.pdf"), paths.pcb)
    run("sch", "export", "pdf", "-o", os.path.join(fab, D.PROJECT + "-schematic.pdf"), paths.sch)
    write_jlc(paths, parts)
    write_macrofab(paths, parts)
    zpath = os.path.join(fab, D.PROJECT + "-gerbers.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in sorted(os.listdir(gbr)):
            z.write(os.path.join(gbr, fn), fn)
    erc = open(os.path.join(fab, "erc.rpt")).read()
    drc_rpt = open(os.path.join(fab, "drc.rpt")).read()
    # --severity-all reports warnings too (e.g. silk_over_copper, downgraded
    # from error since the GND pour covers most of the board and most
    # silkscreen ends up over soldermask-covered copper, same as on most
    # real PCBs); only actual errors should fail the build.
    drc_errors = drc_rpt.count("; error")
    drc_warnings = drc_rpt.count("; warning")
    ok = ("ERC messages: 0 " in erc and drc_errors == 0
          and "Found 0 unconnected pads" in drc_rpt and "Found 0 Footprint errors" in drc_rpt)
    print("KiCad ERC/DRC:", "clean" if ok else "PROBLEMS - see fab/erc.rpt, fab/drc.rpt",
          "(%d warning(s))" % drc_warnings if ok and drc_warnings else "")
    print("Gerbers:", zpath)
    return ok
