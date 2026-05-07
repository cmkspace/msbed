"""Phase 2 design-case breakthrough simulation — Phase 1 consistency gate (Step 5.3).

Runs a 5-hour adsorption simulation at the DBD design point and validates the
PDE chain against three independent acceptance criteria (PHASE2_SPEC §4.4):

  Gate 1 — H2O breakthrough timing (cycle determinant):
      5% breakthrough time in [3.5 h, 4.5 h]
      → validates AA dynamic loading (6 wt%) + LDF rate.

  Gate 2 — CO2 product specification (ASU-grade):
      C_out_co2 < 0.1 ppm at t = 4 h
      → validates 13X capacity + layered-bed assumption (no H2O penetration).
      CO2 breakthrough timing is intentionally NOT part of this gate; it is
      expected at t ≫ 4 h (estimated 6–8 h+) due to 13X working capacity.

  Gate 3 — Mass balance closure:
      |cum_adsorbed − bed_inventory| / cum_inlet < 10 % for both species
      → validates PDE solver numerical accuracy.

If all three gates pass, the simulation chain is physically consistent with
the DBD design and Phase 3 (equipment) entry is unlocked. If any fail, STOP
and investigate the failure-mode interpretation in PHASE2_SPEC §4.4.

Usage:
    uv run python -m phase2_simulation.run_breakthrough             # full 5 h run
    uv run python -m phase2_simulation.run_breakthrough --duration-h 1.0   # short sanity
    uv run python -m phase2_simulation.run_breakthrough --output-dir ./out
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
    inlet_concentrations,
    superficial_velocity,
)
from .adsorption_1d.rhs import SimulationParams
from .adsorption_1d.solver import initial_state_clean_bed, simulate
from .ldf_kinetics import load_dbd

# ---------------------------------------------------------------------------
# Three-gate acceptance criteria (PHASE2_SPEC §4.4, DD-014)
# ---------------------------------------------------------------------------
# Gate 1: H2O timing — 5% breakthrough must land inside the cycle window.
GATE_H2O_BREAKTHROUGH_MIN_H = 3.5
GATE_H2O_BREAKTHROUGH_MAX_H = 4.5
H2O_BREAKTHROUGH_THRESHOLD_FRAC = 0.05     # outlet/inlet = 5%

# Gate 2: CO2 product spec — outlet at design cycle endpoint (4 h) must clear ASU-grade.
GATE_CO2_SPEC_CHECKPOINT_H = 4.0
GATE_CO2_SPEC_PPM = 0.1                    # ASU-grade target (DBD §3.5)

# Gate 3: Mass balance closure — solver self-consistency.
GATE_MASS_BALANCE_TOL_PCT = 10.0

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "outputs" / "phase2" / "breakthrough_curves"


@dataclass
class _MassBalance:
    """Shared closure metrics for both species (input to Gate 3)."""

    cum_inlet_mol: float
    cum_outlet_mol: float
    cum_adsorbed_mol: float               # in − out
    bed_inventory_mol: float              # ε·C·V + (1−ε)·ρ_p·q·V at final t
    error_pct: float                      # 100 · |adsorbed − inventory| / cum_inlet
    passes: bool                          # error_pct < GATE_MASS_BALANCE_TOL_PCT


@dataclass
class H2oGateResult:
    """Gate 1 result — H2O 5% breakthrough timing + mass balance."""

    C_in_mol_m3: float
    breakthrough_time_h: float            # NaN if not reached during sim
    breakthrough_in_window: bool
    cum_inlet_mol: float
    cum_outlet_mol: float
    cum_adsorbed_mol: float
    bed_inventory_mol: float
    mass_balance_error_pct: float
    mass_balance_passes: bool

    def passes(self) -> bool:
        return self.breakthrough_in_window and self.mass_balance_passes


@dataclass
class Co2GateResult:
    """Gate 2 result — CO2 product spec at t=4 h + mass balance."""

    C_in_mol_m3: float
    inlet_ppm: float
    C_out_at_checkpoint_mol_m3: float     # NaN if duration < checkpoint
    out_ppm_at_checkpoint: float          # NaN if duration < checkpoint
    spec_passes: bool                     # out_ppm < GATE_CO2_SPEC_PPM
    cum_inlet_mol: float
    cum_outlet_mol: float
    cum_adsorbed_mol: float
    bed_inventory_mol: float
    mass_balance_error_pct: float
    mass_balance_passes: bool

    def passes(self) -> bool:
        return self.spec_passes and self.mass_balance_passes


@dataclass
class GateReport:
    """Aggregate Phase 1 consistency-gate report."""

    duration_h: float
    wall_time_s: float
    n_steps: int
    avg_ms_per_step: float
    sparsity_nnz: int
    stiffness_band: str
    stiffness_ratio: float
    h2o: H2oGateResult
    co2: Co2GateResult
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))

    def overall_pass(self) -> bool:
        return self.h2o.passes() and self.co2.passes()


def _breakthrough_time_h(
    t_eval_s: np.ndarray,
    C_out: np.ndarray,
    C_in_value: float,
    threshold_frac: float = H2O_BREAKTHROUGH_THRESHOLD_FRAC,
) -> float:
    """First time at which C_out crosses `threshold_frac × C_in`.

    Linear interpolation between the bracketing samples. Returns NaN if the
    threshold is never reached over `t_eval_s`.
    """
    if C_in_value <= 0:
        return float("nan")
    target = threshold_frac * C_in_value
    above = C_out >= target
    if not above.any():
        return float("nan")
    idx = int(np.argmax(above))
    if idx == 0:
        return float(t_eval_s[0] / 3600.0)
    t_lo = t_eval_s[idx - 1]
    t_hi = t_eval_s[idx]
    C_lo = C_out[idx - 1]
    C_hi = C_out[idx]
    if C_hi == C_lo:
        return float(t_hi / 3600.0)
    frac = (target - C_lo) / (C_hi - C_lo)
    t_cross = t_lo + frac * (t_hi - t_lo)
    return float(t_cross / 3600.0)


def _compute_mass_balance(
    t_eval_s: np.ndarray,
    C_in_value: float,
    C_arr: np.ndarray,
    q_arr: np.ndarray,
    u_sup: float,
    A_xs: float,
    eps_b: float,
    rho_p: np.ndarray,
    dz: np.ndarray,
) -> _MassBalance:
    """Cumulative in/out + bed inventory closure (Gate 3, shared by both species)."""
    C_out_t = C_arr[-1, :]                 # outlet cell over time
    cum_in_mol = u_sup * C_in_value * t_eval_s[-1] * A_xs
    cum_out_mol = float(np.trapezoid(u_sup * C_out_t, t_eval_s)) * A_xs
    cum_ads_mol = cum_in_mol - cum_out_mol

    # Bed inventory at final t:  ε·C·V_total + (1−ε)·ρ_p·q·V_total
    gas_inv = float(np.sum(eps_b * C_arr[:, -1] * dz)) * A_xs
    solid_inv = float(np.sum((1.0 - eps_b) * rho_p * q_arr[:, -1] * dz)) * A_xs
    bed_inv = gas_inv + solid_inv

    if cum_in_mol > 0:
        err_pct = 100.0 * abs(cum_ads_mol - bed_inv) / cum_in_mol
    else:
        err_pct = float("nan")

    return _MassBalance(
        cum_inlet_mol=cum_in_mol,
        cum_outlet_mol=cum_out_mol,
        cum_adsorbed_mol=cum_ads_mol,
        bed_inventory_mol=bed_inv,
        error_pct=err_pct,
        passes=bool(np.isfinite(err_pct) and err_pct < GATE_MASS_BALANCE_TOL_PCT),
    )


def _compute_h2o_gate(
    t_eval_s: np.ndarray,
    C_in_h2o: float,
    C_arr: np.ndarray,
    q_arr: np.ndarray,
    u_sup: float,
    A_xs: float,
    eps_b: float,
    rho_p: np.ndarray,
    dz: np.ndarray,
) -> H2oGateResult:
    """Gate 1: H2O 5% breakthrough timing must land in [3.5, 4.5] h."""
    mb = _compute_mass_balance(
        t_eval_s, C_in_h2o, C_arr, q_arr, u_sup, A_xs, eps_b, rho_p, dz
    )
    bt_h = _breakthrough_time_h(t_eval_s, C_arr[-1, :], C_in_h2o)
    bt_in_window = (
        np.isfinite(bt_h)
        and GATE_H2O_BREAKTHROUGH_MIN_H <= bt_h <= GATE_H2O_BREAKTHROUGH_MAX_H
    )
    return H2oGateResult(
        C_in_mol_m3=float(C_in_h2o),
        breakthrough_time_h=bt_h,
        breakthrough_in_window=bool(bt_in_window),
        cum_inlet_mol=mb.cum_inlet_mol,
        cum_outlet_mol=mb.cum_outlet_mol,
        cum_adsorbed_mol=mb.cum_adsorbed_mol,
        bed_inventory_mol=mb.bed_inventory_mol,
        mass_balance_error_pct=mb.error_pct,
        mass_balance_passes=mb.passes,
    )


def _compute_co2_gate(
    t_eval_s: np.ndarray,
    C_in_co2: float,
    inlet_ppm: float,
    C_arr: np.ndarray,
    q_arr: np.ndarray,
    u_sup: float,
    A_xs: float,
    eps_b: float,
    rho_p: np.ndarray,
    dz: np.ndarray,
) -> Co2GateResult:
    """Gate 2: CO2 outlet at t=4 h must be < 0.1 ppm (ASU-grade product spec).

    ppm conversion uses inlet ratio: out_ppm = (C_out / C_in) × inlet_ppm.
    This sidesteps STP-vs-operating-condition unit ambiguity.
    """
    mb = _compute_mass_balance(
        t_eval_s, C_in_co2, C_arr, q_arr, u_sup, A_xs, eps_b, rho_p, dz
    )
    checkpoint_s = GATE_CO2_SPEC_CHECKPOINT_H * 3600.0
    if t_eval_s[-1] >= checkpoint_s and C_in_co2 > 0:
        C_at_chk = float(np.interp(checkpoint_s, t_eval_s, C_arr[-1, :]))
        # Numerical floor: outlet can dip slightly negative from FD truncation;
        # report ≥ 0 ppm so the spec comparison stays physically meaningful.
        out_ppm = max(C_at_chk, 0.0) / C_in_co2 * inlet_ppm
        spec_passes = bool(out_ppm < GATE_CO2_SPEC_PPM)
    else:
        C_at_chk = float("nan")
        out_ppm = float("nan")
        spec_passes = False
    return Co2GateResult(
        C_in_mol_m3=float(C_in_co2),
        inlet_ppm=float(inlet_ppm),
        C_out_at_checkpoint_mol_m3=C_at_chk,
        out_ppm_at_checkpoint=out_ppm,
        spec_passes=spec_passes,
        cum_inlet_mol=mb.cum_inlet_mol,
        cum_outlet_mol=mb.cum_outlet_mol,
        cum_adsorbed_mol=mb.cum_adsorbed_mol,
        bed_inventory_mol=mb.bed_inventory_mol,
        mass_balance_error_pct=mb.error_pct,
        mass_balance_passes=mb.passes,
    )


def run_breakthrough(
    duration_h: float = 5.0,
    samples_per_hour: int = 60,
    skip_stiffness_check: bool = True,
) -> tuple[GateReport, np.ndarray, np.ndarray, np.ndarray]:
    """Run a single design-case breakthrough simulation and produce a gate report.

    Args:
        duration_h: Simulation duration in hours.
        samples_per_hour: Number of t_eval samples per hour (default 60 = 1 / min).
        skip_stiffness_check: Skip the pre-flight estimate (for speed in scripts).

    Returns:
        (report, t_eval_s, C_h2o_outlet_t, C_co2_outlet_t).
    """
    dbd = load_dbd()
    col = ColumnConfig.from_dbd(dbd)
    proc = dbd["process"]
    op = OperatingConditions(
        mode="adsorption",
        flow_nm3h=float(proc["flow_nm3h"]),
        P_op_Pa=(float(proc["pressure_gauge_bar"]) + float(proc["pressure_atm_bar"])) * 1.0e5,
        T_in_K=float(proc["temperature_in_C"]) + 273.15,
        y_h2o_in=float(dbd["loads"]["h2o_inlet_ppm"]) * 1.0e-6,
        y_co2_in=float(proc["co2_in_ppm"]) * 1.0e-6,
        flow_direction="forward",
    )
    params = SimulationParams.build(col, op)

    duration_s = duration_h * 3600.0
    n_eval = int(round(samples_per_hour * duration_h)) + 1
    t_eval = np.linspace(0.0, duration_s, n_eval)

    y0 = initial_state_clean_bed(params)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        result, metrics = simulate(
            params, y0,
            t_span=(0.0, duration_s),
            t_eval=t_eval,
            dense_output=False,                  # 5 h dense interpolants would OOM
            skip_stiffness_check=skip_stiffness_check,
        )
    if not result.success:
        raise RuntimeError(f"Solver failed: {result.message}")

    A_xs = col.cross_section_m2
    u_sup = superficial_velocity(op, op.T_in_K, A_xs)
    C_in = inlet_concentrations(op, op.T_in_K)
    eps_b = col.void_fraction
    dz = params.grid.dz_widths_m

    h2o_gate = _compute_h2o_gate(
        t_eval, C_in["h2o"],
        result.C_h2o(), result.q_h2o(),
        u_sup, A_xs, eps_b, params.rho_p, dz,
    )
    co2_gate = _compute_co2_gate(
        t_eval, C_in["co2"], op.y_co2_in * 1.0e6,
        result.C_co2(), result.q_co2(),
        u_sup, A_xs, eps_b, params.rho_p, dz,
    )

    report = GateReport(
        duration_h=duration_h,
        wall_time_s=metrics.wall_time_s,
        n_steps=metrics.n_steps,
        avg_ms_per_step=metrics.avg_ms_per_step,
        sparsity_nnz=metrics.sparsity_nnz,
        stiffness_band=metrics.stiffness_band,
        stiffness_ratio=metrics.stiffness_ratio,
        h2o=h2o_gate,
        co2=co2_gate,
    )
    return report, t_eval, result.C_h2o()[-1, :], result.C_co2()[-1, :]


def save_outputs(
    report: GateReport,
    t_eval_s: np.ndarray,
    C_h2o_out: np.ndarray,
    C_co2_out: np.ndarray,
    output_dir: Path,
) -> None:
    """Persist the breakthrough curves CSV + summary JSON to `output_dir`."""
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / "design_case_breakthrough.csv"
    header = "t_s,t_h,C_h2o_out_mol_m3,C_co2_out_mol_m3"
    data = np.column_stack([t_eval_s, t_eval_s / 3600.0, C_h2o_out, C_co2_out])
    np.savetxt(csv_path, data, delimiter=",", header=header, comments="")

    json_path = output_dir / "design_case_summary.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(asdict(report), f, indent=2)


def _format_h2o(g: H2oGateResult) -> str:
    bt = (
        f"{g.breakthrough_time_h:.3f} h"
        if np.isfinite(g.breakthrough_time_h)
        else "not reached"
    )
    bt_pass = "PASS" if g.breakthrough_in_window else "FAIL"
    mb_pass = "PASS" if g.mass_balance_passes else "FAIL"
    return (
        f"  [Gate 1 — H2O timing]\n"
        f"    C_in              = {g.C_in_mol_m3:.4f} mol/m^3\n"
        f"    5% breakthrough   = {bt}  "
        f"(window [{GATE_H2O_BREAKTHROUGH_MIN_H}, {GATE_H2O_BREAKTHROUGH_MAX_H}] h) -> {bt_pass}\n"
        f"    cum_inlet         = {g.cum_inlet_mol:.4f} mol\n"
        f"    cum_outlet        = {g.cum_outlet_mol:.4f} mol\n"
        f"    cum_adsorbed      = {g.cum_adsorbed_mol:.4f} mol\n"
        f"    bed_inventory     = {g.bed_inventory_mol:.4f} mol\n"
        f"    [Gate 3a] mass_balance_err = {g.mass_balance_error_pct:.3e}% "
        f"(tol {GATE_MASS_BALANCE_TOL_PCT}%) -> {mb_pass}"
    )


def _format_co2(g: Co2GateResult) -> str:
    if np.isfinite(g.out_ppm_at_checkpoint):
        spec_line = f"{g.out_ppm_at_checkpoint:.3e} ppm"
    else:
        spec_line = "not evaluated (sim < checkpoint)"
    spec_pass = "PASS" if g.spec_passes else "FAIL"
    mb_pass = "PASS" if g.mass_balance_passes else "FAIL"
    return (
        f"  [Gate 2 — CO2 product spec]\n"
        f"    C_in              = {g.C_in_mol_m3:.4f} mol/m^3 ({g.inlet_ppm:.2f} ppm)\n"
        f"    out @ t={GATE_CO2_SPEC_CHECKPOINT_H} h    = {spec_line}  "
        f"(target < {GATE_CO2_SPEC_PPM} ppm) -> {spec_pass}\n"
        f"    cum_inlet         = {g.cum_inlet_mol:.4f} mol\n"
        f"    cum_outlet        = {g.cum_outlet_mol:.4f} mol\n"
        f"    cum_adsorbed      = {g.cum_adsorbed_mol:.4f} mol\n"
        f"    bed_inventory     = {g.bed_inventory_mol:.4f} mol\n"
        f"    [Gate 3b] mass_balance_err = {g.mass_balance_error_pct:.3e}% "
        f"(tol {GATE_MASS_BALANCE_TOL_PCT}%) -> {mb_pass}"
    )


def print_report(report: GateReport) -> None:
    """Pretty-print the gate report to stdout (utf-8 reconfigured for cp949 hosts)."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(f"\n=== Phase 1 consistency gate report ({report.timestamp}) ===")
    print(f"Duration       : {report.duration_h:.2f} h")
    print(
        f"Solver         : {report.wall_time_s:.2f} s wall  ·  {report.n_steps} steps"
        f"  ·  {report.avg_ms_per_step:.2f} ms/step"
    )
    print(
        f"Stiffness      : ratio={report.stiffness_ratio:.3e} band={report.stiffness_band}"
    )
    print(f"Sparsity nnz   : {report.sparsity_nnz}")
    print(_format_h2o(report.h2o))
    print(_format_co2(report.co2))
    overall = "PASS" if report.overall_pass() else "FAIL"
    print(f"\nOverall gate   : {overall}\n")


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--duration-h", type=float, default=5.0, help="Simulation hours")
    p.add_argument(
        "--samples-per-hour", type=int, default=60,
        help="t_eval samples per hour (default 60 = once per minute)",
    )
    p.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help="Directory for CSV + JSON outputs",
    )
    p.add_argument(
        "--skip-stiffness-check", action="store_true",
        help="Skip pre-flight stiffness measurement (faster startup)",
    )
    p.add_argument(
        "--no-save", action="store_true",
        help="Do not persist outputs (for unit tests)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)
    report, t_eval, C_h2o_out, C_co2_out = run_breakthrough(
        duration_h=args.duration_h,
        samples_per_hour=args.samples_per_hour,
        skip_stiffness_check=args.skip_stiffness_check,
    )
    print_report(report)
    if not args.no_save:
        save_outputs(report, t_eval, C_h2o_out, C_co2_out, args.output_dir)
        print(f"Outputs written to {args.output_dir}")
    return 0 if report.overall_pass() else 1


if __name__ == "__main__":
    sys.exit(main())
