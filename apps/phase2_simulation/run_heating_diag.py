"""Heating-step diagnostic on cycle-reality state (Step 5.4.0d).

Pre-flight on a synthetic uniform-loaded state (Step 5.4.0b) PASSED, but
the real cycle's heating step (Step 5.4.1 first attempt) crashed with
BDF "Factor is exactly singular". The cycle-reality state contains stiff
features absent in the synthetic preflight:

  * MTZ gradient in q_h2o (saturated upstream → partially loaded MTZ).
  * Post-depress leftover C (~0.12 mol/m³ in saturated zone, vs zero in
    preflight).

This diagnostic walks heating from a cached cycle-reality state under
multiple `max_step` values and a chunk-by-chunk stiffness profile, so we
can calibrate per-phase `max_step` overrides for `run_cycle.py`.

Sub-commands (single-script CLI):

  generate   — run 4 h adsorption + depressurize, cache the state.
  profile    — chunk-by-chunk integration with `max_step=0.01`; record
               stiffness at every chunk boundary; on crash, log t_crash
               and last successful state.
  sweep      — for each `max_step` in [0.005, 0.002, 0.001], attempt full
               2 h heating; record success/wall/crash.
  all        — generate (if missing) → profile → sweep, in sequence.
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
from .adsorption_1d.rhs import SimulationParams, estimate_stiffness_ratio
from .adsorption_1d.solver import initial_state_clean_bed, simulate
from .adsorption_1d.state import var_slice
from .adsorption_1d.state_transform import depressurize
from .ldf_kinetics import load_dbd

ADSORPTION_DURATION_S = 4.0 * 3600.0
HEATING_DURATION_S = 2.0 * 3600.0
P_LOW_PA = 1.013e5
T_REGEN_K = 273.15 + 200.0
FLOW_REGEN_NM3H = 60.0

DEFAULT_MAX_STEP = 0.01
PROFILE_CHUNK_S = 60.0                                      # 1-min stiffness samples
SWEEP_CANDIDATES = (0.005, 0.002, 0.001)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIAG_DIR = _PROJECT_ROOT / "outputs" / "phase2" / "diag"
STATE_CACHE_PATH = DEFAULT_DIAG_DIR / "heating_initial_state.npz"


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------
@dataclass
class StiffnessProfileEntry:
    t_s: float
    stiffness_ratio: float
    band: str
    chunk_wall_s: float
    cumulative_wall_s: float
    q_h2o_max_mol_kg: float                 # peak loading magnitude (mol/kg)
    q_h2o_max_z_m: float                    # z position of the peak (m, MTZ front)
    T_mid_K: float                          # temperature at z = L/2 (hot-front tracker)


@dataclass
class StiffnessProfileResult:
    max_step: float
    total_attempted_s: float
    chunk_s: float
    success: bool
    crashed_at_s: float                # last successful t (NaN if never run)
    crash_message: str
    cumulative_wall_s: float
    entries: list[StiffnessProfileEntry] = field(default_factory=list)


@dataclass
class SweepEntry:
    max_step: float
    success: bool
    wall_time_s: float
    n_steps: int
    avg_ms_per_step: float
    crash_message: str


# ---------------------------------------------------------------------------
# Cycle-reality state generator
# ---------------------------------------------------------------------------
def _build_adsorption_op(
    dbd: dict,
) -> tuple[ColumnConfig, OperatingConditions, float, float, float]:
    col = ColumnConfig.from_dbd(dbd)
    proc = dbd["process"]
    P_high = (
        float(proc["pressure_gauge_bar"]) + float(proc["pressure_atm_bar"])
    ) * 1.0e5
    y_h2o = float(dbd["loads"]["h2o_inlet_ppm"]) * 1.0e-6
    y_co2 = float(proc["co2_in_ppm"]) * 1.0e-6
    op = OperatingConditions(
        mode="adsorption",
        flow_nm3h=float(proc["flow_nm3h"]),
        P_op_Pa=P_high,
        T_in_K=float(proc["temperature_in_C"]) + 273.15,
        y_h2o_in=y_h2o,
        y_co2_in=y_co2,
        flow_direction="forward",
    )
    return col, op, P_high, y_h2o, y_co2


def generate_cycle_reality_state(output_path: Path = STATE_CACHE_PATH) -> np.ndarray:
    """Run 4 h adsorption + depressurize from clean bed; cache result."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dbd = load_dbd()
    col, op_ads, P_high, _, _ = _build_adsorption_op(dbd)
    params = SimulationParams.build(col, op_ads)
    n = params.grid.n_total

    print(f"[generate] running 4 h adsorption from clean bed (n={n})…")
    y0 = initial_state_clean_bed(params)
    t_eval = np.linspace(0.0, ADSORPTION_DURATION_S, 4 * 60 + 1)
    t0 = time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        result, metrics = simulate(
            params, y0,
            t_span=(0.0, ADSORPTION_DURATION_S),
            t_eval=t_eval,
            dense_output=False,
            skip_stiffness_check=True,
        )
    if not result.success:
        raise RuntimeError(f"adsorption phase failed: {result.message}")
    wall_ads = time.perf_counter() - t0
    print(f"[generate] adsorption OK · wall {wall_ads:.1f} s · steps {metrics.n_steps}")

    state_post_ads = result.y[:, -1].copy()
    print(f"[generate] depressurizing P={P_high:.0f} → {P_LOW_PA:.0f} Pa (well-mixed)")
    state_post_depr, vented = depressurize(
        state_post_ads, n, params.grid.dz_widths_m,
        col.cross_section_m2, col.void_fraction,
        P_high, P_LOW_PA,
    )
    print(
        f"[generate] vented: h2o={vented['h2o']:.4f} mol  "
        f"co2={vented['co2']:.4f} mol"
    )

    np.savez(
        output_path,
        state=state_post_depr,
        n=n,
        P_high=P_high,
        P_low=P_LOW_PA,
        adsorption_duration_s=ADSORPTION_DURATION_S,
        wall_time_s=wall_ads,
    )
    print(f"[generate] saved {output_path}")
    return state_post_depr


