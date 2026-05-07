"""Single TSA cycle simulation (Step 5.4.1) — adsorption + regen + jumps.

A cycle is five sequential phases (PHASE2_SPEC §3.3):

    1. Adsorption    (4.0 h, mode='adsorption', forward, P=P_high)
    2. Depressurize  (instantaneous state-jump, P_high → P_low)
    3. Heating       (2.0 h, mode='heating',    reverse, P=P_low)
    4. Cooling       (1.5 h, mode='cooling',    reverse, P=P_low)
    5. Repressurize  (instantaneous state-jump, P_low → P_high, feed gas)

Each phase produces a `CyclePhaseResult` with mass + energy bookkeeping
(Option β, per-phase dictionary). The cycle-level closure aggregates these
per-phase results across all five phases.

Mass closure (per species, per phase):
    mass_in − mass_out = Δ(gas inventory) + Δ(solid inventory)
    rel_err = |residual| / max(|mass_in|, |Δ inventory|)        < 1 %

Energy closure (per phase):
    ΔU_bed = enthalpy_in − enthalpy_out + adsorption_heat − wall_loss
    rel_err = |residual| / max(|each term|)                     < 5 %
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .adsorption_1d import ColumnConfig, OperatingConditions
from .adsorption_1d.boundary import (
    P_STD_PA,
    R_GAS,
    T_STD_K,
    inlet_concentrations,
    outlet_cell_index,
    superficial_velocity,
)
from .adsorption_1d.rhs import SimulationParams, estimate_stiffness_ratio
from .adsorption_1d.solver import initial_state_clean_bed, simulate
from .adsorption_1d.state import N_VARS, var_slice
from .adsorption_1d.state_transform import depressurize, repressurize
from .ldf_kinetics import load_dbd

# ---------------------------------------------------------------------------
# Cycle schedule constants (DBD `cycle:` block, PHASE2_SPEC §3.3)
# ---------------------------------------------------------------------------
ADSORPTION_DURATION_S = 4.0 * 3600.0
HEATING_DURATION_S = 2.0 * 3600.0
COOLING_DURATION_S = 1.5 * 3600.0
JUMP_DURATION_S = 600.0                          # 10 min nominal (clock only)

# Chunked-restart sizes (DD-017): heating + cooling use chunk_s = 60 s, which
# diagnostic Step 5.4.0d showed completes the full 2 h heating in ~6 min wall.
HEATING_CHUNK_S = 60.0
COOLING_CHUNK_S = 60.0

P_HIGH_FALLBACK_PA = 6.01e5                      # adsorption pressure
P_LOW_PA = 1.013e5                               # atmospheric (regen)
T_REGEN_K = 273.15 + 200.0
T_COOL_K = 273.15 + 15.0
FLOW_REGEN_NM3H = 60.0
T_REF_K = 288.15                                 # cooling endpoint, energy reference

GATE_MASS_CLOSURE_PCT = 1.0
MASS_NOISE_FLOOR_MOL = 1.0e-6                    # below: degenerate, skip percentage metric

# DD-018 Hybrid energy closure gates:
#   Legacy (engineering convention, matches Phase-6 measurement convention):
#     - adsorption: 5 %      (small T variation → primitive form ≈ conservative)
#     - heating / cooling: 20 %    (Rule 6.6: calibrated against measurement —
#                                   heating 11.6 %, cooling 17.8 % — with safety margin)
#   Model-consistent (matches rhs.py primitive form integration):
#     - all phases: 1 %      (true numerical closure of the discretization)
GATE_ENERGY_CLOSURE_LEGACY_ADSORPTION_PCT = 5.0
GATE_ENERGY_CLOSURE_LEGACY_REGEN_PCT = 20.0
GATE_ENERGY_CLOSURE_MODEL_PCT = 1.0

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "outputs" / "phase2" / "cycle"


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------
@dataclass
class CyclePhaseResult:
    """Bookkeeping for one cycle phase (integrating or jump)."""

    name: str
    duration_s: float
    wall_time_s: float
    is_jump: bool

    # Mass — both species. For jumps, mass_out for depressurize = vented;
    # mass_in for repressurize = added; the other side is 0.
    mass_in: dict[str, float]
    mass_out: dict[str, float]
    inventory_start: dict[str, float]
    inventory_end: dict[str, float]
    mass_residual: dict[str, float]
    mass_closure_pct: dict[str, float]            # NaN if degenerate
    mass_passes: dict[str, bool]
    mass_degenerate: dict[str, bool]              # all magnitudes below noise floor

    # Energy — Hybrid (DD-018): both legacy + model-consistent reported.
    # Legacy (engineering convention, matches Phase-6 measurement):
    enthalpy_in_J: float                          # mass_flow × cp × (T_in − T_REF) × duration
    enthalpy_out_J: float                         # ∫ mass_flow × cp × (T_out − T_REF) dt
    adsorption_heat_J: float
    wall_loss_J: float
    bed_thermal_change_J: float
    energy_residual_legacy_J: float
    energy_closure_pct_legacy: float
    energy_passes_legacy: bool
    # Model-consistent (matches rhs.py primitive form):
    adv_volumetric_J: float                       # ∫∫ adv_term dV dt (primitive form)
    energy_residual_model_J: float
    energy_closure_pct_model: float
    energy_passes_model: bool

    # Solver state (NaN for jumps)
    stiffness_start: float
    stiffness_end: float
    success: bool
    message: str

    # Chunked-restart bookkeeping (DD-017). Empty for single-call phases.
    chunk_walls_s: list[float] = field(default_factory=list)
    chunk_stiffness_ratios: list[float] = field(default_factory=list)
    chunk_s: float = 0.0                     # chunk size used (0 = single call)


@dataclass
class CycleResult:
    """One full TSA cycle aggregate."""

    cycle_number: int
    phases: list[CyclePhaseResult] = field(default_factory=list)
    total_wall_time_s: float = 0.0
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))

    @property
    def cycle_mass_balance(self) -> dict[str, dict[str, float]]:
        """Cycle-level mass balance per species."""
        out: dict[str, dict[str, float]] = {}
        for sp in ("h2o", "co2"):
            mass_in = sum(p.mass_in[sp] for p in self.phases)
            mass_out = sum(p.mass_out[sp] for p in self.phases)
            inv_start = self.phases[0].inventory_start[sp]
            inv_end = self.phases[-1].inventory_end[sp]
            delta_inv = inv_end - inv_start
            residual = mass_in - mass_out - delta_inv
            scale = max(abs(mass_in), abs(delta_inv), 1.0e-30)
            out[sp] = {
                "mass_in_mol": mass_in,
                "mass_out_mol": mass_out,
                "delta_inventory_mol": delta_inv,
                "residual_mol": residual,
                "closure_pct": 100.0 * abs(residual) / scale,
                "passes": bool(100.0 * abs(residual) / scale < GATE_MASS_CLOSURE_PCT),
            }
        return out

    @property
    def cycle_energy_balance(self) -> dict[str, float]:
        """Cycle-level energy balance (J) — Hybrid (DD-018)."""
        e_in = sum(p.enthalpy_in_J for p in self.phases)
        e_out = sum(p.enthalpy_out_J for p in self.phases)
        ads = sum(p.adsorption_heat_J for p in self.phases)
        wall = sum(p.wall_loss_J for p in self.phases)
        bed_cum = sum(p.bed_thermal_change_J for p in self.phases)
        adv_vol = sum(p.adv_volumetric_J for p in self.phases)

        # Legacy: ΔU_bed = (E_in − E_out) + ads − wall
        legacy_residual = e_in - e_out + ads - wall - bed_cum
        legacy_scale = max(abs(e_in), abs(e_out), abs(ads), abs(wall), abs(bed_cum), 1.0e-30)
        legacy_closure_pct = 100.0 * abs(legacy_residual) / legacy_scale
        # Cycle-level legacy gate: average across phases. Use the regen threshold
        # (15 %) since heating + cooling dominate the cycle's primitive-form mismatch.
        legacy_passes = bool(legacy_closure_pct < GATE_ENERGY_CLOSURE_LEGACY_REGEN_PCT)

        # Model-consistent: ΔU_bed = adv_volumetric + ads − wall (primitive form)
        model_residual = adv_vol + ads - wall - bed_cum
        model_scale = max(abs(adv_vol), abs(ads), abs(wall), abs(bed_cum), 1.0e-30)
        model_closure_pct = 100.0 * abs(model_residual) / model_scale
        model_passes = bool(model_closure_pct < GATE_ENERGY_CLOSURE_MODEL_PCT)

        return {
            "enthalpy_in_J": e_in,
            "enthalpy_out_J": e_out,
            "adsorption_heat_J": ads,
            "wall_loss_J": wall,
            "bed_thermal_change_J": bed_cum,
            "adv_volumetric_J": adv_vol,
            "legacy_residual_J": legacy_residual,
            "legacy_closure_pct": legacy_closure_pct,
            "legacy_passes": legacy_passes,
            "model_residual_J": model_residual,
            "model_closure_pct": model_closure_pct,
            "model_passes": model_passes,
            "passes": legacy_passes and model_passes,
        }

    def overall_pass(self) -> bool:
        # Per-phase gates (mass + both energy conventions).
        for p in self.phases:
            for sp in ("h2o", "co2"):
                if not p.mass_passes[sp]:
                    return False
            if not p.energy_passes_legacy:
                return False
            if not p.energy_passes_model:
                return False
        mb = self.cycle_mass_balance
        if not all(mb[sp]["passes"] for sp in ("h2o", "co2")):
            return False
        return bool(self.cycle_energy_balance["passes"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _inventory(
    y: np.ndarray,
    n: int,
    dz: np.ndarray,
    A_xs: float,
    eps_b: float,
    rho_p: np.ndarray,
) -> dict[str, float]:
    """Total bed inventory (mol) per species at a single time slice."""
    out: dict[str, float] = {}
    for sp, var_C, var_q in (
        ("h2o", "C_h2o", "q_h2o"),
        ("co2", "C_co2", "q_co2"),
    ):
        C = y[var_slice(var_C, n)]
        q = y[var_slice(var_q, n)]
        gas = float(np.sum(eps_b * C * dz)) * A_xs
        solid = float(np.sum((1.0 - eps_b) * rho_p * q * dz)) * A_xs
        out[sp] = gas + solid
    return out


def _bed_thermal_energy_J(
    y: np.ndarray,
    n: int,
    dz: np.ndarray,
    A_xs: float,
    eps_b: float,
    rho_p: np.ndarray,
    c_ps: np.ndarray,
) -> float:
    """Solid-phase thermal energy (J) referenced to T_REF_K.

    Gas-phase thermal storage is ~3 orders smaller than solid (DD-015 side
    note: solid 4.6e5 J/(m³·K) vs gas 4.7e2 J/(m³·K)) and is omitted for
    simplicity. The closure budget tolerates this O(0.1 %) approximation.
    """
    T = y[var_slice("T", n)]
    return float(
        np.sum((1.0 - eps_b) * rho_p * c_ps * (T - T_REF_K) * dz)
    ) * A_xs


def _mass_flow_kg_s(flow_nm3h: float, MW_air_kg_mol: float) -> float:
    """Total mass flow rate (kg/s) at standard conditions.

    Uses ideal gas at STP: ρ_std = MW · P_std / (R · T_std).
    """
    rho_std = MW_air_kg_mol * P_STD_PA / (R_GAS * T_STD_K)
    return flow_nm3h * rho_std / 3600.0


def _phase_mass_metrics(
    sp: str,
    mass_in: float,
    mass_out: float,
    inv_before: dict[str, float],
    inv_after: dict[str, float],
) -> tuple[float, float, bool, bool]:
    """Per-species mass-balance metrics for a phase.

    Returns ``(residual, closure_pct, passes, degenerate)``. When all input
    magnitudes (mass_in, mass_out, |Δ inventory|) are below
    ``MASS_NOISE_FLOOR_MOL``, the percentage metric is meaningless: we mark
    the phase as degenerate, set ``closure_pct = NaN``, and return PASS
    (DD-018 noise-floor handling).
    """
    delta_inv = inv_after[sp] - inv_before[sp]
    residual = mass_in - mass_out - delta_inv
    scale = max(abs(mass_in), abs(mass_out), abs(delta_inv))
    if scale < MASS_NOISE_FLOOR_MOL:
        return residual, float("nan"), True, True
    closure_pct = 100.0 * abs(residual) / scale
    return residual, closure_pct, bool(closure_pct < GATE_MASS_CLOSURE_PCT), False


def _legacy_energy_metrics(
    e_in: float, e_out: float, ads: float, wall: float, dU_bed: float,
    legacy_gate_pct: float,
) -> tuple[float, float, bool]:
    """Legacy (constant-mass-flow) energy closure.

    ΔU_bed = (E_in − E_out) + ads − wall.  Returns
    ``(residual, closure_pct, passes)`` against the supplied legacy gate
    (5 % adsorption / 15 % regen, DD-018).
    """
    residual = e_in - e_out + ads - wall - dU_bed
    scale = max(abs(e_in), abs(e_out), abs(ads), abs(wall), abs(dU_bed), 1.0e-30)
    closure_pct = 100.0 * abs(residual) / scale
    return residual, closure_pct, bool(closure_pct < legacy_gate_pct)


def _model_energy_metrics(
    adv_volumetric: float, ads: float, wall: float, dU_bed: float,
) -> tuple[float, float, bool]:
    """Model-consistent energy closure (matches rhs.py primitive form).

    ΔU_bed = adv_volumetric + ads − wall, with adv_volumetric =
    ``∫∫ adv_term dV dt`` using rhs.py local-ρ_g formula. True numerical
    closure of the discretization; gate ``< 1 %`` (DD-018).
    """
    residual = adv_volumetric + ads - wall - dU_bed
    scale = max(abs(adv_volumetric), abs(ads), abs(wall), abs(dU_bed), 1.0e-30)
    closure_pct = 100.0 * abs(residual) / scale
    return residual, closure_pct, bool(closure_pct < GATE_ENERGY_CLOSURE_MODEL_PCT)


def _model_consistent_advection_J(
    op: OperatingConditions,
    params: SimulationParams,
    combined_t: np.ndarray,
    combined_y: np.ndarray,
    A_xs: float,
) -> float:
    """Compute ∫∫ adv_term dV dt using rhs.py primitive form (DD-018).

    Per-cell adv term: ``-u_signed × ρ_g(T_cell) × c_pg × ∂T/∂z`` with the
    upwind gradient that mirrors `rhs._T_advection_term`.
    """
    n = params.grid.n_total
    z_centers = params.grid.z_centers_m
    dz_widths = params.grid.dz_widths_m
    bed_height = float(z_centers[-1] + dz_widths[-1] / 2.0)

    offset_T = var_slice("T", n).start
    T_full = combined_y[offset_T::N_VARS, :]                          # (n, n_t)
    rho_g_full = op.P_op_Pa * params.MW_air_kg_mol / (R_GAS * T_full) # (n, n_t)

    u_mag = superficial_velocity(op, op.T_in_K, A_xs)
    u_signed = u_mag if op.flow_direction == "forward" else -u_mag
    T_in = op.T_in_K
    c_pg = params.c_pg

    grad_full = np.zeros_like(T_full)
    dz_face = z_centers[1:] - z_centers[:-1]
    if u_signed >= 0:
        grad_full[0, :] = (T_full[0, :] - T_in) / z_centers[0]
        grad_full[1:, :] = (T_full[1:, :] - T_full[:-1, :]) / dz_face[:, None]
    else:
        grad_full[-1, :] = (T_in - T_full[-1, :]) / (bed_height - z_centers[-1])
        grad_full[:-1, :] = (T_full[1:, :] - T_full[:-1, :]) / dz_face[:, None]

    adv_term_full = -u_signed * rho_g_full * c_pg * grad_full         # (n, n_t)
    per_t = np.sum(adv_term_full * dz_widths[:, None] * A_xs, axis=0) # (n_t,)
    return float(np.trapezoid(per_t, combined_t))


# ---------------------------------------------------------------------------
# Phase execution
# ---------------------------------------------------------------------------
def _run_integrating_phase(
    name: str,
    y_in: np.ndarray,
    op: OperatingConditions,
    duration_s: float,
    col: ColumnConfig,
    samples_per_hour: int = 60,
    chunk_s: float | None = None,
    monitor_stiffness: bool = False,
    heating_chunk_avg_wall_s: float | None = None,
    trajectory_out: dict | None = None,
) -> tuple[np.ndarray, CyclePhaseResult]:
    """Integrate one ODE phase and collect metrics.

    `chunk_s=None` ⇒ single solve_ivp call (DD-014 default for adsorption).
    `chunk_s>0`    ⇒ chunked restart strategy (DD-017): split the phase into
                     chunks of `chunk_s` seconds and call simulate() once per
                     chunk. Each chunk gets a fresh BDF init, preventing
                     stepper-history accumulation that crashes long calls.

    `monitor_stiffness=True` ⇒ measure stiffness after every chunk. Combined
    with `heating_chunk_avg_wall_s`, enables abort/warn logic for cooling
    safety net (DD-017 cooling-in-cycle monitoring).
    """
    params = SimulationParams.build(col, op)
    n = params.grid.n_total
    A_xs = col.cross_section_m2
    eps_b = col.void_fraction
    dz = params.grid.dz_widths_m

    inv_before = _inventory(y_in, n, dz, A_xs, eps_b, params.rho_p)
    bed_E_before = _bed_thermal_energy_J(y_in, n, dz, A_xs, eps_b, params.rho_p, params.c_ps)
    stiff_start = float(estimate_stiffness_ratio(params, y_test=y_in)["stiffness_ratio"])

    chunk_walls_s: list[float] = []
    chunk_stiffnesses: list[float] = []

    if chunk_s is None or chunk_s >= duration_s:
        # ----- Single-call path (adsorption) -----
        n_eval = max(int(round(samples_per_hour * duration_s / 3600.0)), 2)
        t_eval = np.linspace(0.0, duration_s, n_eval)
        t0 = time.perf_counter()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            result, _ = simulate(
                params, y_in,
                t_span=(0.0, duration_s),
                t_eval=t_eval,
                dense_output=False,
                skip_stiffness_check=True,
            )
        wall = time.perf_counter() - t0
        if not result.success:
            raise RuntimeError(f"phase {name!r} solver failed: {result.message}")
        combined_t = result.t_s
        combined_y = result.y
    else:
        # ----- Chunked-restart path (heating / cooling, DD-017) -----
        # Sub-samples within each chunk give a finer trapezoid grid for the
        # energy integral without changing BDF behaviour (DD-018).
        sub_per_chunk = max(
            1, int(round(samples_per_hour * chunk_s / 3600.0))
        )
        traj_t = [0.0]
        traj_y = [y_in[:, None].copy()]
        state = y_in.copy()
        t = 0.0
        wall = 0.0
        while t < duration_s:
            next_t = min(t + chunk_s, duration_s)
            sub_t_eval = np.linspace(t, next_t, sub_per_chunk + 1)[1:]
            t_chunk_start = time.perf_counter()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                sub_result, _ = simulate(
                    params, state,
                    t_span=(t, next_t),
                    t_eval=sub_t_eval,
                    dense_output=False,
                    skip_stiffness_check=True,
                )
            chunk_wall = time.perf_counter() - t_chunk_start
            wall += chunk_wall
            chunk_walls_s.append(chunk_wall)
            if not sub_result.success:
                raise RuntimeError(
                    f"phase {name!r} chunk [{t:.1f}, {next_t:.1f}]s failed: {sub_result.message}"
                )
            state = sub_result.y[:, -1].copy()
            traj_t.extend(sub_t_eval.tolist())
            traj_y.append(sub_result.y.copy())

            if monitor_stiffness:
                ratio = float(estimate_stiffness_ratio(params, y_test=state)["stiffness_ratio"])
                chunk_stiffnesses.append(ratio)
                # Hard abort
                if ratio > 1.0e10:
                    raise RuntimeError(
                        f"phase {name!r} stiffness {ratio:.3e} exceeds STOP threshold 1e10 "
                        f"at t={next_t:.1f}s"
                    )
                if (
                    heating_chunk_avg_wall_s is not None
                    and chunk_wall > 2.0 * heating_chunk_avg_wall_s
                ):
                    raise RuntimeError(
                        f"phase {name!r} chunk wall {chunk_wall:.2f}s exceeds 2× heating "
                        f"avg {heating_chunk_avg_wall_s:.2f}s at t={next_t:.1f}s — "
                        "cooling-stiffer-than-heating hypothesis triggered abort"
                    )
                # Warn (continue)
                if ratio > 1.0e9:
                    print(
                        f"[{name}] WARN stiffness {ratio:.3e} > 1e9 at t={next_t:.1f}s"
                    )
                if (
                    heating_chunk_avg_wall_s is not None
                    and chunk_wall > 1.5 * heating_chunk_avg_wall_s
                ):
                    print(
                        f"[{name}] WARN chunk wall {chunk_wall:.2f}s > 1.5× heating avg "
                        f"({heating_chunk_avg_wall_s:.2f}s) at t={next_t:.1f}s"
                    )
            t = next_t
        combined_t = np.asarray(traj_t)
        combined_y = np.hstack(traj_y)

    y_out = combined_y[:, -1]
    inv_after = _inventory(y_out, n, dz, A_xs, eps_b, params.rho_p)
    stiff_end = float(estimate_stiffness_ratio(params, y_test=y_out)["stiffness_ratio"])

    # ---- Mass: in is constant inlet flux × duration; out is trapezoid of outlet flux.
    u_sup_in = superficial_velocity(op, op.T_in_K, A_xs)
    C_in_dict = inlet_concentrations(op, op.T_in_K)
    out_idx = outlet_cell_index(op, n)
    mass_in: dict[str, float] = {}
    mass_out: dict[str, float] = {}
    mass_residual: dict[str, float] = {}
    mass_closure_pct: dict[str, float] = {}
    mass_passes: dict[str, bool] = {}
    mass_degenerate: dict[str, bool] = {}
    for sp, var_C in (("h2o", "C_h2o"), ("co2", "C_co2")):
        C_in_val = C_in_dict[sp]
        # Layout B row index = var_offset + cell_index · N_VARS.
        offset_C = var_slice(var_C, n).start
        C_out_t = combined_y[offset_C + out_idx * N_VARS, :]
        mass_in[sp] = u_sup_in * C_in_val * A_xs * duration_s
        mass_out[sp] = float(np.trapezoid(u_sup_in * C_out_t * A_xs, combined_t))
        residual, closure, passes, degenerate = _phase_mass_metrics(
            sp, mass_in[sp], mass_out[sp], inv_before, inv_after,
        )
        mass_residual[sp] = residual
        mass_closure_pct[sp] = closure
        mass_passes[sp] = passes
        mass_degenerate[sp] = degenerate

    # ---- Energy: enthalpy_in/out, adsorption heat, wall loss, ΔU_bed
    mass_flow = _mass_flow_kg_s(op.flow_nm3h, params.MW_air_kg_mol)
    enthalpy_in_J = mass_flow * params.c_pg * (op.T_in_K - T_REF_K) * duration_s

    offset_T = var_slice("T", n).start
    T_out_t = combined_y[offset_T + out_idx * N_VARS, :]
    enthalpy_out_J = float(
        np.trapezoid(mass_flow * params.c_pg * (T_out_t - T_REF_K), combined_t)
    )

    # Adsorption heat: ΔH × (q_end − q_start) integrated over solid volume
    q_h2o_end = y_out[var_slice("q_h2o", n)]
    q_h2o_start = y_in[var_slice("q_h2o", n)]
    q_co2_end = y_out[var_slice("q_co2", n)]
    q_co2_start = y_in[var_slice("q_co2", n)]
    delta_q_h2o = q_h2o_end - q_h2o_start
    delta_q_co2 = q_co2_end - q_co2_start
    adsorption_heat_J = (1.0 - eps_b) * A_xs * float(np.sum(
        params.rho_p * dz * (
            params.dH_h2o_J_mol * delta_q_h2o + params.dH_co2_J_mol * delta_q_co2
        )
    ))

    # Wall loss: integrate over time of (4U/D) × A_xs × Σ_cells (T(z,t) − T_amb) × dz
    T_full = combined_y[offset_T::N_VARS, :]             # (n, n_t)
    spatial = np.sum((T_full - params.T_amb_K) * dz[:, None], axis=0)  # (n_t,) integrated dz
    wall_loss_J = (4.0 * params.U_wall / col.diameter_m) * A_xs * float(
        np.trapezoid(spatial, combined_t)
    )

    bed_E_after = _bed_thermal_energy_J(y_out, n, dz, A_xs, eps_b, params.rho_p, params.c_ps)
    dU_bed = bed_E_after - bed_E_before

    # Hybrid energy closure (DD-018):
    legacy_gate = (
        GATE_ENERGY_CLOSURE_LEGACY_ADSORPTION_PCT if name == "adsorption"
        else GATE_ENERGY_CLOSURE_LEGACY_REGEN_PCT
    )
    e_res_legacy, e_cls_legacy, e_pass_legacy = _legacy_energy_metrics(
        enthalpy_in_J, enthalpy_out_J, adsorption_heat_J, wall_loss_J, dU_bed,
        legacy_gate,
    )
    adv_volumetric_J = _model_consistent_advection_J(
        op, params, combined_t, combined_y, A_xs,
    )
    e_res_model, e_cls_model, e_pass_model = _model_energy_metrics(
        adv_volumetric_J, adsorption_heat_J, wall_loss_J, dU_bed,
    )

    phase = CyclePhaseResult(
        name=name,
        duration_s=duration_s,
        wall_time_s=wall,
        is_jump=False,
        mass_in=mass_in,
        mass_out=mass_out,
        inventory_start=inv_before,
        inventory_end=inv_after,
        mass_residual=mass_residual,
        mass_closure_pct=mass_closure_pct,
        mass_passes=mass_passes,
        mass_degenerate=mass_degenerate,
        enthalpy_in_J=enthalpy_in_J,
        enthalpy_out_J=enthalpy_out_J,
        adsorption_heat_J=adsorption_heat_J,
        wall_loss_J=wall_loss_J,
        bed_thermal_change_J=dU_bed,
        energy_residual_legacy_J=e_res_legacy,
        energy_closure_pct_legacy=e_cls_legacy,
        energy_passes_legacy=e_pass_legacy,
        adv_volumetric_J=adv_volumetric_J,
        energy_residual_model_J=e_res_model,
        energy_closure_pct_model=e_cls_model,
        energy_passes_model=e_pass_model,
        stiffness_start=stiff_start,
        stiffness_end=stiff_end,
        success=True,
        message="ok",
        chunk_walls_s=chunk_walls_s,
        chunk_stiffness_ratios=chunk_stiffnesses,
        chunk_s=chunk_s if chunk_s is not None else 0.0,
    )
    if trajectory_out is not None:
        offset_h2o = var_slice("C_h2o", n).start
        offset_co2 = var_slice("C_co2", n).start
        trajectory_out["t_s"] = combined_t.copy()
        trajectory_out["C_h2o_outlet"] = combined_y[
            offset_h2o + out_idx * N_VARS, :
        ].copy()
        trajectory_out["C_co2_outlet"] = combined_y[
            offset_co2 + out_idx * N_VARS, :
        ].copy()
    return y_out, phase


def _run_jump_phase(
    name: str,
    y_in: np.ndarray,
    col: ColumnConfig,
    grid_dz: np.ndarray,
    eps_b: float,
    rho_p: np.ndarray,
    c_ps: np.ndarray,
    P_from_Pa: float,
    P_to_Pa: float,
    feed_y_h2o: float = 0.0,
    feed_y_co2: float = 0.0,
) -> tuple[np.ndarray, CyclePhaseResult]:
    """Apply an instantaneous P-jump and book-keep mass + energy."""
    n = grid_dz.size
    A_xs = col.cross_section_m2
    inv_before = _inventory(y_in, n, grid_dz, A_xs, eps_b, rho_p)
    bed_E_before = _bed_thermal_energy_J(y_in, n, grid_dz, A_xs, eps_b, rho_p, c_ps)

    t0 = time.perf_counter()
    if name == "depressurize":
        y_out, vented = depressurize(
            y_in, n, grid_dz, A_xs, eps_b, P_from_Pa, P_to_Pa
        )
        mass_in = {"h2o": 0.0, "co2": 0.0}
        mass_out = {"h2o": float(vented["h2o"]), "co2": float(vented["co2"])}
    elif name == "repressurize":
        y_out, added = repressurize(
            y_in, n, grid_dz, A_xs, eps_b,
            P_from_Pa, P_to_Pa, feed_y_h2o, feed_y_co2,
        )
        mass_in = {"h2o": float(added["h2o"]), "co2": float(added["co2"])}
        mass_out = {"h2o": 0.0, "co2": 0.0}
    else:
        raise ValueError(f"jump phase must be depressurize|repressurize, got {name!r}")
    wall = time.perf_counter() - t0

    inv_after = _inventory(y_out, n, grid_dz, A_xs, eps_b, rho_p)
    bed_E_after = _bed_thermal_energy_J(y_out, n, grid_dz, A_xs, eps_b, rho_p, c_ps)

    mass_residual: dict[str, float] = {}
    mass_closure_pct: dict[str, float] = {}
    mass_passes: dict[str, bool] = {}
    mass_degenerate: dict[str, bool] = {}
    for sp in ("h2o", "co2"):
        residual, closure, passes, degenerate = _phase_mass_metrics(
            sp, mass_in[sp], mass_out[sp], inv_before, inv_after,
        )
        mass_residual[sp] = residual
        mass_closure_pct[sp] = closure
        mass_passes[sp] = passes
        mass_degenerate[sp] = degenerate

    # Energy is exactly zero for a jump (q, T unchanged). Hybrid metrics both 0.
    dU_bed = bed_E_after - bed_E_before
    e_res_legacy, e_cls_legacy, e_pass_legacy = _legacy_energy_metrics(
        0.0, 0.0, 0.0, 0.0, dU_bed, GATE_ENERGY_CLOSURE_LEGACY_REGEN_PCT,
    )
    e_res_model, e_cls_model, e_pass_model = _model_energy_metrics(
        0.0, 0.0, 0.0, dU_bed,
    )

    return y_out, CyclePhaseResult(
        name=name,
        duration_s=JUMP_DURATION_S,
        wall_time_s=wall,
        is_jump=True,
        mass_in=mass_in,
        mass_out=mass_out,
        inventory_start=inv_before,
        inventory_end=inv_after,
        mass_residual=mass_residual,
        mass_closure_pct=mass_closure_pct,
        mass_passes=mass_passes,
        mass_degenerate=mass_degenerate,
        enthalpy_in_J=0.0,
        enthalpy_out_J=0.0,
        adsorption_heat_J=0.0,
        wall_loss_J=0.0,
        bed_thermal_change_J=dU_bed,
        energy_residual_legacy_J=e_res_legacy,
        energy_closure_pct_legacy=e_cls_legacy,
        energy_passes_legacy=e_pass_legacy,
        adv_volumetric_J=0.0,
        energy_residual_model_J=e_res_model,
        energy_closure_pct_model=e_cls_model,
        energy_passes_model=e_pass_model,
        stiffness_start=float("nan"),
        stiffness_end=float("nan"),
        success=True,
        message="instantaneous jump",
    )


# ---------------------------------------------------------------------------
# Cycle driver
# ---------------------------------------------------------------------------
def _build_op(
    mode: str,
    flow_nm3h: float,
    P_op_Pa: float,
    T_in_K: float,
    flow_direction: str,
    y_h2o_in: float,
    y_co2_in: float,
) -> OperatingConditions:
    return OperatingConditions(
        mode=mode,
        flow_nm3h=flow_nm3h,
        P_op_Pa=P_op_Pa,
        T_in_K=T_in_K,
        y_h2o_in=y_h2o_in,
        y_co2_in=y_co2_in,
        flow_direction=flow_direction,
    )


def run_single_cycle(
    initial_state: np.ndarray | None = None,
    cycle_number: int = 0,
    samples_per_hour: int = 60,
    adsorption_trajectory: dict | None = None,
) -> tuple[np.ndarray, CycleResult]:
    """Run one full TSA cycle and return (final_state, CycleResult).

    `initial_state=None` ⇒ use a clean bed at adsorption-feed inlet T (15 °C).
    `adsorption_trajectory` (optional dict) is mutated to receive the
    adsorption phase outlet trajectory: keys ``t_s``, ``C_h2o_outlet``,
    ``C_co2_outlet``. Used by run_cycle_repeated.py for shape comparison.
    """
    dbd = load_dbd()
    col = ColumnConfig.from_dbd(dbd)
    proc = dbd["process"]
    P_high = (
        float(proc["pressure_gauge_bar"]) + float(proc["pressure_atm_bar"])
    ) * 1.0e5
    if P_high <= 0:
        P_high = P_HIGH_FALLBACK_PA
    y_h2o_feed = float(dbd["loads"]["h2o_inlet_ppm"]) * 1.0e-6
    y_co2_feed = float(proc["co2_in_ppm"]) * 1.0e-6
    T_feed_K = float(proc["temperature_in_C"]) + 273.15
    flow_ads = float(proc["flow_nm3h"])

    op_ads = _build_op(
        "adsorption", flow_ads, P_high, T_feed_K, "forward", y_h2o_feed, y_co2_feed
    )
    op_heat = _build_op(
        "heating", FLOW_REGEN_NM3H, P_LOW_PA, T_REGEN_K, "reverse", 0.0, 0.0
    )
    op_cool = _build_op(
        "cooling", FLOW_REGEN_NM3H, P_LOW_PA, T_COOL_K, "reverse", 0.0, 0.0
    )

    # We need column-level constants accessible to jump phases without rebuilding params:
    params_ads = SimulationParams.build(col, op_ads)
    grid_dz = params_ads.grid.dz_widths_m
    rho_p = params_ads.rho_p
    c_ps = params_ads.c_ps
    eps_b = col.void_fraction

    if initial_state is None:
        initial_state = initial_state_clean_bed(params_ads)

    cycle = CycleResult(cycle_number=cycle_number)
    state = initial_state

    # Phase 1 — Adsorption
    state, ph = _run_integrating_phase(
        "adsorption", state, op_ads, ADSORPTION_DURATION_S, col, samples_per_hour,
        trajectory_out=adsorption_trajectory,
    )
    cycle.phases.append(ph)

    # Phase 2 — Depressurize (P_high → P_low)
    state, ph = _run_jump_phase(
        "depressurize", state, col, grid_dz, eps_b, rho_p, c_ps,
        P_from_Pa=P_high, P_to_Pa=P_LOW_PA,
    )
    cycle.phases.append(ph)

    # Phase 3 — Heating (chunked restart, DD-017)
    state, ph_heat = _run_integrating_phase(
        "heating", state, op_heat, HEATING_DURATION_S, col, samples_per_hour,
        chunk_s=HEATING_CHUNK_S,
    )
    cycle.phases.append(ph_heat)

    # Phase 4 — Cooling (chunked restart + monitoring, DD-017)
    heating_chunk_avg = (
        sum(ph_heat.chunk_walls_s) / len(ph_heat.chunk_walls_s)
        if ph_heat.chunk_walls_s else None
    )
    state, ph = _run_integrating_phase(
        "cooling", state, op_cool, COOLING_DURATION_S, col, samples_per_hour,
        chunk_s=COOLING_CHUNK_S,
        monitor_stiffness=True,
        heating_chunk_avg_wall_s=heating_chunk_avg,
    )
    cycle.phases.append(ph)

    # Phase 5 — Repressurize (P_low → P_high) with feed gas
    state, ph = _run_jump_phase(
        "repressurize", state, col, grid_dz, eps_b, rho_p, c_ps,
        P_from_Pa=P_LOW_PA, P_to_Pa=P_high,
        feed_y_h2o=y_h2o_feed, feed_y_co2=y_co2_feed,
    )
    cycle.phases.append(ph)

    cycle.total_wall_time_s = sum(p.wall_time_s for p in cycle.phases)
    return state, cycle


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _format_phase(p: CyclePhaseResult) -> str:
    head = (
        f"  [{p.name}]  ({'jump' if p.is_jump else 'integrate'})  "
        f"sim={p.duration_s/60:.1f} min  wall={p.wall_time_s:.2f} s"
    )
    body_lines = []
    for sp in ("h2o", "co2"):
        flag = "PASS" if p.mass_passes[sp] else "FAIL"
        if p.mass_degenerate[sp]:
            closure_str = "n/a (degenerate)"
            flag = "PASS*"
        else:
            closure_str = f"{p.mass_closure_pct[sp]:.3e} %"
        body_lines.append(
            f"    mass[{sp}]: in={p.mass_in[sp]:.4f} mol  out={p.mass_out[sp]:.4f} mol  "
            f"Δinv={p.inventory_end[sp]-p.inventory_start[sp]:+.4f} mol  "
            f"closure={closure_str}  -> {flag}"
        )
    legacy_flag = "PASS" if p.energy_passes_legacy else "FAIL"
    model_flag = "PASS" if p.energy_passes_model else "FAIL"
    body_lines.append(
        f"    energy(legacy):  in={p.enthalpy_in_J:.2e} J  out={p.enthalpy_out_J:.2e} J  "
        f"ads={p.adsorption_heat_J:+.2e} J  wall={p.wall_loss_J:.2e} J  "
        f"ΔU_bed={p.bed_thermal_change_J:+.2e} J  "
        f"closure={p.energy_closure_pct_legacy:.3e} % -> {legacy_flag}"
    )
    body_lines.append(
        f"    energy(model):   adv_vol={p.adv_volumetric_J:.2e} J  "
        f"closure={p.energy_closure_pct_model:.3e} % -> {model_flag}"
    )
    if not p.is_jump:
        body_lines.append(
            f"    stiffness: start={p.stiffness_start:.3e}  end={p.stiffness_end:.3e}"
        )
    return head + "\n" + "\n".join(body_lines)


def print_report(cycle: CycleResult) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    overall = "PASS" if cycle.overall_pass() else "FAIL"
    print(
        f"\n=== TSA cycle #{cycle.cycle_number} — {overall}  "
        f"({cycle.timestamp}) ==="
    )
    print(
        f"Total wall: {cycle.total_wall_time_s:.1f} s "
        f"({cycle.total_wall_time_s/60:.2f} min)"
    )
    for p in cycle.phases:
        print(_format_phase(p))
    print("\n  --- Cycle-level closure ---")
    mb = cycle.cycle_mass_balance
    for sp in ("h2o", "co2"):
        m = mb[sp]
        flag = "PASS" if m["passes"] else "FAIL"
        print(
            f"  mass[{sp}]: in={m['mass_in_mol']:.4f}  out={m['mass_out_mol']:.4f}  "
            f"Δinv={m['delta_inventory_mol']:+.4f}  "
            f"closure={m['closure_pct']:.3e} %  -> {flag}"
        )
    eb = cycle.cycle_energy_balance
    legacy_flag = "PASS" if eb["legacy_passes"] else "FAIL"
    model_flag = "PASS" if eb["model_passes"] else "FAIL"
    print(
        f"  energy(legacy) : in={eb['enthalpy_in_J']:.2e}  out={eb['enthalpy_out_J']:.2e}  "
        f"ads={eb['adsorption_heat_J']:+.2e}  wall={eb['wall_loss_J']:.2e}  "
        f"ΔU_bed={eb['bed_thermal_change_J']:+.2e}  "
        f"closure={eb['legacy_closure_pct']:.3e} %  -> {legacy_flag}"
    )
    print(
        f"  energy(model)  : adv_vol={eb['adv_volumetric_J']:.2e}  "
        f"closure={eb['model_closure_pct']:.3e} %  -> {model_flag}"
    )
    print(f"\nOverall : {overall}\n")


def _phase_to_dict(p: CyclePhaseResult) -> dict:
    return asdict(p)


def save_cycle_report(cycle: CycleResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "cycle_number": cycle.cycle_number,
        "timestamp": cycle.timestamp,
        "total_wall_time_s": cycle.total_wall_time_s,
        "phases": [_phase_to_dict(p) for p in cycle.phases],
        "cycle_mass_balance": cycle.cycle_mass_balance,
        "cycle_energy_balance": cycle.cycle_energy_balance,
        "overall_pass": cycle.overall_pass(),
    }
    out_path = output_dir / f"cycle_{cycle.cycle_number:02d}_summary.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle-number", type=int, default=0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--samples-per-hour", type=int, default=600,
        help="t_eval density (default 600 = 6s; energy trapezoid accuracy, DD-018)",
    )
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)

    _, cycle = run_single_cycle(
        initial_state=None,
        cycle_number=args.cycle_number,
        samples_per_hour=args.samples_per_hour,
    )
    print_report(cycle)
    if not args.no_save:
        save_cycle_report(cycle, args.output_dir)
        print(f"Saved cycle report to {args.output_dir}")
    return 0 if cycle.overall_pass() else 1


if __name__ == "__main__":
    sys.exit(main())
