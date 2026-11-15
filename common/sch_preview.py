"""Rough PNG preview of the generated schematic (for checking only;
open the .kicad_sch in KiCad for the real thing)."""
import re
from PIL import Image, ImageDraw, ImageFont

S = 6  # px per mm


def _font(sz):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", sz)
    except OSError:
        try:
            return ImageFont.load_default(size=sz)
        except TypeError:
            return ImageFont.load_default()


def render(sheet, path, w=420, h=297):
    img = Image.new("RGB", (int(w * S), int(h * S)), (255, 255, 250))
    d = ImageDraw.Draw(img)
    ft = _font(int(1.6 * S))
    fs = _font(int(1.3 * S))
    d.rectangle([5 * S, 5 * S, (w - 5) * S, (h - 5) * S], outline=(120, 0, 0), width=2)
    for it in sheet.draw:
        k = it[0]
        if k == "line":
            _, x1, y1, x2, y2 = it
            d.line([x1 * S, y1 * S, x2 * S, y2 * S], fill=(0, 120, 0), width=2)
        elif k == "label":
            _, n, x, y, a = it
            anchor = {0: "lb", 180: "rb", 90: "lb", 270: "lt"}[a]
            if a in (90, 270):
                tw = int(d.textlength(n, font=fs)) + 4
                t = Image.new("RGBA", (tw, int(2 * S)), (0, 0, 0, 0))
                ImageDraw.Draw(t).text((0, 0), n, fill=(0, 0, 160), font=fs)
                t = t.rotate(90 if a == 90 else 90, expand=True)
                if a == 90:
                    img.paste(t, (int(x * S - t.width), int(y * S - t.height)), t)
                else:
                    img.paste(t, (int(x * S - t.width), int(y * S)), t)
            else:
                d.text((x * S, y * S), n, fill=(0, 0, 160), font=fs, anchor=anchor)
        elif k == "nc":
            _, x, y = it
            r = 0.8 * S
            d.line([x * S - r, y * S - r, x * S + r, y * S + r], fill=(0, 0, 200), width=2)
            d.line([x * S - r, y * S + r, x * S + r, y * S - r], fill=(0, 0, 200), width=2)
        elif k == "pwr":
            _, n, x, y, rot = it
            if rot in (90, 270):
                sgn = 1 if ((n == "GND") == (rot == 90)) else -1
                d.line([x * S, y * S, (x + sgn * 2.0) * S, y * S], fill=(150, 0, 0), width=2)
                d.text(((x + sgn * 2.3) * S, y * S), n, fill=(150, 0, 0), font=fs,
                       anchor="lm" if sgn > 0 else "rm")
            elif (n == "GND") == (rot == 0):
                d.polygon([(x * S - 1.27 * S, y * S + 1.27 * S), (x * S + 1.27 * S, y * S + 1.27 * S),
                           (x * S, y * S + 2.54 * S)], outline=(150, 0, 0))
                d.line([x * S, y * S, x * S, y * S + 1.27 * S], fill=(150, 0, 0))
            else:
                d.line([x * S, y * S, x * S, y * S - 2.54 * S], fill=(150, 0, 0), width=2)
                d.text((x * S, y * S - 2.8 * S), n, fill=(150, 0, 0), font=fs, anchor="mb")
        elif k == "text":
            _, t, x, y = it
            d.multiline_text((x * S, y * S), t.replace("\\n", "\n"), fill=(60, 60, 60), font=fs)
        elif k == "sym":
            _, ref, val, sym, x, y = it
            for g in sym.graphics:
                m = re.match(r"\(rectangle \(start (\S+) (\S+)\) \(end (\S+) (\S+)\)", g)
                if m:
                    x0, y0, x1, y1 = map(float, m.groups())
                    d.rectangle([(x + min(x0, x1)) * S, (y - max(y0, y1)) * S,
                                 (x + max(x0, x1)) * S, (y - min(y0, y1)) * S],
                                outline=(130, 0, 0), fill=(255, 255, 200), width=2)
            for num, pname, typ, px, py, ang, hid in sym.pins:
                ex, ey = x + px, y - py
                dx, dy = {0: (1, 0), 180: (-1, 0), 90: (0, -1), 270: (0, 1)}[ang]
                d.line([ex * S, ey * S, (ex + dx * 2.54) * S, (ey + dy * 2.54) * S], fill=(130, 0, 0), width=2)
                if pname != "~":
                    ix, iy = ex + dx * 3.1, ey + dy * 3.1
                    anc = {0: "lm", 180: "rm", 90: "mb", 270: "mt"}[ang]
                    d.text((ix * S, iy * S), pname.replace("~{", "/").replace("}", ""),
                           fill=(0, 100, 100), font=fs, anchor=anc)
            rx, ry = x + sym.ref_at[0], y - sym.ref_at[1]
            d.text((rx * S, ry * S), ref, fill=(0, 0, 0), font=ft, anchor="lb")
            vx, vy = x + sym.val_at[0], y - sym.val_at[1]
            d.text((vx * S, vy * S), val, fill=(0, 0, 0), font=ft, anchor="lt")
    img.save(path)
