"""Copper geometry helpers shared by router, DRC and renderer."""
import math
from board import Part


class Copper:
    """A piece of copper: kind = 'pad' | 'seg' | 'via'."""
    __slots__ = ("kind", "net", "layers", "shape", "x", "y", "w", "h",
                 "x2", "y2", "ref", "num", "drill", "smd")

    def __init__(self, kind, net, layers, **kw):
        self.kind, self.net, self.layers = kind, net, layers
        self.shape = kw.get("shape", "circle")
        self.x, self.y = kw.get("x", 0.0), kw.get("y", 0.0)
        self.w, self.h = kw.get("w", 0.0), kw.get("h", 0.0)
        self.x2, self.y2 = kw.get("x2", 0.0), kw.get("y2", 0.0)
        self.ref, self.num = kw.get("ref"), kw.get("num")
        self.drill = kw.get("drill", 0.0)
        self.smd = kw.get("smd", False)


def part_pads(part: Part):
    out = []
    for pd in part.fp.pads:
        x, y = part.pad_xy(pd)
        w, h = pd.w, pd.h
        if int(round(part.rot)) % 180 == 90:
            w, h = h, w
        layers = ((1,) if part.side == "B" else (0,)) if pd.kind == "smd" else (0, 1)
        out.append(Copper("pad", part.pins.get(pd.num), layers,
                          shape=pd.shape, x=x, y=y, w=w, h=h, ref=part.ref,
                          num=pd.num, drill=pd.drill, smd=pd.kind == "smd"))
    return out


def seg_point_dist(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    L = dx * dx + dy * dy
    if L == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _segs_intersect(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2 = orient(a, b, c), orient(a, b, d)
    o3, o4 = orient(c, d, a), orient(c, d, b)
    return (o1 * o2 < 0) and (o3 * o4 < 0)


def seg_seg_dist(a1, a2, b1, b2):
    if _segs_intersect(a1, a2, b1, b2):
        return 0.0
    return min(seg_point_dist(*a1, *b1, *b2), seg_point_dist(*a2, *b1, *b2),
               seg_point_dist(*b1, *a1, *a2), seg_point_dist(*b2, *a1, *a2))


def rect_point_dist(px, py, cx, cy, w, h):
    dx = max(abs(px - cx) - w / 2, 0.0)
    dy = max(abs(py - cy) - h / 2, 0.0)
    return math.hypot(dx, dy)


def _as_core(c: Copper):
    """Return (core, radius): copper = set of points within radius of core,
    core being a segment (p1,p2) or a rectangle.  Rect pads are handled as
    ('rect', cx, cy, w, h); round-ish pads as segments with radius."""
    if c.kind == "seg":
        return ("seg", (c.x, c.y), (c.x2, c.y2)), c.w / 2
    if c.kind == "via":
        return ("seg", (c.x, c.y), (c.x, c.y)), c.w / 2
    if c.shape == "circle":
        return ("seg", (c.x, c.y), (c.x, c.y)), c.w / 2
    if c.shape == "oval":
        if c.w >= c.h:
            d = (c.w - c.h) / 2
            return ("seg", (c.x - d, c.y), (c.x + d, c.y)), c.h / 2
        d = (c.h - c.w) / 2
        return ("seg", (c.x, c.y - d), (c.x, c.y + d)), c.w / 2
    # rect / roundrect: treat as plain rectangle (conservative)
    return ("rect", c.x, c.y, c.w, c.h), 0.0


def rect_seg_dist(r, p1, p2):
    _, cx, cy, w, h = r
    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    # inside?
    for p in (p1, p2):
        if x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
            return 0.0
    edges = [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
             ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    return min(seg_seg_dist(p1, p2, a, b) for a, b in edges)


def copper_dist(a: Copper, b: Copper):
    """Edge-to-edge distance between two copper items (<=0 = touching)."""
    ca, ra = _as_core(a)
    cb, rb = _as_core(b)
    if ca[0] == "seg" and cb[0] == "seg":
        d = seg_seg_dist(ca[1], ca[2], cb[1], cb[2])
    elif ca[0] == "rect" and cb[0] == "seg":
        d = rect_seg_dist(ca, cb[1], cb[2])
    elif ca[0] == "seg" and cb[0] == "rect":
        d = rect_seg_dist(cb, ca[1], ca[2])
    else:
        _, ax, ay, aw, ah = ca
        _, bx, by, bw, bh = cb
        dx = max(abs(ax - bx) - (aw + bw) / 2, 0.0)
        dy = max(abs(ay - by) - (ah + bh) / 2, 0.0)
        d = math.hypot(dx, dy)
        if dx == 0 and dy == 0:
            d = -1e-9
    return d - ra - rb
