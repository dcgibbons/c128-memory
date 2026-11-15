"""Independent DRC on the generated copper (exact geometry, no grid):
clearance between different nets, copper-to-edge, hole spacing, and
per-net connectivity (every pad of a net reachable through copper)."""
import math
import design as D
from geom import part_pads, copper_dist

HOLE_SPACING = 0.25


def _bbox(c):
    if c.kind == "seg":
        r = c.w / 2
        return (min(c.x, c.x2) - r, min(c.y, c.y2) - r, max(c.x, c.x2) + r, max(c.y, c.y2) + r)
    return (c.x - c.w / 2, c.y - c.h / 2, c.x + c.w / 2, c.y + c.h / 2)


def run(parts, segs, vias, clearance=D.CLEARANCE, tol=1e-3):
    pads = [c for p in parts for c in part_pads(p)]
    items = pads + list(segs) + list(vias)
    errors = []
    # nets: unconnected pads get a unique pseudo-net
    def netof(c):
        return c.net if c.net else "NC:%s.%s" % (c.ref, c.num)

    bbs = [_bbox(c) for c in items]
    # spatial hash.  Boxes are grown by half the largest spacing rule before
    # hashing, so two items closer than the rule always share a cell even
    # when they sit on either side of a cell boundary.
    cell = 2.0
    grow = max(clearance, HOLE_SPACING) / 2 + tol
    grid = {}
    for k, b in enumerate(bbs):
        b = (b[0] - grow, b[1] - grow, b[2] + grow, b[3] + grow)
        for gx in range(int(math.floor(b[0] / cell)), int(math.floor(b[2] / cell)) + 1):
            for gy in range(int(math.floor(b[1] / cell)), int(math.floor(b[3] / cell)) + 1):
                grid.setdefault((gx, gy), []).append(k)
    pairs = set()
    for lst in grid.values():
        for a in range(len(lst)):
            for b in range(a + 1, len(lst)):
                pairs.add((lst[a], lst[b]) if lst[a] < lst[b] else (lst[b], lst[a]))

    parent = list(range(len(items)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ca, cb = items[a], items[b]
        if not set(ca.layers) & set(cb.layers):
            continue
        ba, bb = bbs[a], bbs[b]
        if (ba[0] - clearance > bb[2] or bb[0] - clearance > ba[2] or
                ba[1] - clearance > bb[3] or bb[1] - clearance > ba[3]):
            continue
        d = copper_dist(ca, cb)
        if netof(ca) == netof(cb):
            if d <= tol:
                parent[find(a)] = find(b)
            # Same net is never a short risk, but two same-net VIAS packed
            # together is still JLC's "pad spacing" Danger (confirmed: a
            # router regression once independently placed 6 GND vias along
            # U7 at the IC's own 0.65mm pin pitch -- each one individually
            # satisfied every other check, since same-net copper is normally
            # ignored here, and 0.65mm is well clear of HOLE_SPACING).  Hole
            # spacing (checked below, net-independent) guards the drill;
            # this guards the copper pad overlapping the next via's.
            elif ca.kind == "via" and cb.kind == "via" and d < clearance - tol:
                errors.append("via pad spacing %.3f < %.3f (same net %s) at (%.2f,%.2f)" % (
                    d, clearance, ca.net, ca.x, ca.y))
        elif d < clearance - tol:
            errors.append("clearance %.3f < %.3f: %s %s/%s  vs  %s %s/%s at (%.2f,%.2f)" % (
                d, clearance, ca.kind, ca.net, ca.ref or "", cb.kind, cb.net, cb.ref or "",
                ca.x, ca.y))
        # hole to hole
        if ca.drill and cb.drill:
            hd = math.hypot(ca.x - cb.x, ca.y - cb.y) - (ca.drill + cb.drill) / 2
            if hd < HOLE_SPACING:
                errors.append("hole spacing %.3f at (%.2f,%.2f)" % (hd, ca.x, ca.y))
    # edge clearance
    x0, y0, x1, y1 = D.BOARD
    for c, b in zip(items, bbs):
        e = min(b[0] - x0, x1 - b[2], b[1] - y0, y1 - b[3])
        if e < 0.3 - tol:
            errors.append("edge clearance %.3f: %s %s at (%.2f,%.2f)" % (e, c.kind, c.net, c.x, c.y))
    # track widths
    for s in segs:
        if s.w < 0.15 - tol:
            errors.append("track too thin %.3f" % s.w)
    # connectivity.  Planed power nets (PLANE_POWER) rely on an inner-layer
    # zone fill for same-net connectivity, which this script doesn't model
    # (no zone geometry here) -- every SMD pad still gets an explicit stub
    # + via checked above for clearance, but "are they all one island" is
    # left to the real KiCad DRC, which does fill the zones.
    planed = getattr(D, "PLANE_POWER", False)
    unconnected = []
    bynet = {}
    for k, c in enumerate(items):
        if c.kind == "pad" and c.net and not (planed and c.net in D.POWER_NETS):
            bynet.setdefault(c.net, []).append(k)
    for net, ks in bynet.items():
        roots = {find(k) for k in ks}
        if len(roots) > 1:
            groups = {}
            for k in ks:
                groups.setdefault(find(k), []).append("%s.%s" % (items[k].ref, items[k].num))
            unconnected.append("%s split into %d islands: %s" % (
                net, len(roots), " | ".join(",".join(v) for v in groups.values())))
    # dangling track ends (not touching anything of the same net)
    return errors, unconnected
