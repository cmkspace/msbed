"""ODE state packing/unpacking and SimulationResult container.

State vector layout — **Cell-major (Layout B)** (DD-013, Step 5.1 design choice):

    y[5·i + 0] = C_h2o[i]   (mol/m³)
    y[5·i + 1] = q_h2o[i]   (mol/kg adsorbent)
    y[5·i + 2] = C_co2[i]   (mol/m³)
    y[5·i + 3] = q_co2[i]   (mol/kg adsorbent)
    y[5·i + 4] = T[i]       (K)

for cell index i ∈ [0, N-1]. Cell ordering is from inlet (i=0) to outlet (i=N-1).

Why cell-major: the Jacobian's strongest couplings are within a cell (5×5 LDF +
energy block) and between adjacent cells (advection + conduction). Cell-major
keeps the 5×5 cell-internal block contiguous in memory, which makes the
sparsity pattern in `jacobian.py` block-tridiagonal and friendly to BDF
linear-solve performance.

Use `pack_state` / `unpack_state` for cross-layout safety. For setting up
test states, use `var_slice(var, n_grid)` rather than hard-coding strides:

    y[var_slice("q_h2o", n)] = 0.5     # set q_h2o everywhere

This way future layout changes touch only this module.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import OperatingConditions
from .grid import Grid

STATE_VARS: tuple[str, ...] = ("C_h2o", "q_h2o", "C_co2", "q_co2", "T")
N_VARS = len(STATE_VARS)
_VAR_INDEX = {name: i for i, name in enumerate(STATE_VARS)}


def state_size(n_grid: int) -> int:
    """Total ODE state vector length for a given grid size."""
    if n_grid < 1:
        raise ValueError(f"n_grid must be >= 1, got {n_grid}")
    return N_VARS * n_grid


def var_slice(var: str, n_grid: int | None = None) -> slice:
    """Return a `slice` selecting all cell entries of `var` from a state vector.

    Layout B: y[var_offset::N_VARS] selects every 5th element starting at the
    variable's offset within a cell, which yields the per-cell values in
    cell-index order.

    The `n_grid` parameter is unused for Layout B (kept for API stability if
    we ever switch back to a variable-major layout).

    Args:
        var: One of `STATE_VARS`.
        n_grid: Total grid points (kept for API stability; ignored here).

    Returns:
        `slice(offset, None, N_VARS)` — apply directly to a 1-D state vector.
    """
    _ = n_grid  # accepted for API stability across layouts
    if var not in _VAR_INDEX:
        raise ValueError(f"unknown var {var!r}; must be one of {STATE_VARS}")
    offset = _VAR_INDEX[var]
    return slice(offset, None, N_VARS)


def cell_block_slice(cell_index: int) -> slice:
    """Return a `slice` selecting the 5 cell-internal variables for cell `i`.

    Layout B: cell `i` occupies positions [5i, 5i+5) of the state vector.
    """
    if cell_index < 0:
        raise ValueError(f"cell_index must be >= 0, got {cell_index}")
    start = N_VARS * cell_index
    return slice(start, start + N_VARS)


def pack_state(
    C_h2o: np.ndarray,
    q_h2o: np.ndarray,
    C_co2: np.ndarray,
    q_co2: np.ndarray,
    T: np.ndarray,
) -> np.ndarray:
    """Pack five per-cell arrays into a 5N state vector (Layout B).

    Args:
        C_h2o, q_h2o, C_co2, q_co2, T: Each shape (N,).

    Returns:
        State vector of shape (5N,) in Layout B (cell-major).

    Raises:
        ValueError: If the input arrays have different lengths.
    """
    arrays = (C_h2o, q_h2o, C_co2, q_co2, T)
    n = arrays[0].size
    for name, a in zip(STATE_VARS, arrays, strict=True):
        if a.size != n:
            raise ValueError(
                f"{name} has length {a.size}; expected {n} (matching first array)"
            )
    y = np.empty(N_VARS * n, dtype=arrays[0].dtype)
    for offset, a in enumerate(arrays):
        y[offset::N_VARS] = a
    return y


def unpack_state(
    y: np.ndarray, n_grid: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Unpack a 5N state vector (Layout B) into (C_h2o, q_h2o, C_co2, q_co2, T).

    The returned arrays are strided views into `y` (no copy). Modify with care.
    """
    expected = N_VARS * n_grid
    if y.size != expected:
        raise ValueError(f"state vector size {y.size} ≠ expected {expected} (5×{n_grid})")
    return (
        y[0::N_VARS],
        y[1::N_VARS],
        y[2::N_VARS],
        y[3::N_VARS],
        y[4::N_VARS],
    )


@dataclass
class SimulationResult:
    """Container for an adsorption_1d solve.

    `y` has shape (5N, n_t). Accessors return per-cell time-series matrices
    of shape (N, n_t) by striding with step `N_VARS`.
    """

    t_s: np.ndarray
    y: np.ndarray
    grid: Grid
    op: OperatingConditions
    success: bool
    message: str

    def __post_init__(self) -> None:
        n_t = self.t_s.size
        expected_rows = N_VARS * self.grid.n_total
        if self.y.shape != (expected_rows, n_t):
            raise ValueError(
                f"y shape {self.y.shape} does not match "
                f"({expected_rows}, {n_t}) = (5N, n_t)"
            )

    def C_h2o(self) -> np.ndarray:
        return self.y[0::N_VARS, :]

    def q_h2o(self) -> np.ndarray:
        return self.y[1::N_VARS, :]

    def C_co2(self) -> np.ndarray:
        return self.y[2::N_VARS, :]

    def q_co2(self) -> np.ndarray:
        return self.y[3::N_VARS, :]

    def T(self) -> np.ndarray:
        return self.y[4::N_VARS, :]

    def outlet_C_h2o(self) -> np.ndarray:
        return self.C_h2o()[-1, :]

    def outlet_C_co2(self) -> np.ndarray:
        return self.C_co2()[-1, :]