def load_initial_state(path: Path = STATE_CACHE_PATH) -> np.ndarray:
    if not path.exists():
        raise FileNotFoundError(
            f"cycle-reality state cache not found at {path}; "
            "run `generate` first."
        )
    data = np.load(path, allow_pickle=False)
    return data["state"]


# ---------------------------------------------------------------------------
# Heating-mode params builder (cycle context)
# ---------------------------------------------------------------------------
def _build_heating_params() -> tuple[SimulationParams, ColumnConfig]:
    dbd = load_dbd()
    col = ColumnConfig.from_dbd(dbd)
    op_heat = OperatingConditions(
        mode="heating",
        flow_nm3h=FLOW_REGEN_NM3H,
        P_op_Pa=P_LOW_PA,
        T_in_K=T_REGEN_K,
        y_h2o_in=0.0,
        y_co2_in=0.0,
        flow_direction="reverse",
    )
    return SimulationParams.build(col, op_heat), col


# ---------------------------------------------------------------------------
# Stiffness profile (chunk-by-chunk)
# ---------------------------------------------------------------------------
def _state_diagnostics(
    state: np.ndarray, params: SimulationParams,
) -> tuple[float, float, float]:
    """Return (q_h2o_max_value, q_h2o_max_z, T_at_mid_z) for the current state."""
    n = params.grid.n_total
    z = params.grid.z_centers_m
    q_h2o = state[var_slice("q_h2o", n)]
    T = state[var_slice("T", n)]
    if q_h2o.size > 0 and float(np.max(q_h2o)) > 0.0:
        q_max = float(np.max(q_h2o))
        q_max_z = float(z[int(np.argmax(q_h2o))])
    else:
        q_max = 0.0
        q_max_z = float("nan")
    bed_height = float(z[-1])                              # last cell center ≈ bed top
    mid_idx = int(np.argmin(np.abs(z - 0.5 * bed_height)))
    T_mid = float(T[mid_idx])
    return q_max, q_max_z, T_mid


