//! Rust port of `router.py`'s `Router.astar` inner loop (Milestone 1 of the
//! Rust router rewrite).  This is a 1:1 algorithmic port -- same state
//! encoding, same cost formula, same heap tie-breaking order -- so it
//! returns the same (or equally optimal) path as the pure-Python version
//! for identical inputs.  Everything else (grid rasterisation, PathFinder's
//! negotiated-congestion outer loop, net ordering, plane_connect) stays in
//! Python; see `common/router_rs.py` for the thin subclass that wires this
//! in.
//!
//! State encoding (matches router.py exactly): a "cell" is
//! `L*NL + j*nx + i` for layer L in {0,1}; a "state" is `(cell << 4) | d`
//! where d is 0..=7 for "arrived here heading in direction DIRS[d]", or 8
//! for "start / just came off a via" (no preferred direction, so no bend
//! penalty applies to the next step).
use pyo3::prelude::*;
use std::cmp::Ordering;
use std::collections::BinaryHeap;
use std::collections::HashSet;

const DIRS: [(i64, i64); 8] = [
    (1, 0),
    (1, 1),
    (0, 1),
    (-1, 1),
    (-1, 0),
    (-1, -1),
    (0, -1),
    (1, -1),
];
const DIAG_STEP: f64 = 1.41421; // matches router.py's literal exactly (not sqrt(2))

#[derive(Copy, Clone, PartialEq)]
struct Entry {
    f: f64,
    cost: f64,
    state: i64,
}
impl Eq for Entry {}
impl Ord for Entry {
    fn cmp(&self, other: &Self) -> Ordering {
        // BinaryHeap is a max-heap; reverse every comparison so the lowest
        // (f, cost, state) tuple pops first, matching Python's heapq on
        // plain tuples (lexicographic, ascending).
        other
            .f
            .partial_cmp(&self.f)
            .unwrap_or(Ordering::Equal)
            .then_with(|| other.cost.partial_cmp(&self.cost).unwrap_or(Ordering::Equal))
            .then_with(|| other.state.cmp(&self.state))
    }
}
impl PartialOrd for Entry {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

#[allow(clippy::too_many_arguments)]
#[pyfunction]
#[pyo3(signature = (sources, targets, bl0, bl1, pen0, pen1, viablk, vpen,
                     lcost0, lcost1, nx, ny, window, limit, hw,
                     via_cost, bend_cost, offdir_cost))]
