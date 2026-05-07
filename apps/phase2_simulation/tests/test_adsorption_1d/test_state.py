"""Tests for state packing/unpacking and SimulationResult."""

from __future__ import annotations

import numpy as np
import pytest

from phase2_simulation.adsorption_1d import (
    N_VARS,
    ColumnConfig,
    OperatingConditions,
    SimulationResult,
    build_grid,
    pack_state,
    state_size,
    unpack_state,
    var_slice,
)
from phase2_simulation.ldf_kinetics import load_dbd


@pytest.fixture(scope="module")
def col() -> ColumnConfig:
    return ColumnConfig.from_dbd(load_dbd())


@pytest.fixture(scope="module")
def op() -> OperatingConditions:
    return OperatingConditions(
        mode="adsorption",
        flow_nm3h=200.0,
        P_op_Pa=6.01325e5,
        T_in_K=288.15,
        y_h2o_in=2823.07e-6,
        y_co2_in=400e-6,
        flow_direction="forward",
    )


def test_state_size() -> None:
    assert state_size(100) == 500
    assert state_size(50) == 250
    assert state_size(1) == 5
    with pytest.raises(ValueError):
        state_size(0)


def test_pack_unpack_roundtrip() -> None:
    n = 12
    rng = np.random.default_rng(seed=0)
    arrays = [rng.uniform(size=n) for _ in range(5)]
    y = pack_state(*arrays)
    assert y.shape == (5 * n,)
    out = unpack_state(y, n)
    for orig, recovered in zip(arrays, out, strict=True):
        np.testing.assert_array_equal(recovered, orig)


def test_pack_size_mismatch() -> None:
    with pytest.raises(ValueError, match="length"):
        pack_state(
            np.zeros(10), np.zeros(11), np.zeros(10), np.zeros(10), np.zeros(10)
        )


def test_unpack_size_mismatch() -> None:
    with pytest.raises(ValueError, match="state vector size"):
        unpack_state(np.zeros(99), 20)


# ---------------------------------------------------------------------------
# OperatingConditions validation
# ---------------------------------------------------------------------------

def test_operating_conditions_rejects_bad_mole_fractions() -> None:
    with pytest.raises(ValueError):
        OperatingConditions(
            mode="adsorption",
            flow_nm3h=200.0,
            P_op_Pa=6.01325e5,
            T_in_K=288.15,
            y_h2o_in=0.6,
            y_co2_in=0.5,  # sum > 1
        )


def test_operating_conditions_rejects_bad_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        OperatingConditions(
            mode="depressurize",  # type: ignore[arg-type]
            flow_nm3h=200.0,
            P_op_Pa=6.01325e5,
            T_in_K=288.15,
            y_h2o_in=0.0,
            y_co2_in=0.0,
        )


def test_operating_conditions_default_2a(op: OperatingConditions) -> None:
    """Default adsorbs_in matrix is Decision 2A."""
    assert op.adsorbs("alumina", "h2o") is True
    assert op.adsorbs("alumina", "co2") is False
    assert op.adsorbs("zeolite_13x", "h2o") is False
    assert op.adsorbs("zeolite_13x", "co2") is True


# ---------------------------------------------------------------------------
# SimulationResult
# ---------------------------------------------------------------------------

def test_simulation_result_accessors(col: ColumnConfig, op: OperatingConditions) -> None:
    """Layout B: var accessors stride by N_VARS starting at the variable's offset."""
    g = build_grid(col)
    n_t = 5
    N = g.n_total
    rng = np.random.default_rng(seed=1)
    y_full = rng.uniform(size=(N_VARS * N, n_t))
    res = SimulationResult(
        t_s=np.linspace(0, 1, n_t),
        y=y_full,
        grid=g,
        op=op,
        success=True,
        message="OK",
    )
    np.testing.assert_array_equal(res.C_h2o(), y_full[0::N_VARS, :])
    np.testing.assert_array_equal(res.q_h2o(), y_full[1::N_VARS, :])
    np.testing.assert_array_equal(res.C_co2(), y_full[2::N_VARS, :])
    np.testing.assert_array_equal(res.q_co2(), y_full[3::N_VARS, :])
    np.testing.assert_array_equal(res.T(), y_full[4::N_VARS, :])
    # Outlet (last cell) values
    np.testing.assert_array_equal(res.outlet_C_h2o(), y_full[N_VARS * (N - 1) + 0, :])
    np.testing.assert_array_equal(res.outlet_C_co2(), y_full[N_VARS * (N - 1) + 2, :])


def test_var_slice_layout_b() -> None:
    """var_slice returns the proper stride/offset for each STATE_VAR."""
    n = 4
    y = np.arange(N_VARS * n, dtype=float)
    # Layout B: y = [C_h2o[0], q_h2o[0], C_co2[0], q_co2[0], T[0], C_h2o[1], ...]
    np.testing.assert_array_equal(y[var_slice("C_h2o", n)], [0, 5, 10, 15])
    np.testing.assert_array_equal(y[var_slice("q_h2o", n)], [1, 6, 11, 16])
    np.testing.assert_array_equal(y[var_slice("C_co2", n)], [2, 7, 12, 17])
    np.testing.assert_array_equal(y[var_slice("q_co2", n)], [3, 8, 13, 18])
    np.testing.assert_array_equal(y[var_slice("T", n)], [4, 9, 14, 19])


def test_var_slice_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown var"):
        var_slice("xenon", 10)


def test_simulation_result_shape_validation(
    col: ColumnConfig, op: OperatingConditions
) -> None:
    g = build_grid(col)
    bad_y = np.zeros((10, 5))  # wrong row count
    with pytest.raises(ValueError, match="5N"):
        SimulationResult(
            t_s=np.linspace(0, 1, 5),
            y=bad_y,
            grid=g,
            op=op,
            success=True,
            message="OK",
        )
