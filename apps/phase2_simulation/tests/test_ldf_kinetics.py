"""Tests for the ldf_kinetics module."""

from __future__ import annotations

import math

import pytest

from phase2_simulation.ldf_kinetics import (
    check_grid_resolution,
    compute_ldf_for_adsorbent,
    effective_diffusivity,
    estimate_mtz_width,
    k_ldf_glueckauf,
    load_config,
    molecular_diffusivity,
    sanity_check_at_design_point,
)

# ---------------------------------------------------------------------------
# Glueckauf / Fuller / effective diffusivity unit tests
# ---------------------------------------------------------------------------

def test_glueckauf_units() -> None:
    """k_LDF = 15·D_eff/r_p² has units 1/s."""
    # D_eff = 1e-7 m²/s, r_p = 1e-3 m → k = 15·1e-7/1e-6 = 1.5 s⁻¹
    assert math.isclose(k_ldf_glueckauf(1.0e-7, 1.0e-3), 1.5)


def test_fuller_temperature_dependence() -> None:
    """D_m ∝ T^1.75 (doubling T → factor 2^1.75 ≈ 3.36)."""
    D1 = molecular_diffusivity(298.0, 101325.0, "h2o")
    D2 = molecular_diffusivity(596.0, 101325.0, "h2o")
    assert math.isclose(D2 / D1, 2.0 ** 1.75, rel_tol=1e-9)


def test_fuller_pressure_dependence() -> None:
    """D_m ∝ 1/P (doubling P halves D_m)."""
    D1 = molecular_diffusivity(298.0, 101325.0, "co2")
    D2 = molecular_diffusivity(298.0, 202650.0, "co2")
    assert math.isclose(D2 / D1, 0.5, rel_tol=1e-9)


def test_fuller_h2o_at_298k_1atm() -> None:
    """D_h2o (298 K, 1 atm) ≈ 2.6e-5 m²/s ±20%."""
    D = molecular_diffusivity(298.15, 101325.0, "h2o")
    assert 2.08e-5 <= D <= 3.12e-5, f"D={D:.3e} m²/s outside ±20% of 2.6e-5"


def test_fuller_co2_at_298k_1atm() -> None:
    """D_co2 (298 K, 1 atm) ≈ 1.6e-5 m²/s ±20%."""
    D = molecular_diffusivity(298.15, 101325.0, "co2")
    assert 1.28e-5 <= D <= 1.92e-5, f"D={D:.3e} m²/s outside ±20% of 1.6e-5"


def test_effective_diffusivity_reduction() -> None:
    """D_eff = ε_p·D_m/τ < D_m (since ε_p < 1 ≤ τ)."""
    D_m = 1.0e-5
    D_eff = effective_diffusivity(D_m, 0.4, 3.0)
    assert D_eff < D_m
    assert math.isclose(D_eff, D_m * 0.4 / 3.0)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_input_validation_fuller() -> None:
    with pytest.raises(ValueError):
        molecular_diffusivity(0.0, 101325.0, "h2o")
    with pytest.raises(ValueError):
        molecular_diffusivity(298.0, 0.0, "h2o")
    with pytest.raises(ValueError, match="species"):
        molecular_diffusivity(298.0, 101325.0, "n2")  # type: ignore[arg-type]


def test_input_validation_glueckauf() -> None:
    with pytest.raises(ValueError):
        k_ldf_glueckauf(0.0, 1.0e-3)
    with pytest.raises(ValueError):
        k_ldf_glueckauf(1.0e-7, 0.0)


def test_input_validation_effective() -> None:
    with pytest.raises(ValueError):
        effective_diffusivity(1.0e-5, 1.5, 3.0)   # eps_p > 1
    with pytest.raises(ValueError):
        effective_diffusivity(1.0e-5, 0.4, 0.0)   # tau == 0
    with pytest.raises(ValueError):
        effective_diffusivity(0.0, 0.4, 3.0)      # D_m == 0


# ---------------------------------------------------------------------------
# Design-point k_LDF sanity (Rule 6)
# ---------------------------------------------------------------------------

DESIGN_T = 288.15            # K, 15°C inlet
DESIGN_P = 6.0928e5          # Pa, 5 bar(g) + 1.013 bar atm
DESIGN_U = 0.201             # m/s, DBD §5 superficial velocity


def test_design_point_k_ldf_alumina() -> None:
    """AA k_LDF at design point in [0.001, 1.0] s⁻¹."""
    res = compute_ldf_for_adsorbent("alumina", DESIGN_T, DESIGN_P)
    k = res["k_LDF_s_inv"]
    assert 0.001 <= k <= 1.0, f"AA k_LDF={k:.4f} outside [0.001, 1.0]"


def test_design_point_k_ldf_13x() -> None:
    """13X k_LDF at design point in [0.005, 5.0] s⁻¹."""
    res = compute_ldf_for_adsorbent("zeolite_13x", DESIGN_T, DESIGN_P)
    k = res["k_LDF_s_inv"]
    assert 0.005 <= k <= 5.0, f"13X k_LDF={k:.4f} outside [0.005, 5.0]"


