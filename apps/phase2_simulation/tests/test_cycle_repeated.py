"""Unit tests for the multi-cycle stabilization detector (Step 5.4.2)."""

from __future__ import annotations

import math

import numpy as np

from phase2_simulation.run_cycle_repeated import (
    CONSECUTIVE_REQUIRED,
    CycleSummary,
    find_stabilization_cycle,
    is_stabilized,
)


def _synthetic_summary(
    cycle_number: int,
    *,
    q_h2o: float = 1.0e-4,
    q_co2: float = 1.0e-4,
    energy_legacy_pct: float = 15.0,
    energy_model_pct: float = 0.1,
    adsorption_start_stiffness: float = 1.27e8,
    outlet_h2o_amplitude: float = 1.0,
    outlet_co2_amplitude: float = 1.0,
) -> CycleSummary:
    """Construct a synthetic CycleSummary with controllable per-cycle values."""
    t_s = np.linspace(0.0, 14400.0, 121)             # 1 sample / 2 min
    # Ramp shape similar to real adsorption breakthrough curve.
    ramp = np.where(t_s > 12000.0, (t_s - 12000.0) / 2400.0, 0.0)
    return CycleSummary(
        cycle_number=cycle_number,
        wall_time_s=1300.0,
        overall_pass=True,
        residual_q_h2o_avg_alumina_mol_kg=q_h2o,
        residual_q_co2_avg_13x_mol_kg=q_co2,
        adsorption_t_s=t_s,
        adsorption_C_h2o_outlet=outlet_h2o_amplitude * ramp,
        adsorption_C_co2_outlet=outlet_co2_amplitude * ramp,
        cycle_energy_legacy_pct=energy_legacy_pct,
        cycle_energy_model_pct=energy_model_pct,
        adsorption_start_stiffness=adsorption_start_stiffness,
        heating_start_stiffness=4.3e7,
        cooling_end_stiffness=6.7e7,
    )


def test_is_stabilized_identical_cycles_pass() -> None:
    """Two identical synthetic cycles → all metrics PASS."""
    a = _synthetic_summary(0)
    b = _synthetic_summary(1)
    res = is_stabilized(b, a)
    assert res["overall_stabilized"] is True
    for name, m in res["metrics"].items():
        assert m["status"] == "PASS", f"{name} unexpectedly FAIL"


def test_is_stabilized_perturbed_q_fails() -> None:
    """A 5 % bump in residual q_h2o (above 1 % gate) flips status to FAIL."""
    a = _synthetic_summary(0, q_h2o=1.0e-2)
    b = _synthetic_summary(1, q_h2o=1.05e-2)            # +5 %
    res = is_stabilized(b, a)
    assert res["overall_stabilized"] is False
    assert res["metrics"]["residual_q_h2o"]["status"] == "FAIL"
    # Other metrics should still pass.
    assert res["metrics"]["residual_q_co2"]["status"] == "PASS"


def test_noise_floor_marks_residual_q_degenerate() -> None:
    """When both q values are below noise floor → degenerate, PASS."""
    a = _synthetic_summary(0, q_h2o=1.0e-9, q_co2=1.0e-9)
    b = _synthetic_summary(1, q_h2o=2.0e-9, q_co2=3.0e-9)  # large rel_diff but tiny abs
    res = is_stabilized(b, a)
    h2o = res["metrics"]["residual_q_h2o"]
    co2 = res["metrics"]["residual_q_co2"]
    assert h2o["flag"] == "DEGENERATE"
    assert h2o["status"] == "PASS"
    assert math.isnan(h2o["rel_diff_pct"])
    assert co2["flag"] == "DEGENERATE"
    assert co2["status"] == "PASS"


def test_outlet_shape_diff_detects_amplitude_change() -> None:
    """A 5 % outlet curve amplitude change (above 1 % gate) → shape FAIL."""
    a = _synthetic_summary(0, outlet_h2o_amplitude=1.0)
    b = _synthetic_summary(1, outlet_h2o_amplitude=1.05)
    res = is_stabilized(b, a)
    assert res["metrics"]["outlet_shape_h2o"]["status"] == "FAIL"
    assert res["metrics"]["outlet_shape_co2"]["status"] == "PASS"


