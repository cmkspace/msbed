"""Tests for the isothermal RHS (Step 3) — mass balance verification."""

from __future__ import annotations

import math

import numpy as np
import pytest

from phase2_simulation.adsorption_1d import (
    ColumnConfig,
    OperatingConditions,
    pack_state,
    unpack_state,
)
from phase2_simulation.adsorption_1d.boundary import (
    R_GAS,
    inlet_concentrations,
    signed_velocity,
    superficial_velocity,
)
from phase2_simulation.adsorption_1d.rhs import (
    SimulationParams,
    _q_star_langmuir_vec,
    _q_star_toth_vec,
    estimate_stiffness_ratio,
    rhs_full,
    rhs_isothermal,
)
from phase2_simulation.ldf_kinetics import load_dbd

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
# SimulationParams shape + per-layer values
# ---------------------------------------------------------------------------

def test_simulation_params_shapes(params: SimulationParams) -> None:
    n = params.grid.n_total
    assert params.rho_p.shape == (n,)
    assert params.k_ldf_h2o.shape == (n,)
    assert params.k_ldf_co2.shape == (n,)


def test_simulation_params_layer_density(params: SimulationParams) -> None:
    """Bulk density: 800 kg/m³ in alumina cells, 660 in 13X cells (DBD §4)."""
    aa = params.grid.alumina_mask
    zx = params.grid.thirteen_x_mask
    assert np.allclose(params.rho_p[aa], 800.0)
    assert np.allclose(params.rho_p[zx], 660.0)


def test_simulation_params_decision_2a(params: SimulationParams) -> None:
    """Decision 2A: AA cells adsorb only H₂O, 13X cells only CO₂."""
    aa = params.grid.alumina_mask
    zx = params.grid.thirteen_x_mask
    # H₂O adsorbs in alumina, not in 13X
    assert np.all(params.k_ldf_h2o[aa] > 0)
    assert np.all(params.k_ldf_h2o[zx] == 0)
    # CO₂ adsorbs in 13X, not in alumina
    assert np.all(params.k_ldf_co2[aa] == 0)
    assert np.all(params.k_ldf_co2[zx] > 0)


def test_simulation_params_k_ldf_within_rule6_range(params: SimulationParams) -> None:
    """The k_LDF values stored per layer match Rule 6 ranges from DD-010."""
    aa_k = params.k_ldf_h2o[params.grid.alumina_mask][0]
    zx_k = params.k_ldf_co2[params.grid.thirteen_x_mask][0]
    assert 0.001 <= aa_k <= 1.0, f"AA k_LDF={aa_k}"
    assert 0.005 <= zx_k <= 5.0, f"13X k_LDF={zx_k}"


# ---------------------------------------------------------------------------
# Vectorized isotherm sanity vs scalar reference
# ---------------------------------------------------------------------------

def test_toth_vec_matches_scalar(params: SimulationParams) -> None:
    from phase2_simulation.isotherms import toth_h2o_alumina

    Ps = np.array([0.0, 100.0, 1697.0, 5000.0])
    T = 298.15
    expected = np.array([toth_h2o_alumina(p, T, params.isotherm_params) for p in Ps])
    got = _q_star_toth_vec(Ps, T, params.isotherm_params)
    np.testing.assert_allclose(got, expected, rtol=1e-12, atol=1e-15)


def test_langmuir_vec_matches_scalar(params: SimulationParams) -> None:
    from phase2_simulation.isotherms import langmuir_co2_13x

    Ps = np.array([0.0, 100.0, 240.0, 1.0e5])
    T = 298.15
    expected = np.array([langmuir_co2_13x(p, T, params.isotherm_params) for p in Ps])
    got = _q_star_langmuir_vec(Ps, T, params.isotherm_params)
    np.testing.assert_allclose(got, expected, rtol=1e-12, atol=1e-15)


# ---------------------------------------------------------------------------
# RHS structural properties
# ---------------------------------------------------------------------------

