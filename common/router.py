"""Small 2-layer grid router for the SRAM module.

* A* on a 0.127 mm grid, 8 directions, vias, bend penalties.
* Clearance is enforced against the exact geometry of every copper item by
  rasterising each item's keep-out zone for every track-width class.
* Negotiated congestion (PathFinder): pads, pad-escape reservations and
  pre-routed rails are hard obstacles; other nets' tracks are soft
  obstacles whose cost rises every iteration until no overlaps remain.
"""
import heapq
import math
import time
import numpy as np

import design as D
from geom import Copper, part_pads, copper_dist

G = 0.127                       # grid pitch (mm)
SLOP = 0.012                    # extra keep-out to absorb rasterisation
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
VIA_COST = 12.0
BEND_COST = 0.6
OFFDIR_COST = 0.3              # extra per-step cost for non-preferred direction
STUB_LEN = 0.5                 # pad escape reservation beyond pad end
HOLE_SPACING = 0.25            # DFM: minimum drill-to-drill spacing (matches drc.py)
LEAD_HOLE_MARGIN = 0.3         # DFM "lead to hole": JLC wants more than our own
                                # D.CLEARANCE between a component lead and any
                                # drilled hole for reliable assembly/reflow, not
                                # just enough to avoid an electrical short


class Grid:
    def __init__(self):
        x0, y0, x1, y1 = D.ROUTE_AREA
        self.x0, self.y0 = x0, y0
        self.nx = int(math.floor((x1 - x0) / G)) + 1
        self.ny = int(math.floor((y1 - y0) / G)) + 1
        self.X = x0 + np.arange(self.nx) * G
        self.Y = y0 + np.arange(self.ny) * G

    def to_xy(self, i, j):
        return self.x0 + i * G, self.y0 + j * G

    def to_ij(self, x, y):
        return int(round((x - self.x0) / G)), int(round((y - self.y0) / G))


def width_of(net):
    return D.W_PWR if net in D.POWER_NETS else D.W_SIG


