#!/usr/bin/env python3
"""Package the C128DCR boards (SRAM module + MMU riser) into one release zip.

Collects each board's build outputs (gerbers, BOM/CPL, schematic, fit-check,
DRC/ERC reports, previews), the riser's GAL sources and JEDEC file, the READMEs
and the license, into dist/c128dcr-<version>.zip.  It packages what the last
build wrote; it never runs a build.  Refuses to package if a board has not been
built, its DRC/ERC reports are not clean, or the outputs are older than the
generator sources (pass --allow-stale to override the last check).

    python3 tools/package_release.py [--version v1.0] [--out dist] [--allow-stale]
"""
import argparse
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARDS = {"sram": "c128dcr-sram", "mmu-riser": "c128dcr-mmu-riser"}

# Per-board files, relative to c128dcr/<board>/, kept at the same relative path in the zip.
BOARD_FILES = [
    "README.md",
    "fab/{name}-gerbers.zip",
    "fab/jlc-bom.csv",
    "fab/jlc-cpl.csv",
    "fab/macrofab-bom.csv",
    "fab/macrofab-cpl.csv",
    "fab/{name}-schematic.pdf",
    "fab/{name}-fit-check-1to1.pdf",
    "fab/drc.rpt",
    "fab/erc.rpt",
    "docs/bom.csv",
    "docs/pcb_top.png",
    "docs/pcb_bottom.png",
    "docs/pcb_both.png",
    "docs/schematic_preview.png",
]
EXTRA_FILES = {
    "mmu-riser": ["gal/riser.pld", "gal/riser.jed", "gal/check_gal.py"],
}
TOP_FILES = ["README.md", "LICENSE"]
OPTIONAL_TOP = ["docs/c128dcr-fit-check-1to1.pdf", "tools/memtest/README.md", "tools/memtest/fab/c128-memtest-u36.bin"]
# Generator sources whose change makes fab/ stale.
SOURCE_GLOBS = ["design.py", "sch_content.py", "build.py", "gal/riser.pld"]
SHARED_GLOB = "common/*.py"


def fail(msg):
    sys.exit("package_release: " + msg)


def check_reports(board):
    d = ROOT / "c128dcr" / board / "fab"
    drc, erc = (d / "drc.rpt").read_text(), (d / "erc.rpt").read_text()
    counts = [int(n) for n in re.findall(r"\*\* Found (\d+) ", drc)]
    if len(counts) != 3 or any(counts):
        fail("%s: drc.rpt is not clean (found counts %s); rebuild" % (board, counts))
    m = re.search(r"ERC messages: (\d+)\s+Errors (\d+)\s+Warnings (\d+)", erc)
    if not m or any(int(g) for g in m.groups()):
        fail("%s: erc.rpt is not clean; rebuild" % board)


def check_fresh(board, allow_stale):
    base = ROOT / "c128dcr" / board
    out = (base / "fab" / ("%s-gerbers.zip" % BOARDS[board])).stat().st_mtime
    srcs = [base / g for g in SOURCE_GLOBS if (base / g).exists()] + list((ROOT).glob(SHARED_GLOB))
    newer = [str(s.relative_to(ROOT)) for s in srcs if s.stat().st_mtime > out]
    if newer and not allow_stale:
        fail("%s: fab/ is older than %s; rebuild or pass --allow-stale" % (board, ", ".join(newer)))


def git_rev():
    try:
        rev = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True).stdout.strip()
        return rev + ("-dirty" if dirty else "") if rev else "unknown"
    except OSError:
        return "unknown"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", default="v1.0")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--allow-stale", action="store_true")
    a = ap.parse_args()

    entries = []  # (source path, path inside the zip)
    prefix = "c128dcr-%s" % a.version
    for t in TOP_FILES:
        entries.append((ROOT / t, "%s/%s" % (prefix, t)))
    for t in OPTIONAL_TOP:
        if (ROOT / t).exists():
            entries.append((ROOT / t, "%s/%s" % (prefix, t)))
    for board, name in BOARDS.items():
        base = ROOT / "c128dcr" / board
        if not (base / "fab").is_dir():
            fail("%s has no fab/ folder; run `python3 build.py` in c128dcr/%s first" % (board, board))
        for f in [f.format(name=name) for f in BOARD_FILES] + EXTRA_FILES.get(board, []):
            if not (base / f).exists():
                fail("missing %s/%s; rebuild the board" % (board, f))
            entries.append((base / f, "%s/%s/%s" % (prefix, board, f)))
        check_reports(board)
        check_fresh(board, a.allow_stale)

    out = ROOT / a.out
    out.mkdir(exist_ok=True)
    zpath = out / ("%s.zip" % prefix)
    info = "C128DCR boards %s\nsource commit: %s\npackaged: %s\nboards: %s\n" % (
        a.version, git_rev(), time.strftime("%Y-%m-%d", time.gmtime()), ", ".join(BOARDS))
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("%s/BUILD-INFO.txt" % prefix, info)
        for src, arc in entries:
            z.write(src, arc)
    print("wrote %s (%d files, %.0f KB)" % (zpath.relative_to(ROOT), len(entries) + 1, zpath.stat().st_size / 1024))
    print(info, end="")


if __name__ == "__main__":
    main()