def test_provenance_distinction() -> None:
    """13X k_internal must be MECHANISTIC; AA must be EMPIRICAL (Rule 6.3)."""
    cfg = load_config()
    aa_prov = cfg["mass_transfer"]["alumina"]["k_internal_provenance"]
    zx_prov = cfg["mass_transfer"]["zeolite_13x"]["k_internal_provenance"]
    assert "EMPIRICAL" in aa_prov.upper(), f"AA provenance: {aa_prov}"
    assert "MECHANISTIC" in zx_prov.upper(), f"13X provenance: {zx_prov}"


# ---------------------------------------------------------------------------
# MTZ + grid resolution checks
# ---------------------------------------------------------------------------

def test_design_point_mtz_width() -> None:
    """At design u and computed k_LDF, MTZ widths land in expected order."""
    aa = compute_ldf_for_adsorbent("alumina", DESIGN_T, DESIGN_P)
    zx = compute_ldf_for_adsorbent("zeolite_13x", DESIGN_T, DESIGN_P)
    mtz_aa = estimate_mtz_width(DESIGN_U, aa["k_LDF_s_inv"])
    mtz_zx = estimate_mtz_width(DESIGN_U, zx["k_LDF_s_inv"])
    # Expected from hand calculation: AA ~0.46 m, 13X ~1.37 m
    assert 0.30 < mtz_aa < 0.65, f"AA MTZ={mtz_aa:.3f} m"
    assert 1.00 < mtz_zx < 2.00, f"13X MTZ={mtz_zx:.3f} m"


def test_grid_resolution_at_design() -> None:
    """N=50 per layer must PASS at design point for both adsorbents."""
    aa = compute_ldf_for_adsorbent("alumina", DESIGN_T, DESIGN_P)
    zx = compute_ldf_for_adsorbent("zeolite_13x", DESIGN_T, DESIGN_P)
    chk_aa = check_grid_resolution(
        estimate_mtz_width(DESIGN_U, aa["k_LDF_s_inv"]), 0.925, 50
    )
    chk_zx = check_grid_resolution(
        estimate_mtz_width(DESIGN_U, zx["k_LDF_s_inv"]), 0.776, 50
    )
    assert chk_aa["status"] == "PASS", chk_aa
    assert chk_zx["status"] == "PASS", chk_zx


def test_grid_resolution_at_extreme() -> None:
    """At GHSV 1.5×, grid must PASS (or WARN with valid recommendation)."""
    aa = compute_ldf_for_adsorbent("alumina", DESIGN_T, DESIGN_P)
    zx = compute_ldf_for_adsorbent("zeolite_13x", DESIGN_T, DESIGN_P)
    u_ext = DESIGN_U * 1.5
    chk_aa = check_grid_resolution(estimate_mtz_width(u_ext, aa["k_LDF_s_inv"]), 0.925, 50)
    chk_zx = check_grid_resolution(estimate_mtz_width(u_ext, zx["k_LDF_s_inv"]), 0.776, 50)
    assert chk_aa["status"] in ("PASS", "WARN"), chk_aa
    assert chk_zx["status"] in ("PASS", "WARN"), chk_zx
    if chk_aa["status"] == "WARN":
        assert chk_aa["recommended_n_grid"] > 50
    if chk_zx["status"] == "WARN":
        assert chk_zx["recommended_n_grid"] > 50


def test_grid_resolution_fails_for_coarse_grid() -> None:
    """A deliberately coarse grid against a narrow MTZ must FAIL."""
    chk = check_grid_resolution(mtz_width=0.01, bed_length=1.0, n_grid=10)
    assert chk["status"] == "FAIL"
    assert chk["recommended_n_grid"] > 10


# ---------------------------------------------------------------------------
# Phase 2 consistency gate (Rule 6 enforcement)
# ---------------------------------------------------------------------------

def test_phase2_consistency() -> None:
    """sanity_check_at_design_point() must pass with SSOT YAML + DBD."""
    res = sanity_check_at_design_point()
    assert res["all_pass"], (
        f"LDF gate failed:\n  AA: {res['alumina']}\n  13X: {res['zeolite_13x']}\n"
        f"  Extreme: {res['extreme_ghsv_1p5x']}"
    )


def test_sanity_check_diagnoses_runaway_internal() -> None:
    """If AA k_internal is set to ~∞, dual-resistance reduces to k_macro and FAILS."""
    cfg = load_config()
    cfg["mass_transfer"]["alumina"]["k_internal_s_inv"] = 1.0e9
    with pytest.raises(ValueError, match="AA k_LDF FAIL"):
        sanity_check_at_design_point(config=cfg)


def test_sanity_check_diagnoses_zero_internal() -> None:
    """k_internal ≤ 0 must raise (catches accidental YAML deletion)."""
    cfg = load_config()
    cfg["mass_transfer"]["zeolite_13x"]["k_internal_s_inv"] = 0.0
    with pytest.raises(ValueError):
        sanity_check_at_design_point(config=cfg)
