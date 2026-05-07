"""LDF mass transfer kinetics with dual-resistance correction (DD-010).

This module implements the Linear Driving Force (LDF) approximation for
gas-phase mass transfer to porous adsorbent particles, using the
Ruthven (1984) dual-resistance form to combine macropore and internal
resistances:

    1/k_LDF = 1/k_macro + 1/k_internal

where:
    k_macro    = 15 * D_eff / r_p²       (Glueckauf 1955, macropore)
    D_eff      = ε_p * D_m / τ           (effective pore diffusivity)
    D_m        = Fuller-Schettler-Giddings (binary gas diffusivity in air)
    k_internal = 15 * D_c / r_c²         (Glueckauf micropore — MECHANISTIC for 13X)
                 0.5 s⁻¹                  (EMPIRICAL surrogate for AA)

Provenance distinction (CLAUDE.md Rule 6, DD-010)
-------------------------------------------------
- 13X k_internal: MECHANISTIC. k_micro = 15·D_c/r_c² with Yang (1987) typical
  D_c/r_c² ≈ 0.01 s⁻¹ for CO₂ in 13X crystallites — measurable.
- AA  k_internal: EMPIRICAL. AA lacks well-defined micropores; the value
  0.5 s⁻¹ is a surrogate fit to literature k_LDF measurements
  (Serbezov 1998, Bonnissel et al.). The actual mechanism is a mix of
  macropore + surface diffusion + binding kinetics.
  This 0.5 value should be re-validated against experimental k_LDF data
  in Phase 6 testing.

Sanity gate (Rule 6)
--------------------
`sanity_check_at_design_point()` enforces:
  1. AA k_LDF ∈ [0.001, 1.0] s⁻¹ at design point
  2. 13X k_LDF ∈ [0.005, 5.0] s⁻¹ at design point
  3. MTZ width resolved by ≥5 grid cells per layer (N=50/layer default)
  4. Same checks at GHSV 1.5× extreme — PASS or WARN with recommendation

References
----------
- Fuller, Schettler, Giddings (1966), Ind. Eng. Chem. 58(5), 19-27
- Glueckauf (1955), Trans. Faraday Soc. 51, 1540
- Ruthven (1984), Principles of Adsorption and Adsorption Processes, §6.7
- Yang (1987), Gas Separation by Adsorption Processes
- Serbezov & Sotirchos (1998); Bonnissel et al. (1998)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml

# Universal constants
R_GAS = 8.314462618  # J/(mol·K)

# Fuller atomic diffusion volumes (Fuller-Schettler-Giddings 1966)
_FULLER_VOLUMES = {
    "h2o": 13.1,
    "co2": 26.9,
    "air": 19.7,   # composite air value
}
_MW_G_MOL = {
    "h2o": 18.01528,
    "co2": 44.0095,
    "air": 28.96,
}

# Project SSOT paths
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = _PROJECT_ROOT / "config" / "adsorbent_properties.yaml"
DEFAULT_DBD = _PROJECT_ROOT / "config" / "dbd_locked.yaml"


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load adsorbent_properties.yaml (SSOT for mass_transfer parameters)."""
    p = Path(path) if path else DEFAULT_CONFIG
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_dbd(path: Path | str | None = None) -> dict[str, Any]:
    """Load dbd_locked.yaml (SSOT for process/column/simulation parameters)."""
    p = Path(path) if path else DEFAULT_DBD
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def molecular_diffusivity(
    T: float,
    P: float,
    species: Literal["h2o", "co2"],
) -> float:
    """Binary gas diffusivity in air via Fuller-Schettler-Giddings equation.

    Form (SI):
        D_AB [m²/s] = 1.013e-2 · T^1.75 · √(1/M_A + 1/M_B) /
                      (P[Pa] · (V_A^(1/3) + V_air^(1/3))²)

    Atomic diffusion volumes: H₂O = 13.1, CO₂ = 26.9, air = 19.7.

    Args:
        T: Temperature (K). Must be > 0.
        P: Total pressure (Pa). Must be > 0.
        species: 'h2o' or 'co2'.

    Returns:
        Diffusion coefficient in air (m²/s).

    Raises:
        ValueError: If T ≤ 0, P ≤ 0, or species is not supported.
    """
    if T <= 0:
        raise ValueError(f"T must be > 0 K, got {T}")
    if P <= 0:
        raise ValueError(f"P must be > 0 Pa, got {P}")
    if species not in ("h2o", "co2"):
        raise ValueError(f"species must be 'h2o' or 'co2', got '{species}'")

    V_a = _FULLER_VOLUMES[species]
    V_air = _FULLER_VOLUMES["air"]
    M_a = _MW_G_MOL[species]
    M_air = _MW_G_MOL["air"]

    inv_M = 1.0 / M_a + 1.0 / M_air
    diam_term = (V_a ** (1.0 / 3.0) + V_air ** (1.0 / 3.0)) ** 2
    D = 1.013e-2 * T ** 1.75 * np.sqrt(inv_M) / (P * diam_term)
    return float(D)