def test_rhs_T_unchanged(params: SimulationParams) -> None:
    """Isothermal RHS keeps dT/dt ≡ 0 regardless of state."""
    n = params.grid.n_total
    rng = np.random.default_rng(seed=2)
    y = rng.uniform(size=5 * n)
    dydt = rhs_isothermal(0.0, y, params)
    _, _, _, _, dTdt = unpack_state(dydt, n)
    assert np.all(dTdt == 0.0)


def test_rhs_zero_state_only_inlet_cell_advances(params: SimulationParams) -> None:
    """Zero initial state with inlet flow → only the inlet cell sees ∂C/∂t > 0 at t=0+."""
    n = params.grid.n_total
    y0 = np.zeros(5 * n)
    dydt = rhs_isothermal(0.0, y0, params)
    dC_h2o, dq_h2o, dC_co2, dq_co2, dTdt = unpack_state(dydt, n)

    # Forward flow → inlet at cell 0
    assert dC_h2o[0] > 0
    assert dC_co2[0] > 0
    # All other interior cells start with dC=0 (advective wave hasn't reached them)
    np.testing.assert_array_equal(dC_h2o[1:], 0.0)
    np.testing.assert_array_equal(dC_co2[1:], 0.0)
    # Solid loadings stay flat at t=0+ since q*=0 (P=0 at all cells initially)
    np.testing.assert_array_equal(dq_h2o, 0.0)
    np.testing.assert_array_equal(dq_co2, 0.0)


def test_rhs_no_co2_uptake_in_alumina_cells(params: SimulationParams) -> None:
    """Decision 2A: dq_co2/dt must be zero in alumina cells regardless of state."""
    n = params.grid.n_total
    rng = np.random.default_rng(seed=3)
    y = rng.uniform(size=5 * n) * 1.0e-3
    # Force a non-trivial loading in the q_co2 slice
    y[3 * n : 4 * n] = 0.5 * np.ones(n)
    dydt = rhs_isothermal(0.0, y, params)
    _, _, _, dq_co2, _ = unpack_state(dydt, n)
    aa = params.grid.alumina_mask
    np.testing.assert_array_equal(dq_co2[aa], 0.0)


def test_rhs_no_h2o_uptake_in_13x_cells(params: SimulationParams) -> None:
    """Decision 2A: dq_h2o/dt must be zero in 13X cells regardless of state."""
    n = params.grid.n_total
    rng = np.random.default_rng(seed=4)
    y = rng.uniform(size=5 * n) * 1.0e-3
    y[1 * n : 2 * n] = 0.5 * np.ones(n)  # nontrivial q_h2o everywhere
    dydt = rhs_isothermal(0.0, y, params)
    _, dq_h2o, _, _, _ = unpack_state(dydt, n)
    zx = params.grid.thirteen_x_mask
    np.testing.assert_array_equal(dq_h2o[zx], 0.0)


# ---------------------------------------------------------------------------
# Global mass balance — the key Step 3 verification
# ---------------------------------------------------------------------------

def _global_balance_residual(
    params: SimulationParams, y: np.ndarray, species: str
) -> tuple[float, float]:
    """Return (LHS, RHS) of the integrated mass balance for `species` ('h2o' or 'co2').

    LHS = ∫ [ε·∂C/∂t + (1−ε)·ρ_p·∂q/∂t] dz over the bed length
    RHS = F_in − F_out  (boundary fluxes per total cross-section)

    For an exact FV discretization these must agree to machine precision.
    """
    n = params.grid.n_total
    op = params.op
    eps_b = params.col.void_fraction
    rho_p = params.rho_p

    dydt = rhs_isothermal(0.0, y, params)
    dC_h2o, dq_h2o, dC_co2, dq_co2, _ = unpack_state(dydt, n)

    if species == "h2o":
        dC, dq, C_idx = dC_h2o, dq_h2o, slice(0, n)
    else:
        dC, dq, C_idx = dC_co2, dq_co2, slice(2 * n, 3 * n)

    lhs = np.sum((eps_b * dC + (1.0 - eps_b) * rho_p * dq) * params.grid.dz_widths_m)

    # Boundary fluxes from same physics as RHS implementation
    A_xs = params.col.cross_section_m2
    u_mag = superficial_velocity(op, op.T_in_K, A_xs)
    u_signed = signed_velocity(op, u_mag)
    C_in = inlet_concentrations(op, op.T_in_K)
    C_arr = y[C_idx]

    if u_signed >= 0:
        F_in = u_signed * C_in[species]
        F_out = u_signed * C_arr[-1]   # zero-gradient outlet → F = u·C_outlet
    else:
        F_in = -u_signed * C_in[species]      # magnitude entering at z=L
        F_out = -u_signed * C_arr[0]          # magnitude exiting at z=0

    rhs_val = F_in - F_out
    return float(lhs), float(rhs_val)