fn astar(
    sources: Vec<(i64, i64, i64)>,
    targets: Vec<(i64, i64, i64)>,
    bl0: &[u8],
    bl1: &[u8],
    pen0: &[u8],
    pen1: &[u8],
    viablk: &[u8],
    vpen: &[u8],
    lcost0: f64,
    lcost1: f64,
    nx: i64,
    ny: i64,
    window: Option<(i64, i64, i64, i64)>,
    limit: i64,
    hw: f64,
    via_cost: f64,
    bend_cost: f64,
    offdir_cost: f64,
) -> Option<Vec<(i64, i64, i64)>> {
    if targets.is_empty() {
        return None;
    }
    let (wi0, wj0, wi1, wj1) = window.unwrap_or((0, 0, nx - 1, ny - 1));
    let nl: i64 = nx * ny;

    let bx0 = targets.iter().map(|t| t.0).min().unwrap();
    let bx1 = targets.iter().map(|t| t.0).max().unwrap();
    let by0 = targets.iter().map(|t| t.1).min().unwrap();
    let by1 = targets.iter().map(|t| t.1).max().unwrap();

    let h = |i: i64, j: i64| -> f64 {
        let dx = if bx0 <= i && i <= bx1 {
            0
        } else {
            (i - bx0).abs().min((i - bx1).abs())
        };
        let dy = if by0 <= j && j <= by1 {
            0
        } else {
            (j - by0).abs().min((j - by1).abs())
        };
        hw * (dx.max(dy) as f64 + 0.414 * dx.min(dy) as f64)
    };

    let steps: Vec<(i64, i64, i64, f64)> = DIRS
        .iter()
        .enumerate()
        .map(|(k, &(di, dj))| {
            (
                k as i64,
                di,
                dj,
                if di != 0 && dj != 0 { DIAG_STEP } else { 1.0 },
            )
        })
        .collect();

    // Dense state space: 2 layers * NL cells * 16 direction-slots (only
    // 0..=8 used, but the <<4 encoding reserves 16) -- a flat Vec indexed
    // directly by state, no hashing, per the scoping doc's recommendation.
    let total_states = (2 * nl * 16) as usize;
    let mut best: Vec<f64> = vec![f64::INFINITY; total_states];
    let mut prev: Vec<i64> = vec![-1; total_states];

    let tflat: HashSet<i64> = targets.iter().map(|&(i, j, l)| l * nl + j * nx + i).collect();

    let mut heap: BinaryHeap<Entry> = BinaryHeap::new();
    for &(i, j, l) in &sources {
        let cell = l * nl + j * nx + i;
        let st = (cell << 4) | 8;
        best[st as usize] = 0.0;
        heap.push(Entry {
            f: h(i, j),
            cost: 0.0,
            state: st,
        });
    }

    let mut n: i64 = 0;
    while let Some(Entry { cost: cst, state: st, .. }) = heap.pop() {
        if best[st as usize] < cst {
            continue; // stale heap entry, a cheaper path to this state was already found
        }
        let cell = st >> 4;
        let d = st & 15;
        if tflat.contains(&cell) {
            let mut path: Vec<(i64, i64, i64)> = Vec::new();
            let mut cur = st;
            loop {
                let c = cur >> 4;
                let l = c / nl;
                let r = c - l * nl;
                path.push((r % nx, r / nx, l));
                let p = prev[cur as usize];
                if p < 0 {
                    break;
                }
                cur = p;
            }
            path.reverse();
            return Some(path);
        }
        n += 1;
        if n > limit {
            return None;
        }
        let l = cell / nl;
        let r = cell - l * nl;
        let j = r / nx;
        let i = r % nx;
        let (bl_l, pen_l, lc): (&[u8], &[u8], f64) = if l == 0 {
            (bl0, pen0, lcost0)
        } else {
            (bl1, pen1, lcost1)
        };
        let base = l * nl;

        for &(k, di, dj, step0) in &steps {
            let ni = i + di;
            let nj = j + dj;
            if ni < wi0 || nj < wj0 || ni > wi1 || nj > wj1 {
                continue;
            }
            let nr = nj * nx + ni;
            if bl_l[nr as usize] != 0 {
                continue;
            }
            if di != 0 && dj != 0 {
                let a = (j * nx + ni) as usize;
                let b = (nj * nx + i) as usize;
                if bl_l[a] != 0 && bl_l[b] != 0 {
                    continue; // corner-cut through two orthogonally-blocked cells
                }
            }
            let mut step = step0;
            if l == 0 {
                if dj != 0 && di == 0 {
                    step += offdir_cost;
                }
            } else if di != 0 && dj == 0 {
                step += offdir_cost;
            }
            if d != 8 && d != k {
                let t0 = (k - d).rem_euclid(8);
                let t = if t0 <= 4 { t0 } else { 8 - t0 };
                step += bend_cost * (if t >= 2 { 3.0 } else { 1.0 });
            }
            let nc = cst + step + lc + 0.25 * (pen_l[nr as usize] as f64);
            let ns = ((base + nr) << 4) | k;
            if nc < best[ns as usize] {
                best[ns as usize] = nc;
                prev[ns as usize] = st;
                heap.push(Entry {
                    f: nc + h(ni, nj),
                    cost: nc,
                    state: ns,
                });
            }
        }
        if viablk[r as usize] == 0 {
            let ns = (((1 - l) * nl + r) << 4) | 8;
            let nc = cst + via_cost + 0.25 * (vpen[r as usize] as f64);
            if nc < best[ns as usize] {
                best[ns as usize] = nc;
                prev[ns as usize] = st;
                heap.push(Entry {
                    f: nc + h(i, j),
                    cost: nc,
                    state: ns,
                });
            }
        }
    }
    None
}

#[pymodule]
fn _router_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(astar, m)?)?;
    Ok(())
}
