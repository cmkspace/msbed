"""Tests for the Jacobian sparsity pattern (Step 5.1)."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.sparse import csr_matrix

from phase2_simulation.adsorption_1d import N_VARS
from phase2_simulation.adsorption_1d.jacobian import (
    SPATIAL_VARS,
    expected_nnz,
    jacobian_sparsity_pattern,
)

# ---------------------------------------------------------------------------
# Basic structural properties
# ---------------------------------------------------------------------------

def test_pattern_dimensions() -> None:
    """Pattern is square with side 5N."""
    n = 100
    P = jacobian_sparsity_pattern(n)
    assert isinstance(P, csr_matrix)
    assert P.shape == (5 * n, 5 * n)


def test_pattern_rejects_small_grid() -> None:
    with pytest.raises(ValueError, match="n_grid"):
        jacobian_sparsity_pattern(1)


def test_pattern_nnz_matches_closed_form() -> None:
    """Built pattern's nnz exactly matches the analytical formula."""
    for n in (10, 50, 100, 200):
        P = jacobian_sparsity_pattern(n)
        assert P.nnz == expected_nnz(n), (
            f"N={n}: built nnz={P.nnz} ≠ expected_nnz={expected_nnz(n)}"
        )


def test_pattern_nnz_at_design_grid() -> None:
    """Concrete sanity check at the project's grid size N=100."""
    P = jacobian_sparsity_pattern(100)
    # Closed-form: 5²·100 + 2·3·99 = 2500 + 594 = 3094
    assert P.nnz == 3094, f"N=100 nnz={P.nnz} (expected 3094 from cell-major Layout B)"
    sparsity_pct = 1.0 - P.nnz / (5 * 100) ** 2
    assert sparsity_pct > 0.98, f"sparsity={sparsity_pct:.4f} unexpectedly low"


# ---------------------------------------------------------------------------
# Cell-internal block (main diagonal): 5×5 dense per cell
# ---------------------------------------------------------------------------

def test_pattern_cell_internal_block_dense() -> None:
    """Each cell's 5×5 main-diagonal block is fully dense."""
    n = 20
    P = jacobian_sparsity_pattern(n).toarray()
    for i in range(n):
        block = P[N_VARS * i : N_VARS * (i + 1), N_VARS * i : N_VARS * (i + 1)]
        assert np.all(block == 1.0), f"cell {i}: main block not fully dense"


# ---------------------------------------------------------------------------
# Off-diagonal pattern (advection / conduction): only C_h2o, C_co2, T
# ---------------------------------------------------------------------------

def test_pattern_advection_offset_is_n_vars() -> None:
    """Inter-cell coupling for spatial vars sits at |row-col| = N_VARS = 5."""
    n = 100
    P = jacobian_sparsity_pattern(n).toarray()
    # For spatial var v, P[N_VARS·i + v, N_VARS·(i±1) + v] should be 1.
    for i in range(1, n - 1):
        for v in SPATIAL_VARS:
            assert P[N_VARS * i + v, N_VARS * (i - 1) + v] == 1.0
            assert P[N_VARS * i + v, N_VARS * (i + 1) + v] == 1.0


def test_pattern_q_no_spatial_coupling() -> None:
    """q_h2o and q_co2 must have NO inter-cell entries (LDF is purely local)."""
    n = 50
    P = jacobian_sparsity_pattern(n).toarray()
    for i in range(1, n - 1):
        for v in (1, 3):  # q_h2o, q_co2
            # Off-diagonal positions of q-rows must be zero across cells.
            assert P[N_VARS * i + v, N_VARS * (i - 1) + v] == 0.0
            assert P[N_VARS * i + v, N_VARS * (i + 1) + v] == 0.0
            # Cross-species off-diagonal too.
            other = 0 if v == 1 else 2
            assert P[N_VARS * i + v, N_VARS * (i - 1) + other] == 0.0
            assert P[N_VARS * i + v, N_VARS * (i + 1) + other] == 0.0


def test_pattern_off_diagonal_count_per_pair() -> None:
    """Each off-diagonal cell pair has exactly |SPATIAL_VARS|=3 entries."""
    n = 30
    P = jacobian_sparsity_pattern(n).toarray()
    for i in range(1, n):
        # Sub-diagonal block: rows in cell i, cols in cell i-1
        block = P[N_VARS * i : N_VARS * (i + 1), N_VARS * (i - 1) : N_VARS * i]
        assert int(block.sum()) == len(SPATIAL_VARS), (
            f"cell pair (i, i-1) = ({i}, {i-1}) has {int(block.sum())} entries; "
            f"expected {len(SPATIAL_VARS)}"
        )


# ---------------------------------------------------------------------------
# Boundary cells: one-sided spatial coupling
# ---------------------------------------------------------------------------

