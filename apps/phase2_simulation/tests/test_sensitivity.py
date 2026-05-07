"""Unit tests for run_sensitivity.py — case matrix + duration mapping (Step 5.5)."""

from __future__ import annotations

from itertools import product

import pytest

from phase2_simulation.run_sensitivity import (
    CYCLE_TIME_LEVELS_H,
    GHSV_LEVELS,
    MAX_CYCLES_PER_CASE,
    MIN_CYCLES_BEFORE_CHECK,
    T_REGEN_LEVELS_C,
    CaseConfig,
    _design_point_case_id,
    generate_27_cases,
)


def test_case_matrix_27_unique_ids() -> None:
    """The grid produces exactly 27 cases with unique 1..27 ids."""
    cases = generate_27_cases(dbd_default_flow_nm3h=200.0)
    assert len(cases) == 27
    ids = sorted(c.case_id for c in cases)
    assert ids == list(range(1, 28))


def test_case_matrix_covers_full_grid() -> None:
    """Every (GHSV, T_regen, cycle_time) triple appears once."""
    cases = generate_27_cases(dbd_default_flow_nm3h=200.0)
    seen = {(c.ghsv_factor, c.regen_temp_C, c.cycle_time_h) for c in cases}
    expected = set(product(GHSV_LEVELS, T_REGEN_LEVELS_C, CYCLE_TIME_LEVELS_H))
    assert seen == expected


def test_case_matrix_levels_match_constants() -> None:
    """The levels in the case grid agree with the module constants."""
    assert GHSV_LEVELS == (0.5, 1.0, 1.5)
    assert T_REGEN_LEVELS_C == (150.0, 180.0, 200.0)
    assert CYCLE_TIME_LEVELS_H == (3.0, 4.0, 5.0)


@pytest.mark.parametrize(
    ("cycle_time_h", "exp_heat_h", "exp_cool_h"),
    [
        (3.0, 1.5, 1.0),                       # short cycle: heating held at 1.5 h
        (4.0, 2.0, 1.5),                       # baseline
        (5.0, 2.0, 2.0),                       # long cycle: cooling extended
    ],
)
def test_cycle_time_regen_mapping(cycle_time_h: float,
                                  exp_heat_h: float, exp_cool_h: float) -> None:
    """Heating + cooling durations follow DD-020 mapping."""
    cfg = CaseConfig(
        case_id=99, ghsv_factor=1.0, regen_temp_C=200.0,
        cycle_time_h=cycle_time_h, dbd_default_flow_nm3h=200.0,
    )
    assert cfg.adsorption_duration_s == cycle_time_h * 3600.0
    assert cfg.heating_duration_s == exp_heat_h * 3600.0
    assert cfg.cooling_duration_s == exp_cool_h * 3600.0


def test_overrides_dict_keys() -> None:
    """`to_overrides()` returns the keys consumed by `run_single_cycle`."""
    cfg = CaseConfig(
        case_id=14, ghsv_factor=1.0, regen_temp_C=200.0,
        cycle_time_h=4.0, dbd_default_flow_nm3h=200.0,
    )
    overrides = cfg.to_overrides()
    expected_keys = {
        "adsorption_flow_nm3h", "adsorption_duration_s",
        "heating_duration_s", "cooling_duration_s", "regen_T_K",
    }
    assert set(overrides.keys()) == expected_keys
    # Spot-check values.
    assert overrides["adsorption_flow_nm3h"] == pytest.approx(200.0)
    assert overrides["adsorption_duration_s"] == pytest.approx(14400.0)
    assert overrides["heating_duration_s"] == pytest.approx(7200.0)
    assert overrides["cooling_duration_s"] == pytest.approx(5400.0)
    assert overrides["regen_T_K"] == pytest.approx(473.15)


def test_ghsv_factor_scales_flow() -> None:
    """`adsorption_flow_nm3h = ghsv_factor × dbd_default_flow_nm3h`."""
    for g in GHSV_LEVELS:
        cfg = CaseConfig(
            case_id=1, ghsv_factor=g, regen_temp_C=180.0,
            cycle_time_h=4.0, dbd_default_flow_nm3h=200.0,
        )
        assert cfg.adsorption_flow_nm3h == pytest.approx(g * 200.0)


def test_design_point_case_id_lookup() -> None:
    """Design point (1.0×, 200 °C, 4 h) is locatable in the 27-grid."""
    cid = _design_point_case_id()
    cases = generate_27_cases(dbd_default_flow_nm3h=200.0)
    cfg = next(c for c in cases if c.case_id == cid)
    assert cfg.ghsv_factor == 1.0
    assert cfg.regen_temp_C == 200.0
    assert cfg.cycle_time_h == 4.0


def test_constants_sane() -> None:
    """Min-cycles-before-check ≥ 3 (need 3 to compare 2 against 1)."""
    assert MIN_CYCLES_BEFORE_CHECK >= 3
    assert MAX_CYCLES_PER_CASE >= MIN_CYCLES_BEFORE_CHECK
