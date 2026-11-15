"""Regression tests for drc.py.  Run from the repo root:
    python3 common/test_drc.py   (or: python3 -m pytest common/test_drc.py)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(os.path.dirname(HERE), "c128dcr", "sram"), HERE]

import drc  # noqa: E402
from geom import Copper  # noqa: E402


def seg(net, x1, y1, x2, y2, w=0.2):
    return Copper("seg", net, (0,), x=x1, y=y1, x2=x2, y2=y2, w=w, h=w)


def test_clearance_across_hash_cell_boundary():
    # two 0.2 mm tracks either side of the x = 2.0 cell boundary, 0.11 mm
    # apart edge to edge (rule 0.15): found by the reviewer, missed before
    a = seg("A", 1.79, 5.0, 1.79, 8.0)          # right edge at 1.89
    b = seg("B", 2.10, 5.0, 2.10, 8.0)          # left edge at 2.00
    errs, _ = drc.run([], [a, b], [])
    assert any(e.startswith("clearance") for e in errs), errs


def test_no_false_clearance_error():
    a = seg("A", 1.70, 5.0, 1.70, 8.0)
    b = seg("B", 2.10, 5.0, 2.10, 8.0)          # 0.2 mm apart: fine
    errs, _ = drc.run([], [a, b], [])
    assert not any(e.startswith("clearance") for e in errs), errs


if __name__ == "__main__":
    test_clearance_across_hash_cell_boundary()
    test_no_false_clearance_error()
    print("drc tests passed")
