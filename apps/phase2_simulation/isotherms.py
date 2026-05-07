"""Isotherm models for H₂O on Activated Alumina (Toth) and CO₂ on Zeolite 13X (Langmuir).

Convention
----------
ΔH (heat of adsorption) is stored as a **positive magnitude** in J/mol.
For exothermic adsorption, the affinity parameter b increases as T decreases
via the Van't Hoff form:

    Toth:     b(T) = b0 · exp[ΔH / (R · T_ref) · (T_ref/T − 1)]
    Langmuir: b(T) = b0 · exp(+ΔH / (R · T))

Both forms reduce to b = b0 at T = T_ref (Toth) or as ΔH → 0 (Langmuir).

Parameter Provenance (DD-009)
-----------------------------
- Toth (Alumina/H₂O): provisional, calibrated against DBD 6 wt% at design
  point (P=1697 Pa, T=298.15 K). b0 = 1.0e-3 Pa⁻¹.
  Pending literature fitting from Serbezov & Sotirchos (1998).
- Langmuir (13X/CO₂): provisional, validated within ±20% of
  Cavenati, Grande & Rodrigues (2004) at 298 K, 100 Pa CO₂ → q ≈ 2.5 mol/kg.
  b0 = 4.0e-9 Pa⁻¹.

The function `sanity_check_at_design_point` is the automated gate enforced
per CLAUDE.md Rule 6: any change to isotherm parameters must pass this check
before commit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import yaml

# Universal constants
R_GAS = 8.314462618  # J/(mol·K)
MW_H2O = 18.01528    # g/mol
MW_CO2 = 44.0095     # g/mol

# Project SSOT paths (this file: apps/phase2_simulation/isotherms.py → parents[2] = repo root)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = _PROJECT_ROOT / "config" / "adsorbent_properties.yaml"


def load_isotherm_params(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load isotherm parameters from YAML config.

    Args:
        config_path: Path to adsorbent_properties.yaml. Defaults to project SSOT.

    Returns:
        Parsed dict containing `alumina_h2o_toth` and `zeolite_13x_co2_langmuir`.
    """
    path = Path(config_path) if config_path else DEFAULT_CONFIG
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def toth_h2o_alumina(
    P_h2o: float,
    T: float,
    params: dict[str, Any] | None = None,
) -> float:
    """H₂O equilibrium loading on Activated Alumina via Toth equation.

    Convention:
        ΔH is stored as positive magnitude (J/mol). For exothermic adsorption,
        b increases as T decreases via
        b(T) = b0 · exp[ΔH / (R · T_ref) · (T_ref/T − 1)].

    Toth form:
        q* = (q_m · b · P) / [1 + (b·P)^t]^(1/t)
        q_m(T) = q_m0 · exp[χ · (1 − T/T_ref)]

    Args:
        P_h2o: H₂O partial pressure (Pa). Must be ≥ 0.
        T: Temperature (K). Must be > 0.
        params: Pre-loaded parameter dict. If None, loads from project SSOT.

    Returns:
        Equilibrium loading (mol H₂O / kg adsorbent).

    Raises:
        ValueError: If P_h2o < 0 or T ≤ 0.
    """
    if P_h2o < 0:
        raise ValueError(f"P_h2o must be ≥ 0, got {P_h2o}")
    if T <= 0:
        raise ValueError(f"T must be > 0 K, got {T}")

    if params is None:
        params = load_isotherm_params()
    p = params["alumina_h2o_toth"]

    q_m0 = p["q_m0_mol_kg"]
    chi = p["chi_qm"]
    T_ref = p["T_ref_K"]
    b0 = p["b0_Pa_inv"]
    delta_H = p["delta_H_J_mol"]
    t = p["t_heterogeneity"]

    if P_h2o == 0.0:
        return 0.0

    q_m = q_m0 * np.exp(chi * (1.0 - T / T_ref))
    b = b0 * np.exp(delta_H / (R_GAS * T_ref) * (T_ref / T - 1.0))
    bP = b * P_h2o
    q = q_m * bP / (1.0 + bP**t) ** (1.0 / t)
    return float(q)


