#!/usr/bin/env python3
"""Check riser.jed against the intended remap, independently of GALasm.

Simulates the GAL16V8 fuse array from the JEDEC file (complex mode, fuse map
as documented in GALasm's GALASM-SPEC.md, sections 6-7 and 9) for all 512
combinations of the nine inputs, and compares the two outputs with a plain
statement of what the second MMU must see.

    python3 check_gal.py [riser.jed]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

PINS = {"A0": 1, "A1": 2, "A2": 3, "A3": 4, "RW": 5, "D0": 6, "D1": 7, "D6": 8, "D7": 9}
OUTS = {"D0X": 15, "D6X": 16}
# GAL16V8 complex mode: array column of each pin's true signal (+1 = complement)
COLUMN = {1: 2, 2: 0, 3: 4, 4: 8, 5: 12, 6: 16, 7: 20, 8: 24, 9: 28, 11: 30,
          13: 26, 14: 22, 15: 18, 16: 14, 17: 10, 18: 6}
NCOL, NFUSE = 32, 2194


def load_jed(path):
    text = open(path).read()
    default = 0
    for f in text.split("*"):
        f = f.strip()
        if f.startswith("F"):
            default = int(f[1:])
    fuses = [default] * NFUSE
    for f in text.split("*"):
        f = f.strip()
        if f.startswith("L"):
            addr, bits = f[1:].split(None, 1)
            bits = "".join(bits.split())
            for k, b in enumerate(bits):
                fuses[int(addr) + k] = int(b)
    return fuses


def simulate(fuses, inputs):
    """inputs: name -> 0/1.  Returns name -> 0/1/None (None = output off)."""
    assert fuses[2192] == 1 and fuses[2193] == 1, "expected complex mode (SYN=1, AC0=1)"
    level = {}
    for name, pin in PINS.items():
        level[COLUMN[pin]] = inputs[name]
        level[COLUMN[pin] + 1] = 1 - inputs[name]

    def row(r):
        bits = fuses[r * NCOL:(r + 1) * NCOL]
        return all(level.get(c, 0) for c, b in enumerate(bits) if b == 0)

    out = {}
    for name, pin in OUTS.items():
        k = 19 - pin
        assert fuses[2120 + k] == 1, "%s is not a tristate output (AC1)" % name
        base = k * 8
        oe = row(base)
        s = any(row(base + i) for i in range(1, 8))
        value = int(s) if fuses[2048 + k] == 1 else int(not s)
        out[name] = value if oe else None
    return out


def intended(inputs):
    """What the second MMU must receive (None = not driven)."""
    nib = inputs["A0"] | inputs["A1"] << 1 | inputs["A2"] << 2 | inputs["A3"] << 3
    if inputs["RW"]:                       # read cycle: outputs off
        return {"D0X": None, "D6X": None}
    d6x = inputs["D6"] if nib in (5, 7, 9) else inputs["D7"]       # MCR, P0L, P1L
    d0x = inputs["D1"] if nib in (8, 10) else inputs["D0"]         # P0H, P1H
    return {"D0X": d0x, "D6X": d6x}


def check(path=os.path.join(HERE, "riser.jed")):
    fuses = load_jed(path)
    names = list(PINS)
    bad = []
    for v in range(1 << len(names)):
        inputs = {n: (v >> i) & 1 for i, n in enumerate(names)}
        got, want = simulate(fuses, inputs), intended(inputs)
        if got != want:
            bad.append((inputs, got, want))
    return bad


if __name__ == "__main__":
    bad = check(*sys.argv[1:])
    for inputs, got, want in bad[:10]:
        print("MISMATCH", inputs, "got", got, "want", want)
    print("GAL check: %s (512 input combinations)" % ("OK" if not bad else "%d mismatches" % len(bad)))
    sys.exit(1 if bad else 0)
