"""Heating-step pre-flight (Step 5.4.0b) — verify long-time stability + stiffness regime.

Phase 2 unit tests cover heating mode at 60 s timescale only. Before integrating
heating into a full TSA cycle, we verify that a 1.5 h heating step:

  1. Runs to completion under the provisional `max_step = 0.01` (DD-014)
     without BDF Newton singularity (timeout 30 min wall as guard).
  2. Drives the bulk loading down by ≥ 50 % (desorption is actually working).
  3. Stiffness ratio measured at start / mid / end stays inside the WARN band
     (< 1e10 = STOP threshold).

Initial state (synthetic, design-point loaded):
    q_h2o[alumina cells] = q*_toth(P_h2o_design, T_ads)
    q_co2[13x cells]     = q*_langmuir(P_co2_design, T_ads)
    Other q = 0  (Decision 2A: AA only H2O, 13X only CO2)
    C = 0       (post-depressurization, dry purge cleared the void)
    T = 288.15 K (adsorption temperature, before heating starts)

Heating operating conditions (PHASE2_SPEC §3.3 + DBD `cycle:`):
    mode='heating', flow_nm3h=60, T_in=200°C=473.15 K,
    P_op=1.013 bar (atmospheric, post-depressurize),
    flow_direction='reverse', y_h2o=y_co2=0 (dry purge).

Exit behaviour:
    return code 0 if all three pre-flight gates pass; 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .adsorption_1d import ColumnConfig, OperatingConditions
from .adsorption_1d.rhs import (
    SimulationParams,
    estimate_stiffness_ratio,
)
from .adsorption_1d.solver import simulate
from .adsorption_1d.state import pack_state, var_slice
from .isotherms import langmuir_co2_13x, load_isotherm_params, toth_h2o_alumina
from .ldf_kinetics import load_dbd

# ---------------------------------------------------------------------------
# Pre-flight gate criteria
# ---------------------------------------------------------------------------
PREFLIGHT_DURATION_H = 1.5
WALL_TIMEOUT_S = 1800.0                  # 30 min hard guard
DESORPTION_REDUCTION_THRESHOLD_PCT = 50.0
STIFFNESS_STOP_THRESHOLD = 1.0e10        # mirrors DD-012 STOP band

P_REGEN_PA = 1.013e5                     # atmospheric (post-depressurize)
T_REGEN_K = 273.15 + 200.0
T_ADS_K = 273.15 + 15.0
FLOW_REGEN_NM3H = 60.0


@dataclass
class StiffnessSample:
    label: str
    t_s: float
    ratio: float
    band: str


@dataclass
class PreflightReport:
    duration_h: float
    wall_time_s: float
    timeout_hit: bool
    n_steps: int
    avg_ms_per_step: float
    success: bool
    solver_message: str
    stiffness_samples: list[StiffnessSample]
    avg_q_h2o_initial_mol_kg: float
    avg_q_h2o_final_mol_kg: float
    avg_q_co2_initial_mol_kg: float
    avg_q_co2_final_mol_kg: float
    desorption_h2o_reduction_pct: float
    desorption_co2_reduction_pct: float
    desorption_passes: bool
    duration_passes: bool
    stiffness_passes: bool

    def overall_pass(self) -> bool:
        return self.duration_passes and self.desorption_passes and self.stiffness_passes


def _build_initial_loaded_state(
    n: int,
    alumina_mask: np.ndarray,
    P_op_ads_Pa: float,
    y_h2o: float,
    y_co2: float,
    T_ads_K: float,
) -> np.ndarray:
    """Synthesize a uniformly loaded post-adsorption / post-depress state.

    Each layer is loaded to its design-point equilibrium loading; off-layer
    species (Decision 2A) are zero. C is zero (depressurized, dry purge ready).
    """
    iso = load_isotherm_params()
    P_h2o = y_h2o * P_op_ads_Pa
    P_co2 = y_co2 * P_op_ads_Pa
    q_h2o_eq = toth_h2o_alumina(P_h2o, T_ads_K, iso)
    q_co2_eq = langmuir_co2_13x(P_co2, T_ads_K, iso)

    q_h2o = np.zeros(n)
    q_co2 = np.zeros(n)
    q_h2o[alumina_mask] = q_h2o_eq
    q_co2[~alumina_mask] = q_co2_eq
    C_h2o = np.zeros(n)
    C_co2 = np.zeros(n)
    T = np.full(n, T_ads_K)
    return pack_state(C_h2o, q_h2o, C_co2, q_co2, T)


def _avg_loading_over_mask(
    y: np.ndarray, n: int, mask: np.ndarray, var_name: str
) -> float:
    """Mean per-cell loading (mol/kg) over `mask` cells."""
    arr = y[var_slice(var_name, n)]
    if not mask.any():
        return float("nan")
    return float(np.mean(arr[mask]))


def run_preflight() -> PreflightReport:
    """Execute the heating pre-flight and return a structured gate report."""
    dbd = load_dbd()
    col = ColumnConfig.from_dbd(dbd)
    proc = dbd["process"]

    P_op_ads = (
        float(proc["pressure_gauge_bar"]) + float(proc["pressure_atm_bar"])
    ) * 1.0e5
    y_h2o_ads = float(dbd["loads"]["h2o_inlet_ppm"]) * 1.0e-6
    y_co2_ads = float(proc["co2_in_ppm"]) * 1.0e-6

    op_heat = OperatingConditions(
        mode="heating",
        flow_nm3h=FLOW_REGEN_NM3H,
        P_op_Pa=P_REGEN_PA,
        T_in_K=T_REGEN_K,
        y_h2o_in=0.0,
        y_co2_in=0.0,
        flow_direction="reverse",
    )
    params = SimulationParams.build(col, op_heat)

    n = params.grid.n_total
    y0 = _build_initial_loaded_state(
        n, params.grid.alumina_mask, P_op_ads, y_h2o_ads, y_co2_ads, T_ADS_K
    )

    avg_q_h2o_init = _avg_loading_over_mask(
        y0, n, params.grid.alumina_mask, "q_h2o"
    )
    avg_q_co2_init = _avg_loading_over_mask(
        y0, n, params.grid.thirteen_x_mask, "q_co2"
    )

    # Sample stiffness at start.
    info_start = estimate_stiffness_ratio(params, y_test=y0)
    samples = [
        StiffnessSample(
            label="start",
            t_s=0.0,
            ratio=float(info_start["stiffness_ratio"]),
            band=str(info_start["band"]),
        )
    ]

    duration_s = PREFLIGHT_DURATION_H * 3600.0
    t_start = time.perf_counter()
    timeout_hit = False
    success = False
    solver_message = "(not run)"
    n_steps = 0
    avg_ms_per_step = float("nan")
    y_final = y0.copy()
    t_eval = np.linspace(0.0, duration_s, 91)        # 1 sample / min

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            result, metrics = simulate(
                params, y0,
                t_span=(0.0, duration_s),
                t_eval=t_eval,
                dense_output=False,
                skip_stiffness_check=True,
            )
        n_steps = metrics.n_steps
        avg_ms_per_step = metrics.avg_ms_per_step
        success = bool(result.success)
        solver_message = str(result.message)
        if success:
            y_final = result.y[:, -1]
            mid_idx = result.t_s.size // 2
            y_mid = result.y[:, mid_idx]
            info_mid = estimate_stiffness_ratio(params, y_test=y_mid)
            samples.append(
                StiffnessSample(
                    label="mid",
                    t_s=float(result.t_s[mid_idx]),
                    ratio=float(info_mid["stiffness_ratio"]),
                    band=str(info_mid["band"]),
                )
            )
            info_end = estimate_stiffness_ratio(params, y_test=y_final)
            samples.append(
                StiffnessSample(
                    label="end",
                    t_s=float(result.t_s[-1]),
                    ratio=float(info_end["stiffness_ratio"]),
                    band=str(info_end["band"]),
                )
            )
    finally:
        wall = time.perf_counter() - t_start
        if wall >= WALL_TIMEOUT_S:
            timeout_hit = True

    avg_q_h2o_final = _avg_loading_over_mask(
        y_final, n, params.grid.alumina_mask, "q_h2o"
    )
    avg_q_co2_final = _avg_loading_over_mask(
        y_final, n, params.grid.thirteen_x_mask, "q_co2"
    )

    def _reduction_pct(init: float, final: float) -> float:
        if init <= 0:
            return float("nan")
        return 100.0 * (init - final) / init

    red_h2o = _reduction_pct(avg_q_h2o_init, avg_q_h2o_final)
    red_co2 = _reduction_pct(avg_q_co2_init, avg_q_co2_final)

    duration_passes = bool(success and not timeout_hit)
    desorption_passes = bool(
        np.isfinite(red_h2o)
        and np.isfinite(red_co2)
        and red_h2o >= DESORPTION_REDUCTION_THRESHOLD_PCT
        and red_co2 >= DESORPTION_REDUCTION_THRESHOLD_PCT
    )
    stiffness_passes = bool(
        all(s.ratio < STIFFNESS_STOP_THRESHOLD for s in samples)
    )

    return PreflightReport(
        duration_h=PREFLIGHT_DURATION_H,
        wall_time_s=wall,
        timeout_hit=timeout_hit,
        n_steps=n_steps,
        avg_ms_per_step=avg_ms_per_step,
        success=success,
        solver_message=solver_message,
        stiffness_samples=samples,
        avg_q_h2o_initial_mol_kg=avg_q_h2o_init,
        avg_q_h2o_final_mol_kg=avg_q_h2o_final,
        avg_q_co2_initial_mol_kg=avg_q_co2_init,
        avg_q_co2_final_mol_kg=avg_q_co2_final,
        desorption_h2o_reduction_pct=red_h2o,
        desorption_co2_reduction_pct=red_co2,
        desorption_passes=desorption_passes,
        duration_passes=duration_passes,
        stiffness_passes=stiffness_passes,
    )


def print_report(r: PreflightReport) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    overall = "PASS" if r.overall_pass() else "FAIL"
    print(f"\n=== Heating pre-flight (Step 5.4.0b) — {overall} ===")
    print(
        f"Duration : {r.duration_h:.2f} h  ·  wall {r.wall_time_s:.1f} s"
        f"  ·  steps {r.n_steps}  ·  avg {r.avg_ms_per_step:.2f} ms/step"
    )
    print(
        f"Solver   : success={r.success}  timeout_hit={r.timeout_hit}  "
        f"msg={r.solver_message!r}"
    )
    print("Stiffness samples:")
    for s in r.stiffness_samples:
        print(
            f"  {s.label:>5s} @ t={s.t_s:8.1f}s : ratio={s.ratio:.3e}  band={s.band}"
        )
    print(
        f"Loading H2O (avg over alumina): {r.avg_q_h2o_initial_mol_kg:.4f} -> "
        f"{r.avg_q_h2o_final_mol_kg:.4f} mol/kg  "
        f"({r.desorption_h2o_reduction_pct:.1f}% reduction)"
    )
    print(
        f"Loading CO2 (avg over 13X)    : {r.avg_q_co2_initial_mol_kg:.4f} -> "
        f"{r.avg_q_co2_final_mol_kg:.4f} mol/kg  "
        f"({r.desorption_co2_reduction_pct:.1f}% reduction)"
    )
    print(
        f"Gates    : duration={r.duration_passes}  "
        f"desorption(>{DESORPTION_REDUCTION_THRESHOLD_PCT:.0f}%)={r.desorption_passes}  "
        f"stiffness(<{STIFFNESS_STOP_THRESHOLD:.0e})={r.stiffness_passes}"
    )
    print(f"Overall  : {overall}\n")


def save_report(r: PreflightReport, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    data = asdict(r)
    data["stiffness_samples"] = [asdict(s) for s in r.stiffness_samples]
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "outputs" / "phase2" / "preflight" / "heating_preflight.json",
        help="Where to save the structured gate report (JSON).",
    )
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)
    report = run_preflight()
    print_report(report)
    if not args.no_save:
        save_report(report, args.output_json)
        print(f"Saved: {args.output_json}")
    return 0 if report.overall_pass() else 1


if __name__ == "__main__":
    sys.exit(main())