def test_mass_balance_zero_state(params: SimulationParams) -> None:
    """Mass balance closes at the zero-state (clean bed)."""
    n = params.grid.n_total
    y0 = np.zeros(5 * n)
    for species in ("h2o", "co2"):
        lhs, rhs = _global_balance_residual(params, y0, species)
        # Both should equal u·C_in (mass entering, all stored in cell 0)
        assert math.isclose(lhs, rhs, rel_tol=1.0e-10), (
            f"{species}: LHS={lhs} ≠ RHS={rhs}"
        )
        assert lhs > 0


def test_mass_balance_loaded_state(params: SimulationParams) -> None:
    """Mass balance closes for an arbitrary, partially-loaded state."""
    n = params.grid.n_total
    rng = np.random.default_rng(seed=5)
    # Random partial loadings, but positive concentrations
    C_h2o = rng.uniform(0, 0.1, size=n)
    q_h2o = rng.uniform(0, 1.0, size=n)
    C_co2 = rng.uniform(0, 0.05, size=n)
    q_co2 = rng.uniform(0, 1.0, size=n)
    T = np.full(n, params.op.T_in_K)
    y = pack_state(C_h2o, q_h2o, C_co2, q_co2, T)
    for species in ("h2o", "co2"):
        lhs, rhs = _global_balance_residual(params, y, species)
        # FV scheme is mass-conservative to machine precision
        assert math.isclose(lhs, rhs, rel_tol=1.0e-9, abs_tol=1.0e-12), (
            f"{species}: LHS={lhs} ≠ RHS={rhs}, residual={lhs - rhs}"
        )


# ---------------------------------------------------------------------------
# Step 4 — non-isothermal RHS: structural + global energy balance
# ---------------------------------------------------------------------------

def test_isothermal_flag_zero_dT(params: SimulationParams) -> None:
    """`isothermal=True` forces dT/dt ≡ 0 even on a non-trivial state."""
    n = params.grid.n_total
    rng = np.random.default_rng(seed=7)
    # Realistic state: nonzero loadings, T near design
    C_h2o = rng.uniform(0, 0.5, n)
    q_h2o = rng.uniform(0, 1.0, n)
    C_co2 = rng.uniform(0, 0.1, n)
    q_co2 = rng.uniform(0, 0.5, n)
    T = np.full(n, params.op.T_in_K)
    y = pack_state(C_h2o, q_h2o, C_co2, q_co2, T)
    dydt = rhs_full(0.0, y, params, isothermal=True)
    _, _, _, _, dTdt = unpack_state(dydt, n)
    assert np.all(dTdt == 0.0)


def test_uniform_T_at_ambient_no_flow_state() -> None:
    """T = T_amb uniform, q = C = 0, no flow gradient → dT/dt ≈ 0 from wall+adv terms."""
    col = ColumnConfig.from_dbd(load_dbd())
    op_t_at_amb = OperatingConditions(
        mode="adsorption",
        flow_nm3h=200.0,
        P_op_Pa=6.01325e5,
        T_in_K=298.15,                    # set inlet T = T_amb so adv-grad = 0
        y_h2o_in=0.0,                      # no feed → no S_ads
        y_co2_in=0.0,
        flow_direction="forward",
    )
    p = SimulationParams.build(col, op_t_at_amb, D_ax=1.0e-4)
    n = p.grid.n_total
    y = np.zeros(5 * n)
    y[4 * n : 5 * n] = p.T_amb_K
    dydt = rhs_full(0.0, y, p, isothermal=False)
    _, _, _, _, dTdt = unpack_state(dydt, n)
    # T = T_amb, no feed, no gradient → all source terms vanish
    np.testing.assert_allclose(dTdt, 0.0, atol=1.0e-10)


