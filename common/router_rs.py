"""Optional Rust-accelerated drop-in for router.Router.

This subclasses the pure-Python Router unchanged and overrides only astar(),
the inner grid search loop. Grid rasterisation, plane_connect, route_net,
conflicts, and the PathFinder negotiated-congestion outer loop in run() are
inherited as-is from router.py, and the A* search is a 1:1 algorithmic port
(see router_rs/src/lib.rs for the state encoding).

buildlib.py imports this module when the compiled extension is present and
falls back automatically to pure-Python router.Router otherwise, so a Rust
toolchain is never required to build a board.

To build the optional local accelerator (git-ignored, platform-specific):
    cd common/router_rs
    maturin build --release -i python3 && \\
      cp "$(find target/wheels -name '_router_rs*')" ../
Built with PyO3's abi3-py39 feature so one wheel works with any Python >= 3.9
on the host OS/architecture.
"""
from router import Router as _PyRouter, VIA_COST, BEND_COST, OFFDIR_COST

try:
    from _router_rs import astar as _rust_astar
except ImportError as e:  # pragma: no cover - exercised via buildlib's own fallback
    raise ImportError(
        "router_rs: compiled _router_rs extension not importable (%s). "
        "Build it (see this module's docstring) or import router.Router "
        "directly for the pure-Python fallback." % e) from e


class Router(_PyRouter):
    def astar(self, sources, targets, bl, pen, viablk, vpen, lcost,
              window=None, limit=2_000_000, hw=1.2):
        nx, ny = self.g.nx, self.g.ny
        wi0, wj0, wi1, wj1 = window if window is not None else (0, 0, nx - 1, ny - 1)
        path = _rust_astar(sources, targets, bl[0], bl[1], pen[0], pen[1],
                            viablk, vpen, lcost[0], lcost[1], nx, ny,
                            (wi0, wj0, wi1, wj1), limit, hw,
                            VIA_COST, BEND_COST, OFFDIR_COST)
        return path