def test_outlet_shape_degenerate_when_both_zero() -> None:
    """If both outlet trajectories are zero, shape metric is degenerate."""
    a = _synthetic_summary(0, outlet_h2o_amplitude=0.0)
    b = _synthetic_summary(1, outlet_h2o_amplitude=0.0)
    res = is_stabilized(b, a)
    assert res["metrics"]["outlet_shape_h2o"]["flag"] == "DEGENERATE"
    assert res["metrics"]["outlet_shape_h2o"]["status"] == "PASS"


def test_energy_legacy_uses_absolute_diff() -> None:
    """Energy stability is measured in absolute %-pt difference, not relative."""
    a = _synthetic_summary(0, energy_legacy_pct=15.0)
    b = _synthetic_summary(1, energy_legacy_pct=15.4)         # +0.4 pct-pt → PASS
    res = is_stabilized(b, a)
    assert res["metrics"]["energy_legacy_abs_diff"]["status"] == "PASS"
    c = _synthetic_summary(2, energy_legacy_pct=16.0)         # +1.0 pct-pt → FAIL
    res2 = is_stabilized(c, a)
    assert res2["metrics"]["energy_legacy_abs_diff"]["status"] == "FAIL"


def test_stiffness_relaxed_5pct_gate() -> None:
    """Adsorption-start stiffness uses a relaxed 5 % gate."""
    a = _synthetic_summary(0, adsorption_start_stiffness=1.0e8)
    # 4.5 % rel_diff → PASS (under 5 %)
    b = _synthetic_summary(1, adsorption_start_stiffness=1.045e8)
    res = is_stabilized(b, a)
    assert res["metrics"]["adsorption_start_stiffness"]["status"] == "PASS"
    # 6 % rel_diff → FAIL
    c = _synthetic_summary(2, adsorption_start_stiffness=1.06e8)
    res2 = is_stabilized(c, a)
    assert res2["metrics"]["adsorption_start_stiffness"]["status"] == "FAIL"


def test_find_stabilization_consecutive_required() -> None:
    """One stable transition isn't enough; two consecutive are required."""
    # Cycles 0..4 with cycle 2 deviating slightly → only (0,1) and (3,4) stable
    base = _synthetic_summary(0).adsorption_C_h2o_outlet
    summaries = [
        _synthetic_summary(0, q_h2o=1e-4),
        _synthetic_summary(1, q_h2o=1e-4),               # (0, 1) stable
        _synthetic_summary(2, q_h2o=1.5e-4),             # (1, 2) FAIL — q jump
        _synthetic_summary(3, q_h2o=1.5e-4),             # (2, 3) stable on q
        _synthetic_summary(4, q_h2o=1.5e-4),             # (3, 4) stable on q
    ]
    _ = base
    n_stable, flags = find_stabilization_cycle(summaries, require_consecutive=2)
    # Earliest 2-consecutive stable window starts after the disturbance:
    # pair_stable = [True, False, True, True] → first 2-consecutive at index (2,3)
    # → stabilization first ESTABLISHED at cycle index (i - needed + 2) where
    #    i is the index in pair_stable where the second 'True' lands.
    # In our case i = 3 (pair (3,4)), so stabilization cycle = summaries[3-2+2] = 3.
    assert flags == [True, False, True, True]
    assert n_stable == 3


def test_find_stabilization_returns_none_if_never() -> None:
    """If no two-consecutive stable transition exists, returns None."""
    summaries = [
        _synthetic_summary(0, q_h2o=1e-4),
        _synthetic_summary(1, q_h2o=2e-4),
        _synthetic_summary(2, q_h2o=3e-4),
        _synthetic_summary(3, q_h2o=4e-4),
        _synthetic_summary(4, q_h2o=5e-4),
    ]
    n_stable, flags = find_stabilization_cycle(summaries, require_consecutive=2)
    assert n_stable is None
    assert all(f is False for f in flags)


def test_find_stabilization_short_run_returns_none() -> None:
    """Less than `require_consecutive + 1` cycles → cannot even attempt."""
    summaries = [_synthetic_summary(0), _synthetic_summary(1)]   # only 2 cycles
    n_stable, flags = find_stabilization_cycle(summaries, require_consecutive=2)
    assert n_stable is None
    assert flags == []


def test_consecutive_required_constant() -> None:
    """Sanity: project default is 2-consecutive."""
    assert CONSECUTIVE_REQUIRED == 2
