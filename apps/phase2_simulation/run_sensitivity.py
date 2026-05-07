"""27-case sensitivity matrix execution (Step 5.5).

Sweeps three operational variables at three levels each (3 × 3 × 3 = 27 cases):
  - GHSV factor (× DBD design flow)        : 0.5, 1.0, 1.5
  - Regeneration peak temperature (°C)     : 150, 180, 200
  - Cycle time (= adsorption duration, h)  : 3.0, 4.0, 5.0

For each case, runs cycles **adaptively** until 1-pair stabilization is
detected (≥ 3 cycles minimum, ≤ 5 cycles maximum, DD-019 metrics with
relaxed gates). The MEASUREMENT cycle is the one whose stabilization check
passes (typically cycle 2 or 3). Per-case results are written to
``outputs/phase2/sensitivity/results.csv`` after every case completion so
intermediate progress is durable.

Cycle-time → regen-time mapping (DD-020):
  3.0 h cycle: heating 1.5 h, cooling 1.0 h, buffer 0.5 h
  4.0 h cycle: heating 2.0 h, cooling 1.5 h, buffer 0.5 h
  5.0 h cycle: heating 2.0 h, cooling 2.0 h, buffer 1.0 h
Heating duration is held ≥ 1.5 h to keep regen efficient at short cycles.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .adsorption_1d import ColumnConfig
from .adsorption_1d.boundary import (
    P_STD_PA,
    R_GAS,
    T_STD_K,
)
from .adsorption_1d.grid import build_grid
from .ldf_kinetics import load_dbd
from .run_cycle import CycleResult, run_single_cycle
from .run_cycle_repeated import (
    CycleSummary,
    _summary_from_cycle,
    is_stabilized,
)

# ---------------------------------------------------------------------------
# Sweep configuration
# ---------------------------------------------------------------------------
GHSV_LEVELS: tuple[float, ...] = (0.5, 1.0, 1.5)
T_REGEN_LEVELS_C: tuple[float, ...] = (150.0, 180.0, 200.0)
CYCLE_TIME_LEVELS_H: tuple[float, ...] = (3.0, 4.0, 5.0)

MIN_CYCLES_BEFORE_CHECK = 3                    # at least 3 cycles before stabilization check
MAX_CYCLES_PER_CASE = 5
DEFAULT_SAMPLES_PER_HOUR = 600

MW_H2O_KG_MOL = 0.018015
MW_CO2_KG_MOL = 0.044010

H2O_BREAKTHROUGH_THRESHOLD_FRAC = 0.05         # mirror Step 5.3a definition

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "outputs" / "phase2" / "sensitivity"


# ---------------------------------------------------------------------------
# Case configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CaseConfig:
    """One sensitivity case (1..27)."""

    case_id: int
    ghsv_factor: float
    regen_temp_C: float
    cycle_time_h: float
    dbd_default_flow_nm3h: float

    @property
    def adsorption_flow_nm3h(self) -> float:
        return self.ghsv_factor * self.dbd_default_flow_nm3h

    @property
    def regen_T_K(self) -> float:
        return 273.15 + self.regen_temp_C

    @property
    def adsorption_duration_s(self) -> float:
        return self.cycle_time_h * 3600.0

    @property
    def heating_duration_s(self) -> float:
        # Hold heating ≥ 1.5 h for regen efficiency (DD-020 design choice).
        if self.cycle_time_h <= 3.0:
            return 1.5 * 3600.0
        return 2.0 * 3600.0

    @property
    def cooling_duration_s(self) -> float:
        if self.cycle_time_h <= 3.0:
            return 1.0 * 3600.0
        if self.cycle_time_h <= 4.0:
            return 1.5 * 3600.0
        return 2.0 * 3600.0

    def to_overrides(self) -> dict:
        """Pass-through dict for `run_single_cycle(case_overrides=…)`."""
        return {
            "adsorption_flow_nm3h": self.adsorption_flow_nm3h,
            "adsorption_duration_s": self.adsorption_duration_s,
            "heating_duration_s": self.heating_duration_s,
            "cooling_duration_s": self.cooling_duration_s,
            "regen_T_K": self.regen_T_K,
        }


def generate_27_cases(dbd_default_flow_nm3h: float = 200.0) -> list[CaseConfig]:
    """Build the 3×3×3 = 27 sensitivity case grid."""
    cases: list[CaseConfig] = []
    cid = 1
    for g in GHSV_LEVELS:
        for T in T_REGEN_LEVELS_C:
            for t in CYCLE_TIME_LEVELS_H:
                cases.append(
                    CaseConfig(
                        case_id=cid, ghsv_factor=g, regen_temp_C=T, cycle_time_h=t,
                        dbd_default_flow_nm3h=dbd_default_flow_nm3h,
                    )
                )
                cid += 1
    return cases


# ---------------------------------------------------------------------------
# Case result
# ---------------------------------------------------------------------------
@dataclass
class CaseResult:
    """Aggregate output for a single sensitivity case."""

    case_id: int
    ghsv_factor: float
    regen_temp_C: float
    cycle_time_h: float

    # Adaptive stabilization
    num_cycles_executed: int
    stabilization_status: str          # 'stable' | 'unstable'
    measurement_cycle_idx: int         # which cycle was used for outputs

    # Working capacity (per cycle, kg)
    working_capacity_h2o_kg: float
    working_capacity_co2_kg: float

    # Outlet purity at end of adsorption
    outlet_h2o_ppm_at_end: float
    outlet_co2_ppm_at_end: float
    breakthrough_time_h2o_5pct_h: float    # NaN if not reached

    # Energy
    regen_energy_kJ_per_kg_co2: float
    cycle_energy_balance_legacy_pct: float
    cycle_energy_balance_model_pct: float

    # Cycle dynamics
    max_bed_T_during_heating_C: float       # T_regen (proxy; full bed-T not stored)
    residual_q_h2o_kg_per_kg: float
    residual_q_co2_kg_per_kg: float

    # Solver health
    max_stiffness_during_cycle: float
    cycle_wall_time_min: float
    overall_pass: bool
    warning: str = ""


# ---------------------------------------------------------------------------
# Per-case driver
# ---------------------------------------------------------------------------
def _ppm_at_outlet_end(C_outlet_t: np.ndarray, C_in_value: float, inlet_ppm: float) -> float:
    """Mole-fraction ratio at the last sample, expressed in inlet ppm."""
    if C_in_value <= 0 or len(C_outlet_t) == 0:
        return float("nan")
    last = float(C_outlet_t[-1])
    return max(last, 0.0) / C_in_value * inlet_ppm


VOID_CLEARANCE_SKIP_S = 60.0
"""Skip the post-repressurize void-clearance transient when detecting breakthrough.

