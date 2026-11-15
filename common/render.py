"""PNG preview of the PCB (top + bottom copper) using Pillow."""
from PIL import Image, ImageDraw, ImageFont
from design import BOARD
from geom import part_pads

S = 24  # px per mm
COL = {0: (200, 50, 50), 1: (60, 110, 220)}


def _font(sz):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", sz)
    except OSError:
        try:
            return ImageFont.load_default(size=sz)
        except TypeError:
            return ImageFont.load_default()


def render(parts, segs, vias, path, layers=(1, 0), labels=True, title=""):
    x0, y0, x1, y1 = BOARD
    m = 2
    W, H = int((x1 - x0 + 2 * m) * S), int((y1 - y0 + 2 * m) * S) + 30
    img = Image.new("RGB", (W, H), (20, 32, 24))
    d = ImageDraw.Draw(img, "RGBA")

    def X(x):
        return (x - x0 + m) * S

    def Y(y):
        return (y - y0 + m) * S + 30

    d.rectangle([X(x0), Y(y0), X(x1), Y(y1)], fill=(18, 70, 40),
                outline=(230, 230, 80), width=2)
    for L in layers:
        c = COL[L] + (170,)
        for s in segs:
            if s.layers[0] != L:
                continue
            d.line([X(s.x), Y(s.y), X(s.x2), Y(s.y2)], fill=c,
                   width=max(1, int(s.w * S)))
            r = s.w * S / 2
            for (px, py) in ((s.x, s.y), (s.x2, s.y2)):
                d.ellipse([X(px) - r, Y(py) - r, X(px) + r, Y(py) + r], fill=c)
    f = _font(int(0.9 * S))
    fs = _font(int(0.45 * S))
    for p in parts:
        for c in part_pads(p):
            col = (220, 180, 60, 255) if not c.smd else (230, 120, 90, 255)
            if 0 not in layers and c.smd:
                continue
            bx = [X(c.x - c.w / 2), Y(c.y - c.h / 2), X(c.x + c.w / 2), Y(c.y + c.h / 2)]
            if c.shape == "circle":
                d.ellipse(bx, fill=col)
            else:
                d.rectangle(bx, fill=col)
            if c.drill:
                r = c.drill / 2 * S
                d.ellipse([X(c.x) - r, Y(c.y) - r, X(c.x) + r, Y(c.y) + r], fill=(10, 10, 10))
            if labels and c.net and c.w * S > 14:
                d.text((X(c.x), Y(c.y)), c.net.replace("_", ""), fill=(0, 0, 0),
                       font=fs, anchor="mm")
        if labels:
            d.text((X(p.x) + (0 if p.ref[0] not in "P" else 3.8 * S),
                    Y(p.y) + (0 if p.ref[0] not in "P" else -3.3 * S)),
                   p.ref, fill=(255, 255, 255, 200), font=f, anchor="mm")
    for v in vias:
        r = v.w / 2 * S
        d.ellipse([X(v.x) - r, Y(v.y) - r, X(v.x) + r, Y(v.y) + r], fill=(200, 200, 200))
        r = v.drill / 2 * S
        d.ellipse([X(v.x) - r, Y(v.y) - r, X(v.x) + r, Y(v.y) + r], fill=(10, 10, 10))
    d.text((10, 6), title, fill=(255, 255, 255), font=_font(20))
    img.save(path)
