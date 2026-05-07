"""Multi-cycle TSA stabilization analysis (Step 5.4.2).

Runs N consecutive TSA cycles, chaining the final state of each cycle into
the next, and detects when the system reaches steady-state cyclic operation
via a multi-metric stabilization criterion (DD-019, all 6 must pass for two
consecutive cycle pairs):

  1. Residual q_h2o (avg over alumina) — rel_diff < 1 %
  2. Residual q_co2 (avg over 13X)     — rel_diff < 1 %
  3. Adsorption outlet H2O curve shape — ∫|ΔC|dt / ∫C dt < 1 %
  4. Adsorption outlet CO2 curve shape — same metric < 1 %
  5. Cycle energy balance (legacy)     — abs_diff < 0.5 %
  6. Adsorption-start stiffness        — rel_diff < 5 % (relaxed)

Mass / stiffness metrics include the DD-018 noise floor:
  - q noise floor: 1e-6 mol/kg → degenerate flag, treated as PASS.
  - shape integral noise floor: 1e-12 mol·s/m³ → degenerate flag.

`find_stabilization_cycle()` requires **2 consecutive stable transitions**
to mark stabilization (single-cycle noise filter).

Usage:
    uv run python -m phase2_simulation.run_cycle_repeated --n-cycles 5
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .adsorption_1d import ColumnConfig
from .adsorption_1d.config import LAYER_ALUMINA
from .adsorption_1d.grid import build_grid
from .adsorption_1d.state import var_slice
from .ldf_kinetics import load_dbd
from .run_cycle import CycleResult, run_single_cycle

# ---------------------------------------------------------------------------
# Stabilization thresholds (DD-019)
# ---------------------------------------------------------------------------
STAB_TOL_RESIDUAL_Q_PCT = 1.0
STAB_TOL_OUTLET_SHAPE_PCT = 1.0
STAB_TOL_ENERGY_LEGACY_ABS_PCT = 0.5
STAB_TOL_ADSORPTION_START_STIFF_PCT = 5.0
STAB_NOISE_FLOOR_Q_MOL_KG = 1.0e-6
STAB_NOISE_FLOOR_SHAPE_MOL_S_M3 = 1.0e-12

CONSECUTIVE_REQUIRED = 2

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "outputs" / "phase2" / "cycle_repeated"


# ---------------------------------------------------------------------------
# Per-cycle compact summary
# ---------------------------------------------------------------------------
@dataclass
class CycleSummary:
    """Compact per-cycle data needed for stabilization detection + plotting."""

    cycle_number: int
    wall_time_s: float
    overall_pass: bool

    # Residual loadings — mean q at end of cycle (post-repressurize) over
    # the layer where each species adsorbs (Decision 2A).
    residual_q_h2o_avg_alumina_mol_kg: float
    residual_q_co2_avg_13x_mol_kg: float

    # Adsorption-phase outlet trajectories (for shape comparison)
    adsorption_t_s: np.ndarray
    adsorption_C_h2o_outlet: np.ndarray
    adsorption_C_co2_outlet: np.ndarray

    # Cycle-level energy closure (legacy convention, %)
    cycle_energy_legacy_pct: float
    cycle_energy_model_pct: float

    # Stiffness snapshots (NaN if N/A)
    adsorption_start_stiffness: float
    heating_start_stiffness: float
    cooling_end_stiffness: float


# ---------------------------------------------------------------------------
# Stabilization metric
# ---------------------------------------------------------------------------
def _rel_diff_with_floor(a: float, b: float, floor: float) -> tuple[float, bool]:
    """Relative percent difference with absolute noise floor.

    Returns (rel_diff_pct, degenerate). Degenerate when both magnitudes are
    below `floor`; in that case rel_diff_pct is NaN.
    """
    scale = max(abs(a), abs(b))
    if scale < floor:
        return float("nan"), True
    return 100.0 * abs(a - b) / scale, False


def _outlet_shape_diff_pct(
    t_n: np.ndarray, C_n: np.ndarray, t_nm1: np.ndarray, C_nm1: np.ndarray,
) -> tuple[float, bool]:
    """L1 shape distance: ∫|C_n − C_{n−1}|dt / ∫C_{n−1}dt × 100.

    Both trajectories must share the same t grid (same samples_per_hour).
    Returns (rel_diff_pct, degenerate) — degenerate when ∫C_{n-1}dt is below
    the shape noise floor.
    """
    if t_n.shape != t_nm1.shape or C_n.shape != C_nm1.shape:
        raise ValueError("trajectories have mismatched shapes")
    norm = float(np.trapezoid(C_nm1, t_nm1))
    if abs(norm) < STAB_NOISE_FLOOR_SHAPE_MOL_S_M3:
        return float("nan"), True
    diff = float(np.trapezoid(np.abs(C_n - C_nm1), t_n))
    return 100.0 * diff / abs(norm), False


def is_stabilized(
    cn: CycleSummary,
    cnm1: CycleSummary,
) -> dict[str, dict]:
    """Multi-metric stabilization check between cycle N and N-1.

    Returns dict with per-metric `{rel_diff_pct, status, flag}` plus an
    `overall_stabilized` boolean (True iff every metric passes).
    """
    metrics: dict[str, dict] = {}

    # 1, 2: residual q
    for sp, attr in (
        ("h2o", "residual_q_h2o_avg_alumina_mol_kg"),
        ("co2", "residual_q_co2_avg_13x_mol_kg"),
    ):
        a = getattr(cn, attr)
        b = getattr(cnm1, attr)
        rel, deg = _rel_diff_with_floor(a, b, STAB_NOISE_FLOOR_Q_MOL_KG)
        if deg:
            metrics[f"residual_q_{sp}"] = {"rel_diff_pct": float("nan"),
                                            "status": "PASS", "flag": "DEGENERATE"}
        else:
            metrics[f"residual_q_{sp}"] = {
                "rel_diff_pct": rel,
                "status": "PASS" if rel < STAB_TOL_RESIDUAL_Q_PCT else "FAIL",
                "flag": "NORMAL",
            }

    # 3, 4: outlet shape (adsorption phase)
    for sp, attr in (
        ("h2o", "adsorption_C_h2o_outlet"),
        ("co2", "adsorption_C_co2_outlet"),
    ):
        rel, deg = _outlet_shape_diff_pct(
            cn.adsorption_t_s, getattr(cn, attr),
            cnm1.adsorption_t_s, getattr(cnm1, attr),
        )
        if deg:
            metrics[f"outlet_shape_{sp}"] = {"rel_diff_pct": float("nan"),
                                              "status": "PASS", "flag": "DEGENERATE"}
        else:
            metrics[f"outlet_shape_{sp}"] = {
                "rel_diff_pct": rel,
                "status": "PASS" if rel < STAB_TOL_OUTLET_SHAPE_PCT else "FAIL",
                "flag": "NORMAL",
            }

    # 5: cycle energy balance (legacy, ABS difference, not relative)
    energy_abs_diff_pct = abs(cn.cycle_energy_legacy_pct - cnm1.cycle_energy_legacy_pct)
    metrics["energy_legacy_abs_diff"] = {
        "abs_diff_pct": energy_abs_diff_pct,
        "status": "PASS" if energy_abs_diff_pct < STAB_TOL_ENERGY_LEGACY_ABS_PCT else "FAIL",
        "flag": "NORMAL",
    }

    # 6: adsorption-start stiffness
    rel_stiff, deg_stiff = _rel_diff_with_floor(
        cn.adsorption_start_stiffness, cnm1.adsorption_start_stiffness, 1.0
    )
    if deg_stiff:
        metrics["adsorption_start_stiffness"] = {"rel_diff_pct": float("nan"),
                                                   "status": "PASS", "flag": "DEGENERATE"}
    else:
        metrics["adsorption_start_stiffness"] = {
            "rel_diff_pct": rel_stiff,
            "status": ("PASS" if rel_stiff < STAB_TOL_ADSORPTION_START_STIFF_PCT
                       else "FAIL"),
            "flag": "NORMAL",
        }

    overall = all(m["status"] == "PASS" for m in metrics.values())
    return {"overall_stabilized": overall, "metrics": metrics}


def find_stabilization_cycle(
    summaries: list[CycleSummary], require_consecutive: int = CONSECUTIVE_REQUIRED,
) -> tuple[int | None, list[bool]]:
    """Earliest cycle index where `require_consecutive` consecutive transitions stabilize.

    For 2 transitions: at index N we check (N-1, N) and (N, N+1) both stable.
    Returns (stabilization_cycle_number, list_of_per_pair_stable_flags).
    `stabilization_cycle_number` is the cycle.cycle_number where stabilization
    is first established; None if not reached.
    """
    if len(summaries) < require_consecutive + 1:
        return None, []
    pair_stable: list[bool] = []
    for i in range(1, len(summaries)):
        pair_stable.append(is_stabilized(summaries[i], summaries[i - 1])["overall_stabilized"])
    # Earliest i where pair_stable[i-1 .. i + require_consecutive - 2] are all True.
    needed = require_consecutive
    for i in range(needed - 1, len(pair_stable)):
        window = pair_stable[i - needed + 1 : i + 1]
        if all(window):
            # Stabilization first ESTABLISHED at the earliest cycle of the window.
            return summaries[i - needed + 2].cycle_number, pair_stable
    return None, pair_stable


# ---------------------------------------------------------------------------
# Multi-cycle driver
# ---------------------------------------------------------------------------
def _residual_q_avg(state: np.ndarray, alumina_mask: np.ndarray) -> tuple[float, float]:
    """Mean q_h2o on alumina, mean q_co2 on 13X (Decision 2A)."""
    n = alumina_mask.size
    q_h2o = state[var_slice("q_h2o", n)]
    q_co2 = state[var_slice("q_co2", n)]
    h2o_mask = alumina_mask
    co2_mask = ~alumina_mask
    avg_h2o = float(np.mean(q_h2o[h2o_mask])) if h2o_mask.any() else float("nan")
    avg_co2 = float(np.mean(q_co2[co2_mask])) if co2_mask.any() else float("nan")
    return avg_h2o, avg_co2


def _summary_from_cycle(
    cycle: CycleResult,
    final_state: np.ndarray,
    adsorption_traj: dict,
    alumina_mask: np.ndarray,
) -> CycleSummary:
    avg_q_h2o, avg_q_co2 = _residual_q_avg(final_state, alumina_mask)
    cycle_eb = cycle.cycle_energy_balance
    # Phase ordering by name (run_cycle.py: ads, depress, heat, cool, repress)
    phase_by_name = {p.name: p for p in cycle.phases}
    return CycleSummary(
        cycle_number=cycle.cycle_number,
        wall_time_s=cycle.total_wall_time_s,
        overall_pass=cycle.overall_pass(),
        residual_q_h2o_avg_alumina_mol_kg=avg_q_h2o,
        residual_q_co2_avg_13x_mol_kg=avg_q_co2,
        adsorption_t_s=adsorption_traj["t_s"],
        adsorption_C_h2o_outlet=adsorption_traj["C_h2o_outlet"],
        adsorption_C_co2_outlet=adsorption_traj["C_co2_outlet"],
        cycle_energy_legacy_pct=float(cycle_eb["legacy_closure_pct"]),
        cycle_energy_model_pct=float(cycle_eb["model_closure_pct"]),
        adsorption_start_stiffness=phase_by_name["adsorption"].stiffness_start,
        heating_start_stiffness=phase_by_name["heating"].stiffness_start,
        cooling_end_stiffness=phase_by_name["cooling"].stiffness_end,
    )


def run_n_cycles(
    n_cycles: int,
    samples_per_hour: int = 600,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    save_intermediate: bool = True,
) -> list[CycleSummary]:
    """Chain `n_cycles` consecutive cycles; return per-cycle summaries.

    Saves intermediate state + summary after each cycle (resilient to interruption).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    dbd = load_dbd()
    col = ColumnConfig.from_dbd(dbd)
    grid = build_grid(col)
    alumina_mask = (grid.layer_ids == 0)         # 0 = LAYER_ALUMINA
    assert alumina_mask.any(), "alumina_mask is empty — DBD layer config off"
    _ = LAYER_ALUMINA  # explicit imported reference (lint)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    state = None
    summaries: list[CycleSummary] = []
    for i in range(n_cycles):
        print(f"\n[run_n_cycles] starting cycle {i} (n_total={n_cycles})...", flush=True)
        traj: dict = {}
        t0 = time.perf_counter()
        state, cycle = run_single_cycle(
            initial_state=state,
            cycle_number=i,
            samples_per_hour=samples_per_hour,
            adsorption_trajectory=traj,
        )
        wall = time.perf_counter() - t0
        summary = _summary_from_cycle(cycle, state, traj, alumina_mask)
        summaries.append(summary)
        print(
            f"[run_n_cycles] cycle {i} done -- wall {wall/60:.2f} min  "
            f"overall_pass={summary.overall_pass}  "
            f"q_h2o_avg={summary.residual_q_h2o_avg_alumina_mol_kg:.3e}  "
            f"q_co2_avg={summary.residual_q_co2_avg_13x_mol_kg:.3e}",
            flush=True,
        )
        if save_intermediate:
            _save_summary(summary, output_dir / f"cycle_{i:02d}_summary.npz")
            np.savez(output_dir / f"cycle_{i:02d}_state.npz", state=state)
    return summaries