class Router:
    def __init__(self, parts, nets, log=print):
        self.log = log
        self.g = Grid()
        self.parts, self.nets = parts, nets
        self.wcs = {"sig": D.W_SIG / 2, "pwr": D.W_PWR / 2, "via": D.VIA_D / 2}
        ny, nx = self.g.ny, self.g.nx
        self.hard = {wc: np.zeros((2, ny, nx), np.int16) for wc in self.wcs}
        self.soft = {wc: np.zeros((2, ny, nx), np.int16) for wc in self.wcs}
        self.hist = np.zeros((2, ny, nx), np.float32)
        self.objs_by_net = {}      # net -> list of (Copper, masks, is_soft)
        self.pads = []
        self.paths = {}            # net -> list of paths [(i,j,L), ...]
        self.prewires = []
        self.plane_items = []      # stub+via Copper for PLANE_POWER nets
        for p in parts:
            for c in part_pads(p):
                self.pads.append(c)
                self._add(c, soft=False)
        # pad escape reservations (virtual copper, never output)
        for p in parts:
            smd = [c for c in part_pads(p) if c.smd]
            if len(smd) < 5:
                continue
            for c in smd:
                vx, vy = c.x - p.x, c.y - p.y
                if c.w > c.h:
                    ux, uy, ln = (1 if vx > 0 else -1), 0, c.w / 2
                else:
                    ux, uy, ln = 0, (1 if vy > 0 else -1), c.h / 2
                ext = ln + STUB_LEN
                self._add(Copper("seg", c.net, (0,), x=c.x, y=c.y,
                                 x2=c.x + ux * ext, y2=c.y + uy * ext, w=0.2,
                                 ref="__stub__"), soft=False)
        # no vias in/near SMD pads or THT holes (any net).  SMD pads get a
        # tighter 0.05mm floor (not D.CLEARANCE): tried raising this to
        # D.CLEARANCE uniformly to get more margin against JLC's DFM
        # advisories, but on this board's density the router couldn't
        # converge (3 nets stuck fighting for 45 min, real clearance
        # violations in the output) -- reverted. The realized clearance
        # already comes out at >=0.163mm in practice (verified against
        # exact pad/via geometry), comfortably above D.CLEARANCE, so this
        # floor is a routability allowance, not a clearance risk.
        self.novia = np.zeros((ny, nx), bool)
        for c in self.pads:
            sy, sx, m = self._mask(c, (0.05 if c.smd else D.CLEARANCE) + D.VIA_D / 2)
            self.novia[sy, sx] |= m
        # no vias under a chip R/C's body either, even where clear of its
        # pads: one hidden under the soldered part can't be inspected or
        # reworked, and it looks like the via belongs to that part even
        # when it's actually a stitching via for an unrelated net.
        for p in parts:
            if p.sym not in ("r", "c"):
                continue
            xs = [pt for layer, kind, data in p.fp.graphics if layer == "F.CrtYd" and kind == "line"
                  for pt in (data[0], data[2])]
            ys = [pt for layer, kind, data in p.fp.graphics if layer == "F.CrtYd" and kind == "line"
                  for pt in (data[1], data[3])]
            if not xs:
                continue
            a = math.radians(p.rot)
            ca, sa = math.cos(a), math.sin(a)
            corners = [(lx, ly) for lx in (min(xs), max(xs)) for ly in (min(ys), max(ys))]
            bxy = [(p.x + lx * ca + ly * sa, p.y - lx * sa + ly * ca) for lx, ly in corners]
            bx0, bx1 = min(x for x, y in bxy), max(x for x, y in bxy)
            by0, by1 = min(y for x, y in bxy), max(y for x, y in bxy)
            body = Copper("pad", "", (0, 1), shape="rect", x=(bx0 + bx1) / 2, y=(by0 + by1) / 2,
                          w=bx1 - bx0, h=by1 - by0)
            sy, sx, m = self._mask(body, D.VIA_D / 2 + D.CLEARANCE)
            self.novia[sy, sx] |= m
        X, Y = np.meshgrid(self.g.X, self.g.Y)
        x0, y0, x1, y1 = D.ROUTE_AREA
        de = np.minimum.reduce([X - x0, x1 - X, Y - y0, y1 - Y])
        self.edge = {wc: de < (D.EDGE_CLR + hw) for wc, hw in self.wcs.items()}

    # -- rasterisation ----------------------------------------------------
    def _mask(self, c, r):
        g = self.g
        if c.kind == "seg":
            xa, xb = sorted((c.x, c.x2))
            ya, yb = sorted((c.y, c.y2))
            ext = r + c.w / 2
        else:
            xa = xb = c.x
            ya = yb = c.y
            ext = r + max(c.w, c.h) / 2
        i0 = max(0, int((xa - ext - g.x0) / G) - 1)
        i1 = min(g.nx, int((xb + ext - g.x0) / G) + 2)
        j0 = max(0, int((ya - ext - g.y0) / G) - 1)
        j1 = min(g.ny, int((yb + ext - g.y0) / G) + 2)
        X, Y = np.meshgrid(g.X[i0:i1], g.Y[j0:j1])
        if c.kind in ("seg", "via") or c.shape == "circle":
            if c.kind == "seg":
                x1, y1, x2, y2 = c.x, c.y, c.x2, c.y2
            else:
                x1 = x2 = c.x
                y1 = y2 = c.y
            rad = c.w / 2
            dx, dy = x2 - x1, y2 - y1
            L = dx * dx + dy * dy
            if L == 0:
                d = np.hypot(X - x1, Y - y1)
            else:
                t = np.clip(((X - x1) * dx + (Y - y1) * dy) / L, 0, 1)
                d = np.hypot(X - (x1 + t * dx), Y - (y1 + t * dy))
            d = d - rad
        else:
            d = np.hypot(np.maximum(np.abs(X - c.x) - c.w / 2, 0),
                         np.maximum(np.abs(Y - c.y) - c.h / 2, 0))
        return slice(j0, j1), slice(i0, i1), d < r

    def _add(self, c, soft):
        masks = {}
        arr = self.soft if soft else self.hard
        for wc, hw in self.wcs.items():
            sy, sx, m = self._mask(c, D.CLEARANCE + hw + SLOP)
            masks[wc] = (sy, sx, m)
            for L in c.layers:
                arr[wc][L][sy, sx] += m
        rec = (c, masks, soft)
        self.objs_by_net.setdefault(c.net, []).append(rec)
        return rec

    def _remove(self, rec):
        c, masks, soft = rec
        arr = self.soft if soft else self.hard
        for wc, (sy, sx, m) in masks.items():
            for L in c.layers:
                arr[wc][L][sy, sx] -= m
        self.objs_by_net[c.net].remove(rec)

    def _others(self, net, wc, soft):
        arr = (self.soft if soft else self.hard)[wc].copy()
        for c, masks, s in self.objs_by_net.get(net, []):
            if s != soft:
                continue
            sy, sx, m = masks[wc]
            for L in c.layers:
                arr[L][sy, sx] -= m
        return arr

    # -- inner-layer power planes (PLANE_POWER) --------------------------------
    def _plane_clear(self, net, c, wc):
        x0, y0, x1, y1 = D.BOARD
        m0 = D.EDGE_CLR + self.wcs[wc]
        for x, y in ((c.x, c.y), (c.x2, c.y2)) if c.kind == "seg" else ((c.x, c.y),):
            if x < x0 + m0 or x > x1 - m0 or y < y0 + m0 or y > y1 - m0:
                return False
        if c.kind == "via":
            # same-net copper is never a *short* risk, but two same-net
            # vias can still be too close for (a) the drill bit, or (b) JLC
            # "pad spacing": its own copper pad overlapping the next via's.
            # These are different constraints with different margins, not
            # one check -- (a) cares about the drilled hole (D.VIA_DRILL),
            # not the copper pad (D.VIA_D, nearly double the drill size);
            # using the pad size for (a) made every hole-to-hole check in
            # this search needlessly strict by ~0.3mm.  But dropping (b)
            # entirely (tried, after fixing share_via's own copper-overlap
            # bug below) let independent per-pin searches on U7's dense GND
            # row each find their own "natural" via at the same 0.65mm pin
            # pitch with nothing stopping them from stacking into a wall --
            # confirmed: 5 new 0.05mm via-to-via Dangers, each one honestly
            # >= HOLE_SPACING apart (0.65mm > 0.55mm) so (a) never objected,
            # yet with only a 0.05mm copper gap between annular rings.
            for it in self.plane_items:
                if it.kind != "via":
                    continue
                d = math.hypot(it.x - c.x, it.y - c.y)
                if d < HOLE_SPACING + D.VIA_DRILL:
                    return False
                if it.net == net and d < D.VIA_D + D.CLEARANCE:
                    return False
            margin = LEAD_HOLE_MARGIN  # JLC "lead to hole", not just D.CLEARANCE
        else:
            margin = D.CLEARANCE
        # `other[wc]` (via _others/_add) already marks every point within
        # D.CLEARANCE + self.wcs[wc] + SLOP of another net's copper EDGE --
        # exactly the forbidden zone for the *centreline* of a wc-width
        # item, the same contract route_net() relies on by indexing this
        # array directly.  Re-masking the whole candidate `c` here by
        # ANOTHER margin + self.wcs[wc] double- (for a via, effectively
        # triple-) counted its own half-width on top of what `other`
        # already encodes, inflating the real required gap from
        # margin+D.CLEARANCE to margin+D.CLEARANCE+VIA_D or so (verified:
        # ~1.0mm instead of the intended ~0.3mm for a via).  That silently
        # broke the search for nearly every pad on this board -- 19 of the
        # ~20 GND/+5V SMD pads fell straight through to the unchecked
        # single-shot fallback below, which only looked safe because real
        # board density happens to be loose enough for blind placement to
        # usually land fine.  Only the extra margin *beyond* what's already
        # baked into `other` (margin - D.CLEARANCE, zero for the plain
        # D.CLEARANCE "pwr" stub case) needs to be added here.
        sy, sx, m = self._mask(c, max(0.0, margin - D.CLEARANCE))
        other = self._others(net, wc, False)
        return not any(((other[L][sy, sx] > 0) & m).any() for L in c.layers)

    def plane_connect(self, net):
        """Give every SMD pad of `net` a short stub + via straight to its
        dedicated inner-layer plane, instead of routing it hop-by-hop on
        F.Cu/B.Cu.  Through-hole pads need nothing: their copper already
        spans every layer, so a filled plane connects to them directly.
        First choice for any pad is always to share an already-placed
        same-net via nearby (see share_via below) -- a GND/+5V plane-stitch
        via costs nothing extra to share as long as the pad reaching it
        stays short, and two parts crowded next to each other (a decoupling
        cap right by its IC, say) routinely qualify.  Only once that fails
        does a pad get a dedicated via: try the pad's natural escape heading
        first, then step around the compass at the same short radius
        (never reaching further out -- a long stub is more likely to cut
        across something unrelated than a short one in a different
        direction) before falling back to the heading anyway."""
        dirs8 = [(1, 0), (0.7071, 0.7071), (0, 1), (-0.7071, 0.7071),
                 (-1, 0), (-0.7071, -0.7071), (0, -1), (0.7071, -0.7071)]
        for p in self.parts:
            L = 1 if getattr(p, "side", "F") == "B" else 0
            pad_neighbors = part_pads(p)  # for the same-footprint clearance
            # check below -- real neighbour pads (e.g. an IC's own run of
            # adjacent same-net GND pins), not just the stub's own pad.
            for pd in p.fp.pads:
                if p.pins.get(pd.num) != net or pd.kind != "smd":
                    continue
                neighbors = [cp for cp in pad_neighbors if cp.num != pd.num]
                x, y = p.pad_xy(pd)
                w, h = pd.w, pd.h
                if int(round(p.rot)) % 180 == 90:
                    w, h = h, w
                half_w, half_h = w / 2, h / 2
                vx, vy = x - p.x, y - p.y
                if p.sym in ("r", "c"):
                    # A 2-terminal chip part's two pads sit purely along one
                    # axis (the gap between them), so the offset from the
                    # part centre (vx, vy) always points straight away from
                    # the body along that same axis -- unambiguous, unlike
                    # an IC's pad shape (below).  Picking by w>h instead
                    # (0603/0805 pads are nearly square, h fractionally
                    # bigger than w) made BOTH pads of a part pick the same
                    # absolute heading regardless of which side they're on,
                    # so under some rotations both vias aimed at whatever
                    # neighbour happened to sit in that one direction
                    # (confirmed root cause of the C6/U10 collision).
                    pux, puy = ((1 if vx >= 0 else -1), 0) if abs(vx) >= abs(vy) else (0, (1 if vy >= 0 else -1))
                else:
                    # IC leads: the pad's own aspect ratio (radial pad arm
                    # long, tangential short), not (vx, vy), decides the
                    # escape axis -- a pin near the end of a long row can
                    # have |vy| exceed the fixed |vx| offset from the body
                    # even though the correct escape is still straight out
                    # in x, away from the body.
                    pux, puy = ((1 if vx >= 0 else -1), 0) if w > h else (0, (1 if vy >= 0 else -1))
                margin = 0.05 + LEAD_HOLE_MARGIN + D.VIA_D / 2 + 0.05

                def exit_dist(ux, uy, half_w=half_w, half_h=half_h):
                    # True distance from the pad centre to its own edge
                    # along (ux, uy).  A single scalar (the longer
                    # half-extent) reused for every fallback direction used
                    # to under-measure the pad near its corners -- a via
                    # landing in a diagonal fallback direction could end up
                    # placed short of the real corner and overlap the pad
                    # (exactly the "pad spacing"/"via to pad" DFM dangers
                    # JLC and MacroFab both flagged).
                    if ux == 0:
                        return half_h
                    if uy == 0:
                        return half_w
                    return min(half_w / abs(ux), half_h / abs(uy))

                def via_clear_of_neighbors(via):
                    # _plane_clear() deliberately ignores same-net copper
                    # (electrically harmless), but a via overlapping a
                    # *neighbouring* pad of the same footprint -- same net
                    # or not -- is still a real DFM risk (solder wicking
                    # into the via instead of staying on the pad).  Common
                    # on an IC with several adjacent pins strapped to the
                    # same net (e.g. a run of GND pins at 0.65mm pitch), and
                    # never relaxed: a via has to clear every neighbour
                    # regardless of how the stub reaching it is routed.
                    return all(copper_dist(via, cp) >= LEAD_HOLE_MARGIN for cp in neighbors)

                def seg_clear_of_neighbors(seg, strict):
                    # A via clear of every neighbour isn't enough on its
                    # own, though -- a diagonal heading can put the via
                    # somewhere perfectly clear while the straight stub
                    # reaching it still clips a neighbour's corner along the
                    # way (confirmed: a pad's own dedicated via, found by
                    # this exact search, with the via itself clear but its
                    # stub overlapping the adjacent pad by ~0.2mm).  Check
                    # the stub too, at the same differentiated margin
                    # share_via() uses: a same-net neighbour only needs
                    # plain copper clearance, a different-net one gets the
                    # full lead-to-hole margin.  strict=False is the genuine
                    # last resort -- same-net neighbour pads are a DFM risk
                    # worth a fallback warning (never a short), so it's the
                    # only thing allowed to relax here.
                    return all((not strict and cp.net == net) or
                               copper_dist(seg, cp) >= (D.CLEARANCE if cp.net == net else LEAD_HOLE_MARGIN)
                               for cp in neighbors)

                def share_via():
                    # If an existing same-net via is already close enough
                    # that a dedicated one for this pad would be redundant
                    # (and risk landing on top of it -- JLC's "pad
                    # spacing"/"hole to hole" dangers), wire straight to it
                    # instead.  SHARE_R is generous enough to catch genuinely
                    # adjacent parts (decoupling caps crowded onto the same
                    # IC, stitching vias a fraction of a mm apart) but short
                    # enough that it never reaches for a far-away via when a
                    # short dedicated stub would serve the pad better --
                    # this is the plane-stitch via, not the pad's own
                    # decoupling trace, so sharing it costs nothing
                    # electrically as long as it stays short.
                    # Measure from where this pad's *own* via would actually
                    # land (its natural escape point, same heading pass 1/2
                    # start from), not from the pad itself -- the pad is
                    # already ~0.7mm from that point (margin below), so
                    # comparing raw pad-to-via distance badly underestimates
                    # how close two vias would really end up.
                    SHARE_R = 1.2
                    d0 = exit_dist(pux, puy) + margin
                    nat_x, nat_y = x + pux * d0, y + puy * d0
                    best, best_d = None, None
                    for it in self.plane_items:
                        if it.kind != "via" or it.net != net:
                            continue
                        d = math.hypot(it.x - nat_x, it.y - nat_y)
                        if d > SHARE_R or (best_d is not None and d >= best_d):
                            continue
                        seg = Copper("seg", net, (L,), x=x, y=y, x2=it.x, y2=it.y, w=width_of(net))
                        if not self._plane_clear(net, seg, "pwr"):
                            continue
                        # A same-net neighbour pad is never a bridging risk
                        # (it's the same electrical node), so it only needs
                        # plain copper clearance (D.CLEARANCE), not the full
                        # LEAD_HOLE_MARGIN other-net pads get. Checking
                        # same-net at LEAD_HOLE_MARGIN too made this reject
                        # every candidate on an IC with a run of same-net
                        # pins (e.g. U7's 8 contiguous GND pins): the
                        # straight line from one GND pin to the next GND
                        # pin's via necessarily passes within
                        # LEAD_HOLE_MARGIN of the GND pins between them, so
                        # share_via() could never fire inside exactly the
                        # cluster it would help most.  But dropping the
                        # same-net check to zero (tried first) let a shared
                        # via's own diagonal trace clip the corner of its
                        # *source* pad by ~0.1mm -- same-net, electrically
                        # harmless, but exactly the sloppy copper-overlap
                        # DFM pattern this whole mechanism exists to avoid.
                        # D.CLEARANCE is the middle ground: tight enough for
                        # one-pin-pitch sharing to still work when the
                        # geometry allows it, but it rejects a trace that
                        # actually clips a neighbour's copper instead of
                        # just passing near it.
                        if not seg_clear_of_neighbors(seg, True):
                            continue
                        best, best_d = (seg, None, None, False), d
                    return best

                heading = [(pux, puy)] + [uv for uv in dirs8 if uv != (pux, puy)]
                # DRAFT_MODE (for a big structural churn, e.g. swapping a
                # part's whole footprint): skip the expensive DFM-polish
                # search -- the neighbour-pad check and the wide multi-radius
                # budget -- since it's wasted effort while placement is
                # still moving.  Electrical safety (_plane_clear's other-net
                # check, hole-to-hole) is never part of this tradeoff and
                # stays on either way.  Flip DRAFT_MODE off and do one full
                # rebuild before trusting the board again.
                draft = getattr(D, "DRAFT_MODE", False)
                extras = (0.0, 0.5) if draft else (0.0, 0.3, 0.6, 1.0, 1.5, 2.2, 3.2, 4.5)

                def seg_ok(seg, strict):
                    return self._plane_clear(net, seg, "pwr") and seg_clear_of_neighbors(seg, strict)

                def search(strict):
                    for extra in extras:
                        for ux, uy in heading:
                            d = exit_dist(ux, uy) + margin + extra
                            vx2, vy2 = x + ux * d, y + uy * d
                            via = Copper("via", net, (0, 1), x=vx2, y=vy2, w=D.VIA_D,
                                        h=D.VIA_D, drill=D.VIA_DRILL)
                            if not (self._plane_clear(net, via, "via") and via_clear_of_neighbors(via)):
                                continue
                            straight = Copper("seg", net, (L,), x=x, y=y, x2=vx2, y2=vy2, w=width_of(net))
                            if seg_ok(straight, True):
                                return (straight, via, None, False)
                            # The straight stub clips a neighbour -- try
                            # routing it as an L instead: out along the
                            # pad's own row/column first (where no other
                            # pad of this footprint sits, by construction),
                            # then turn 90 deg onto the via.  A straight
                            # diagonal between two different rows
                            # necessarily crosses whatever sits between them;
                            # going via the pad's own row never does
                            # (confirmed root cause of a dedicated via
                            # clipping its neighbour by ~0.2mm).
                            mx, my = (vx2, y) if puy == 0 else (x, vy2)
                            if (mx, my) != (x, y):
                                seg1 = Copper("seg", net, (L,), x=x, y=y, x2=mx, y2=my, w=width_of(net))
                                seg2 = Copper("seg", net, (L,), x=mx, y=my, x2=vx2, y2=vy2, w=width_of(net))
                                if seg_ok(seg1, True) and seg_ok(seg2, True):
                                    return (seg1, via, seg2, False)
                            if not strict and seg_ok(straight, False):
                                return (straight, via, None, True)
                    return None

                # Pass 1: respect the neighbour-pad DFM check in full,
                # searching further out than the short-stub common case ever
                # needs.  Skipped entirely in DRAFT_MODE.  Pass 2 relaxes
                # only the same-net half of that check (a same-net pad
                # overlap is a DFM risk worth a fallback warning, never a
                # short) and only runs if pass 1 truly has no solution --
                # the via-clear-of-every-neighbour and different-net-stub
                # requirements are never relaxed, in either pass.  (A third
                # pass that dropped _plane_clear's other-net check entirely,
                # keeping only the hole-to-hole test, was tried here and
                # made things worse -- it landed a via 0.047mm from
                # different-net copper, a real clearance error, not just a
                # DFM warning.  Reverted: when pass 2 has no solution within
                # budget, this pocket is genuinely blocked by other-net
                # copper in every direction, and the single-shot fallback
                # below is the safer choice.)
                # Try sharing an existing nearby via before searching for a
                # dedicated one at all -- cheapest option, tried for every
                # pad, not just ones that end up stuck (see share_via above).
                found = share_via()
                if not found:
                    found = not draft and search(True)
                    if not found:
                        found = search(False)
                if not found:
                    # share_via() already covers "an existing via happens to
                    # be close enough" -- if we're still here, the nearest
                    # same-net via is either further than SHARE_R or blocked
                    # by other copper, and passes 1/2 just proved there's no
                    # clear spot anywhere in `heading`/`extras` either.
                    # Nothing left but the single-shot placement, same as
                    # ever: safe in practice (verified against DRC each
                    # build) but not guaranteed DFM-clean.
                    d0 = exit_dist(pux, puy) + margin
                    vx2, vy2 = x + pux * d0, y + puy * d0
                    via = Copper("via", net, (0, 1), x=vx2, y=vy2, w=D.VIA_D,
                                h=D.VIA_D, drill=D.VIA_DRILL)
                    seg = Copper("seg", net, (L,), x=x, y=y, x2=vx2, y2=vy2, w=width_of(net))
                    found = (seg, via, None, False)
                    self.log("plane_connect: no clear spot for %s %s.%s, placed anyway at (%.2f,%.2f)"
                             % (net, p.ref, pd.num, vx2, vy2))
                elif found[1] is None:
                    self.log("plane_connect: %s %s.%s shares an existing via instead of a new one"
                             % (net, p.ref, pd.num))
                elif found[3]:
                    self.log("plane_connect: %s %s.%s needed pass 2 (stub may graze a same-net "
                             "neighbour pad)" % (net, p.ref, pd.num))
                seg, via, extra, _ = found
                self._add(seg, soft=False)
                self.plane_items.append(seg)
                if extra is not None:
                    self._add(extra, soft=False)
                    self.plane_items.append(extra)
                if via is not None:
                    self._add(via, soft=False)
                    self.plane_items.append(via)

    # -- fixed pre-routed copper (power rails) --------------------------------
    def add_prewire(self, net, layer, x1, y1, x2, y2, w):
        c = Copper("seg", net, (layer,), x=x1, y=y1, x2=x2, y2=y2, w=w)
        self._add(c, soft=False)
        self.prewires.append(c)

    def _prewire_tree(self, net, pads):
        cells, done = [], set()
        g = self.g
        for c in self.prewires:
            if c.net != net:
                continue
            L = c.layers[0]
            n = int(max(abs(c.x2 - c.x), abs(c.y2 - c.y)) / G) + 1
            for k in range(n + 1):
                t = k / n
                x, y = c.x + t * (c.x2 - c.x), c.y + t * (c.y2 - c.y)
                i, j = g.to_ij(x, y)
                gx, gy = g.to_xy(i, j)
                tol = 0.02 + (c.w / 2 - 0.1)
                if abs(gx - x) < tol and abs(gy - y) < tol:
                    cells.append((i, j, L))
            for k, p in enumerate(pads):
                if L in p.layers and copper_dist(c, p) <= 0:
                    done.add(k)
        return cells, done

    # -- pad terminals --------------------------------------------------------
    def pad_cells(self, c, hard_bl):
        g = self.g
        cells = []
        hw, hh = c.w / 2, c.h / 2
        i0, j0 = g.to_ij(c.x - hw, c.y - hh)
        i1, j1 = g.to_ij(c.x + hw, c.y + hh)
        for j in range(max(j0 - 1, 0), min(j1 + 2, g.ny)):
            for i in range(max(i0 - 1, 0), min(i1 + 2, g.nx)):
                x, y = g.to_xy(i, j)
                if c.shape == "circle":
                    inside = math.hypot(x - c.x, y - c.y) <= c.w / 2 - 0.05
                else:
                    inside = abs(x - c.x) <= hw - 0.02 and abs(y - c.y) <= hh - 0.02
                if inside:
                    for L in c.layers:
                        if not hard_bl[L][j, i]:
                            cells.append((i, j, L))
        return cells

    # -- A* ---------------------------------------------------------------
    def astar(self, sources, targets, bl, pen, viablk, vpen, lcost,
              window=None, limit=2_000_000, hw=1.2):
        g = self.g
        nx, ny = g.nx, g.ny
        if not targets:
            return None
        if window is None:
            wi0, wj0, wi1, wj1 = 0, 0, nx - 1, ny - 1
        else:
            wi0, wj0, wi1, wj1 = window
        tx = [t[0] for t in targets]
        ty = [t[1] for t in targets]
        bx0, bx1, by0, by1 = min(tx), max(tx), min(ty), max(ty)
        NL = nx * ny
        tflat = set((L * NL + j * nx + i) for (i, j, L) in targets)
        best, prev, heap = {}, {}, []
        push, pop = heapq.heappush, heapq.heappop

        def h(i, j):
            dx = 0 if bx0 <= i <= bx1 else min(abs(i - bx0), abs(i - bx1))
            dy = 0 if by0 <= j <= by1 else min(abs(j - by0), abs(j - by1))
            return hw * (max(dx, dy) + 0.414 * min(dx, dy))

        for (i, j, L) in sources:
            st = ((L * NL + j * nx + i) << 4) | 8
            best[st] = 0.0
            push(heap, (h(i, j), 0.0, st))
        steps = [(k, di, dj, 1.41421 if di and dj else 1.0)
                 for k, (di, dj) in enumerate(DIRS)]
        n = 0
        while heap:
            f, cst, st = pop(heap)
            if best.get(st, 1e18) < cst:
                continue
            cell = st >> 4
            d = st & 15
            if cell in tflat:
                path = []
                while True:
                    c = st >> 4
                    L = c // NL
                    r = c - L * NL
                    path.append((r % nx, r // nx, L))
                    if st not in prev:
                        break
                    st = prev[st]
                path.reverse()
                return path
            n += 1
            if n > limit:
                return None
            L = cell // NL
            r = cell - L * NL
            j, i = divmod(r, nx)
            blL, penL, lc = bl[L], pen[L], lcost[L]
            base = L * NL
            for k, di, dj, step in steps:
                ni, nj = i + di, j + dj
                if ni < wi0 or nj < wj0 or ni > wi1 or nj > wj1:
                    continue
                nr = nj * nx + ni
                if blL[nr]:
                    continue
                if di and dj and blL[j * nx + ni] and blL[nj * nx + i]:
                    continue
                if L == 0:
                    if dj and not di:
                        step += OFFDIR_COST
                elif di and not dj:
                    step += OFFDIR_COST
                if d != 8 and d != k:
                    t = (k - d) % 8
                    t = t if t <= 4 else 8 - t
                    step += BEND_COST * (3.0 if t >= 2 else 1.0)
                nc = cst + step + lc + 0.25 * penL[nr]
                ns = ((base + nr) << 4) | k
                if nc < best.get(ns, 1e18):
                    best[ns] = nc
                    prev[ns] = st
                    push(heap, (nc + h(ni, nj), nc, ns))
            if not viablk[r]:
                ns = (((1 - L) * NL + r) << 4) | 8
                nc = cst + VIA_COST + 0.25 * vpen[r]
                if nc < best.get(ns, 1e18):
                    best[ns] = nc
                    prev[ns] = st
                    push(heap, (nc + h(i, j), nc, ns))
        return None

    # -- routing a net ---------------------------------------------------------
    def route_net(self, net, pf):
        wc = "pwr" if net in D.POWER_NETS else "sig"
        w = width_of(net)
        hard = self._others(net, wc, False) > 0
        hard |= self.edge[wc][None]
        soft = self._others(net, wc, True)
        hv = (self._others(net, "via", False) > 0)
        sv = self._others(net, "via", True)
        viablk = (hv[0] | hv[1] | self.novia | self.edge["via"]).tobytes()
        penf = np.clip(4.0 * (pf * soft + self.hist * (soft > 0) + 0.25 * self.hist), 0, 250)
        pen = (penf[0].astype(np.uint8).tobytes(), penf[1].astype(np.uint8).tobytes())
        vp = np.clip(4.0 * (pf * (sv[0] + sv[1]) + self.hist[0] + self.hist[1]), 0, 250)
        vpen = vp.astype(np.uint8).tobytes()
        bl = (hard[0].tobytes(), hard[1].tobytes())
        lcost = (1.5, 0.0) if net in D.POWER_NETS else (0.0, 0.0)

        pads = [c for c in self.pads if c.net == net]
        term = [self.pad_cells(c, hard) for c in pads]
        for c, t in zip(pads, term):
            if not t:
                return False, "pad %s.%s has no free cell" % (c.ref, c.num)
        pre_cells, pre_done = self._prewire_tree(net, pads)
        if pre_cells:
            tree, done = list(pre_cells), set(pre_done)
            for k in pre_done:
                tree.extend(term[k])
        else:
            cx = sum(c.x for c in pads) / len(pads)
            cy = sum(c.y for c in pads) / len(pads)
            k0 = min(range(len(pads)), key=lambda k: (pads[k].x - cx) ** 2 + (pads[k].y - cy) ** 2)
            tree, done = list(term[k0]), {k0}
        new_paths = []
        while len(done) < len(pads):
            tmap = {}
            for k in range(len(pads)):
                if k not in done:
                    for t in term[k]:
                        tmap.setdefault(t, k)
            pts = tree + list(tmap.keys())
            m = int(5.0 / G)
            win = (max(0, min(p[0] for p in pts) - m), max(0, min(p[1] for p in pts) - m),
                   min(self.g.nx - 1, max(p[0] for p in pts) + m),
                   min(self.g.ny - 1, max(p[1] for p in pts) + m))
            path = self.astar(tree, list(tmap.keys()), bl, pen, viablk, vpen, lcost,
                              window=win, limit=500_000)
            if path is None:
                path = self.astar(tree, list(tmap.keys()), bl, pen, viablk, vpen,
                                  lcost, limit=2_500_000)
            if path is None:
                for p in new_paths:
                    self._uncommit(net, p)
                return False, "no path"
            k = tmap[path[-1]]
            done.add(k)
            new_paths.append(path)
            self._commit(net, path, w)
            tree.extend(path)
            tree.extend(term[k])
        self.paths[net] = new_paths
        return True, ""

    def _items(self, net, path, w, junction=()):
        g = self.g
        items, run = [], [path[0]]

        def flush(run):
            if len(run) < 2:
                return []
            pts = [run[0]]
            for k in range(1, len(run) - 1):
                a, b, c = run[k - 1], run[k], run[k + 1]
                if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]) or b in junction:
                    pts.append(b)
            pts.append(run[-1])
            L = run[0][2]
            out = []
            for a, b in zip(pts, pts[1:]):
                x1, y1 = g.to_xy(a[0], a[1])
                x2, y2 = g.to_xy(b[0], b[1])
                out.append(Copper("seg", net, (L,), x=x1, y=y1, x2=x2, y2=y2, w=w))
            return out

        for a, b in zip(path, path[1:]):
            if b[2] != a[2]:
                items += flush(run)
                x, y = g.to_xy(a[0], a[1])
                items.append(Copper("via", net, (0, 1), x=x, y=y, w=D.VIA_D,
                                    h=D.VIA_D, drill=D.VIA_DRILL))
                run = [b]
            else:
                run.append(b)
        items += flush(run)
        return items

    def _commit(self, net, path, w):
        for it in self._items(net, path, w):
            rec = self._add(it, soft=True)
            rec[0].ref = id(path)

    def _uncommit(self, net, path):
        for rec in list(self.objs_by_net.get(net, [])):
            if rec[2] and rec[0].ref == id(path):
                self._remove(rec)

    def rip(self, net):
        for rec in list(self.objs_by_net.get(net, [])):
            if rec[2]:
                self._remove(rec)
        self.paths[net] = []

    # -- conflicts ---------------------------------------------------------------
    def conflicts(self):
        """nets whose routed copper violates clearance to another net's
        routed copper; also bumps history cost there."""
        bad = set()
        for net, paths in self.paths.items():
            if not paths:
                continue
            wc = "pwr" if net in D.POWER_NETS else "sig"
            soft = self._others(net, wc, True)
            sv = self._others(net, "via", True)
            for p in paths:
                for a, b in zip(p, p[1:] + [None]):
                    i, j, L = a
                    hit = soft[L][j, i] > 0
                    if b is not None and b[2] != L:      # via here
                        hit = hit or sv[0][j, i] > 0 or sv[1][j, i] > 0
                    if hit:
                        bad.add(net)
                        self.hist[L, j, i] += 1.0
        return bad

    def run(self, order, iters=40, pf0=1.0, pf_mult=1.4):
        pf = pf0
        failed = set()
        todo = list(order)
        for it in range(iters):
            t0 = time.time()
            failed = set()
            for net in todo:
                self.rip(net)
                ok, why = self.route_net(net, pf)
                if not ok:
                    failed.add(net)
            bad = self.conflicts()
            self.log("iter %2d  pf=%.1f  rerouted=%d  overlaps=%d  unroutable=%d  (%.0fs) %s"
                     % (it, pf, len(todo), len(bad), len(failed), time.time() - t0,
                        sorted(bad | failed) if len(bad | failed) < 12 else ""))
            if not bad and not failed:
                return set()
            todo = [n for n in order if n in bad or n in failed]
            pf *= pf_mult
        return self.conflicts() | failed

    # -- output ---------------------------------------------------------------
    def items(self):
        segs, vias = [], []
        for net, paths in self.paths.items():
            w = width_of(net)
            junction = set()
            for p in paths:
                junction.add(p[0])
                junction.add(p[-1])
            for p in paths:
                for it in self._items(net, p, w, junction):
                    (vias if it.kind == "via" else segs).append(it)
        segs.extend(self.prewires)
        for it in self.plane_items:
            (vias if it.kind == "via" else segs).append(it)
        return segs, vias