def langmuir_co2_13x(
    P_co2: float,
    T: float,
    params: dict[str, Any] | None = None,
) -> float:
    """CO₂ equilibrium loading on Zeolite 13X via Langmuir equation.

    Convention:
        ΔH is stored as positive magnitude (J/mol). For exothermic adsorption,
        b increases as T decreases via b(T) = b0 · exp(+ΔH / (R · T)).

    Langmuir form:
        q* = q_m · b · P / (1 + b · P)

    Args:
        P_co2: CO₂ partial pressure (Pa). Must be ≥ 0.
        T: Temperature (K). Must be > 0.
        params: Pre-loaded parameter dict. If None, loads from project SSOT.

    Returns:
        Equilibrium loading (mol CO₂ / kg adsorbent).

    Raises:
        ValueError: If P_co2 < 0 or T ≤ 0.
    """
    if P_co2 < 0:
        raise ValueError(f"P_co2 must be ≥ 0, got {P_co2}")
    if T <= 0:
        raise ValueError(f"T must be > 0 K, got {T}")

    if params is None:
        params = load_isotherm_params()
    p = params["zeolite_13x_co2_langmuir"]

    q_m = p["q_m_mol_kg"]
    b0 = p["b0_Pa_inv"]
    delta_H = p["delta_H_J_mol"]

    b = b0 * np.exp(delta_H / (R_GAS * T))
    bP = b * P_co2
    q = q_m * bP / (1.0 + bP)
    return float(q)