def profile_stiffness_until_crash(
    initial_state: np.ndarray,
    max_step: float = DEFAULT_MAX_STEP,
    chunk_s: float = PROFILE_CHUNK_S,
    total_s: float = HEATING_DURATION_S,
) -> StiffnessProfileResult:
    """Walk forward in chunks of `chunk_s`, recording stiffness at every chunk."""
    params, _ = _build_heating_params()
    info0 = estimate_stiffness_ratio(params, y_test=initial_state)
    q_max0, q_max_z0, T_mid0 = _state_diagnostics(initial_state, params)
    res = StiffnessProfileResult(
        max_step=max_step,
        total_attempted_s=total_s,
        chunk_s=chunk_s,
        success=False,
        crashed_at_s=0.0,
        crash_message="",
        cumulative_wall_s=0.0,
    )
    res.entries.append(
        StiffnessProfileEntry(
            t_s=0.0,
            stiffness_ratio=float(info0["stiffness_ratio"]),
            band=str(info0["band"]),
            chunk_wall_s=0.0,
            cumulative_wall_s=0.0,
            q_h2o_max_mol_kg=q_max0,
            q_h2o_max_z_m=q_max_z0,
            T_mid_K=T_mid0,
        )
    )

    state = initial_state.copy()
    t = 0.0
    cumulative_wall = 0.0
    while t < total_s:
        next_t = min(t + chunk_s, total_s)
        chunk_t0 = time.perf_counter()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                result, _ = simulate(
                    params, state,
                    t_span=(t, next_t),
                    t_eval=np.array([next_t]),
                    dense_output=False,
                    skip_stiffness_check=True,
                    max_step=max_step,
                )
            chunk_wall = time.perf_counter() - chunk_t0
            cumulative_wall += chunk_wall
            if not result.success:
                res.crashed_at_s = t
                res.crash_message = f"solver returned success=False: {result.message}"
                break
            state = result.y[:, -1]
            info = estimate_stiffness_ratio(params, y_test=state)
            q_max, q_max_z, T_mid = _state_diagnostics(state, params)
            res.entries.append(
                StiffnessProfileEntry(
                    t_s=next_t,
                    stiffness_ratio=float(info["stiffness_ratio"]),
                    band=str(info["band"]),
                    chunk_wall_s=chunk_wall,
                    cumulative_wall_s=cumulative_wall,
                    q_h2o_max_mol_kg=q_max,
                    q_h2o_max_z_m=q_max_z,
                    T_mid_K=T_mid,
                )
            )
            t = next_t
            print(
                f"[profile] t={t:7.1f}s  stiffness={info['stiffness_ratio']:.3e}"
                f"  band={info['band']}  chunk_wall={chunk_wall:.2f}s  "
                f"cum={cumulative_wall:.1f}s"
            )
        except Exception as exc:                            # noqa: BLE001
            chunk_wall = time.perf_counter() - chunk_t0
            cumulative_wall += chunk_wall
            res.crashed_at_s = t
            res.crash_message = f"{type(exc).__name__}: {exc}"
            print(
                f"[profile] CRASH at t={t:.1f}s during chunk to {next_t:.1f}s: "
                f"{res.crash_message}"
            )
            break
    else:
        res.success = True

    res.cumulative_wall_s = cumulative_wall
    return res


