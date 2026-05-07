"""Smoke tests for run_breakthrough.py (Step 5.3).

The full 5 h gate is verified manually (~14 min wall time). These tests run a
short simulation to validate that the three-gate pipeline (PHASE2_SPEC §4.4)
produces well-formed output: H2O timing, CO2 spec, mass balance.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from phase2_simulation.run_breakthrough import (
    GATE_CO2_SPEC_CHECKPOINT_H,
    GATE_CO2_SPEC_PPM,
    GATE_MASS_BALANCE_TOL_PCT,
    H2O_BREAKTHROUGH_THRESHOLD_FRAC,
    Co2GateResult,
    H2oGateResult,
    _breakthrough_time_h,
    run_breakthrough,
)


def test_breakthrough_time_reached_linearly() -> None:
    """Linear-interpolated breakthrough time matches a synthetic ramp."""
    t = np.array([0.0, 100.0, 200.0, 300.0, 400.0])  # seconds
    # outlet ramps 0..1 mol/m^3; threshold = 0.05 * 1 = 0.05; reached between 0..100s
    C_out = np.array([0.0, 0.50, 0.80, 0.95, 1.00])
    bt_h = _breakthrough_time_h(t, C_out, C_in_value=1.0)
    # target=0.05; (0.05 − 0) / (0.50 − 0) = 0.1 of [0..100s] = 10 s = 10/3600 h
    assert math.isclose(bt_h, 10.0 / 3600.0, rel_tol=1.0e-9)


def test_breakthrough_time_not_reached() -> None:
    """If C_out never crosses 5% of C_in, return NaN."""
    t = np.array([0.0, 100.0, 200.0])
    C_out = np.array([0.0, 0.001, 0.002])
    assert math.isnan(_breakthrough_time_h(t, C_out, C_in_value=1.0))


def test_h2o_threshold_constant() -> None:
    """Sanity: H2O timing gate uses the documented 5% threshold."""
    assert H2O_BREAKTHROUGH_THRESHOLD_FRAC == 0.05


def test_co2_spec_constants() -> None:
    """Sanity: CO2 spec gate is < 0.1 ppm at t = 4 h (DBD §3.5)."""
    assert GATE_CO2_SPEC_PPM == 0.1
    assert GATE_CO2_SPEC_CHECKPOINT_H == 4.0


@pytest.mark.slow
def test_run_breakthrough_short_30min() -> None:
    """30-min sim returns a well-formed report:

    * H2oGateResult: no 5% breakthrough yet, mass balance closes.
    * Co2GateResult: spec gate not yet evaluable (sim < 4 h checkpoint),
      so out_ppm is NaN and spec_passes is False — but mass balance closes.
    """
    report, t_eval, C_h2o_out, C_co2_out = run_breakthrough(
        duration_h=0.5, samples_per_hour=120, skip_stiffness_check=True
    )

    assert report.duration_h == 0.5
    assert report.wall_time_s > 0
    assert t_eval.shape == C_h2o_out.shape == C_co2_out.shape
    assert t_eval[-1] == pytest.approx(1800.0)

    h2o = report.h2o
    assert isinstance(h2o, H2oGateResult)
    assert h2o.cum_inlet_mol > 0
    # 30-min sim shouldn't see H2O breakthrough at design conditions.
    assert (
        math.isnan(h2o.breakthrough_time_h) or h2o.breakthrough_time_h > 0.4
    ), f"unexpected early H2O breakthrough: {h2o.breakthrough_time_h}"
    assert h2o.mass_balance_error_pct < GATE_MASS_BALANCE_TOL_PCT, (
        f"H2O mass balance err {h2o.mass_balance_error_pct:.2f}% "
        f">= {GATE_MASS_BALANCE_TOL_PCT}%"
    )

    co2 = report.co2
    assert isinstance(co2, Co2GateResult)
    assert co2.cum_inlet_mol > 0
    assert co2.inlet_ppm == pytest.approx(400.0, rel=1e-9)
    # Sim too short to evaluate the 4 h checkpoint.
    assert math.isnan(co2.out_ppm_at_checkpoint)
    assert co2.spec_passes is False
    assert co2.mass_balance_error_pct < GATE_MASS_BALANCE_TOL_PCT, (
        f"CO2 mass balance err {co2.mass_balance_error_pct:.2f}% "
        f">= {GATE_MASS_BALANCE_TOL_PCT}%"
    )
