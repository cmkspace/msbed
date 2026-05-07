"""Configuration dataclasses for the 1D adsorption PDE solver.

Locked decisions (DD-011 pending):
  - **Decision 1**: single ODE RHS dispatched on `mode` ∈
    {'adsorption', 'heating', 'cooling'}. Depressurize / repressurize are
    handled as state jumps in run_cycle.py (out of scope here).
  - **Decision 2A** (default): AA layer adsorbs only H₂O, 13X layer adsorbs
    only CO₂. The (layer, species) → bool matrix is stored on
    `OperatingConditions.adsorbs_in`, so flipping to **2C** (full 2×2
    adsorption) is a one-line config change.
  - **Decision 3B**: 5N ODE state always (C_h2o, q_h2o, C_co2, q_co2, T per
    cell). Energy balance is included from the start; the `test_rhs_isothermal`
    case forces ΔH=0 to verify isothermal mass balance before activating
    energy coupling in regular use.
  - **Decision 4A**: P is constant during a single mode integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import pi
from typing import Any, Literal

# Layer + species identifiers
LAYER_ALUMINA = "alumina"
LAYER_13X = "zeolite_13x"
LAYERS: tuple[str, str] = (LAYER_ALUMINA, LAYER_13X)
SPECIES: tuple[str, str] = ("h2o", "co2")

Mode = Literal["adsorption", "heating", "cooling"]
FlowDirection = Literal["forward", "reverse"]


def default_adsorbs_in_2a() -> dict[tuple[str, str], bool]:
    """Decision 2A (default): AA only H₂O, 13X only CO₂."""
    return {
        (LAYER_ALUMINA, "h2o"): True,
        (LAYER_ALUMINA, "co2"): False,
        (LAYER_13X, "h2o"): False,
        (LAYER_13X, "co2"): True,
    }


def default_adsorbs_in_2c() -> dict[tuple[str, str], bool]:
    """Decision 2C (future): full 2×2 — both layers adsorb both species."""
    return {(layer, sp): True for layer in LAYERS for sp in SPECIES}


@dataclass(frozen=True)
class ColumnConfig:
    """Column geometry + grid configuration.

    `bed_height_m` is a derived property (alumina + 13X) — the layer heights
    are the single source of truth. This avoids the mm-scale rounding mismatch
    that exists in dbd_locked.yaml between `bed_height_m: 1.700` and the
    actual sum `0.925 + 0.776 = 1.701`.

    Attributes:
        diameter_m: Internal column diameter (m).
        alumina_height_m: Alumina layer height (m).
        zeolite_13x_height_m: 13X layer height (m).
        void_fraction: Inter-particle bed voidage ε_b (-).
        n_grid_total: Total finite-volume cells (must be even, ≥ 4).
            Split evenly: n_alumina = n_13x = n_grid_total / 2.
    """

    diameter_m: float
    alumina_height_m: float
    zeolite_13x_height_m: float
    void_fraction: float
    n_grid_total: int

    def __post_init__(self) -> None:
        if self.diameter_m <= 0:
            raise ValueError(f"diameter_m must be > 0, got {self.diameter_m}")
        if self.alumina_height_m <= 0 or self.zeolite_13x_height_m <= 0:
            raise ValueError("Layer heights must be > 0")
        if not 0 < self.void_fraction < 1:
            raise ValueError(f"void_fraction must be in (0,1), got {self.void_fraction}")
        if self.n_grid_total < 4 or self.n_grid_total % 2 != 0:
            raise ValueError(
                f"n_grid_total must be even and >= 4, got {self.n_grid_total}"
            )

    @property
    def bed_height_m(self) -> float:
        return self.alumina_height_m + self.zeolite_13x_height_m

    @property
    def n_alumina(self) -> int:
        return self.n_grid_total // 2

    @property
    def n_13x(self) -> int:
        return self.n_grid_total - self.n_alumina

    @property
    def cross_section_m2(self) -> float:
        return pi * self.diameter_m**2 / 4.0

    @classmethod
    def from_dbd(cls, dbd: dict[str, Any]) -> ColumnConfig:
        """Construct from a parsed dbd_locked.yaml dict.

        Validates that the DBD's listed `bed_height_m` matches the layer sum
        within 5 mm tolerance (DBD rounds to 1 mm in display).
        """
        col = dbd["column"]
        sim = dbd["simulation"]
        cfg = cls(
            diameter_m=float(col["diameter_m"]),
            alumina_height_m=float(col["alumina_height_m"]),
            zeolite_13x_height_m=float(col["zeolite_13x_height_m"]),
            void_fraction=float(col["void_fraction"]),
            n_grid_total=int(sim["grid_points"]),
        )
        if "bed_height_m" in col:
            dbd_bed = float(col["bed_height_m"])
            if abs(cfg.bed_height_m - dbd_bed) > 5.0e-3:
                raise ValueError(
                    f"DBD bed_height_m ({dbd_bed}) and layer sum "
                    f"({cfg.bed_height_m:.6f}) disagree by more than 5 mm"
                )
        return cfg


@dataclass(frozen=True)
class OperatingConditions:
    """Mode-specific operating state — held constant within a single solve_ivp call.

    Decision 4A: pressure is constant during the mode. Inter-mode jumps
    (depressurize / repressurize) are handled outside this module by
    advancing the state vector with a separate algorithm.

    Attributes:
        mode: 'adsorption' | 'heating' | 'cooling'.
        flow_nm3h: Standard volumetric flow (Nm³/h, 0 °C / 1 atm reference).
        P_op_Pa: Operating absolute pressure (Pa).
        T_in_K: Inlet gas temperature (K).
        y_h2o_in, y_co2_in: Inlet mole fractions.
        flow_direction: 'forward' (z increasing) for adsorption,
            'reverse' for regen (counter-current).
        adsorbs_in: (layer, species) → bool matrix. Default = Decision 2A.
            Pass `default_adsorbs_in_2c()` to enable full 2×2 adsorption.
    """

    mode: Mode
    flow_nm3h: float
    P_op_Pa: float
    T_in_K: float
    y_h2o_in: float
    y_co2_in: float
    flow_direction: FlowDirection = "forward"
    adsorbs_in: dict[tuple[str, str], bool] = field(default_factory=default_adsorbs_in_2a)

    def __post_init__(self) -> None:
        if self.flow_nm3h <= 0:
            raise ValueError(f"flow_nm3h must be > 0, got {self.flow_nm3h}")
        if self.P_op_Pa <= 0:
            raise ValueError(f"P_op_Pa must be > 0, got {self.P_op_Pa}")
        if self.T_in_K <= 0:
            raise ValueError(f"T_in_K must be > 0, got {self.T_in_K}")
        if not (0.0 <= self.y_h2o_in <= 1.0):
            raise ValueError(f"y_h2o_in must be in [0,1], got {self.y_h2o_in}")
        if not (0.0 <= self.y_co2_in <= 1.0):
            raise ValueError(f"y_co2_in must be in [0,1], got {self.y_co2_in}")
        if self.y_h2o_in + self.y_co2_in > 1.0 + 1.0e-9:
            raise ValueError(
                f"y_h2o + y_co2 = {self.y_h2o_in + self.y_co2_in} must not exceed 1"
            )
        if self.mode not in ("adsorption", "heating", "cooling"):
            raise ValueError(f"unknown mode: {self.mode!r}")
        if self.flow_direction not in ("forward", "reverse"):
            raise ValueError(f"unknown flow_direction: {self.flow_direction!r}")

    def adsorbs(self, layer: str, species: str) -> bool:
        """Whether `species` adsorbs in `layer` under the current adsorbs_in matrix."""
        return self.adsorbs_in.get((layer, species), False)