def test_pattern_boundary_cells_one_sided() -> None:
    """Cell 0 has no sub-diagonal; cell N-1 has no super-diagonal."""
    n = 20
    P = jacobian_sparsity_pattern(n).toarray()

    # Cell 0: no sub-diagonal entries (no cells to its left)
    cell0_rows = slice(0, N_VARS)
    cell_neg1_cols = slice(-N_VARS, None)
    # The "wrap-around" check is meaningless; we just confirm the row block
    # doesn't reference any column index < 0 — implicitly true since we only
    # added neighbors when i > 0.
    # Concretely: the spatial entries in cell 0 must point only to cell 1.
    for v in SPATIAL_VARS:
        # cell 0 → cell 1 must exist
        assert P[v, N_VARS + v] == 1.0
        # cell 0 has no sub-diagonal cell — verify there's nothing pointing to "cell -1"
        # by checking the band of off-diagonal columns that would correspond to a -1
        # neighbor: there is none.
    # Total non-self-block columns hit by cell 0 rows = exactly the cell-1 spatial entries
    cell0_band = P[cell0_rows, N_VARS : 2 * N_VARS]
    assert int(cell0_band.sum()) == len(SPATIAL_VARS)
    # Cell 0 has NO entries to columns left of its own block (trivially true here)
    _ = cell_neg1_cols  # acknowledge unused

    # Cell N-1: no super-diagonal
    last_rows = slice(N_VARS * (n - 1), N_VARS * n)
    last_band_super = P[last_rows, N_VARS * n : ]   # empty slice
    assert last_band_super.size == 0
    # Sub-diagonal: cell N-1 ← cell N-2 has |SPATIAL_VARS| entries.
    sub_band = P[last_rows, N_VARS * (n - 2) : N_VARS * (n - 1)]
    assert int(sub_band.sum()) == len(SPATIAL_VARS)


# ---------------------------------------------------------------------------
# Asymmetry — pattern is NOT symmetric (advection is directional, but
# we include both sub- and super-diagonal because dispersion uses central diff
# which IS symmetric. Net effect: pattern IS symmetric.). Document via a check.
# ---------------------------------------------------------------------------

def test_pattern_is_symmetric_by_construction() -> None:
    """With central-difference dispersion + symmetric conduction, the structural
    pattern is symmetric even though advection is directional (upwind cell still
    appears in BOTH directions because dispersion fills the missing side)."""
    n = 25
    P = jacobian_sparsity_pattern(n).toarray()
    # Verify P == P.T element-wise
    assert np.array_equal(P, P.T), "Jacobian sparsity pattern unexpectedly asymmetric"


# ---------------------------------------------------------------------------
# Sanity: pattern compatible with rhs_full Jacobian
# (numerical FD Jacobian non-zeros should be a subset of pattern's non-zeros)
# ---------------------------------------------------------------------------

def test_pattern_covers_numerical_jacobian() -> None:
    """Every numerically-detected non-zero entry of J at a realistic state must be
    inside the declared sparsity pattern."""
    from phase2_simulation.adsorption_1d import (
        ColumnConfig,
        OperatingConditions,
        cell_block_slice,
        var_slice,
    )
    from phase2_simulation.adsorption_1d.boundary import inlet_concentrations
    from phase2_simulation.adsorption_1d.rhs import SimulationParams, rhs_full
    from phase2_simulation.ldf_kinetics import load_dbd

    dbd = load_dbd()
    col = ColumnConfig.from_dbd(dbd)
    op = OperatingConditions(
        mode="adsorption",
        flow_nm3h=200.0,
        P_op_Pa=6.01325e5,
        T_in_K=288.15,
        y_h2o_in=2823.07e-6,
        y_co2_in=400e-6,
        flow_direction="forward",
    )
    params = SimulationParams.build(col, op, D_ax=1.0e-4)
    n = params.grid.n_total
    pattern = jacobian_sparsity_pattern(n).toarray()

    # Realistic state: feed at cell 0, T uniform at op.T_in_K
    y = np.zeros(N_VARS * n)
    y[var_slice("T", n)] = op.T_in_K
    C_in = inlet_concentrations(op, op.T_in_K)
    cell0 = cell_block_slice(0)
    y[cell0.start + 0] = C_in["h2o"]
    y[cell0.start + 2] = C_in["co2"]

    # FD Jacobian (central) — accept ~1e-4 absolute as a "structural" non-zero
    base_eps = np.cbrt(np.finfo(float).eps)
    eps = base_eps * np.maximum(np.abs(y), 1.0)
    fd_J = np.zeros((N_VARS * n, N_VARS * n))
    for j in range(N_VARS * n):
        y_p = y.copy()
        y_m = y.copy()
        y_p[j] += eps[j]
        y_m[j] -= eps[j]
        fd_J[:, j] = (rhs_full(0.0, y_p, params) - rhs_full(0.0, y_m, params)) / (2.0 * eps[j])

    # Find non-zero FD entries (above noise floor)
    fd_nonzero = np.abs(fd_J) > 1.0e-3 * np.max(np.abs(fd_J))
    coverage = np.where(fd_nonzero, pattern > 0, True)
    assert coverage.all(), (
        f"FD-detected couplings not covered by pattern; missed entries = "
        f"{int((~coverage).sum())}"
    )
