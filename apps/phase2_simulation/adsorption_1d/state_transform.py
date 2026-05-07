"""Instantaneous well-mixed pressure transforms between cycle modes (Decision 1).

Depressurization and repressurization are not solved by the ODE; they are
state jumps applied at mode boundaries (DD-014; PHASE2_SPEC §3.3). Both use
a well-mixed, per-cell-independent model — the simplest defensible
approximation that conserves mass to machine precision and keeps `q` and
`T` invariant (instantaneous, no heat exchange).

Depressurize (P_high → P_low):
    C_new = C_old · (P_low / P_high)        # all gas-phase species
    q_new = q_old                           # adsorbed phase frozen
    T_new = T_old
    vented_mol[species] = Σ_cells (C_old − C_new) · ε · dz · A_xs

Repressurize (P_low → P_high) with feed gas at composition `y_feed`:
    ΔC[species] = y_feed[species] · (P_high − P_low) / (R · T_local)
    C_new = C_old + ΔC                      # well-mixed addition
    q_new = q_old, T_new = T_old
    added_mol[species] = Σ_cells ΔC · ε · dz · A_xs

Conservation: depressurize · repressurize is a closed-form roundtrip for
`q` and `T` (both unchanged), and the C-recovery is exact when the feed
composition matches the original gas composition.
"""

from __future__ import annotations

import numpy as np

from .state import N_VARS, var_slice

R_GAS = 8.314462618  # J/(mol·K), CODATA


def depressurize(
    y: np.ndarray,
    n_grid: int,
    dz_widths_m: np.ndarray,
    cross_section_m2: float,
    void_fraction: float,
    P_high_Pa: float,
    P_low_Pa: float,
) -> tuple[np.ndarray, dict[str, float]]:
    """Apply instantaneous well-mixed depressurization to the state vector.

    Args:
        y: State vector (5·n_grid,) in Layout B.
        n_grid: Total grid cell count N.
        dz_widths_m: Per-cell width (N,), m.
        cross_section_m2: Empty column cross-section A_xs, m².
        void_fraction: Inter-particle voidage ε_b (-).
        P_high_Pa: Pressure before depressurize (Pa).
        P_low_Pa: Pressure after depressurize (Pa).

    Returns:
        (y_new, vented_mol) where `vented_mol = {'h2o': mol, 'co2': mol}`
        is the gas vented through the low-side end during depressurization.

    Raises:
        ValueError: If pressures are non-positive or P_low > P_high.
    """
    if P_high_Pa <= 0 or P_low_Pa <= 0:
        raise ValueError(
            f"P_high_Pa and P_low_Pa must be > 0; got {P_high_Pa}, {P_low_Pa}"
        )
    if P_low_Pa > P_high_Pa:
        raise ValueError(
            f"P_low_Pa ({P_low_Pa}) must be ≤ P_high_Pa ({P_high_Pa})"
        )
    if y.size != N_VARS * n_grid:
        raise ValueError(f"y size {y.size} ≠ 5·n_grid = {N_VARS * n_grid}")
    if dz_widths_m.size != n_grid:
        raise ValueError(f"dz_widths_m size {dz_widths_m.size} ≠ n_grid {n_grid}")

    ratio = P_low_Pa / P_high_Pa
    y_new = y.copy()
    sl_h2o = var_slice("C_h2o", n_grid)
    sl_co2 = var_slice("C_co2", n_grid)
    C_h2o_old = y[sl_h2o].copy()
    C_co2_old = y[sl_co2].copy()
    y_new[sl_h2o] = C_h2o_old * ratio
    y_new[sl_co2] = C_co2_old * ratio
    # q and T already preserved by the .copy(); intentional no-op for clarity.

    cell_void_vol = void_fraction * dz_widths_m * cross_section_m2
    vented_h2o = float(np.sum((C_h2o_old - y_new[sl_h2o]) * cell_void_vol))
    vented_co2 = float(np.sum((C_co2_old - y_new[sl_co2]) * cell_void_vol))
    return y_new, {"h2o": vented_h2o, "co2": vented_co2}


def repressurize(
    y: np.ndarray,
    n_grid: int,
    dz_widths_m: np.ndarray,
    cross_section_m2: float,
    void_fraction: float,
    P_low_Pa: float,
    P_high_Pa: float,
    y_h2o_feed: float,
    y_co2_feed: float,
) -> tuple[np.ndarray, dict[str, float]]:
    """Apply instantaneous well-mixed repressurization with feed-composition gas.

    Per-cell new concentrations use the local cell temperature (T may vary
    along the bed at the moment of repressurization).

    Args:
        y: State vector (5·n_grid,) in Layout B at P_low.
        n_grid: Total grid cell count N.
        dz_widths_m: Per-cell width (N,), m.
        cross_section_m2: Empty column cross-section A_xs, m².
        void_fraction: Inter-particle voidage ε_b (-).
        P_low_Pa: Pressure before repressurize (Pa).
        P_high_Pa: Pressure after repressurize (Pa).
        y_h2o_feed, y_co2_feed: Feed-gas mole fractions (-).

    Returns:
        (y_new, added_mol) where `added_mol = {'h2o': mol, 'co2': mol}`
        is the gas added from the feed line during repressurization.

    Raises:
        ValueError: On bad input (non-positive pressures, P_high < P_low,
            negative or summing-to->1 mole fractions, malformed y).
    """
    if P_high_Pa <= 0 or P_low_Pa <= 0:
        raise ValueError(
            f"P_high_Pa and P_low_Pa must be > 0; got {P_high_Pa}, {P_low_Pa}"
        )
    if P_high_Pa < P_low_Pa:
        raise ValueError(
            f"P_high_Pa ({P_high_Pa}) must be ≥ P_low_Pa ({P_low_Pa})"
        )
    if y.size != N_VARS * n_grid:
        raise ValueError(f"y size {y.size} ≠ 5·n_grid = {N_VARS * n_grid}")
    if dz_widths_m.size != n_grid:
        raise ValueError(f"dz_widths_m size {dz_widths_m.size} ≠ n_grid {n_grid}")
    if y_h2o_feed < 0 or y_co2_feed < 0:
        raise ValueError(
            f"feed mole fractions must be ≥ 0; got h2o={y_h2o_feed}, co2={y_co2_feed}"
        )
    if y_h2o_feed + y_co2_feed > 1.0 + 1.0e-9:
        raise ValueError(
            f"y_h2o_feed + y_co2_feed = {y_h2o_feed + y_co2_feed} must not exceed 1"
        )

    sl_T = var_slice("T", n_grid)
    sl_h2o = var_slice("C_h2o", n_grid)
    sl_co2 = var_slice("C_co2", n_grid)
    T_arr = y[sl_T]
    if np.any(T_arr <= 0):
        raise ValueError("T contains non-positive values; check state vector")

    delta_P = P_high_Pa - P_low_Pa
    delta_C_h2o = y_h2o_feed * delta_P / (R_GAS * T_arr)
    delta_C_co2 = y_co2_feed * delta_P / (R_GAS * T_arr)

    y_new = y.copy()
    y_new[sl_h2o] = y[sl_h2o] + delta_C_h2o
    y_new[sl_co2] = y[sl_co2] + delta_C_co2
    # q and T already preserved by the .copy().

    cell_void_vol = void_fraction * dz_widths_m * cross_section_m2
    added_h2o = float(np.sum(delta_C_h2o * cell_void_vol))
    added_co2 = float(np.sum(delta_C_co2 * cell_void_vol))
    return y_new, {"h2o": added_h2o, "co2": added_co2}