def test_wall_heat_loss_only_cools_bed() -> None:
    """Hot uniform bed (T = 400 K) with T_in = 400 K and no feed → S_wall cools every cell."""
    col = ColumnConfig.from_dbd(load_dbd())
    op_hot = OperatingConditions(
        mode="cooling",
        flow_nm3h=60.0,
        P_op_Pa=6.01325e5,
        T_in_K=400.0,                     # match bed T → no advective gradient
        y_h2o_in=0.0,                      # no S_ads
        y_co2_in=0.0,
        flow_direction="reverse",
    )
    p = SimulationParams.build(col, op_hot, D_ax=1.0e-4)
    n = p.grid.n_total
    y = np.zeros(5 * n)
    y[4 * n : 5 * n] = 400.0
    dydt = rhs_full(0.0, y, p, isothermal=False)
    _, _, _, _, dTdt = unpack_state(dydt, n)
    # Every cell cools (S_wall > 0 since T > T_amb)
    assert np.all(dTdt < 0)
    # Magnitude check at a single cell:
    expected = -(4.0 * p.U_wall / col.diameter_m) * (400.0 - p.T_amb_K)
    expected_dTdt = expected / ((1.0 - col.void_fraction) * p.rho_p[0] * p.c_ps[0])
    assert math.isclose(dTdt[0], expected_dTdt, rel_tol=1.0e-10)


def test_advection_cools_inlet_when_feed_colder() -> None:
    """Hot bed (T = 400 K) with cold feed (T_in = 288 K) → cell 0 cools strongly via advection."""
    col = ColumnConfig.from_dbd(load_dbd())
    op_cold_feed = OperatingConditions(
        mode="adsorption",
        flow_nm3h=200.0,
        P_op_Pa=6.01325e5,
        T_in_K=288.15,
        y_h2o_in=0.0,                      # no feed gas → isolate advection effect
        y_co2_in=0.0,
        flow_direction="forward",
    )
    p = SimulationParams.build(col, op_cold_feed, D_ax=1.0e-4)
    n = p.grid.n_total
    y = np.zeros(5 * n)
    y[4 * n : 5 * n] = 400.0
    dydt = rhs_full(0.0, y, p, isothermal=False)
    _, _, _, _, dTdt = unpack_state(dydt, n)
    # Advection wave cools cell 0 (only cell where T-gradient is non-zero in this state)
    assert dTdt[0] < 0
    # Cells 1..N-1 have uniform T = 400 → no advection gradient there.
    # Their dT comes from the wall term (T=400 > T_amb=298 → cooling), so still negative.
    assert np.all(dTdt < 0)
    # Cell 0 should cool faster than the rest (advection + wall vs wall only).
    assert dTdt[0] < dTdt[10]


def test_adsorption_heat_release_warms_inlet_cell() -> None:
    """Feed at cell 0, T = T_in everywhere, T_in = T_amb → S_ads must dominate at cell 0."""
    col = ColumnConfig.from_dbd(load_dbd())
    # Pick T_in = T_amb so the wall and adv terms are both zero at the initial state.
    op_neutral = OperatingConditions(
        mode="adsorption",
        flow_nm3h=200.0,
        P_op_Pa=6.01325e5,
        T_in_K=298.15,
        y_h2o_in=2823.07e-6,
        y_co2_in=400e-6,
        flow_direction="forward",
    )
    p = SimulationParams.build(col, op_neutral, D_ax=1.0e-4)
    n = p.grid.n_total
    # Inject feed concentration at cell 0; rest of bed clean
    from phase2_simulation.adsorption_1d.boundary import inlet_concentrations as _ic
    C_in = _ic(op_neutral, op_neutral.T_in_K)
    y = np.zeros(5 * n)
    y[0] = C_in["h2o"]
    y[2 * n] = C_in["co2"]
    y[4 * n : 5 * n] = op_neutral.T_in_K        # = T_amb
    dydt = rhs_full(0.0, y, p, isothermal=False)
    _, dq_h2o, _, _, dTdt = unpack_state(dydt, n)
    # H2O adsorbs in alumina cell 0 with positive driving force
    assert dq_h2o[0] > 0
    # T_in == T_amb cancels wall + advection; only S_ads remains at cell 0.
    assert dTdt[0] > 0
    # Other cells: q* = 0 (C = 0), T = T_amb so wall = 0, no inflow → dT ≈ 0
    np.testing.assert_allclose(dTdt[1:], 0.0, atol=1.0e-9)


