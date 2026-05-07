"""Tests for Danckwerts boundary conditions."""

from __future__ import annotations

import math

import pytest

from phase2_simulation.adsorption_1d import OperatingConditions
from phase2_simulation.adsorption_1d.boundary import (
    R_GAS,
    inlet_cell_index,
    inlet_concentrations,
    inlet_temperature,
    outlet_cell_index,
    signed_velocity,
    superficial_velocity,
)


@pytest.fixture
def op_adsorption() -> OperatingConditions:
    return OperatingConditions(
        mode="adsorption",
        flow_nm3h=200.0,
        P_op_Pa=6.01325e5,
        T_in_K=288.15,
        y_h2o_in=2823.07e-6,
        y_co2_in=400e-6,
        flow_direction="forward",
    )


@pytest.fixture
def op_heating() -> OperatingConditions:
    return OperatingConditions(
        mode="heating",
        flow_nm3h=60.0,                # 30% of feed (DBD §3 regen ratio)
        P_op_Pa=6.01325e5,
        T_in_K=473.15,                 # 200 °C peak regen
        y_h2o_in=0.0,
        y_co2_in=0.0,
        flow_direction="reverse",
    )


@pytest.fixture
def op_cooling() -> OperatingConditions:
    return OperatingConditions(
        mode="cooling",
        flow_nm3h=60.0,
        P_op_Pa=6.01325e5,
        T_in_K=288.15,
        y_h2o_in=0.0,
        y_co2_in=0.0,
        flow_direction="reverse",
    )


# ---------------------------------------------------------------------------
# Inlet concentrations
# ---------------------------------------------------------------------------

def test_inlet_concentrations_adsorption(op_adsorption: OperatingConditions) -> None:
    T = op_adsorption.T_in_K
    P = op_adsorption.P_op_Pa
    C = inlet_concentrations(op_adsorption, T)
    assert math.isclose(C["h2o"], op_adsorption.y_h2o_in * P / (R_GAS * T))
    assert math.isclose(C["co2"], op_adsorption.y_co2_in * P / (R_GAS * T))


def test_inlet_concentrations_heating_dry(op_heating: OperatingConditions) -> None:
    C = inlet_concentrations(op_heating, op_heating.T_in_K)
    assert C["h2o"] == 0.0
    assert C["co2"] == 0.0


def test_inlet_concentrations_cooling_dry(op_cooling: OperatingConditions) -> None:
    C = inlet_concentrations(op_cooling, op_cooling.T_in_K)
    assert C["h2o"] == 0.0
    assert C["co2"] == 0.0


def test_inlet_concentrations_rejects_bad_T(op_adsorption: OperatingConditions) -> None:
    with pytest.raises(ValueError, match="T_inlet_K"):
        inlet_concentrations(op_adsorption, 0.0)


def test_inlet_temperature_passthrough(
    op_adsorption: OperatingConditions, op_heating: OperatingConditions
) -> None:
    assert inlet_temperature(op_adsorption) == op_adsorption.T_in_K
    assert math.isclose(inlet_temperature(op_heating), 473.15)


# ---------------------------------------------------------------------------
# Geometric inlet/outlet placement
# ---------------------------------------------------------------------------

def test_inlet_outlet_indices_forward(op_adsorption: OperatingConditions) -> None:
    assert inlet_cell_index(op_adsorption, 100) == 0
    assert outlet_cell_index(op_adsorption, 100) == 99


def test_inlet_outlet_indices_reverse(op_heating: OperatingConditions) -> None:
    assert inlet_cell_index(op_heating, 100) == 99
    assert outlet_cell_index(op_heating, 100) == 0


# ---------------------------------------------------------------------------
# Superficial velocity
# ---------------------------------------------------------------------------

def test_superficial_velocity_design(op_adsorption: OperatingConditions) -> None:
    """At design (200 Nm³/h, 6.013 bar(a), 15 °C, D=0.25 m): u ≈ 0.201 m/s (DBD §5)."""
    A_xs = math.pi * 0.250**2 / 4
    u = superficial_velocity(op_adsorption, op_adsorption.T_in_K, A_xs)
    assert math.isclose(u, 0.201, abs_tol=0.005), f"u = {u:.5f} m/s"


def test_superficial_velocity_temperature_scales(op_adsorption: OperatingConditions) -> None:
    """u scales linearly with T (ideal gas)."""
    A_xs = math.pi * 0.250**2 / 4
    u_cold = superficial_velocity(op_adsorption, 288.15, A_xs)
    u_hot = superficial_velocity(op_adsorption, 576.30, A_xs)
    assert math.isclose(u_hot / u_cold, 2.0, rel_tol=1e-9)


def test_superficial_velocity_pressure_scales(op_adsorption: OperatingConditions) -> None:
    """u scales inversely with P (ideal gas)."""
    A_xs = math.pi * 0.250**2 / 4
    op_high_P = OperatingConditions(
        mode=op_adsorption.mode,
        flow_nm3h=op_adsorption.flow_nm3h,
        P_op_Pa=op_adsorption.P_op_Pa * 2,
        T_in_K=op_adsorption.T_in_K,
        y_h2o_in=op_adsorption.y_h2o_in,
        y_co2_in=op_adsorption.y_co2_in,
        flow_direction=op_adsorption.flow_direction,
    )
    u1 = superficial_velocity(op_adsorption, 288.15, A_xs)
    u2 = superficial_velocity(op_high_P, 288.15, A_xs)
    assert math.isclose(u2 / u1, 0.5, rel_tol=1e-9)


def test_superficial_velocity_rejects_bad_inputs(op_adsorption: OperatingConditions) -> None:
    with pytest.raises(ValueError):
        superficial_velocity(op_adsorption, 0.0, 0.05)
    with pytest.raises(ValueError):
        superficial_velocity(op_adsorption, 288.15, 0.0)


# ---------------------------------------------------------------------------
# Signed velocity (flow-direction sign convention)
# ---------------------------------------------------------------------------

def test_signed_velocity_forward(op_adsorption: OperatingConditions) -> None:
    assert signed_velocity(op_adsorption, 0.201) == 0.201


def test_signed_velocity_reverse(op_heating: OperatingConditions) -> None:
    assert signed_velocity(op_heating, 0.201) == -0.201


def test_signed_velocity_rejects_negative(op_adsorption: OperatingConditions) -> None:
    with pytest.raises(ValueError):
        signed_velocity(op_adsorption, -0.1)