def sanity_check_at_design_point(
    iso_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate isotherm parameters against the DBD design point.

    Acts as an automated gate per CLAUDE.md Rule 6: any change to isotherm
    parameters must pass this check before commit.

    Checks performed:
      1. Toth (AA-H₂O) at (P_h2o = 1697 Pa, T = 298.15 K):
         q × MW_H₂O within DBD 6 wt% ± 50% (i.e. 3.0–9.0 wt%).
         The 1697 Pa is the inlet H₂O partial pressure at 5 bar(g)·15°C·100% RH
         (Phase 1 Antoine calculation). 298.15 K matches the YAML T_ref so the
         calibration is anchored at b = b0.
      2. Langmuir (13X-CO₂) at (P_co2 = 100 Pa, T = 298.15 K):
         q within Cavenati 2004 reference (2.5 mol/kg) ± 20% (2.0–3.0 mol/kg).

    Args:
        iso_params: Pre-loaded isotherm params. If None, loads from SSOT.

    Returns:
        Dict with computed q values, expected ranges, and pass/fail flags.

    Raises:
        ValueError: If any check fails. The message identifies which equation
            and which parameter is most likely responsible.
    """
    if iso_params is None:
        iso_params = load_isotherm_params()

    # ------------------------------------------------------------------
    # Check 1 — Toth (AA-H₂O) at DBD design point
    # ------------------------------------------------------------------
    P_h2o_design = 1697.0      # Pa  (5 bar(g)·15°C·100% RH, Phase 1 Antoine)
    T_design = 298.15          # K   (matches YAML T_ref)
    expected_wt_pct = 6.0      # DBD §4.1 dynamic loading assumption
    tol_pct_toth = 50.0
    wt_lo = expected_wt_pct * (1 - tol_pct_toth / 100)
    wt_hi = expected_wt_pct * (1 + tol_pct_toth / 100)

    q_toth = toth_h2o_alumina(P_h2o_design, T_design, iso_params)
    wt_toth = q_toth * MW_H2O / 10.0   # mol/kg → g/kg → wt% (dry basis: ×MW÷1000×100)
    toth_ok = wt_lo <= wt_toth <= wt_hi

    # ------------------------------------------------------------------
    # Check 2 — Langmuir (13X-CO₂) Cavenati 2004 reference point
    # ------------------------------------------------------------------
    P_co2_lit = 100.0          # Pa
    T_lit = 298.15             # K
    expected_q_lang = 2.5      # mol/kg, Cavenati 2004 reading at 298 K, 100 Pa
    tol_pct_lang = 20.0
    q_lo = expected_q_lang * (1 - tol_pct_lang / 100)
    q_hi = expected_q_lang * (1 + tol_pct_lang / 100)

    q_lang = langmuir_co2_13x(P_co2_lit, T_lit, iso_params)
    lang_ok = q_lo <= q_lang <= q_hi

    result = {
        "toth_h2o_alumina": {
            "P_Pa": P_h2o_design,
            "T_K": T_design,
            "q_mol_kg": q_toth,
            "q_wt_pct": wt_toth,
            "expected_wt_pct": expected_wt_pct,
            "tolerance_pct": tol_pct_toth,
            "allowed_range_wt_pct": (wt_lo, wt_hi),
            "pass": toth_ok,
        },
        "langmuir_co2_13x": {
            "P_Pa": P_co2_lit,
            "T_K": T_lit,
            "q_mol_kg": q_lang,
            "expected_mol_kg": expected_q_lang,
            "tolerance_pct": tol_pct_lang,
            "allowed_range_mol_kg": (q_lo, q_hi),
            "reference": "Cavenati, Grande, Rodrigues (2004) — J. Chem. Eng. Data 49, 1095",
            "pass": lang_ok,
        },
        "all_pass": toth_ok and lang_ok,
    }

    if not toth_ok:
        ap = iso_params["alumina_h2o_toth"]
        raise ValueError(
            "Toth (AA-H₂O) sanity FAIL at DBD design point "
            f"(P={P_h2o_design} Pa, T={T_design} K):\n"
            f"  computed q = {q_toth:.4f} mol/kg → {wt_toth:.3f} wt%\n"
            f"  expected   = {expected_wt_pct} wt% ± {tol_pct_toth}% "
            f"→ allowed range [{wt_lo:.2f}, {wt_hi:.2f}] wt%\n"
            f"  current params: q_m0={ap['q_m0_mol_kg']}, "
            f"b0={ap['b0_Pa_inv']}, t={ap['t_heterogeneity']}, "
            f"ΔH={ap['delta_H_J_mol']} J/mol\n"
            "  Most likely culprit: b0. Toth saturation behavior is highly "
            "sensitive to the b·P magnitude through the [1+(b·P)^t]^(1/t) "
            "denominator. See DD-009."
        )
    if not lang_ok:
        lp = iso_params["zeolite_13x_co2_langmuir"]
        raise ValueError(
            "Langmuir (13X-CO₂) sanity FAIL at Cavenati 2004 reference point "
            f"(P={P_co2_lit} Pa, T={T_lit} K):\n"
            f"  computed q = {q_lang:.4f} mol/kg\n"
            f"  expected   = {expected_q_lang} mol/kg ± {tol_pct_lang}% "
            f"→ allowed range [{q_lo:.2f}, {q_hi:.2f}] mol/kg\n"
            f"  current params: q_m={lp['q_m_mol_kg']}, "
            f"b0={lp['b0_Pa_inv']}, ΔH={lp['delta_H_J_mol']} J/mol\n"
            "  Most likely culprit: b0 or ΔH "
            "(Van't Hoff form b(T) = b0·exp(+ΔH/RT) — note positive sign "
            "convention). See DD-009."
        )

    return result


if __name__ == "__main__":
    # Quick self-check when invoked directly: python -m phase2_simulation.isotherms
    import sys

    # Force UTF-8 so unicode literals in reference strings (em-dash, etc.)
    # render correctly on cp949 Windows consoles.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    res = sanity_check_at_design_point()
    print("Sanity check at DBD design point:")
    for key in ("toth_h2o_alumina", "langmuir_co2_13x"):
        section = res[key]
        print(f"  [{key}]")
        for k, v in section.items():
            print(f"    {k}: {v}")
    print(f"  all_pass: {res['all_pass']}")
