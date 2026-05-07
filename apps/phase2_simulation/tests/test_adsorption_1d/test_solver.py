"""Tests for the BDF + sparse-Jac solver wrapper (Step 5.2)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from phase2_simulation.adsorption_1d import (
    ColumnConfig,
    OperatingConditions,
)
from phase2_simulation.adsorption_1d.rhs import SimulationParams
from phase2_simulation.adsorption_1d.solver import (
    SolverMetrics,
    initial_state_clean_bed,
    simulate,
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


@pytest.fixture(scope="module")
def params(col: ColumnConfig, op: OperatingConditions) -> SimulationParams:
    return SimulationParams.build(col, op, D_ax=1.0e-4)


# ---------------------------------------------------------------------------
# Basic invocation + metrics
# ---------------------------------------------------------------------------

def test_simulate_short_run_succeeds(params: SimulationParams) -> None:
    """1-second clean-bed simulation completes successfully."""
    y0 = initial_state_clean_bed(params)
    result, metrics = simulate(
        params, y0, t_span=(0.0, 1.0), skip_stiffness_check=True
    )
    assert result.success, result.message
    assert isinstance(metrics, SolverMetrics)
    # Inlet cell sees feed: C_h2o[0] grew from 0
    C_h2o_final = result.C_h2o()[:, -1]
    assert C_h2o_final[0] > 0


def test_simulate_metrics_populated(params: SimulationParams) -> None:
    """All metric fields populated and consistent."""
    y0 = initial_state_clean_bed(params)
    _, m = simulate(params, y0, t_span=(0.0, 0.5), skip_stiffness_check=True)
    assert m.wall_time_s > 0
    assert m.n_steps >= 1
    assert m.n_eval_rhs >= m.n_steps
    assert m.avg_ms_per_step > 0
    assert m.sparsity_nnz == 3094  # N=100 closed-form (DD-013)
    assert m.sparsity_pct > 98.0
    assert m.method == "BDF"
    assert m.stiffness_band == "skipped"


def test_simulate_isothermal_keeps_T_constant(params: SimulationParams) -> None:
    """isothermal=True freezes T at the initial value."""
    y0 = initial_state_clean_bed(params)
    result, _ = simulate(
        params, y0, t_span=(0.0, 5.0), isothermal=True, skip_stiffness_check=True
    )
    T_history = result.T()                       # (N, n_t)
    np.testing.assert_allclose(
        T_history, params.op.T_in_K, rtol=0, atol=1.0e-9,
        err_msg="isothermal mode produced T drift",
    )


def test_simulate_rejects_wrong_y0_size(params: SimulationParams) -> None:
    bad_y0 = np.zeros(10)  # too small
    with pytest.raises(ValueError, match="y0 size"):
        simulate(params, bad_y0, t_span=(0.0, 1.0), skip_stiffness_check=True)


# ---------------------------------------------------------------------------
# Pre-flight stiffness band dispatch (DD-012)
# ---------------------------------------------------------------------------

def test_simulate_aborts_on_extreme_stiffness(
    col: ColumnConfig, op: OperatingConditions
) -> None:
    """If the STOP threshold is set absurdly low, pre-flight raises RuntimeError."""
    # Build params with stop_above pulled below the actual stiffness ratio.
    p = SimulationParams.build(col, op, D_ax=1.0e-4)
    # Patch thresholds: design-point stiffness ~1.27e8 → set stop=1e4 to force ABORT
    object.__setattr__(p, "stiffness_warn", 1.0e2)
    object.__setattr__(p, "stiffness_stop", 1.0e4)
    y0 = initial_state_clean_bed(p)
    with pytest.raises(RuntimeError, match="exceeds STOP threshold"):
        simulate(p, y0, t_span=(0.0, 1.0), skip_stiffness_check=False)


def test_simulate_band_recorded_when_check_runs(params: SimulationParams) -> None:
    """When pre-flight is enabled, the band is recorded in metrics."""
    y0 = initial_state_clean_bed(params)
    _, m = simulate(params, y0, t_span=(0.0, 0.1), skip_stiffness_check=False)
    # At design point our system is in WARN band (1.27e8)
    assert m.stiffness_band in ("OK", "WARN")
    assert math.isfinite(m.stiffness_ratio)


# ---------------------------------------------------------------------------
# Mass balance retained over time (sanity for the BDF discretization)
# ---------------------------------------------------------------------------

def test_simulate_mass_balance_short_run(params: SimulationParams) -> None:
    """Cumulative inlet ≈ outlet + bed accumulation over a 60 s run.

    For a clean bed at design point with feed entering at z=0:
      ∫(C_in − C_out) dt · u · A_xs ≈ amount accumulated in the bed.
    """
    from phase2_simulation.adsorption_1d.boundary import (
        inlet_concentrations,
        superficial_velocity,
    )

    y0 = initial_state_clean_bed(params)
    t_eval = np.linspace(0.0, 60.0, 121)  # 0.5 s sampling
    result, _ = simulate(
        params, y0, t_span=(0.0, 60.0), t_eval=t_eval, skip_stiffness_check=True
    )
    assert result.success, result.message

    op = params.op
    A_xs = params.col.cross_section_m2
    eps_b = params.col.void_fraction
    u = superficial_velocity(op, op.T_in_K, A_xs)
    C_in = inlet_concentrations(op, op.T_in_K)

    for species, C_in_value, C_arr_func, q_arr_func in (
        ("h2o", C_in["h2o"], result.C_h2o, result.q_h2o),
        ("co2", C_in["co2"], result.C_co2, result.q_co2),
    ):
        C = C_arr_func()
        q = q_arr_func()
        # Cumulative inlet flux (mol/m² of bed cross-section, integrated over time)
        cum_in = u * C_in_value * t_eval[-1]
        # Outlet integral via trapezoidal rule on the last cell
        C_out = C[-1, :]
        cum_out = np.trapezoid(u * C_out, t_eval)
        # Accumulation in the bed (final state)
        gas_acc = np.sum(eps_b * C[:, -1] * params.grid.dz_widths_m)
        solid_acc = np.sum(
            (1.0 - eps_b) * params.rho_p * q[:, -1] * params.grid.dz_widths_m
        )
        # Balance: cum_in - cum_out = gas_acc + solid_acc
        balance_in = cum_in - cum_out
        balance_acc = gas_acc + solid_acc
        rel = abs(balance_in - balance_acc) / max(abs(balance_in), 1.0e-15)
        assert rel < 1.0e-3, (
            f"{species}: mass balance closure rel={rel:.3e} > 1e-3. "
            f"in={cum_in:.4e}, out={cum_out:.4e}, gas={gas_acc:.4e}, "
            f"solid={solid_acc:.4e}"
        )
