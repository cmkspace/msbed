"""Tests for the isotherms module."""

from __future__ import annotations

import math

import pytest

from phase2_simulation.isotherms import (
    MW_H2O,
    langmuir_co2_13x,
    load_isotherm_params,
    sanity_check_at_design_point,
    toth_h2o_alumina,
)


@pytest.fixture(scope="module")
def params() -> dict:
    return load_isotherm_params()


# ---------------------------------------------------------------------------
# Structural / asymptotic tests
# ---------------------------------------------------------------------------

def test_toth_zero_pressure(params: dict) -> None:
    """At P=0, Toth must return q=0 exactly."""
    assert toth_h2o_alumina(0.0, 298.15, params) == 0.0
    assert toth_h2o_alumina(0.0, 423.0, params) == 0.0


def test_langmuir_zero_pressure(params: dict) -> None:
    """At P=0, Langmuir must return q=0 exactly."""
    assert langmuir_co2_13x(0.0, 298.15, params) == 0.0


def test_langmuir_high_pressure(params: dict) -> None:
    """At P→∞, Langmuir must approach q_m."""
    q_m = params["zeolite_13x_co2_langmuir"]["q_m_mol_kg"]
    q_high = langmuir_co2_13x(1.0e10, 298.15, params)
    assert math.isclose(q_high, q_m, rel_tol=1e-6)


def test_toth_high_pressure(params: dict) -> None:
    """At P→∞, Toth must approach q_m within 1%."""
    q_m0 = params["alumina_h2o_toth"]["q_m0_mol_kg"]
    q_high = toth_h2o_alumina(1.0e12, 298.15, params)
    assert q_high <= q_m0
    assert q_high >= 0.99 * q_m0


def test_temperature_dependence_langmuir(params: dict) -> None:
    """Langmuir: q must decrease monotonically as T increases (exothermic)."""
    P = 240.0  # design CO₂ partial pressure (~400 ppm × 6 bar(a))
    q_cold = langmuir_co2_13x(P, 288.15, params)   # 15°C inlet
    q_warm = langmuir_co2_13x(P, 298.15, params)   # 25°C
    q_hot = langmuir_co2_13x(P, 473.15, params)    # 200°C regen
    assert q_cold > q_warm > q_hot


def test_temperature_dependence_toth(params: dict) -> None:
    """Toth: q must decrease monotonically as T increases (exothermic)."""
    P = 1697.0  # design H₂O partial pressure
    q_cold = toth_h2o_alumina(P, 288.15, params)
    q_warm = toth_h2o_alumina(P, 298.15, params)
    q_hot = toth_h2o_alumina(P, 473.15, params)
    assert q_cold > q_warm > q_hot


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_negative_pressure_raises(params: dict) -> None:
    with pytest.raises(ValueError):
        toth_h2o_alumina(-1.0, 298.15, params)
    with pytest.raises(ValueError):
        langmuir_co2_13x(-1.0, 298.15, params)


def test_nonpositive_temperature_raises(params: dict) -> None:
    with pytest.raises(ValueError):
        toth_h2o_alumina(100.0, 0.0, params)
    with pytest.raises(ValueError):
        langmuir_co2_13x(100.0, -50.0, params)


# ---------------------------------------------------------------------------
# Literature / Phase 1 calibration tests
# ---------------------------------------------------------------------------

@pytest.mark.literature
def test_known_data_point_cavenati(params: dict) -> None:
    """Langmuir 13X-CO₂ at 298 K, 100 Pa → 2.5 mol/kg ±20% (Cavenati 2004)."""
    q = langmuir_co2_13x(100.0, 298.15, params)
    assert 2.0 <= q <= 3.0, (
        f"q={q:.3f} mol/kg outside [2.0, 3.0] — Cavenati 2004 calibration "
        "violated; check Langmuir b0 or ΔH (DD-009)."
    )


def test_known_data_point_aa_wt_pct(params: dict) -> None:
    """Toth AA-H₂O at 298 K, 1697 Pa → 6 wt% ±50% (DBD §4.1 calibration)."""
    q = toth_h2o_alumina(1697.0, 298.15, params)
    wt_pct = q * MW_H2O / 10.0
    assert 3.0 <= wt_pct <= 9.0, (
        f"wt%={wt_pct:.3f} outside [3.0, 9.0] — DBD 6 wt% sanity violated; "
        "check Toth b0 (DD-009)."
    )


def test_phase1_consistency() -> None:
    """sanity_check_at_design_point() must pass with the SSOT YAML params."""
    result = sanity_check_at_design_point()
    assert result["all_pass"], (
        f"Sanity gate failed:\n  Toth: {result['toth_h2o_alumina']}\n"
        f"  Langmuir: {result['langmuir_co2_13x']}"
    )
    # Spot-check the recorded values are within their advertised ranges
    toth = result["toth_h2o_alumina"]
    lang = result["langmuir_co2_13x"]
    assert toth["allowed_range_wt_pct"][0] <= toth["q_wt_pct"] <= toth["allowed_range_wt_pct"][1]
    assert lang["allowed_range_mol_kg"][0] <= lang["q_mol_kg"] <= lang["allowed_range_mol_kg"][1]


def test_sanity_check_diagnoses_bad_toth_b0() -> None:
    """Pre-calibration Toth b0 placeholder must trip the Toth FAIL path."""
    bad = {
        "alumina_h2o_toth": {
            "q_m0_mol_kg": 13.0,
            "chi_qm": 0.0,
            "T_ref_K": 298.15,
            "b0_Pa_inv": 1.0e-9,        # the original placeholder — should fail
            "delta_H_J_mol": 54000,
            "t_heterogeneity": 0.45,
        },
        "zeolite_13x_co2_langmuir": {
            "q_m_mol_kg": 5.5,
            "b0_Pa_inv": 4.0e-9,
            "delta_H_J_mol": 36000,
        },
    }
    with pytest.raises(ValueError, match="Toth"):
        sanity_check_at_design_point(iso_params=bad)


def test_sanity_check_diagnoses_bad_langmuir_b0() -> None:
    """If Langmuir b0 reverts to the pre-calibration value, sanity must FAIL."""
    bad = {
        "alumina_h2o_toth": {
            "q_m0_mol_kg": 13.0,
            "chi_qm": 0.0,
            "T_ref_K": 298.15,
            "b0_Pa_inv": 1.0e-3,
            "delta_H_J_mol": 54000,
            "t_heterogeneity": 0.45,
        },
        "zeolite_13x_co2_langmuir": {
            "q_m_mol_kg": 5.5,
            "b0_Pa_inv": 2.4e-7,        # original placeholder — should fail
            "delta_H_J_mol": 36000,
        },
    }
    with pytest.raises(ValueError, match="Langmuir"):
        sanity_check_at_design_point(iso_params=bad)