Decision 1 (DD-014) repressurize fills the bed void with feed-composition gas.
At adsorption start the outlet cell's void already holds C ≈ C_in_feed; that
gas is swept out by advection over ~30 s. A naive 5 %-of-C_in detector would
mark this initial spike as breakthrough at t = 0. Skipping the first 60 s of
adsorption (≈ 2× residence time) lets the *real* breakthrough — the MTZ
reaching the outlet later in the cycle — be measured.

The skip duration only matters for cycles that follow a previous cycle's
repressurize; clean-bed runs (Step 5.3a) start at C = 0 and are unaffected.
"""


def _breakthrough_time_h(
    t_eval_s: np.ndarray, C_out: np.ndarray, C_in_value: float,
    threshold_frac: float = H2O_BREAKTHROUGH_THRESHOLD_FRAC,
    skip_initial_s: float = VOID_CLEARANCE_SKIP_S,
) -> float:
    """First time after `skip_initial_s` at which C_out crosses
    ``threshold_frac × C_in``; NaN if never."""
    if C_in_value <= 0:
        return float("nan")
    target = threshold_frac * C_in_value
    after_skip = t_eval_s >= skip_initial_s
    if not after_skip.any():
        return float("nan")
    t_search = t_eval_s[after_skip]
    C_search = C_out[after_skip]
    above = C_search >= target
    if not above.any():
        return float("nan")
    idx = int(np.argmax(above))
    if idx == 0:
        return float(t_search[0] / 3600.0)
    t_lo, t_hi = t_search[idx - 1], t_search[idx]
    C_lo, C_hi = C_search[idx - 1], C_search[idx]
    if C_hi == C_lo:
        return float(t_hi / 3600.0)
    frac = (target - C_lo) / (C_hi - C_lo)
    return float((t_lo + frac * (t_hi - t_lo)) / 3600.0)


def _build_case_result(
    config: CaseConfig,
    measurement_cycle: CycleResult,
    measurement_summary: CycleSummary,
    num_cycles: int,
    stabilization_status: str,
    cycle_wall_min: float,
    inlet_ppm: dict[str, float],
    inlet_C: dict[str, float],
    warning: str = "",
) -> CaseResult:
    phase_by_name = {p.name: p for p in measurement_cycle.phases}
    ph_ads = phase_by_name["adsorption"]
    ph_heat = phase_by_name["heating"]
    cycle_eb = measurement_cycle.cycle_energy_balance

    # Working capacity = mass adsorbed during one adsorption phase (kg)
    wc_h2o_mol = float(ph_ads.mass_in["h2o"] - ph_ads.mass_out["h2o"])
    wc_co2_mol = float(ph_ads.mass_in["co2"] - ph_ads.mass_out["co2"])
    wc_h2o_kg = wc_h2o_mol * MW_H2O_KG_MOL
    wc_co2_kg = wc_co2_mol * MW_CO2_KG_MOL

    # Outlet purity at adsorption end (ppm)
    outlet_h2o_ppm = _ppm_at_outlet_end(
        measurement_summary.adsorption_C_h2o_outlet, inlet_C["h2o"], inlet_ppm["h2o"]
    )
    outlet_co2_ppm = _ppm_at_outlet_end(
        measurement_summary.adsorption_C_co2_outlet, inlet_C["co2"], inlet_ppm["co2"]
    )
    bt_h2o = _breakthrough_time_h(
        measurement_summary.adsorption_t_s,
        measurement_summary.adsorption_C_h2o_outlet,
        inlet_C["h2o"],
    )

    # Specific regen energy (kJ per kg CO2 captured per cycle)
    if wc_co2_kg > 0:
        regen_energy_kj_per_kg = ph_heat.enthalpy_in_J / 1000.0 / wc_co2_kg
    else:
        regen_energy_kj_per_kg = float("nan")

    # Max stiffness across phases (we have start/end snapshots per phase)
    stiff_vals = []
    for ph in measurement_cycle.phases:
        if not ph.is_jump:
            stiff_vals.extend([ph.stiffness_start, ph.stiffness_end])
    max_stiff = float(np.nanmax(stiff_vals)) if stiff_vals else float("nan")

    return CaseResult(
        case_id=config.case_id,
        ghsv_factor=config.ghsv_factor,
        regen_temp_C=config.regen_temp_C,
        cycle_time_h=config.cycle_time_h,
        num_cycles_executed=num_cycles,
        stabilization_status=stabilization_status,
        measurement_cycle_idx=measurement_cycle.cycle_number,
        working_capacity_h2o_kg=wc_h2o_kg,
        working_capacity_co2_kg=wc_co2_kg,
        outlet_h2o_ppm_at_end=outlet_h2o_ppm,
        outlet_co2_ppm_at_end=outlet_co2_ppm,
        breakthrough_time_h2o_5pct_h=bt_h2o,
        regen_energy_kJ_per_kg_co2=regen_energy_kj_per_kg,
        cycle_energy_balance_legacy_pct=float(cycle_eb["legacy_closure_pct"]),
        cycle_energy_balance_model_pct=float(cycle_eb["model_closure_pct"]),
        max_bed_T_during_heating_C=config.regen_temp_C,
        residual_q_h2o_kg_per_kg=measurement_summary.residual_q_h2o_avg_alumina_mol_kg
        * MW_H2O_KG_MOL,
        residual_q_co2_kg_per_kg=measurement_summary.residual_q_co2_avg_13x_mol_kg
        * MW_CO2_KG_MOL,
        max_stiffness_during_cycle=max_stiff,
        cycle_wall_time_min=cycle_wall_min,
        overall_pass=measurement_cycle.overall_pass(),
        warning=warning,
    )


def run_single_case(
    config: CaseConfig,
    samples_per_hour: int = DEFAULT_SAMPLES_PER_HOUR,
    max_cycles: int = MAX_CYCLES_PER_CASE,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> CaseResult:
    """Run one sensitivity case adaptively until stabilization (≤ max_cycles)."""
    dbd = load_dbd()
    col = ColumnConfig.from_dbd(dbd)
    grid = build_grid(col)
    alumina_mask = (grid.layer_ids == 0)

    proc = dbd["process"]
    P_high = (
        float(proc["pressure_gauge_bar"]) + float(proc["pressure_atm_bar"])
    ) * 1.0e5
    y_h2o_in = float(dbd["loads"]["h2o_inlet_ppm"]) * 1.0e-6
    y_co2_in = float(proc["co2_in_ppm"]) * 1.0e-6
    T_in = float(proc["temperature_in_C"]) + 273.15
    inlet_C = {
        "h2o": y_h2o_in * P_high / (R_GAS * T_in),
        "co2": y_co2_in * P_high / (R_GAS * T_in),
    }
    inlet_ppm = {"h2o": y_h2o_in * 1.0e6, "co2": y_co2_in * 1.0e6}

    overrides = config.to_overrides()
    state: np.ndarray | None = None
    cycle_results: list[CycleResult] = []
    cycle_summaries: list[CycleSummary] = []
    cycle_walls_min: list[float] = []
    case_dir = output_dir / f"case_{config.case_id:02d}"
    case_dir.mkdir(parents=True, exist_ok=True)

    for cycle_idx in range(max_cycles):
        traj: dict = {}
        t0 = time.perf_counter()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            state, cycle = run_single_cycle(
                initial_state=state,
                cycle_number=cycle_idx,
                samples_per_hour=samples_per_hour,
                adsorption_trajectory=traj,
                case_overrides=overrides,
            )
        wall_min = (time.perf_counter() - t0) / 60.0
        summary = _summary_from_cycle(cycle, state, traj, alumina_mask)
        cycle_results.append(cycle)
        cycle_summaries.append(summary)
        cycle_walls_min.append(wall_min)
        print(
            f"  [case {config.case_id:02d}] cycle {cycle_idx} done "
            f"wall={wall_min:.2f} min  pass={summary.overall_pass}",
            flush=True,
        )

        # Stabilization check (only after ≥ 3 cycles run)
        if cycle_idx + 1 >= MIN_CYCLES_BEFORE_CHECK:
            stab = is_stabilized(cycle_summaries[-1], cycle_summaries[-2])
            if stab["overall_stabilized"]:
                return _build_case_result(
                    config,
                    cycle_results[-1],
                    cycle_summaries[-1],
                    num_cycles=cycle_idx + 1,
                    stabilization_status="stable",
                    cycle_wall_min=cycle_walls_min[-1],
                    inlet_ppm=inlet_ppm,
                    inlet_C=inlet_C,
                )

    # Reached max_cycles without 1-pair stabilization
    return _build_case_result(
        config,
        cycle_results[-1],
        cycle_summaries[-1],
        num_cycles=max_cycles,
        stabilization_status="unstable",
        cycle_wall_min=cycle_walls_min[-1],
        inlet_ppm=inlet_ppm,
        inlet_C=inlet_C,
        warning=f"did not stabilize within {max_cycles} cycles",
    )


# ---------------------------------------------------------------------------
# Multi-case sweep
# ---------------------------------------------------------------------------
@dataclass
class SweepReport:
    n_cases: int
    case_results: list[CaseResult] = field(default_factory=list)
    total_wall_time_h: float = 0.0
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))


def run_27_cases(
    samples_per_hour: int = DEFAULT_SAMPLES_PER_HOUR,
    max_cycles: int = MAX_CYCLES_PER_CASE,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    only_case_ids: list[int] | None = None,
) -> SweepReport:
    """Run all 27 cases (or a subset) and persist results after each case."""
    output_dir.mkdir(parents=True, exist_ok=True)
    dbd = load_dbd()
    flow_default = float(dbd["process"]["flow_nm3h"])
    cases = generate_27_cases(dbd_default_flow_nm3h=flow_default)
    if only_case_ids is not None:
        cases = [c for c in cases if c.case_id in set(only_case_ids)]

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    report = SweepReport(n_cases=len(cases))
    sweep_t0 = time.perf_counter()
    for i, cfg in enumerate(cases):
        print(
            f"\n[sweep {i + 1}/{len(cases)}] case {cfg.case_id}: "
            f"GHSV={cfg.ghsv_factor:.1f}x  T_regen={cfg.regen_temp_C:.0f}C  "
            f"cycle_time={cfg.cycle_time_h:.1f}h "
            f"(flow={cfg.adsorption_flow_nm3h:.1f} Nm3/h)",
            flush=True,
        )
        result = run_single_case(
            cfg, samples_per_hour=samples_per_hour, max_cycles=max_cycles,
            output_dir=output_dir,
        )
        report.case_results.append(result)
        save_results_csv(report.case_results, output_dir / "results.csv")
        save_results_json(report, output_dir / "sweep_progress.json")
        print(
            f"[sweep {i + 1}/{len(cases)}] case {cfg.case_id} done -- "
            f"status={result.stabilization_status}  cycles={result.num_cycles_executed}  "
            f"wc_h2o={result.working_capacity_h2o_kg:.3f} kg  "
            f"wc_co2={result.working_capacity_co2_kg:.3f} kg  "
            f"out_co2_ppm={result.outlet_co2_ppm_at_end:.3e}",
            flush=True,
        )
    report.total_wall_time_h = (time.perf_counter() - sweep_t0) / 3600.0
    save_results_json(report, output_dir / "sweep_report.json")
    return report


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _case_to_row(r: CaseResult) -> dict:
    return {
        "case_id": r.case_id,
        "ghsv_factor": r.ghsv_factor,
        "regen_temp_C": r.regen_temp_C,
        "cycle_time_h": r.cycle_time_h,
        "num_cycles_executed": r.num_cycles_executed,
        "stabilization_status": r.stabilization_status,
        "measurement_cycle_idx": r.measurement_cycle_idx,
        "working_capacity_h2o_kg": r.working_capacity_h2o_kg,
        "working_capacity_co2_kg": r.working_capacity_co2_kg,
        "outlet_h2o_ppm_at_end": r.outlet_h2o_ppm_at_end,
        "outlet_co2_ppm_at_end": r.outlet_co2_ppm_at_end,
        "breakthrough_time_h2o_5pct_h": r.breakthrough_time_h2o_5pct_h,
        "regen_energy_kJ_per_kg_co2": r.regen_energy_kJ_per_kg_co2,
        "cycle_energy_balance_legacy_pct": r.cycle_energy_balance_legacy_pct,
        "cycle_energy_balance_model_pct": r.cycle_energy_balance_model_pct,
        "max_bed_T_during_heating_C": r.max_bed_T_during_heating_C,
        "residual_q_h2o_kg_per_kg": r.residual_q_h2o_kg_per_kg,
        "residual_q_co2_kg_per_kg": r.residual_q_co2_kg_per_kg,
        "max_stiffness_during_cycle": r.max_stiffness_during_cycle,
        "cycle_wall_time_min": r.cycle_wall_time_min,
        "overall_pass": r.overall_pass,
        "warning": r.warning,
    }


def save_results_csv(results: list[CaseResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not results:
        return
    rows = [_case_to_row(r) for r in results]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def save_results_json(report: SweepReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "n_cases": report.n_cases,
        "timestamp": report.timestamp,
        "total_wall_time_h": report.total_wall_time_h,
        "case_results": [asdict(r) for r in report.case_results],
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=str,
        default="all",
        help='Case ids: "all" (default), "smoke" (= case 14, design point), '
        'or comma-separated ids "1,2,5".',
    )
    parser.add_argument("--samples-per-hour", type=int, default=DEFAULT_SAMPLES_PER_HOUR)
    parser.add_argument("--max-cycles", type=int, default=MAX_CYCLES_PER_CASE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    only_ids: list[int] | None
    if args.cases == "all":
        only_ids = None
    elif args.cases == "smoke":
        only_ids = [_design_point_case_id()]
    else:
        only_ids = [int(s) for s in args.cases.split(",") if s.strip()]

    report = run_27_cases(
        samples_per_hour=args.samples_per_hour,
        max_cycles=args.max_cycles,
        output_dir=args.output_dir,
        only_case_ids=only_ids,
    )
    print(f"\nSweep complete. Total wall: {report.total_wall_time_h:.2f} h")
    return 0


def _design_point_case_id() -> int:
    """Return the case id for GHSV=1.0×, T_regen=200°C, cycle_time=4h."""
    cases = generate_27_cases(dbd_default_flow_nm3h=200.0)
    for c in cases:
        if (
            abs(c.ghsv_factor - 1.0) < 1e-9
            and abs(c.regen_temp_C - 200.0) < 1e-9
            and abs(c.cycle_time_h - 4.0) < 1e-9
        ):
            return c.case_id
    raise RuntimeError("design-point case not found in 27-case grid")


# Keep a stable reference to constants used by the docstring (lint + future ref).
_ = (P_STD_PA, T_STD_K)


if __name__ == "__main__":
    sys.exit(main())