def test_global_energy_balance_uniform_state() -> None:
    """Uniform T + zero loading: dT/dt closes against S_wall exactly (machine precision)."""
    col = ColumnConfig.from_dbd(load_dbd())
    op = OperatingConditions(
        mode="adsorption",
        flow_nm3h=200.0,
        P_op_Pa=6.01325e5,
        T_in_K=350.0,                    # uniform bed T, also = T_inlet (no adv grad)
        y_h2o_in=0.0,                     # no feed → no S_ads
        y_co2_in=0.0,
        flow_direction="forward",
    )
    p = SimulationParams.build(col, op, D_ax=1.0e-4)
    n = p.grid.n_total
    y = np.zeros(5 * n)
    y[4 * n : 5 * n] = 350.0
    dydt = rhs_full(0.0, y, p, isothermal=False)
    _, _, _, _, dTdt = unpack_state(dydt, n)

    # Discrete enthalpy change rate per unit area:
    H_eff = (1.0 - col.void_fraction) * p.rho_p * p.c_ps
    enthalpy_change = np.sum(H_eff * dTdt * p.grid.dz_widths_m)
    # Wall loss integral:
    S_wall_total = np.sum(
        (4.0 * p.U_wall / col.diameter_m) * (350.0 - p.T_amb_K) * p.grid.dz_widths_m
    )
    # Closure: enthalpy_change = -S_wall_total (cooling drains enthalpy)
    rel_residual = abs(enthalpy_change + S_wall_total) / abs(S_wall_total)
    assert rel_residual < 1.0e-12, (
        f"Energy closure violated: enthalpy_change={enthalpy_change:.3e}, "
        f"S_wall_total={S_wall_total:.3e}, rel_residual={rel_residual:.3e}"
    )


def test_global_energy_balance_full_active_state(params: SimulationParams) -> None:
    """At a partially-loaded design state, energy closure < 1e-3 (DD-012 STOP threshold)."""
    n = params.grid.n_total
    op = params.op
    col = params.col
    rng = np.random.default_rng(seed=11)
    # Realistic state: bed 50% loaded, slight T elevation
    C_h2o = rng.uniform(0, 0.3, n)
    q_h2o = rng.uniform(0, 0.5, n)
    C_co2 = rng.uniform(0, 0.05, n)
    q_co2 = rng.uniform(0, 0.3, n)
    T = np.full(n, op.T_in_K + 5.0)             # 5 K hotter than feed
    y = pack_state(C_h2o, q_h2o, C_co2, q_co2, T)

    dydt = rhs_full(0.0, y, params, isothermal=False)
    dC_h2o, dq_h2o, dC_co2, dq_co2, dTdt = unpack_state(dydt, n)

    H_eff = (1.0 - col.void_fraction) * params.rho_p * params.c_ps
    enthalpy_change = np.sum(H_eff * dTdt * params.grid.dz_widths_m)

    # Sources / sinks
    S_ads_total = np.sum(
        (1.0 - col.void_fraction) * params.rho_p * (
            params.dH_h2o_J_mol * dq_h2o + params.dH_co2_J_mol * dq_co2
        ) * params.grid.dz_widths_m
    )
    S_wall_total = np.sum(
        (4.0 * params.U_wall / col.diameter_m) * (T - params.T_amb_K) * params.grid.dz_widths_m
    )

    # Advective net contribution: integrate the upwind primitive form over the bed.
    A_xs = col.cross_section_m2
    u_mag = superficial_velocity(op, op.T_in_K, A_xs)
    u_signed = signed_velocity(op, u_mag)
    rho_g = op.P_op_Pa * params.MW_air_kg_mol / (R_GAS * T)
    if u_signed >= 0:
        grad = np.zeros(n)
        grad[0] = (T[0] - op.T_in_K) / params.grid.z_centers_m[0]
        grad[1:] = (T[1:] - T[:-1]) / (params.grid.z_centers_m[1:] - params.grid.z_centers_m[:-1])
    else:
        grad = np.zeros(n)
        grad[-1] = (op.T_in_K - T[-1]) / (col.bed_height_m - params.grid.z_centers_m[-1])
        grad[:-1] = (T[1:] - T[:-1]) / (params.grid.z_centers_m[1:] - params.grid.z_centers_m[:-1])
    adv_total = np.sum(-u_signed * rho_g * params.c_pg * grad * params.grid.dz_widths_m)

    # Conduction integrated with zero-flux BCs telescopes to zero contribution.
    expected = adv_total + S_ads_total - S_wall_total
    scale = max(abs(expected), abs(enthalpy_change), 1.0)
    rel_residual = abs(enthalpy_change - expected) / scale
    assert rel_residual < 1.0e-3, (
        f"Energy closure rel_residual={rel_residual:.3e} exceeds DD-012 STOP "
        f"threshold (1e-3). enthalpy_change={enthalpy_change:.3e}, "
        f"expected={expected:.3e}"
    )


