"""Placed part: footprint + position + pin -> net map (shared by all boards)."""
import math


class Part:
    def __init__(self, ref, value, fp, x, y, rot, sym, pins,
                 mpn="", descr="", side="F"):
        self.ref, self.value, self.fp = ref, value, fp
        self.x, self.y, self.rot = x, y, rot
        self.sym = sym          # schematic symbol key
        self.pins = pins        # pin number(str) -> net name (None = NC)
        self.mpn, self.descr = mpn, descr
        self.side = side        # "F" (top, default) or "B" (back-mounted SMD)

    def pad_xy(self, pad):
        a = math.radians(self.rot)
        c, s = math.cos(a), math.sin(a)
        px = -pad.x if self.side == "B" else pad.x
        return (self.x + px * c + pad.y * s,
                self.y - px * s + pad.y * c)


def P(n):
    return {str(k): v for k, v in n.items()}