# ---------------------------------------------------------------------------
# Persistence + reporting
# ---------------------------------------------------------------------------
def _save_summary(s: CycleSummary, path: Path) -> None:
    np.savez(
        path,
        cycle_number=s.cycle_number,
        wall_time_s=s.wall_time_s,
        overall_pass=s.overall_pass,
        residual_q_h2o_avg_alumina_mol_kg=s.residual_q_h2o_avg_alumina_mol_kg,
        residual_q_co2_avg_13x_mol_kg=s.residual_q_co2_avg_13x_mol_kg,
        adsorption_t_s=s.adsorption_t_s,
        adsorption_C_h2o_outlet=s.adsorption_C_h2o_outlet,
        adsorption_C_co2_outlet=s.adsorption_C_co2_outlet,
        cycle_energy_legacy_pct=s.cycle_energy_legacy_pct,
        cycle_energy_model_pct=s.cycle_energy_model_pct,
        adsorption_start_stiffness=s.adsorption_start_stiffness,
        heating_start_stiffness=s.heating_start_stiffness,
        cooling_end_stiffness=s.cooling_end_stiffness,
    )


def _summary_to_json_dict(s: CycleSummary) -> dict:
    """Same fields, but ndarray → list (truncated stats only) for JSON."""
    return {
        "cycle_number": s.cycle_number,
        "wall_time_s": s.wall_time_s,
        "overall_pass": s.overall_pass,
        "residual_q_h2o_avg_alumina_mol_kg": s.residual_q_h2o_avg_alumina_mol_kg,
        "residual_q_co2_avg_13x_mol_kg": s.residual_q_co2_avg_13x_mol_kg,
        "cycle_energy_legacy_pct": s.cycle_energy_legacy_pct,
        "cycle_energy_model_pct": s.cycle_energy_model_pct,
        "adsorption_start_stiffness": s.adsorption_start_stiffness,
        "heating_start_stiffness": s.heating_start_stiffness,
        "cooling_end_stiffness": s.cooling_end_stiffness,
    }


