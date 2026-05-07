"""Tests for state_transform.py — well-mixed P-jump transforms (Step 5.4.0a)."""

from __future__ import annotations

import numpy as np
import pytest

from phase2_simulation.adsorption_1d.state import pack_state, var_slice
from phase2_simulation.adsorption_1d.state_transform import (
    R_GAS,
    depressurize,
    repressurize,
)


# ---------------------------------------------------------------------------
# Synthetic fixture: 10-cell uniform bed loaded with feed-composition gas
# ---------------------------------------------------------------------------
@pytest.fixture
def fixture_state() -> dict:
    n = 10
    dz = np.full(n, 0.17)                    # 1.7 m / 10 cells
    A_xs = np.pi * 0.25**2 / 4.0             # D=0.25 m
    eps = 0.38
    P_high = 6.01e5                          # 6.01 bar
    P_low = 1.013e5                          # 1.013 bar
    T = 288.15                               # 15 °C
    y_h2o, y_co2 = 2823e-6, 400e-6
    C_h2o = np.full(n, y_h2o * P_high / (R_GAS * T))
    C_co2 = np.full(n, y_co2 * P_high / (R_GAS * T))
    q_h2o = np.full(n, 1.5)                  # arbitrary loading mol/kg
    q_co2 = np.full(n, 0.5)
    T_arr = np.full(n, T)
    y = pack_state(C_h2o, q_h2o, C_co2, q_co2, T_arr)
    return dict(
        n=n, dz=dz, A_xs=A_xs, eps=eps,
        P_high=P_high, P_low=P_low, T=T,
        y_h2o=y_h2o, y_co2=y_co2,
        C_h2o=C_h2o, C_co2=C_co2,
        q_h2o=q_h2o, q_co2=q_co2,
        y=y,
    )


# ---------------------------------------------------------------------------
# Depressurize
# ---------------------------------------------------------------------------
def test_depressurize_mass_conservation(fixture_state: dict) -> None:
    """Vented gas equals (1 − ratio) × initial void inventory (machine precision)."""
    f = fixture_state
    y_new, vented = depressurize(
        f["y"], f["n"], f["dz"], f["A_xs"], f["eps"], f["P_high"], f["P_low"]
    )
    ratio = f["P_low"] / f["P_high"]
    cell_vol = f["eps"] * f["dz"] * f["A_xs"]
    expected_h2o = float(np.sum(f["C_h2o"] * cell_vol)) * (1.0 - ratio)
    expected_co2 = float(np.sum(f["C_co2"] * cell_vol)) * (1.0 - ratio)
    assert vented["h2o"] == pytest.approx(expected_h2o, rel=1e-12)
    assert vented["co2"] == pytest.approx(expected_co2, rel=1e-12)


def test_depressurize_q_unchanged(fixture_state: dict) -> None:
    """Adsorbed phase is invariant under depressurization."""
    f = fixture_state
    y_new, _ = depressurize(
        f["y"], f["n"], f["dz"], f["A_xs"], f["eps"], f["P_high"], f["P_low"]
    )
    np.testing.assert_array_equal(y_new[var_slice("q_h2o", f["n"])], f["q_h2o"])
    np.testing.assert_array_equal(y_new[var_slice("q_co2", f["n"])], f["q_co2"])


def test_depressurize_T_unchanged(fixture_state: dict) -> None:
    """Temperature is invariant (instantaneous, no heat transfer)."""
    f = fixture_state
    y_new, _ = depressurize(
        f["y"], f["n"], f["dz"], f["A_xs"], f["eps"], f["P_high"], f["P_low"]
    )
    np.testing.assert_array_equal(y_new[var_slice("T", f["n"])], np.full(f["n"], f["T"]))


def test_depressurize_C_scales_by_pressure_ratio(fixture_state: dict) -> None:
    """C_new = C_old × (P_low / P_high) cell-by-cell."""
    f = fixture_state
    y_new, _ = depressurize(
        f["y"], f["n"], f["dz"], f["A_xs"], f["eps"], f["P_high"], f["P_low"]
    )
    ratio = f["P_low"] / f["P_high"]
    np.testing.assert_allclose(
        y_new[var_slice("C_h2o", f["n"])], f["C_h2o"] * ratio, rtol=1e-14
    )
    np.testing.assert_allclose(
        y_new[var_slice("C_co2", f["n"])], f["C_co2"] * ratio, rtol=1e-14
    )


def test_depressurize_rejects_increase(fixture_state: dict) -> None:
    f = fixture_state
    with pytest.raises(ValueError, match="P_low_Pa"):
        depressurize(
            f["y"], f["n"], f["dz"], f["A_xs"], f["eps"],
            P_high_Pa=f["P_low"], P_low_Pa=f["P_high"],
        )


