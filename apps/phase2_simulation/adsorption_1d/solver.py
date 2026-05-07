"""scipy.integrate.solve_ivp wrapper with BDF + sparse Jacobian pattern (Step 5.2).

This module provides the high-level entry point for time-integrating the
1D adsorption PDE. The wrapper handles:

  - **Pre-flight stiffness band dispatch** (DD-012): estimate ratio at y0 and
    refuse to start the solve if the system is in the ABORT band.
  - **BDF + sparse Jacobian pattern** (DD-013): pass `jac_sparsity` to
    `solve_ivp` so its internal finite-difference Jacobian only probes the
    structurally-nonzero entries (~3,094 for N=100 instead of 250,000).
  - **Performance instrumentation**: wall time, accepted BDF steps, RHS
    evaluations, average ms/step, sparsity stats — returned in `SolverMetrics`
    for downstream comparison against the Phase 5B (analytical Jac) decision
    threshold (4 h sim ≷ 30 min wall).

Reference: PHASE2_SPEC §3.2; DD-012 (stiffness thresholds); DD-013 (sparsity).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.integrate import solve_ivp

from .jacobian import jacobian_sparsity_pattern
from .rhs import SimulationParams, estimate_stiffness_ratio, rhs_full
from .state import N_VARS, SimulationResult, var_slice


@dataclass
class SolverMetrics:
    """Performance metrics from a single `simulate()` call."""

    wall_time_s: float
    n_steps: int                 # accepted integration steps (sol.t.size − 1)
    n_eval_rhs: int              # total RHS evaluations (sol.nfev)
    avg_ms_per_step: float
    sparsity_nnz: int
    sparsity_pct: float          # percent zero entries
    stiffness_band: str          # "OK" | "WARN" | "ABORT" | "skipped"
    stiffness_ratio: float
    method: str                  # always "BDF" for now

    def summary(self) -> str:
        return (
            f"{self.wall_time_s:.3f}s wall · {self.n_steps} steps · "
            f"{self.avg_ms_per_step:.2f} ms/step · {self.n_eval_rhs} rhs evals · "
            f"stiffness={self.stiffness_ratio:.2e} [{self.stiffness_band}] · "
            f"sparse {self.sparsity_nnz} nnz ({self.sparsity_pct:.2f}% zero)"
        )


def initial_state_clean_bed(
    params: SimulationParams,
    T_init_K: float | None = None,
) -> np.ndarray:
    """Default initial state for a clean bed: C = q = 0, T uniform.

    Args:
        params: Pre-built SimulationParams.
        T_init_K: Initial bed temperature (K). Defaults to `params.op.T_in_K`.

    Returns:
        State vector of length 5N in Layout B (cell-major).
    """
    n = params.grid.n_total
    y0 = np.zeros(N_VARS * n)
    y0[var_slice("T", n)] = T_init_K if T_init_K is not None else params.op.T_in_K
    return y0


DEFAULT_MAX_STEP_S = 0.1
"""Default upper bound on integrator step (s).

Empirically determined for the Phase 5A path (numerical-FD Jacobian via
`jac_sparsity`). Without a max_step cap, BDF takes overly aggressive steps
once the bed begins to load, and the Newton-iteration matrix becomes
ill-conditioned around t ~ 30–60 s ("RuntimeError: Factor is exactly
singular"). max_step=0.1 yields ~1000 accepted steps per 60 s with no
stability issues at design conditions; smaller values work but cost more.
Phase 5B (analytical Jacobian) is expected to relax this constraint.
"""


def simulate(
    params: SimulationParams,
    y0: np.ndarray,
    t_span: tuple[float, float],
    *,
    isothermal: bool = False,
    rtol: float = 1.0e-6,
    atol: float = 1.0e-9,
    max_step: float | None = DEFAULT_MAX_STEP_S,
    t_eval: np.ndarray | None = None,
    skip_stiffness_check: bool = False,
) -> tuple[SimulationResult, SolverMetrics]:
    """Integrate the adsorption ODE over `t_span` using BDF + sparse Jacobian.

    Pre-flight stiffness band (DD-012):
        * `band == "OK"`    (ratio < warn): proceed with BDF, no Jac.
        * `band == "WARN"`  (warn ≤ ratio < stop): proceed with BDF +
          jac_sparsity (current path; mandatory for our 1.27e8 system).
        * `band == "ABORT"` (ratio ≥ stop): raise RuntimeError before solving.

    Args:
        params: Pre-built `SimulationParams` (energy + stiffness fields populated).
        y0: Initial state vector of length 5N (Layout B).
        t_span: (t_start, t_end) in seconds.
        isothermal: If True, ∂T/∂t ≡ 0 (Step 3 mass-balance verification mode).
        rtol, atol: BDF tolerances. Defaults match `dbd.simulation`.
        max_step: Maximum solver step (s). Default lets BDF auto-select.
        t_eval: Times at which to record output. Default = adaptive grid.
        skip_stiffness_check: Skip the pre-flight (useful for unit tests).

    Returns:
        Tuple (`SimulationResult`, `SolverMetrics`).

    Raises:
        RuntimeError: If pre-flight stiffness band is "ABORT".
        ValueError: If y0 length does not match 5·N.
    """
    n = params.grid.n_total
    if y0.size != N_VARS * n:
        raise ValueError(f"y0 size {y0.size} ≠ expected {N_VARS * n} (5·N)")

    # ---------------- Pre-flight stiffness check ----------------
    if skip_stiffness_check:
        band = "skipped"
        ratio = float("nan")
    else:
        info = estimate_stiffness_ratio(params, y_test=y0)
        band = info["band"]
        ratio = float(info["stiffness_ratio"])
        if band == "ABORT":
            raise RuntimeError(
                f"Pre-flight stiffness ratio {ratio:.2e} exceeds STOP threshold "
                f"{info['stop_threshold']:.1e}; refusing to simulate. "
                f"Recommendation: {info['solver_recommendation']}"
            )

    # ---------------- Sparsity pattern (DD-013) ----------------
    pattern = jacobian_sparsity_pattern(n)

    def f(t: float, y: np.ndarray) -> np.ndarray:
        return rhs_full(t, y, params, isothermal=isothermal)

    kwargs: dict[str, Any] = {
        "method": "BDF",
        "rtol": rtol,
        "atol": atol,
        "jac_sparsity": pattern,
        "dense_output": True,
    }
    if max_step is not None:
        kwargs["max_step"] = max_step
    if t_eval is not None:
        kwargs["t_eval"] = t_eval

    # ---------------- Solve + measure ----------------
    t_start = time.perf_counter()
    sol = solve_ivp(f, t_span, y0, **kwargs)
    wall = time.perf_counter() - t_start

    n_steps = max(sol.t.size - 1, 1)
    avg_ms = (wall / n_steps) * 1000.0
    n_state = N_VARS * n
    sparsity_pct = (1.0 - pattern.nnz / (n_state * n_state)) * 100.0
    n_eval = int(getattr(sol, "nfev", 0))

    metrics = SolverMetrics(
        wall_time_s=wall,
        n_steps=n_steps,
        n_eval_rhs=n_eval,
        avg_ms_per_step=avg_ms,
        sparsity_nnz=pattern.nnz,
        sparsity_pct=sparsity_pct,
        stiffness_band=band,
        stiffness_ratio=ratio,
        method="BDF",
    )

    result = SimulationResult(
        t_s=sol.t,
        y=sol.y,
        grid=params.grid,
        op=params.op,
        success=bool(sol.success),
        message=str(sol.message),
    )

    return result, metrics