def effective_diffusivity(D_m: float, eps_p: float, tortuosity: float) -> float:
    """Macropore effective diffusivity D_eff = ε_p · D_m / τ.

    Args:
        D_m: Molecular (free-gas) diffusivity (m²/s). Must be > 0.
        eps_p: Particle porosity (-). Must be in (0, 1).
        tortuosity: Tortuosity factor τ (-). Must be > 0.

    Returns:
        Effective pore diffusivity (m²/s).
    """
    if D_m <= 0:
        raise ValueError(f"D_m must be > 0, got {D_m}")
    if not 0 < eps_p < 1:
        raise ValueError(f"eps_p must be in (0,1), got {eps_p}")
    if tortuosity <= 0:
        raise ValueError(f"tortuosity must be > 0, got {tortuosity}")
    return eps_p * D_m / tortuosity


def k_ldf_glueckauf(D_eff: float, r_p: float) -> float:
    """Glueckauf (1955) LDF coefficient: k = 15·D_eff/r_p².

    Applies to either macropore (using r_particle and macropore D_eff) or
    micropore (using r_crystal and intracrystalline D_c).

    Args:
        D_eff: Effective diffusivity (m²/s). Must be > 0.
        r_p: Characteristic radius (m). Must be > 0.

    Returns:
        LDF rate coefficient (1/s).
    """
    if D_eff <= 0:
        raise ValueError(f"D_eff must be > 0, got {D_eff}")
    if r_p <= 0:
        raise ValueError(f"r_p must be > 0 m, got {r_p}")
    return 15.0 * D_eff / r_p ** 2


