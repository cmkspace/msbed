"""ODE state packing/unpacking and SimulationResult container.

State vector layout (5N total entries):

    y[0       : N    ] = C_h2o   (mol/m³)
    y[N       : 2N   ] = q_h2o   (mol/kg adsorbent)
    y[2N      : 3N   ] = C_co2   (mol/m³)
    y[3N      : 4N   ] = q_co2   (mol/kg adsorbent)
    y[4N      : 5N   ] = T       (K)

Convention: cell-centered values, ordered from inlet (index 0) to outlet (N-1).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import OperatingConditions
from .grid import Grid

STATE_VARS: tuple[str, ...] = ("C_h2o", "q_h2o", "C_co2", "q_co2", "T")
N_VARS = len(STATE_VARS)


def state_size(n_grid: int) -> int:
    """Total ODE state vector length for a given grid size."""
    if n_grid < 1:
        raise ValueError(f"n_grid must be >= 1, got {n_grid}")
    return N_VARS * n_grid


def pack_state(
    C_h2o: np.ndarray,
    q_h2o: np.ndarray,
    C_co2: np.ndarray,
    q_co2: np.ndarray,
    T: np.ndarray,
) -> np.ndarray:
    """Pack five per-cell arrays into a single 5N ODE state vector.

    Args:
        C_h2o, q_h2o, C_co2, q_co2, T: Each shape (N,).

    Returns:
        State vector of shape (5N,) in the order C_h2o, q_h2o, C_co2, q_co2, T.

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
    return np.concatenate(arrays)


def unpack_state(
    y: np.ndarray, n_grid: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Unpack a 5N state vector into (C_h2o, q_h2o, C_co2, q_co2, T)."""
    expected = N_VARS * n_grid
    if y.size != expected:
        raise ValueError(f"state vector size {y.size} ≠ expected {expected} (5×{n_grid})")
    return (
        y[0 * n_grid : 1 * n_grid],
        y[1 * n_grid : 2 * n_grid],
        y[2 * n_grid : 3 * n_grid],
        y[3 * n_grid : 4 * n_grid],
        y[4 * n_grid : 5 * n_grid],
    )


@dataclass
class SimulationResult:
    """Container for an adsorption_1d solve.

    Stores the time array and the full (5N, n_t) state-versus-time matrix
    along with the grid and operating conditions used. Convenience accessors
    return per-component (N, n_t) views.
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
        N = self.grid.n_total
        return self.y[0 * N : 1 * N, :]

    def q_h2o(self) -> np.ndarray:
        N = self.grid.n_total
        return self.y[1 * N : 2 * N, :]

    def C_co2(self) -> np.ndarray:
        N = self.grid.n_total
        return self.y[2 * N : 3 * N, :]

    def q_co2(self) -> np.ndarray:
        N = self.grid.n_total
        return self.y[3 * N : 4 * N, :]

    def T(self) -> np.ndarray:
        N = self.grid.n_total
        return self.y[4 * N : 5 * N, :]

    def outlet_C_h2o(self) -> np.ndarray:
        return self.C_h2o()[-1, :]

    def outlet_C_co2(self) -> np.ndarray:
        return self.C_co2()[-1, :]