@dataclass
class StabilizationReport:
    n_cycles: int
    stabilization_cycle: int | None
    pair_stable_flags: list[bool] = field(default_factory=list)
    per_pair_metrics: list[dict] = field(default_factory=list)
    summaries_compact: list[dict] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))


def build_report(summaries: list[CycleSummary]) -> StabilizationReport:
    stab, flags = find_stabilization_cycle(summaries)
    per_pair = []
    for i in range(1, len(summaries)):
        per_pair.append({
            "n": summaries[i].cycle_number,
            "n_minus_1": summaries[i - 1].cycle_number,
            "result": is_stabilized(summaries[i], summaries[i - 1]),
        })
    return StabilizationReport(
        n_cycles=len(summaries),
        stabilization_cycle=stab,
        pair_stable_flags=flags,
        per_pair_metrics=per_pair,
        summaries_compact=[_summary_to_json_dict(s) for s in summaries],
    )


def save_report(report: StabilizationReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(asdict(report), f, indent=2)


def plot_stabilization(
    summaries: list[CycleSummary],
    report: StabilizationReport,
    output_path: Path,
) -> None:
    """Three-panel cycle stabilization plot.

    Panel A: Adsorption outlet C(t) overlay across cycles.
    Panel B: Adsorption-start stiffness vs cycle.
    Panel C: Cycle energy closure (legacy + model) vs cycle.

    Vertical line at the stabilization cycle (when reached).
    """
    import matplotlib.pyplot as plt

    if not summaries:
        return
    fig, (axA, axB, axC) = plt.subplots(3, 1, figsize=(9, 10))

    # Panel A: outlet H2O overlay
    cmap = plt.get_cmap("viridis", len(summaries))
    for i, s in enumerate(summaries):
        axA.plot(
            s.adsorption_t_s / 3600.0, s.adsorption_C_h2o_outlet,
            label=f"cycle {s.cycle_number}", color=cmap(i), linewidth=1.6,
        )
    axA.set_xlabel("adsorption time t (h)")
    axA.set_ylabel("outlet C_h2o (mol/m^3)")
    axA.set_title("Adsorption outlet H2O — cycle-to-cycle convergence")
    axA.legend(fontsize=8, loc="upper left")
    axA.grid(True, linestyle=":", alpha=0.5)

    # Panel B: stiffness evolution
    cycle_nums = [s.cycle_number for s in summaries]
    stiff = [s.adsorption_start_stiffness for s in summaries]
    axB.semilogy(cycle_nums, stiff, "o-", color="tab:blue", label="adsorption start")
    heat_stiff = [s.heating_start_stiffness for s in summaries]
    axB.semilogy(cycle_nums, heat_stiff, "s--", color="tab:orange", label="heating start")
    axB.set_xlabel("cycle #")
    axB.set_ylabel("stiffness ratio (log)")
    axB.set_title("Stiffness evolution (cycle 0 = clean bed; rest = steady-state regime)")
    axB.legend(fontsize=9)
    axB.grid(True, which="both", linestyle=":", alpha=0.5)

    # Panel C: energy closure trend
    e_leg = [s.cycle_energy_legacy_pct for s in summaries]
    e_mod = [s.cycle_energy_model_pct for s in summaries]
    axC.plot(cycle_nums, e_leg, "o-", color="tab:green", label="legacy %")
    axC.plot(cycle_nums, e_mod, "s-", color="tab:red", label="model %")
    axC.set_xlabel("cycle #")
    axC.set_ylabel("cycle energy closure (%)")
    axC.set_title("Cycle energy closure — both metrics across cycles")
    axC.legend(fontsize=9)
    axC.grid(True, linestyle=":", alpha=0.5)

    if report.stabilization_cycle is not None:
        for ax in (axA, axB, axC):
            ax.axvline(
                report.stabilization_cycle,
                color="tab:red", linestyle="--", linewidth=1.5, alpha=0.6,
            )
        axB.text(
            report.stabilization_cycle, axB.get_ylim()[1] * 0.4,
            f" stabilization cycle = {report.stabilization_cycle}",
            color="tab:red", fontsize=10, va="top",
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_path, dpi=130)
    plt.close(fig)


def print_report(report: StabilizationReport) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(f"\n=== Multi-cycle stabilization report ({report.timestamp}) ===")
    print(f"n_cycles: {report.n_cycles}")
    if report.stabilization_cycle is None:
        print("STABILIZATION: NOT REACHED within run")
    else:
        print(f"STABILIZATION: reached at cycle {report.stabilization_cycle}")
    print("\nPer-cycle compact:")
    for s in report.summaries_compact:
        print(
            f"  cycle {s['cycle_number']}  wall={s['wall_time_s']/60:.2f} min  "
            f"q_h2o_avg={s['residual_q_h2o_avg_alumina_mol_kg']:.3e}  "
            f"q_co2_avg={s['residual_q_co2_avg_13x_mol_kg']:.3e}  "
            f"E_legacy={s['cycle_energy_legacy_pct']:.2f}%  "
            f"stiff_start={s['adsorption_start_stiffness']:.2e}"
        )
    print("\nPer-pair stabilization (each metric):")
    for entry in report.per_pair_metrics:
        n, nm1 = entry["n"], entry["n_minus_1"]
        result = entry["result"]
        print(f"  pair ({nm1}, {n}) — overall: {result['overall_stabilized']}")
        for name, m in result["metrics"].items():
            val_key = "rel_diff_pct" if "rel_diff_pct" in m else "abs_diff_pct"
            val = m.get(val_key, "n/a")
            val_s = f"{val:.3e}%" if isinstance(val, float) and not np.isnan(val) else str(val)
            print(f"    {name}: {val_s}  status={m['status']}  flag={m.get('flag', '-')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-cycles", type=int, default=5)
    parser.add_argument("--samples-per-hour", type=int, default=600)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)

    summaries = run_n_cycles(
        n_cycles=args.n_cycles,
        samples_per_hour=args.samples_per_hour,
        output_dir=args.output_dir,
        save_intermediate=not args.no_save,
    )
    report = build_report(summaries)
    print_report(report)
    if not args.no_save:
        save_report(report, args.output_dir / "stabilization_report.json")
        plot_stabilization(
            summaries, report, args.output_dir / "stabilization_plot.png",
        )
        print(f"Saved stabilization report to {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
