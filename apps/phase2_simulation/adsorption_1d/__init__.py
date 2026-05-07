"""1D adsorption PDE solver package — Phase 2.

Modules
-------
- config.py:   ColumnConfig, OperatingConditions dataclasses (DD-011 decisions)
- grid.py:     cell-centered finite-volume grid with layer assignment
- state.py:    5N ODE state packing/unpacking + SimulationResult container
- boundary.py: Danckwerts inlet/outlet boundary conditions (Step 2)
- rhs.py:      single ODE right-hand side with mode dispatch (Step 3-4)
- solver.py:   scipy.solve_ivp BDF wrapper (Step 5)
"""

from .config import (
    LAYER_13X,
    LAYER_ALUMINA,
    LAYERS,
    SPECIES,
    ColumnConfig,
    FlowDirection,
    Mode,
    OperatingConditions,
    default_adsorbs_in_2a,
    default_adsorbs_in_2c,
)
from .grid import Grid, build_grid
from .state import (
    N_VARS,
    STATE_VARS,
    SimulationResult,
    cell_block_slice,
    pack_state,
    state_size,
    unpack_state,
    var_slice,
)

__all__ = [
    "ColumnConfig",
    "FlowDirection",
    "Grid",
    "LAYER_13X",
    "LAYER_ALUMINA",
    "LAYERS",
    "Mode",
    "N_VARS",
    "OperatingConditions",
    "STATE_VARS",
    "SPECIES",
    "SimulationResult",
    "build_grid",
    "cell_block_slice",
    "default_adsorbs_in_2a",
    "default_adsorbs_in_2c",
    "pack_state",
    "state_size",
    "unpack_state",
    "var_slice",
]
