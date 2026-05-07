"""Jacobian sparsity pattern for the 1D adsorption PDE (Step 5.1).

State vector layout — Cell-major (Layout B), see `state.py`:

    y[5·i + k] = state variable k of cell i, where k ∈ {0,1,2,3,4}
                = (C_h2o, q_h2o, C_co2, q_co2, T)[k]

Sparsity pattern of J = ∂rhs/∂y has block-tridiagonal structure with three
distinct contributions:

1. **Cell-internal (main diagonal, 5×5 per cell — conservatively dense)**:
   All five variables of a cell may couple to all five within the same cell:
     - C_h2o ← C_h2o (advection self), q_h2o (LDF source), T (isotherm T-dep)
     - q_h2o ← C_h2o (q*), q_h2o (LDF self), T (q* T-dep)
     - C_co2 ← C_co2, q_co2, T (analogous)
     - q_co2 ← C_co2, q_co2, T
     - T     ← all five (energy: S_ads from dq's, ρ_g(T), wall, advection self)
   We use the **conservative 5×5 dense block** to avoid losing structure when
   the adsorbs_in matrix is widened (Decision 2A → 2C).

2. **Sub-diagonal (cell i ← cell i-1)** and **super-diagonal (cell i ← i+1)**:
   Only spatially-transported variables couple across cells:
     - C_h2o[i] ← C_h2o[i±1]           (advection / dispersion)
     - C_co2[i] ← C_co2[i±1]
     - T[i]     ← T[i±1]                (advection / conduction)
     - q_h2o, q_co2: NO spatial coupling (LDF is purely local).
   So 3 entries per off-diagonal block.

Boundary cells (i=0, i=N-1) lose one-sided neighbor blocks but retain the
diagonal cell-internal coupling.

Expected nnz for N=100 (cell-major Layout B):
    main diagonal:   25 · 100 = 2500
    sub-diagonal:    3  · 99  =  297
    super-diagonal:  3  · 99  =  297
    total            =        = 3094       (≈98.4% sparsity)
"""

from __future__ import annotations

import numpy as np
from scipy.sparse import csr_matrix, lil_matrix

from .state import N_VARS

# Cell-internal variable indices (within a 5-element cell block, Layout B)
_VAR_C_H2O = 0
_VAR_Q_H2O = 1
_VAR_C_CO2 = 2
_VAR_Q_CO2 = 3
_VAR_T = 4

# Variables that participate in spatial (inter-cell) coupling.
# q_h2o (1) and q_co2 (3) are LDF-local; never coupled across cells.
SPATIAL_VARS: tuple[int, ...] = (_VAR_C_H2O, _VAR_C_CO2, _VAR_T)


def jacobian_sparsity_pattern(n_grid: int) -> csr_matrix:
    """Build the 5N × 5N Jacobian sparsity pattern for our 1D adsorption PDE.

    State layout — Cell-major (Layout B), see `state.py`:
        y[5i + 0] = C_h2o[i]
        y[5i + 1] = q_h2o[i]
        y[5i + 2] = C_co2[i]
        y[5i + 3] = q_co2[i]
        y[5i + 4] = T[i]

    Closed-form non-zero count:
        nnz = 5²·N + 2·3·(N-1) = 25N + 6(N-1)

      - **Cell-internal block (5×5 dense per cell): 25N entries.** All five
        variables couple within the same cell via:
          * LDF (q ↔ C, q ↔ q itself)
          * Adsorption isotherm equilibrium (q* depends on T, C)
          * Heat of adsorption (T ↔ q via dq/dt source)
      - **Spatial coupling (3 entries per off-diagonal pair):** ONLY C_h2o,
        C_co2, T have spatial derivatives (advection ∂C/∂z, dispersion
        ∂²C/∂z², thermal conduction). q_h2o and q_co2 are LOCAL — the LDF
        equation `dq/dt = k(q* − q)` has no spatial term, so q-rows in the
        Jacobian have no inter-cell entries.

    For N=100: nnz = 2500 + 594 = **3,094** (sparsity 98.76%).

    The initial design estimate (3,900) was a 26% over-count produced by
    assuming all 5 variables had inter-cell spatial coupling. Verified by:
      1. Closed-form formula (above).
      2. FD numerical Jacobian (`test_pattern_covers_numerical_jacobian`):
         every numerical non-zero entry lies inside the declared pattern.

    See DD-013 for the estimate-vs-measurement calibration.

    Args:
        n_grid: Number of finite-volume cells N. Must be ≥ 2.

    Returns:
        scipy.sparse.csr_matrix of shape (5N, 5N) with float 1.0 entries.

    Raises:
        ValueError: If n_grid < 2.
    """
    if n_grid < 2:
        raise ValueError(f"n_grid must be ≥ 2, got {n_grid}")

    n_state = N_VARS * n_grid
    pattern = lil_matrix((n_state, n_state), dtype=np.float64)

    for i in range(n_grid):
        row_block_start = N_VARS * i

        # ---- Main diagonal: 5×5 dense cell-internal block ----
        for r in range(N_VARS):
            for c in range(N_VARS):
                pattern[row_block_start + r, row_block_start + c] = 1.0

        # ---- Sub-diagonal: cell i ← cell i-1 (3 entries: C_h2o, C_co2, T) ----
        if i > 0:
            prev_block_start = N_VARS * (i - 1)
            for v in SPATIAL_VARS:
                pattern[row_block_start + v, prev_block_start + v] = 1.0

        # ---- Super-diagonal: cell i ← cell i+1 (same 3 spatial vars) ----
        if i < n_grid - 1:
            next_block_start = N_VARS * (i + 1)
            for v in SPATIAL_VARS:
                pattern[row_block_start + v, next_block_start + v] = 1.0

    return pattern.tocsr()


def expected_nnz(n_grid: int) -> int:
    """Closed-form expected non-zero count for `jacobian_sparsity_pattern(n_grid)`.

    main + sub + super = N_VARS² · n_grid + 2 · |SPATIAL_VARS| · (n_grid − 1)
    """
    return N_VARS * N_VARS * n_grid + 2 * len(SPATIAL_VARS) * (n_grid - 1)