def compute_ldf_for_adsorbent(
    adsorbent_name: Literal["alumina", "zeolite_13x"],
    T: float,
    P: float,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute lumped LDF coefficient via Ruthven (1984) dual-resistance.

    Combines macropore (Glueckauf) and internal (micropore for 13X,
    surface-diffusion proxy for AA) resistances:

        1/k_LDF = 1/k_macro + 1/k_internal

    Note on AA k_internal:
        Unlike 13X (where k_micro = 15·D_c/r_c² is mechanistic with D_c/r_c²
        measurable), AA's k_internal = 0.5 s⁻¹ is an EMPIRICAL surrogate fit
        to literature k_LDF measurements (Serbezov 1998, Bonnissel et al.).
        AA lacks well-defined micropores; the actual mechanism is a mix of
        macropore + surface diffusion + binding kinetics.
        This 0.5 value should be re-validated when experimental k_LDF data
        becomes available in Phase 6 testing.

    Args:
        adsorbent_name: 'alumina' or 'zeolite_13x'.
        T: Temperature (K).
        P: Total pressure (Pa).
        config: Pre-loaded adsorbent_properties.yaml dict; loads SSOT if None.

    Returns:
        Dict containing intermediate values and the final k_LDF (1/s):
        adsorbent, T_K, P_Pa, D_m_m2_s, D_eff_m2_s, k_macro_s_inv,
        k_internal_s_inv, k_internal_provenance, k_LDF_s_inv.
    """
    if adsorbent_name not in ("alumina", "zeolite_13x"):
        raise ValueError(
            "adsorbent_name must be 'alumina' or 'zeolite_13x', "
            f"got '{adsorbent_name}'"
        )
    if config is None:
        config = load_config()

    mt = config["mass_transfer"][adsorbent_name]
    species: Literal["h2o", "co2"] = "h2o" if adsorbent_name == "alumina" else "co2"

    D_m = molecular_diffusivity(T, P, species)
    D_eff = effective_diffusivity(D_m, mt["particle_porosity"], mt["tortuosity"])
    k_macro = k_ldf_glueckauf(D_eff, mt["particle_radius_m"])
    k_internal = float(mt["k_internal_s_inv"])
    if k_internal <= 0:
        raise ValueError(
            f"{adsorbent_name}.k_internal_s_inv must be > 0, got {k_internal}"
        )
    k_internal_provenance = mt.get("k_internal_provenance", "unspecified")

    inv_k = 1.0 / k_macro + 1.0 / k_internal
    k_LDF = 1.0 / inv_k

    return {
        "adsorbent": adsorbent_name,
        "T_K": T,
        "P_Pa": P,
        "D_m_m2_s": D_m,
        "D_eff_m2_s": D_eff,
        "k_macro_s_inv": k_macro,
        "k_internal_s_inv": k_internal,
        "k_internal_provenance": k_internal_provenance,
        "k_LDF_s_inv": k_LDF,
    }


def estimate_mtz_width(u_superficial: float, k_ldf: float) -> float:
    """Order-of-magnitude estimate of MTZ width as u/k.

    This is a rough approximation valid for linear (Henry-regime) adsorption.
    For nonlinear isotherms (Langmuir, Toth) the actual MTZ shape is governed
    by isotherm curvature; this estimate is intended only for grid-resolution
    sanity checks before a PDE solve.

    Args:
        u_superficial: Superficial gas velocity in the bed (m/s). Must be > 0.
        k_ldf: LDF coefficient (1/s). Must be > 0.

    Returns:
        MTZ width estimate (m).
    """
    if u_superficial <= 0:
        raise ValueError(f"u_superficial must be > 0, got {u_superficial}")
    if k_ldf <= 0:
        raise ValueError(f"k_ldf must be > 0, got {k_ldf}")
    return u_superficial / k_ldf


def check_grid_resolution(
    mtz_width: float,
    bed_length: float,
    n_grid: int,
    min_cells_in_mtz: int = 5,
) -> dict[str, Any]:
    """Check whether a uniform spatial grid resolves the MTZ adequately.

    A status of:
      - 'PASS' is returned when cells_in_mtz ≥ min_cells_in_mtz.
      - 'WARN' when cells_in_mtz is between (min/2) and min — borderline,
        a recommended_n_grid is provided but the simulation can proceed.
      - 'FAIL' when cells_in_mtz < min/2 — grid too coarse, the suggested
        n_grid should be applied.

    When MTZ width exceeds bed length the front is gradual rather than abrupt,
    which is trivially well-resolved (status 'PASS').

    Args:
        mtz_width: Estimated MTZ width (m).
        bed_length: Bed length over which the grid is laid (m).
        n_grid: Number of grid points in `bed_length`.
        min_cells_in_mtz: Minimum cells expected to span the MTZ.

    Returns:
        Dict with status, mtz_width_m, dz_m, n_grid, cells_in_mtz,
        min_cells_required, recommended_n_grid.
    """
    if mtz_width <= 0:
        raise ValueError(f"mtz_width must be > 0, got {mtz_width}")
    if bed_length <= 0:
        raise ValueError(f"bed_length must be > 0, got {bed_length}")
    if n_grid < 2:
        raise ValueError(f"n_grid must be >= 2, got {n_grid}")
    if min_cells_in_mtz < 1:
        raise ValueError(f"min_cells_in_mtz must be >= 1, got {min_cells_in_mtz}")

    dz = bed_length / (n_grid - 1)
    cells_in_mtz = mtz_width / dz

    if cells_in_mtz >= min_cells_in_mtz:
        status = "PASS"
        recommended = n_grid
    elif cells_in_mtz >= max(2.0, min_cells_in_mtz / 2.0):
        status = "WARN"
        recommended = int(np.ceil(min_cells_in_mtz * bed_length / mtz_width)) + 1
    else:
        status = "FAIL"
        recommended = int(np.ceil(min_cells_in_mtz * bed_length / mtz_width)) + 1

    return {
        "status": status,
        "mtz_width_m": mtz_width,
        "dz_m": dz,
        "n_grid": n_grid,
        "cells_in_mtz": cells_in_mtz,
        "min_cells_required": min_cells_in_mtz,
        "recommended_n_grid": recommended,
    }


def sanity_check_at_design_point(
    config: dict[str, Any] | None = None,
    dbd: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate LDF kinetics against the DBD design point and grid constraints.

    Acts as the automated gate per CLAUDE.md Rule 6 for the LDF module.

    Checks performed:
      1. AA k_LDF in [0.001, 1.0] s⁻¹ at design point
      2. 13X k_LDF in [0.005, 5.0] s⁻¹ at design point
      3. MTZ width resolved by ≥5 cells per layer (N_per_layer = grid_points / 2)
      4. Same checks at extreme GHSV 1.5× — PASS or WARN with recommendation

    Design point (from DBD SSOT):
      - T = T_in (15°C → 288.15 K)
      - P = (P_gauge + P_atm) × 1e5 Pa
      - u_superficial = 0.201 m/s (DBD §5)

    Args:
        config: Pre-loaded adsorbent_properties dict. Loads SSOT if None.
        dbd: Pre-loaded dbd_locked dict. Loads SSOT if None.

    Returns:
        Dict with computed values, allowed ranges, and pass/fail flags.

    Raises:
        ValueError: If any check fails. The message identifies which check
            and the suspect parameter.
    """
    if config is None:
        config = load_config()
    if dbd is None:
        dbd = load_dbd()

    T_design = float(dbd["process"]["temperature_in_C"]) + 273.15
    P_design = (
        float(dbd["process"]["pressure_gauge_bar"])
        + float(dbd["process"]["pressure_atm_bar"])
    ) * 1.0e5
    u_design = 0.201  # m/s, DBD §5 Column Specifications
    L_alumina = float(dbd["column"]["alumina_height_m"])
    L_13x = float(dbd["column"]["zeolite_13x_height_m"])
    N_total = int(dbd["simulation"]["grid_points"])
    N_per_layer = N_total // 2  # 50/50 layered split per PHASE2_SPEC §3.1

    aa = compute_ldf_for_adsorbent("alumina", T_design, P_design, config)
    zx = compute_ldf_for_adsorbent("zeolite_13x", T_design, P_design, config)

    aa_range = (0.001, 1.0)
    zx_range = (0.005, 5.0)
    aa_ok = aa_range[0] <= aa["k_LDF_s_inv"] <= aa_range[1]
    zx_ok = zx_range[0] <= zx["k_LDF_s_inv"] <= zx_range[1]

    # Grid resolution at design point
    mtz_aa = estimate_mtz_width(u_design, aa["k_LDF_s_inv"])
    mtz_zx = estimate_mtz_width(u_design, zx["k_LDF_s_inv"])
    grid_aa = check_grid_resolution(mtz_aa, L_alumina, N_per_layer)
    grid_zx = check_grid_resolution(mtz_zx, L_13x, N_per_layer)

    # Grid resolution at GHSV 1.5× (extreme of 27-case sensitivity matrix)
    u_ext = u_design * 1.5
    mtz_aa_ext = estimate_mtz_width(u_ext, aa["k_LDF_s_inv"])
    mtz_zx_ext = estimate_mtz_width(u_ext, zx["k_LDF_s_inv"])
    grid_aa_ext = check_grid_resolution(mtz_aa_ext, L_alumina, N_per_layer)
    grid_zx_ext = check_grid_resolution(mtz_zx_ext, L_13x, N_per_layer)

    result = {
        "design_point": {
            "T_K": T_design,
            "P_Pa": P_design,
            "u_superficial_m_s": u_design,
            "L_alumina_m": L_alumina,
            "L_13x_m": L_13x,
            "N_per_layer": N_per_layer,
        },
        "alumina": {
            **aa,
            "allowed_range_s_inv": aa_range,
            "k_LDF_pass": aa_ok,
            "mtz_width_m": mtz_aa,
            "grid_check": grid_aa,
        },
        "zeolite_13x": {
            **zx,
            "allowed_range_s_inv": zx_range,
            "k_LDF_pass": zx_ok,
            "mtz_width_m": mtz_zx,
            "grid_check": grid_zx,
        },
        "extreme_ghsv_1p5x": {
            "alumina_grid_check": grid_aa_ext,
            "zeolite_13x_grid_check": grid_zx_ext,
        },
        "all_pass": (
            aa_ok and zx_ok
            and grid_aa["status"] == "PASS"
            and grid_zx["status"] == "PASS"
            and grid_aa_ext["status"] in ("PASS", "WARN")
            and grid_zx_ext["status"] in ("PASS", "WARN")
        ),
    }

    if not aa_ok:
        raise ValueError(
            f"AA k_LDF FAIL at design point: {aa['k_LDF_s_inv']:.4f} s⁻¹ "
            f"outside {aa_range}.\n"
            f"  Components: k_macro={aa['k_macro_s_inv']:.3f} s⁻¹, "
            f"k_internal={aa['k_internal_s_inv']:.3f} s⁻¹ "
            f"({aa['k_internal_provenance']}).\n"
            "  AA k_internal is EMPIRICAL — most likely culprit if calibration drifts. "
            "See DD-010."
        )
    if not zx_ok:
        raise ValueError(
            f"13X k_LDF FAIL at design point: {zx['k_LDF_s_inv']:.4f} s⁻¹ "
            f"outside {zx_range}.\n"
            f"  Components: k_macro={zx['k_macro_s_inv']:.3f} s⁻¹, "
            f"k_internal={zx['k_internal_s_inv']:.3f} s⁻¹ "
            f"({zx['k_internal_provenance']}).\n"
            "  Check Yang 1987 D_c/r_c² value (currently 0.01 s⁻¹). See DD-010."
        )
    if grid_aa["status"] == "FAIL":
        raise ValueError(
            f"AA grid resolution FAIL at design point: "
            f"{grid_aa['cells_in_mtz']:.1f} cells in MTZ "
            f"(need ≥{grid_aa['min_cells_required']}). "
            f"Increase grid_points to ≥{2 * grid_aa['recommended_n_grid']} total "
            f"(≥{grid_aa['recommended_n_grid']} per layer)."
        )
    if grid_zx["status"] == "FAIL":
        raise ValueError(
            f"13X grid resolution FAIL at design point: "
            f"{grid_zx['cells_in_mtz']:.1f} cells in MTZ "
            f"(need ≥{grid_zx['min_cells_required']}). "
            f"Increase grid_points to ≥{2 * grid_zx['recommended_n_grid']} total "
            f"(≥{grid_zx['recommended_n_grid']} per layer)."
        )
    if grid_aa_ext["status"] == "FAIL" or grid_zx_ext["status"] == "FAIL":
        raise ValueError(
            "Grid resolution FAIL at extreme GHSV 1.5×. "
            "27-case sensitivity sweep would be unreliable.\n"
            f"  AA: {grid_aa_ext}\n  13X: {grid_zx_ext}\n"
            "Increase grid_points before running run_sensitivity.py."
        )

    return result


if __name__ == "__main__":
    # Quick self-check: uv run python -m phase2_simulation.ldf_kinetics
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    res = sanity_check_at_design_point()
    print("LDF sanity check at DBD design point:")
    print(f"  Design point: {res['design_point']}")
    for key in ("alumina", "zeolite_13x"):
        section = res[key]
        print(f"  [{key}]")
        for k, v in section.items():
            if k == "grid_check":
                print("    grid_check:")
                for kk, vv in v.items():
                    print(f"      {kk}: {vv}")
            else:
                print(f"    {k}: {v}")
    print("  extreme_ghsv_1p5x:")
    for k, v in res["extreme_ghsv_1p5x"].items():
        print(f"    {k}: {v}")
    print(f"  all_pass: {res['all_pass']}")
