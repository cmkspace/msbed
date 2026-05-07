"""Tests for ColumnConfig and Grid construction."""

from __future__ import annotations

import math

import numpy as np
import pytest

from phase2_simulation.adsorption_1d import (
    ColumnConfig,
    build_grid,
)
from phase2_simulation.ldf_kinetics import load_dbd


@pytest.fixture(scope="module")
def col() -> ColumnConfig:
    return ColumnConfig.from_dbd(load_dbd())


# ---------------------------------------------------------------------------
# ColumnConfig validation
# ---------------------------------------------------------------------------

def test_column_config_rejects_bad_void() -> None:
    with pytest.raises(ValueError, match="void_fraction"):
        ColumnConfig(
            diameter_m=0.25,
            alumina_height_m=0.925,
            zeolite_13x_height_m=0.776,
            void_fraction=1.5,
            n_grid_total=100,
        )


def test_column_config_rejects_odd_grid() -> None:
    with pytest.raises(ValueError, match="even"):
        ColumnConfig(
            diameter_m=0.25,
            alumina_height_m=0.925,
            zeolite_13x_height_m=0.776,
            void_fraction=0.38,
            n_grid_total=99,
        )


def test_column_config_rejects_nonpositive_layer() -> None:
    with pytest.raises(ValueError, match="Layer heights"):
        ColumnConfig(
            diameter_m=0.25,
            alumina_height_m=0.0,
            zeolite_13x_height_m=0.776,
            void_fraction=0.38,
            n_grid_total=100,
        )


def test_column_config_from_dbd_rejects_height_mismatch() -> None:
    """If the optional DBD bed_height_m disagrees with the layer sum > 5 mm, fail."""
    bad_dbd = {
        "column": {
            "diameter_m": 0.25,
            "alumina_height_m": 0.925,
            "zeolite_13x_height_m": 0.776,
            "bed_height_m": 2.0,  # off by ~30 cm
            "void_fraction": 0.38,
        },
        "simulation": {"grid_points": 100},
    }
    with pytest.raises(ValueError, match="disagree"):
        ColumnConfig.from_dbd(bad_dbd)


def test_column_config_from_dbd(col: ColumnConfig) -> None:
    """from_dbd reads SSOT correctly; bed_height_m is derived from layer sum."""
    assert col.n_grid_total == 100
    assert col.n_alumina == 50
    assert col.n_13x == 50
    assert math.isclose(col.diameter_m, 0.250)
    # Derived bed_height is the layer sum (1.701), not the rounded DBD display (1.700)
    assert math.isclose(col.bed_height_m, 1.701, abs_tol=1.0e-9)
    assert math.isclose(col.cross_section_m2, math.pi * 0.250**2 / 4)


# ---------------------------------------------------------------------------
# Grid layout
# ---------------------------------------------------------------------------

def test_grid_total_size(col: ColumnConfig) -> None:
    g = build_grid(col)
    assert g.n_total == 100
    assert g.z_centers_m.shape == (100,)
    assert g.dz_widths_m.shape == (100,)
    assert g.layer_ids.shape == (100,)


def test_grid_layer_split(col: ColumnConfig) -> None:
    g = build_grid(col)
    assert g.n_alumina == 50
    assert g.n_13x == 50
    assert np.all(g.layer_ids[:50] == 0)
    assert np.all(g.layer_ids[50:] == 1)


def test_grid_monotonic_centers(col: ColumnConfig) -> None:
    g = build_grid(col)
    assert np.all(np.diff(g.z_centers_m) > 0)


def test_grid_uniform_within_layer(col: ColumnConfig) -> None:
    """dz uniform within each layer; different across boundary."""
    g = build_grid(col)
    dz_a = g.dz_widths_m[g.alumina_mask]
    dz_b = g.dz_widths_m[g.thirteen_x_mask]
    assert np.allclose(dz_a, dz_a[0])
    assert np.allclose(dz_b, dz_b[0])
    # alumina/13X layer heights differ ⇒ dz differs
    assert not math.isclose(dz_a[0], dz_b[0])
    # numerical sanity
    assert math.isclose(dz_a[0], col.alumina_height_m / col.n_alumina)
    assert math.isclose(dz_b[0], col.zeolite_13x_height_m / col.n_13x)


def test_grid_full_coverage(col: ColumnConfig) -> None:
    """Cell faces span exactly [0, bed_height_m]."""
    g = build_grid(col)
    z_inlet_face = g.z_centers_m[0] - g.dz_widths_m[0] / 2
    z_outlet_face = g.z_centers_m[-1] + g.dz_widths_m[-1] / 2
    assert math.isclose(z_inlet_face, 0.0, abs_tol=1.0e-9)
    assert math.isclose(z_outlet_face, col.bed_height_m, abs_tol=1.0e-9)


def test_grid_layer_boundary_alignment(col: ColumnConfig) -> None:
    """Last alumina face and first 13X face align at alumina_height_m."""
    g = build_grid(col)
    z_a_top = g.z_centers_m[col.n_alumina - 1] + g.dz_widths_m[col.n_alumina - 1] / 2
    z_b_bot = g.z_centers_m[col.n_alumina] - g.dz_widths_m[col.n_alumina] / 2
    assert math.isclose(z_a_top, col.alumina_height_m, abs_tol=1.0e-9)
    assert math.isclose(z_b_bot, col.alumina_height_m, abs_tol=1.0e-9)


def test_grid_layer_name_at(col: ColumnConfig) -> None:
    g = build_grid(col)
    assert g.layer_name_at(0) == "alumina"
    assert g.layer_name_at(col.n_alumina - 1) == "alumina"
    assert g.layer_name_at(col.n_alumina) == "zeolite_13x"
    assert g.layer_name_at(col.n_grid_total - 1) == "zeolite_13x"