# ---------------------------------------------------------------------------
# max_step sweep
# ---------------------------------------------------------------------------
def sweep_max_step(
    initial_state: np.ndarray,
    candidates: tuple[float, ...] = SWEEP_CANDIDATES,
    total_s: float = HEATING_DURATION_S,
) -> list[SweepEntry]:
    """Attempt full heating for each candidate `max_step`; record outcome."""
    params, _ = _build_heating_params()
    results: list[SweepEntry] = []
    for ms in candidates:
        print(f"[sweep] attempting full {total_s/3600:.2f} h heating with max_step={ms}…")
        n_eval = max(int(round(total_s / 60.0)), 2)
        t_eval = np.linspace(0.0, total_s, n_eval)
        t0 = time.perf_counter()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                result, metrics = simulate(
                    params, initial_state,
                    t_span=(0.0, total_s),
                    t_eval=t_eval,
                    dense_output=False,
                    skip_stiffness_check=True,
                    max_step=ms,
                )
            wall = time.perf_counter() - t0
            ok = bool(result.success)
            msg = "" if ok else str(result.message)
            results.append(SweepEntry(
                max_step=ms, success=ok, wall_time_s=wall,
                n_steps=int(metrics.n_steps),
                avg_ms_per_step=float(metrics.avg_ms_per_step),
                crash_message=msg,
            ))
            tag = "PASS" if ok else "FAIL"
            print(
                f"[sweep] max_step={ms} {tag}  wall={wall:.1f}s  "
                f"steps={metrics.n_steps}"
            )
            if ok:
                # Smallest-passing-max_step is enough for production; stop early.
                break
        except Exception as exc:                            # noqa: BLE001
            wall = time.perf_counter() - t0
            results.append(SweepEntry(
                max_step=ms, success=False, wall_time_s=wall,
                n_steps=0, avg_ms_per_step=float("nan"),
                crash_message=f"{type(exc).__name__}: {exc}",
            ))
            print(f"[sweep] max_step={ms} CRASH after {wall:.1f}s: {exc}")
    return results


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _save_profile_json(profile: StiffnessProfileResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(profile)
    data["entries"] = [asdict(e) for e in profile.entries]
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def plot_stiffness_profile(profile: StiffnessProfileResult, output_path: Path) -> None:
    """Plot the time profile: stiffness (log) + MTZ position + T_mid overlays.

    Vertical line marks crash time when `profile.success=False`.
    """
    # Local import to avoid hard dep at startup.
    import matplotlib.pyplot as plt

    if not profile.entries:
        return
    t = np.array([e.t_s for e in profile.entries])
    stiff = np.array([e.stiffness_ratio for e in profile.entries])
    q_z = np.array([e.q_h2o_max_z_m for e in profile.entries])
    T_mid = np.array([e.T_mid_K for e in profile.entries])

    fig, ax_stiff = plt.subplots(figsize=(9, 5))
    ax_stiff.set_yscale("log")
    ax_stiff.plot(t, stiff, "o-", color="tab:blue", label="stiffness ratio")
    ax_stiff.set_xlabel("heating sim time t (s)")
    ax_stiff.set_ylabel("stiffness ratio (log)", color="tab:blue")
    ax_stiff.tick_params(axis="y", labelcolor="tab:blue")
    ax_stiff.grid(True, which="both", linestyle=":", alpha=0.5)

    ax_state = ax_stiff.twinx()
    ax_state.plot(t, q_z, "s--", color="tab:green", label="q_h2o max z (m)")
    ax_state.plot(t, (T_mid - 273.15), "^-", color="tab:red", label="T(z=L/2) − 273.15 (°C)")
    ax_state.set_ylabel("MTZ z (m)  ·  T_mid (°C)", color="tab:gray")
    ax_state.tick_params(axis="y", labelcolor="tab:gray")

    if not profile.success and np.isfinite(profile.crashed_at_s):
        ax_stiff.axvline(profile.crashed_at_s, color="tab:orange", linestyle="--", linewidth=2)
        ax_stiff.text(
            profile.crashed_at_s, ax_stiff.get_ylim()[1] * 0.4,
            f" crash @ t={profile.crashed_at_s:.0f}s",
            color="tab:orange", rotation=90, va="top",
        )

    title = (
        f"Heating stiffness profile (max_step={profile.max_step}, chunk={profile.chunk_s:.0f}s)"
        + ("  —  COMPLETED" if profile.success else "  —  CRASHED")
    )
    ax_stiff.set_title(title)

    lines_a, labels_a = ax_stiff.get_legend_handles_labels()
    lines_b, labels_b = ax_state.get_legend_handles_labels()
    ax_stiff.legend(lines_a + lines_b, labels_a + labels_b, loc="best")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_path, dpi=130)
    plt.close(fig)


def _save_sweep_json(entries: list[SweepEntry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump([asdict(e) for e in entries], f, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("generate", "profile", "sweep", "all"),
        help="diagnostic step to run",
    )
    parser.add_argument("--max-step", type=float, default=DEFAULT_MAX_STEP)
    parser.add_argument("--chunk-s", type=float, default=PROFILE_CHUNK_S)
    parser.add_argument("--total-s", type=float, default=HEATING_DURATION_S)
    parser.add_argument("--state-path", type=Path, default=STATE_CACHE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DIAG_DIR)
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.command in ("generate", "all"):
        if not args.state_path.exists() or args.command == "generate":
            generate_cycle_reality_state(args.state_path)
        else:
            print(f"[generate] cache exists at {args.state_path}; skipping")

    if args.command in ("profile", "all"):
        state = load_initial_state(args.state_path)
        profile = profile_stiffness_until_crash(
            state, max_step=args.max_step, chunk_s=args.chunk_s,
            total_s=args.total_s,
        )
        out = args.output_dir / f"profile_max_step_{args.max_step:g}.json"
        _save_profile_json(profile, out)
        plot_path = args.output_dir / f"profile_max_step_{args.max_step:g}.png"
        plot_stiffness_profile(profile, plot_path)
        print(f"[profile] saved {out}")
        print(f"[profile] saved {plot_path}")

    if args.command in ("sweep", "all"):
        state = load_initial_state(args.state_path)
        entries = sweep_max_step(state, total_s=args.total_s)
        out = args.output_dir / "max_step_sweep.json"
        _save_sweep_json(entries, out)
        print(f"[sweep] saved {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