# ---------------------------------------------------------------------------
# Repressurize
# ---------------------------------------------------------------------------
def test_repressurize_mole_balance(fixture_state: dict) -> None:
    """Total feed mole input equals Σ_cells ΔC × void_volume to machine precision."""
    f = fixture_state
    y_new, added = repressurize(
        f["y"], f["n"], f["dz"], f["A_xs"], f["eps"],
        f["P_low"], f["P_high"], f["y_h2o"], f["y_co2"],
    )
    delta_P = f["P_high"] - f["P_low"]
    cell_vol = f["eps"] * f["dz"] * f["A_xs"]
    expected_h2o = float(
        np.sum(f["y_h2o"] * delta_P / (R_GAS * f["T"]) * cell_vol)
    )
    expected_co2 = float(
        np.sum(f["y_co2"] * delta_P / (R_GAS * f["T"]) * cell_vol)
    )
    assert added["h2o"] == pytest.approx(expected_h2o, rel=1e-12)
    assert added["co2"] == pytest.approx(expected_co2, rel=1e-12)


def test_repressurize_q_unchanged(fixture_state: dict) -> None:
    """Adsorbed phase is invariant under repressurization."""
    f = fixture_state
    y_new, _ = repressurize(
        f["y"], f["n"], f["dz"], f["A_xs"], f["eps"],
        f["P_low"], f["P_high"], f["y_h2o"], f["y_co2"],
    )
    np.testing.assert_array_equal(y_new[var_slice("q_h2o", f["n"])], f["q_h2o"])
    np.testing.assert_array_equal(y_new[var_slice("q_co2", f["n"])], f["q_co2"])


def test_repressurize_T_unchanged(fixture_state: dict) -> None:
    f = fixture_state
    y_new, _ = repressurize(
        f["y"], f["n"], f["dz"], f["A_xs"], f["eps"],
        f["P_low"], f["P_high"], f["y_h2o"], f["y_co2"],
    )
    np.testing.assert_array_equal(y_new[var_slice("T", f["n"])], np.full(f["n"], f["T"]))


def test_repressurize_uses_local_T(fixture_state: dict) -> None:
    """Per-cell T variation drives per-cell ΔC: ΔC[i] ∝ 1 / T[i]."""
    f = fixture_state
    y_grad = f["y"].copy()
    n = f["n"]
    T_grad = np.linspace(288.15, 473.15, n)             # cooling tail to heating front
    y_grad[var_slice("T", n)] = T_grad
    y_new, _ = repressurize(
        y_grad, n, f["dz"], f["A_xs"], f["eps"],
        f["P_low"], f["P_high"], f["y_h2o"], f["y_co2"],
    )
    delta_C_h2o = y_new[var_slice("C_h2o", n)] - y_grad[var_slice("C_h2o", n)]
    expected = f["y_h2o"] * (f["P_high"] - f["P_low"]) / (R_GAS * T_grad)
    np.testing.assert_allclose(delta_C_h2o, expected, rtol=1e-14)


def test_repressurize_rejects_decrease(fixture_state: dict) -> None:
    f = fixture_state
    with pytest.raises(ValueError, match="P_high_Pa"):
        repressurize(
            f["y"], f["n"], f["dz"], f["A_xs"], f["eps"],
            P_low_Pa=f["P_high"], P_high_Pa=f["P_low"],
            y_h2o_feed=f["y_h2o"], y_co2_feed=f["y_co2"],
        )


# ---------------------------------------------------------------------------
# Closed-loop roundtrip
# ---------------------------------------------------------------------------
def test_full_pressure_cycle_preserves_q_and_T(fixture_state: dict) -> None:
    """Depress → repress: q and T are exactly preserved through the round trip."""
    f = fixture_state
    y_after_depr, _ = depressurize(
        f["y"], f["n"], f["dz"], f["A_xs"], f["eps"], f["P_high"], f["P_low"]
    )
    y_after_repr, _ = repressurize(
        y_after_depr, f["n"], f["dz"], f["A_xs"], f["eps"],
        f["P_low"], f["P_high"], f["y_h2o"], f["y_co2"],
    )
    np.testing.assert_array_equal(
        y_after_repr[var_slice("q_h2o", f["n"])], f["q_h2o"]
    )
    np.testing.assert_array_equal(
        y_after_repr[var_slice("q_co2", f["n"])], f["q_co2"]
    )
    np.testing.assert_array_equal(
        y_after_repr[var_slice("T", f["n"])], np.full(f["n"], f["T"])
    )


def test_full_pressure_cycle_C_recovers_when_feed_matches_void(
    fixture_state: dict,
) -> None:
    """If the void gas was already at feed composition, C is recovered exactly.

    Initial void is feed-composition (set in fixture). After depress·repress
    the new C should equal the original C (mass added back = mass vented).
    """
    f = fixture_state
    y_after_depr, _ = depressurize(
        f["y"], f["n"], f["dz"], f["A_xs"], f["eps"], f["P_high"], f["P_low"]
    )
    y_after_repr, _ = repressurize(
        y_after_depr, f["n"], f["dz"], f["A_xs"], f["eps"],
        f["P_low"], f["P_high"], f["y_h2o"], f["y_co2"],
    )
    np.testing.assert_allclose(
        y_after_repr[var_slice("C_h2o", f["n"])], f["C_h2o"], rtol=1e-14
    )
    np.testing.assert_allclose(
        y_after_repr[var_slice("C_co2", f["n"])], f["C_co2"], rtol=1e-14
    )
