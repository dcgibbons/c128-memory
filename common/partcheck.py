"""Check every part number against the JLC/LCSC catalog.

For each part with an LCSC number, the catalog entry must exist and match:
its model must start with the part's MPN (or, for passives without an MPN,
its description must contain the value).  Parts with an MPN but no LCSC
number are looked up by MPN and must have an exact model match.  Low stock
is only a warning, since stock changes daily and ICs are hand-soldered.

Needs network access.  With --offline the check is skipped explicitly; if the
network fails, the check fails as incomplete (keeping any failures found so
far), so an unchecked build never looks like a checked one.
"""
import json
import re
import urllib.request

URL = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"
LOW_STOCK = 50
NOT_IN_CATALOG = ("MOS ",)          # salvage-only parts (e.g. the 8722 MMU)


def _search(keyword):
    req = urllib.request.Request(URL, data=json.dumps({"keyword": keyword, "currentPage": 1,
                                                       "pageSize": 25}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)["data"]["componentPageInfo"]["list"] or []
    return data


def _value_tokens(value):
    """'10k' -> ['10kΩ', '10k'], '100nF' -> ['100nF'], '68' -> ['68Ω']."""
    v = value.strip()
    if re.fullmatch(r"[\d.]+[kKM]?", v):
        return [v + "Ω", v]
    return [v]


def run(parts, offline=False):
    """Returns (ok, lines).  ok is False for mismatches or an incomplete check."""
    if offline:
        return True, ["part check: skipped (--offline)"]
    lines, ok = [], True
    for p in sorted(parts, key=lambda p: p.ref):
        mpn, lcsc = (p.mpn or "").strip(), (getattr(p, "lcsc", "") or "").strip()
        if not lcsc and (not mpn or mpn.startswith(NOT_IN_CATALOG)):
            continue
        try:
            hits = _search(lcsc or mpn)
        except Exception as e:  # network trouble: report, keep earlier failures
            lines.insert(0, "part check: INCOMPLETE (catalog unreachable at %s: %s); "
                            "use --offline to build without it" % (p.ref, e))
            return False, lines
        if lcsc:
            hit = next((h for h in hits if h.get("componentCode") == lcsc), None)
            if not hit:
                ok = False
                lines.append("  FAIL %-4s %s: LCSC number not in the catalog" % (p.ref, lcsc))
                continue
            model, desc = hit.get("componentModelEn", ""), hit.get("describe", "") or ""
            if mpn:
                good = model.upper().startswith(mpn.upper())
                what = "model %s vs MPN %s" % (model, mpn)
            else:
                good = any(t in desc or t in model for t in _value_tokens(p.value))
                what = "%s vs value %s" % (desc[:50], p.value)
            if not good:
                ok = False
                lines.append("  FAIL %-4s %s: %s" % (p.ref, lcsc, what))
                continue
        else:
            hit = next((h for h in hits if h.get("componentModelEn", "").upper() == mpn.upper()), None)
            if not hit:
                ok = False
                lines.append("  FAIL %-4s %s: MPN not found in the catalog" % (p.ref, mpn))
                continue
        stock = hit.get("stockCount", 0)
        if stock < LOW_STOCK:
            lines.append("  warn %-4s %s %s: stock %d" % (p.ref, hit.get("componentCode"),
                                                          hit.get("componentModelEn"), stock))
    lines.insert(0, "part check: %s" % ("all part numbers found" if ok else "PROBLEMS"))
    return ok, lines
