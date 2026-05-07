"""ODE RHS for the 1D adsorption PDE — Method of Lines (Steps 3 + 4).

Mass balance (per unit total volume, written for component C and loading q):

    ε_b · ∂C/∂t + (1 − ε_b) · ρ_p · ∂q/∂t + ∂F_C/∂z = 0
    F_C = u_sup · C − D_ax · ∂C/∂z          (u_sup = superficial velocity)

LDF: ∂q/∂t = k_LDF · (q* − q)

Energy balance (Decision 3B; following Yang 1987 / Cavenati 2004):

    H_eff · ∂T/∂t = -u·ρ_g·c_pg·(∂T/∂z)_upwind
                    + ∂(λ_ax·∂T/∂z)/∂z
                    + (1−ε_b)·ρ_p · Σ_i |ΔH_i| · ∂q_i/∂t        (adsorption heat)
                    − (4·U/D)·(T − T_amb)                       (wall loss)
    H_eff = (1 − ε_b)·ρ_p·c_ps                                    (solid-dominant)

Sign convention (DD-009 + DD-012): ΔH stored as positive magnitude.

Spatial discretization — cell-centered finite volume:
  Mass:
    - Advective face flux: 1st-order upwind via sign(u_signed).
    - Dispersive face flux: central difference using cell-center spacing.
    - Inlet face: Danckwerts (advective only, F = u·C_in).
    - Outlet face: zero gradient (advective only, F = u·C_outlet).
  Energy:
    - Advective contribution: upwind in primitive form (PDE), boundary uses
      face-to-cell-center distance for the gradient.
    - Conductive flux: central difference, zero-flux at both boundaries.

The single function `rhs_full(t, y, params, isothermal)` handles both
isothermal (Step 3 mass-balance verification, ∂T/∂t = 0) and full
non-isothermal (Step 4) cases. Backwards-compatible `rhs_isothermal`
is a thin wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ..isotherms import load_isotherm_params
from ..ldf_kinetics import compute_ldf_for_adsorbent, load_config, load_dbd
from .boundary import (
    R_GAS,
    inlet_concentrations,
    inlet_temperature,
    signed_velocity,
    superficial_velocity,
)
from .config import LAYERS, ColumnConfig, OperatingConditions
from .grid import Grid, build_grid
from .state import pack_state, unpack_state


@dataclass
class SimulationParams:
    """Pre-computed per-cell arrays + bed-level constants for fast RHS evaluation.

    Built once per simulation, reused across all RHS calls. Energy-balance
    parameters are populated from the SSOT (DBD + adsorbent_properties.yaml)
    and used by the non-isothermal branch of `rhs_full`.
    """

    col: ColumnConfig
    op: OperatingConditions
    grid: Grid
    isotherm_params: dict[str, Any]

    # Mass-balance per-cell arrays
    rho_p: np.ndarray            # (N,) bulk density per cell, kg/m³
    k_ldf_h2o: np.ndarray        # (N,) 0 where H₂O does not adsorb, 1/s
    k_ldf_co2: np.ndarray        # (N,) 0 where CO₂ does not adsorb, 1/s
    D_ax: float                  # axial dispersion (m²/s) — Step 3 placeholder constant

    # Energy-balance per-cell + scalar parameters
    c_ps: np.ndarray             # (N,) solid heat capacity, J/(kg·K)
    c_pg: float                  # gas heat capacity, J/(kg·K)
    MW_air_kg_mol: float         # average gas molar mass, kg/mol
    lambda_ax: float             # axial thermal conductivity, W/(m·K)
    U_wall: float                # wall heat transfer coefficient, W/(m²·K)
    T_amb_K: float               # ambient temperature, K
    dH_h2o_J_mol: float          # |ΔH_ads,H2O|, J/mol (positive magnitude)
    dH_co2_J_mol: float          # |ΔH_ads,CO2|, J/mol (positive magnitude)

    # Stiffness thresholds (DD-012; from dbd.simulation.stiffness_thresholds)
    stiffness_warn: float = 1.0e8
    stiffness_stop: float = 1.0e10

    @classmethod
    def build(
        cls,
        col: ColumnConfig,
        op: OperatingConditions,
        adsorbent_yaml: dict[str, Any] | None = None,
        dbd: dict[str, Any] | None = None,
        D_ax: float = 1.0e-4,
    ) -> SimulationParams:
        """Construct from project SSOT YAMLs by default."""
        if adsorbent_yaml is None:
            adsorbent_yaml = load_isotherm_params()
        if dbd is None:
            dbd = load_dbd()

        # Single SSOT YAML: must contain mass_transfer; reload via load_config if not present.
        if "mass_transfer" not in adsorbent_yaml:
            adsorbent_yaml = load_config()

        grid = build_grid(col)
        n = grid.n_total
        rho_p = np.zeros(n)
        k_h2o = np.zeros(n)
        k_co2 = np.zeros(n)
        c_ps = np.zeros(n)

        for layer_idx, layer_name in enumerate(LAYERS):
            mask = grid.layer_ids == layer_idx
            ads_props = dbd["adsorbent_properties"][layer_name]
            rho_p[mask] = ads_props["bulk_density_kg_m3"]
            c_ps[mask] = ads_props["heat_capacity_kJ_kg_K"] * 1000.0   # kJ/(kg·K) → J/(kg·K)

            ldf = compute_ldf_for_adsorbent(layer_name, op.T_in_K, op.P_op_Pa, adsorbent_yaml)
            if op.adsorbs(layer_name, "h2o"):
                k_h2o[mask] = ldf["k_LDF_s_inv"]
            if op.adsorbs(layer_name, "co2"):
                k_co2[mask] = ldf["k_LDF_s_inv"]

        # Energy-balance scalars from DBD + adsorbent_properties heat_transfer block.
        gas_section = dbd["gas"]
        c_pg = gas_section["air_cp_kJ_kg_K"] * 1000.0                  # J/(kg·K)
        MW_air = gas_section["air_mw_kg_kmol"] * 1.0e-3                # kg/mol

        # ΔH stored in DBD as kJ/kg-adsorbate; convert to J/mol via MW_species.
        MW_h2o = gas_section["h2o_mw_kg_kmol"] * 1.0e-3                # kg/mol
        MW_co2 = gas_section["co2_mw_kg_kmol"] * 1.0e-3                # kg/mol
        dH_h2o_J_mol = (
            dbd["adsorbent_properties"]["alumina"]["heat_of_adsorption_kJ_kg_h2o"]
            * 1000.0 * MW_h2o
        )
        dH_co2_J_mol = (
            dbd["adsorbent_properties"]["zeolite_13x"]["heat_of_adsorption_kJ_kg_co2"]
            * 1000.0 * MW_co2
        )

        ht = adsorbent_yaml["heat_transfer"]
        lambda_ax = float(ht["axial_thermal_conductivity_W_m_K"])
        U_wall = float(ht["wall_heat_transfer_U_W_m2_K"])
        T_amb_K = float(ht["ambient_temperature_K"])

        # Stiffness thresholds — DD-012 calibrated from Step 4 measurement
        thr = dbd.get("simulation", {}).get("stiffness_thresholds", {})
        stiffness_warn = float(thr.get("warn_above", 1.0e8))
        stiffness_stop = float(thr.get("stop_above", 1.0e10))

        return cls(
            col=col,
            op=op,
            grid=grid,
            isotherm_params=adsorbent_yaml,
            rho_p=rho_p,
            k_ldf_h2o=k_h2o,
            k_ldf_co2=k_co2,
            D_ax=D_ax,
            c_ps=c_ps,
            c_pg=c_pg,
            MW_air_kg_mol=MW_air,
            lambda_ax=lambda_ax,
            U_wall=U_wall,
            T_amb_K=T_amb_K,
            dH_h2o_J_mol=dH_h2o_J_mol,
            dH_co2_J_mol=dH_co2_J_mol,
            stiffness_warn=stiffness_warn,
            stiffness_stop=stiffness_stop,
        )


# ---------------------------------------------------------------------------
# Vectorized isotherm helpers — accept scalar or array T (broadcasts).
# ---------------------------------------------------------------------------

def _q_star_toth_vec(
    P: np.ndarray, T: float | np.ndarray, params: dict[str, Any]
) -> np.ndarray:
    """Toth equation, vectorized over P (and T, if array)."""
    p = params["alumina_h2o_toth"]
    q_m0 = p["q_m0_mol_kg"]
    chi = p["chi_qm"]
    T_ref = p["T_ref_K"]
    b0 = p["b0_Pa_inv"]
    dH = p["delta_H_J_mol"]
    t = p["t_heterogeneity"]

    q_m = q_m0 * np.exp(chi * (1.0 - T / T_ref))
    b = b0 * np.exp(dH / (R_GAS * T_ref) * (T_ref / T - 1.0))
    bP = b * np.maximum(P, 0.0)
    q = q_m * bP / (1.0 + bP**t) ** (1.0 / t)
    return np.where(P > 0, q, 0.0)


def _q_star_langmuir_vec(
    P: np.ndarray, T: float | np.ndarray, params: dict[str, Any]
) -> np.ndarray:
    """Langmuir equation, vectorized over P (and T, if array)."""
    p = params["zeolite_13x_co2_langmuir"]
    q_m = p["q_m_mol_kg"]
    b0 = p["b0_Pa_inv"]
    dH = p["delta_H_J_mol"]

    b = b0 * np.exp(dH / (R_GAS * T))
    bP = b * np.maximum(P, 0.0)
    return q_m * bP / (1.0 + bP)


# ---------------------------------------------------------------------------
# Mass-balance face fluxes (Danckwerts inlet + zero-gradient outlet)
# ---------------------------------------------------------------------------

def _mass_face_fluxes(
    C: np.ndarray,
    C_in: float,
    u_signed: float,
    D_ax: float,
    z_centers: np.ndarray,
) -> np.ndarray:
    """Mass flux F_C = u·C − D·∂C/∂z at all N+1 faces (see module docstring)."""
    n = C.size
    F = np.zeros(n + 1)
    dz_face = z_centers[1:] - z_centers[:-1]

    if u_signed >= 0:
        F[0] = u_signed * C_in
        F[n] = u_signed * C[-1]
        F[1:n] = u_signed * C[:-1] - D_ax * (C[1:] - C[:-1]) / dz_face
    else:
        F[n] = u_signed * C_in
        F[0] = u_signed * C[0]
        F[1:n] = u_signed * C[1:] - D_ax * (C[1:] - C[:-1]) / dz_face
    return F


# ---------------------------------------------------------------------------
# Energy-balance helpers
# ---------------------------------------------------------------------------

def _T_advection_term(
    T: np.ndarray,
    T_in: float,
    u_signed: float,
    rho_g: np.ndarray,
    c_pg: float,
    z_centers: np.ndarray,
    bed_height: float,
) -> np.ndarray:
    """−u·ρ_g·c_pg·(∂T/∂z)_upwind per cell, primitive form (Yang 1987)."""
    n = T.size
    grad = np.zeros(n)

    if u_signed >= 0:
        grad[0] = (T[0] - T_in) / z_centers[0]
        grad[1:] = (T[1:] - T[:-1]) / (z_centers[1:] - z_centers[:-1])
    else:
        grad[-1] = (T_in - T[-1]) / (bed_height - z_centers[-1])
        # interior cells use forward diff (upwind from i+1 for u<0)
        grad[:-1] = (T[1:] - T[:-1]) / (z_centers[1:] - z_centers[:-1])
    return -u_signed * rho_g * c_pg * grad


def _T_conduction_face_flux(
    T: np.ndarray,
    lambda_ax: float,
    z_centers: np.ndarray,
) -> np.ndarray:
    """Conductive face flux F_q = -λ·∂T/∂z, zero at both boundaries (no-flux)."""
    n = T.size
    F = np.zeros(n + 1)
    dz_face = z_centers[1:] - z_centers[:-1]
    F[1:n] = -lambda_ax * (T[1:] - T[:-1]) / dz_face
    return F


# ---------------------------------------------------------------------------
# Single dispatchable RHS (handles isothermal/non-isothermal via flag)
# ---------------------------------------------------------------------------

def rhs_full(
    t: float,
    y: np.ndarray,
    params: SimulationParams,
    isothermal: bool = False,
) -> np.ndarray:
    """Compute dy/dt for the full 5N adsorption PDE system.

    Args:
        t: Time (s) — RHS is autonomous; mode is fixed in `params.op`.
        y: 5N state vector packed as (C_h2o, q_h2o, C_co2, q_co2, T).
        params: Pre-built SimulationParams.
        isothermal: If True, ∂T/∂t ≡ 0 and Toth/Langmuir evaluated at op.T_in_K.
            Used for Step 3 mass-balance verification and as a debug fallback
            if the non-isothermal branch produces non-physical results.

    Returns:
        dy/dt of length 5N.
    """
    grid = params.grid
    n = grid.n_total
    op = params.op
    col = params.col

    C_h2o, q_h2o, C_co2, q_co2, T = unpack_state(y, n)

    # In isothermal mode, Toth/Langmuir use op.T_in_K (fixed).
    T_eval = np.full(n, op.T_in_K) if isothermal else T

    # ---------------- Equilibrium loadings (only at adsorbing cells) ----------------
    q_star_h2o = np.zeros(n)
    q_star_co2 = np.zeros(n)
    h2o_mask = params.k_ldf_h2o > 0.0
    co2_mask = params.k_ldf_co2 > 0.0
    if h2o_mask.any():
        T_h2o = T_eval[h2o_mask]
        P_h2o = C_h2o[h2o_mask] * R_GAS * T_h2o
        q_star_h2o[h2o_mask] = _q_star_toth_vec(P_h2o, T_h2o, params.isotherm_params)
    if co2_mask.any():
        T_co2 = T_eval[co2_mask]
        P_co2 = C_co2[co2_mask] * R_GAS * T_co2
        q_star_co2[co2_mask] = _q_star_langmuir_vec(P_co2, T_co2, params.isotherm_params)

    dq_h2o_dt = params.k_ldf_h2o * (q_star_h2o - q_h2o)
    dq_co2_dt = params.k_ldf_co2 * (q_star_co2 - q_co2)

    # ---------------- Velocity + boundary inlet values ----------------
    A_xs = col.cross_section_m2
    T_inlet_for_u = op.T_in_K  # constant during a single mode under Decision 4A
    u_mag = superficial_velocity(op, T_inlet_for_u, A_xs)
    u_signed = signed_velocity(op, u_mag)
    C_in = inlet_concentrations(op, T_inlet_for_u)
    T_in = inlet_temperature(op)

    # ---------------- Gas-phase mass balance ----------------
    F_h2o = _mass_face_fluxes(C_h2o, C_in["h2o"], u_signed, params.D_ax, grid.z_centers_m)
    F_co2 = _mass_face_fluxes(C_co2, C_in["co2"], u_signed, params.D_ax, grid.z_centers_m)
    eps_b = col.void_fraction
    flux_div_h2o = (F_h2o[1:] - F_h2o[:-1]) / grid.dz_widths_m
    flux_div_co2 = (F_co2[1:] - F_co2[:-1]) / grid.dz_widths_m
    dC_h2o_dt = (-flux_div_h2o - (1.0 - eps_b) * params.rho_p * dq_h2o_dt) / eps_b
    dC_co2_dt = (-flux_div_co2 - (1.0 - eps_b) * params.rho_p * dq_co2_dt) / eps_b

    # ---------------- Energy balance ----------------
    if isothermal:
        dTdt = np.zeros(n)
    else:
        # Local gas density via ideal gas at constant P.
        rho_g = op.P_op_Pa * params.MW_air_kg_mol / (R_GAS * T)

        H_eff = (1.0 - eps_b) * params.rho_p * params.c_ps               # J/(m³·K)

        adv_term = _T_advection_term(                                    # J/(m³·s)
            T, T_in, u_signed, rho_g, params.c_pg, grid.z_centers_m, col.bed_height_m
        )
        F_T_cond = _T_conduction_face_flux(T, params.lambda_ax, grid.z_centers_m)
        cond_term = -(F_T_cond[1:] - F_T_cond[:-1]) / grid.dz_widths_m

        S_ads = (1.0 - eps_b) * params.rho_p * (
            params.dH_h2o_J_mol * dq_h2o_dt + params.dH_co2_J_mol * dq_co2_dt
        )
        S_wall = (4.0 * params.U_wall / col.diameter_m) * (T - params.T_amb_K)

        dTdt = (adv_term + cond_term + S_ads - S_wall) / H_eff

    return pack_state(dC_h2o_dt, dq_h2o_dt, dC_co2_dt, dq_co2_dt, dTdt)


def rhs_isothermal(t: float, y: np.ndarray, params: SimulationParams) -> np.ndarray:
    """Backwards-compatible thin wrapper around `rhs_full(..., isothermal=True)`.

    Used by Step 3 mass-balance verification tests.
    """
    return rhs_full(t, y, params, isothermal=True)


# ---------------------------------------------------------------------------
# Stiffness profiling — Jacobian eigenvalue spread (for Step 5 solver choice)
# ---------------------------------------------------------------------------

def estimate_stiffness_ratio(
    params: SimulationParams,
    y_test: np.ndarray | None = None,
) -> dict[str, Any]:
    """Estimate ODE stiffness via numerical Jacobian eigenvalue spread.

    Builds a forward-difference Jacobian J = ∂rhs/∂y at `y_test` and computes
    the spectrum. The stiffness ratio is the magnitude ratio of the largest
    to the smallest non-zero real-part eigenvalue.

    Solver-selection guidance:
      ratio < 100      → explicit (e.g. RK45) is sufficient.
      100 ≤ ratio < 1e4 → BDF without analytical Jacobian.
      1e4 ≤ ratio < 1e6 → BDF with analytical Jacobian recommended.
      ratio ≥ 1e6      → STOP — system likely unsuitable even for BDF; review
                         discretization or split the time integration.

    Args:
        params: SimulationParams (built once; energy fields populated).
        y_test: State at which to evaluate the Jacobian. Default = "wave just
            entered" — feed concentration injected at the inlet cell, T = T_in
            elsewhere. Captures realistic transient stiffness.

    Returns:
        Dict with eigenvalue stats, characteristic timescales, ratio, and
        a string `solver_recommendation` keyed off the bands above.
    """
    n = params.grid.n_total
    op = params.op

    if y_test is None:
        y_test = np.zeros(5 * n)
        # Feed concentrations injected at the inlet cell (forward) or outlet cell (reverse)
        from .boundary import inlet_cell_index
        idx = inlet_cell_index(op, n)
        C_in = inlet_concentrations(op, op.T_in_K)
        y_test[0 * n + idx] = C_in["h2o"]
        y_test[2 * n + idx] = C_in["co2"]
        # Uniform T at op.T_in_K (avoids 1/T → ∞ in ρ_g)
        y_test[4 * n : 5 * n] = op.T_in_K

    n_state = y_test.size

    # Central-difference Jacobian for ~O(eps²) accuracy. eps chosen as
    # cube-root of machine epsilon for central FD (≈6e-6 for double precision).
    base_eps = np.cbrt(np.finfo(float).eps)
    eps = base_eps * np.maximum(np.abs(y_test), 1.0)
    J = np.zeros((n_state, n_state))
    for j in range(n_state):
        y_plus = y_test.copy()
        y_minus = y_test.copy()
        y_plus[j] += eps[j]
        y_minus[j] -= eps[j]
        f_plus = rhs_full(0.0, y_plus, params, isothermal=False)
        f_minus = rhs_full(0.0, y_minus, params, isothermal=False)
        J[:, j] = (f_plus - f_minus) / (2.0 * eps[j])

    eigvals = np.linalg.eigvals(J)
    real_parts = np.real(eigvals)
    abs_real = np.abs(real_parts)
    max_real = float(np.max(abs_real)) if abs_real.size else 0.0

    # Filter numerical-zero eigenvalues: keep those bigger than max·1e-10.
    # This avoids spurious huge ratios when FD noise leaves a few |eig| ~ 1e-15
    # in an otherwise well-behaved spectrum.
    if max_real == 0.0:
        return {
            "stiffness_ratio": 1.0,
            "max_eigenvalue_abs": 0.0,
            "min_eigenvalue_abs": 0.0,
            "characteristic_timescale_fast_s": float("inf"),
            "characteristic_timescale_slow_s": float("inf"),
            "solver_recommendation": "explicit (RK45) - system is degenerate at y_test",
            "n_state": n_state,
        }
    rel_floor = max_real * 1.0e-10
    physical = abs_real >= rel_floor
    abs_phys = abs_real[physical]
    min_real = float(np.min(abs_phys))
    ratio = max_real / min_real

    # Bands per DD-012 / dbd_locked.yaml::simulation.stiffness_thresholds:
    #   ratio < warn:               OK   — standard BDF (no Jac) sufficient.
    #   warn ≤ ratio < stop:        WARN — BDF + analytical Jacobian mandatory.
    #   ratio ≥ stop:               ABORT — exceeds known solver capability.
    if ratio < params.stiffness_warn:
        rec = "OK - standard BDF (no analytical Jacobian) sufficient"
    elif ratio < params.stiffness_stop:
        rec = (
            "WARN - BDF + analytical Jacobian mandatory (sparse pattern recommended); "
            "stiffness within TSA literature range (Cavenati 2004, Casas 2013)"
        )
    else:
        rec = (
            f"ABORT - ratio exceeds STOP threshold {params.stiffness_stop:.1e}; "
            "review discretization or split time integration"
        )

    if ratio < params.stiffness_warn:
        band = "OK"
    elif ratio < params.stiffness_stop:
        band = "WARN"
    else:
        band = "ABORT"

    return {
        "max_eigenvalue_abs": max_real,
        "min_eigenvalue_abs": min_real,
        "stiffness_ratio": ratio,
        "characteristic_timescale_fast_s": 1.0 / max_real,
        "characteristic_timescale_slow_s": 1.0 / min_real,
        "solver_recommendation": rec,
        "band": band,
        "warn_threshold": params.stiffness_warn,
        "stop_threshold": params.stiffness_stop,
        "n_state": n_state,
    }