# ---------------------------------------------------------------------------
# Stiffness profiling — informational measurement
# ---------------------------------------------------------------------------

def test_stiffness_at_design_point(params: SimulationParams) -> None:
    """Measure + log stiffness at the design point.

    PASS if ratio < stop_threshold (1e10 per DD-012). Bands:
      OK    (< warn=1e8) — standard BDF works
      WARN  (warn..stop) — BDF + analytical Jacobian mandatory (Step 5 spec)
      ABORT (≥ stop=1e10) — exceeds known solver capability

    The expected band for our system is WARN (1e7~1e8) per Cavenati 2004 and
    Casas 2013 TSA simulation literature.
    """
    info = estimate_stiffness_ratio(params)
    print(
        f"\n[STIFFNESS] ratio = {info['stiffness_ratio']:.3e}  band = {info['band']}\n"
        f"  fast timescale = {info['characteristic_timescale_fast_s']:.3e} s\n"
        f"  slow timescale = {info['characteristic_timescale_slow_s']:.3e} s\n"
        f"  thresholds: warn={info['warn_threshold']:.1e}, stop={info['stop_threshold']:.1e}\n"
        f"  recommendation: {info['solver_recommendation']}\n"
    )
    assert info["band"] != "ABORT", (
        f"Stiffness ratio {info['stiffness_ratio']:.3e} exceeds STOP threshold "
        f"{info['stop_threshold']:.1e}. {info['solver_recommendation']}"
    )


def test_mass_balance_reverse_flow() -> None:
    """Mass balance closes for reverse flow (regen mode), inlet at z=L."""
    col = ColumnConfig.from_dbd(load_dbd())
    op_regen = OperatingConditions(
        mode="heating",
        flow_nm3h=60.0,
        P_op_Pa=6.01325e5,
        T_in_K=473.15,
        y_h2o_in=0.0,                 # dry purge
        y_co2_in=0.0,
        flow_direction="reverse",
    )
    p = SimulationParams.build(col, op_regen, D_ax=1.0e-4)
    n = p.grid.n_total
    rng = np.random.default_rng(seed=6)
    # Pre-loaded state (typical end-of-adsorption initial for regen)
    C_h2o = rng.uniform(0, 0.1, size=n)
    q_h2o = rng.uniform(0, 1.0, size=n)
    C_co2 = rng.uniform(0, 0.05, size=n)
    q_co2 = rng.uniform(0, 1.0, size=n)
    T = np.full(n, op_regen.T_in_K)
    y = pack_state(C_h2o, q_h2o, C_co2, q_co2, T)
    for species in ("h2o", "co2"):
        lhs, rhs = _global_balance_residual(p, y, species)
        assert math.isclose(lhs, rhs, rel_tol=1.0e-9, abs_tol=1.0e-12), (
            f"reverse {species}: LHS={lhs} ≠ RHS={rhs}, residual={lhs - rhs}"
        )
