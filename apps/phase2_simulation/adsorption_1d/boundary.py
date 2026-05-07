"""Danckwerts boundary conditions for the 1D adsorption PDE.

Mode-aware inlet boundary values:
  - 'adsorption': inlet C from feed composition (y_h2o, y_co2); T_in is feed T.
  - 'heating'   : inlet C = 0 (dry purge gas); T_in is regen T.
  - 'cooling'   : inlet C = 0 (dry purge gas); T_in is cooling T.

Geometric inlet location depends on `flow_direction`:
  - 'forward': inlet at z=0 → cell index 0.
  - 'reverse': inlet at z=L → cell index n_grid - 1.

This module provides the boundary VALUES; the spatial discretization in
rhs.py handles their geometric placement and the actual face-flux
construction (upwind advection + central-difference dispersion + Danckwerts).
"""

from __future__ import annotations

from .config import OperatingConditions

# Universal gas constant (J/(mol·K))
R_GAS = 8.314462618

# Standard reference state for Nm³ → actual m³ conversion
P_STD_PA = 101325.0
T_STD_K = 273.15


def inlet_concentrations(op: OperatingConditions, T_inlet_K: float) -> dict[str, float]:
    """Inlet boundary concentrations (mol/m³) per species under the active mode.

    Args:
        op: OperatingConditions defining mode + composition.
        T_inlet_K: Inlet boundary temperature (K) for ideal-gas C = yP/RT.
            Typically `op.T_in_K`, but during transient mode start the local
            T at the inlet face may differ.

    Returns:
        Dict ``{'h2o': C_h2o, 'co2': C_co2}`` (mol/m³).
    """
    if T_inlet_K <= 0:
        raise ValueError(f"T_inlet_K must be > 0, got {T_inlet_K}")
    if op.mode == "adsorption":
        y_h2o, y_co2 = op.y_h2o_in, op.y_co2_in
    else:
        # heating / cooling: dry purge gas
        y_h2o, y_co2 = 0.0, 0.0
    factor = op.P_op_Pa / (R_GAS * T_inlet_K)
    return {"h2o": y_h2o * factor, "co2": y_co2 * factor}


def inlet_temperature(op: OperatingConditions) -> float:
    """Inlet boundary temperature (K) under the active mode."""
    return op.T_in_K


def inlet_cell_index(op: OperatingConditions, n_grid: int) -> int:
    """Cell index where the inlet boundary applies.

    Forward flow → inlet at z=0 → index 0.
    Reverse flow → inlet at z=L → index n_grid - 1.
    """
    if n_grid < 1:
        raise ValueError(f"n_grid must be >= 1, got {n_grid}")
    return 0 if op.flow_direction == "forward" else n_grid - 1


def outlet_cell_index(op: OperatingConditions, n_grid: int) -> int:
    """Cell index at the geometric outlet (opposite of inlet)."""
    if n_grid < 1:
        raise ValueError(f"n_grid must be >= 1, got {n_grid}")
    return n_grid - 1 if op.flow_direction == "forward" else 0


def superficial_velocity(
    op: OperatingConditions,
    T_K: float,
    cross_section_m2: float,
) -> float:
    """Superficial gas velocity (m/s) at the bed inlet from standard volumetric flow.

    flow_nm3h is referenced to standard conditions (0 °C, 1 atm). Conversion
    to actual volumetric flow at (op.P_op_Pa, T_K) follows the ideal-gas law:

        Q_actual = Q_std · (P_std / P_op) · (T / T_std)

    The returned value is always positive (magnitude); flow direction is
    handled by `flow_direction` in rhs.py.

    Args:
        op: OperatingConditions providing flow_nm3h and P_op_Pa.
        T_K: Local gas temperature (K) at which to evaluate the velocity.
        cross_section_m2: Empty column cross-section area A_xs = π·D²/4.

    Returns:
        Superficial velocity magnitude (m/s).
    """
    if T_K <= 0:
        raise ValueError(f"T_K must be > 0, got {T_K}")
    if cross_section_m2 <= 0:
        raise ValueError(f"cross_section_m2 must be > 0, got {cross_section_m2}")
    Q_std_m3_s = op.flow_nm3h / 3600.0
    Q_actual_m3_s = Q_std_m3_s * (P_STD_PA / op.P_op_Pa) * (T_K / T_STD_K)
    return Q_actual_m3_s / cross_section_m2


def signed_velocity(op: OperatingConditions, u_magnitude: float) -> float:
    """Signed superficial velocity: positive for forward flow, negative for reverse.

    rhs.py uses the sign to drive upwind logic without conditional branches.
    """
    if u_magnitude < 0:
        raise ValueError(f"u_magnitude must be >= 0 (caller passes magnitude), got {u_magnitude}")
    return u_magnitude if op.flow_direction == "forward" else -u_magnitude
