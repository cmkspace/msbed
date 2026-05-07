"""Cell-centered finite-volume grid with layer assignment.

Layout (PHASE2_SPEC §3.1, "층 경계에서 노드 일치" interpreted as cell-face
alignment at the layer boundary):

    Alumina layer (n_alumina cells):
        - Uniform within layer: dz_a = alumina_height_m / n_alumina
        - Cell i centers: ((i + 0.5) * dz_a) for i in 0..n_alumina-1
        - Cell faces span [0, alumina_height_m]
    13X layer (n_13x cells):
        - Uniform within layer: dz_b = zeolite_13x_height_m / n_13x
        - Cell j centers: alumina_height_m + (j + 0.5) * dz_b
        - Cell faces span [alumina_height_m, bed_height_m]

Total cells = n_alumina + n_13x = n_grid_total.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import LAYERS, ColumnConfig


@dataclass(frozen=True)
class Grid:
    """Cell-centered 1D grid with layer assignment.

    Attributes:
        z_centers_m: (N,) cell center positions (m).
        dz_widths_m: (N,) cell widths (m); uniform within each layer.
        layer_ids:   (N,) layer index — 0 = alumina, 1 = 13X.
        n_total:     total cell count N.
    """

    z_centers_m: np.ndarray
    dz_widths_m: np.ndarray
    layer_ids: np.ndarray
    n_total: int

    @property
    def n_alumina(self) -> int:
        return int(np.sum(self.layer_ids == 0))

    @property
    def n_13x(self) -> int:
        return int(np.sum(self.layer_ids == 1))

    @property
    def alumina_mask(self) -> np.ndarray:
        return self.layer_ids == 0

    @property
    def thirteen_x_mask(self) -> np.ndarray:
        return self.layer_ids == 1

    def layer_name_at(self, i: int) -> str:
        """Return 'alumina' or 'zeolite_13x' for cell index i."""
        return LAYERS[int(self.layer_ids[i])]


def build_grid(col: ColumnConfig) -> Grid:
    """Construct a layered cell-centered grid from a ColumnConfig.

    Args:
        col: Column geometry + n_grid_total.

    Returns:
        Grid with col.n_alumina alumina cells followed by col.n_13x 13X cells.
    """
    n_a = col.n_alumina
    n_b = col.n_13x

    dz_a = col.alumina_height_m / n_a
    dz_b = col.zeolite_13x_height_m / n_b

    z_a = (np.arange(n_a) + 0.5) * dz_a
    z_b = col.alumina_height_m + (np.arange(n_b) + 0.5) * dz_b

    z = np.concatenate([z_a, z_b])
    dz = np.concatenate([np.full(n_a, dz_a), np.full(n_b, dz_b)])
    layer_ids = np.concatenate([np.zeros(n_a, dtype=int), np.ones(n_b, dtype=int)])

    return Grid(
        z_centers_m=z,
        dz_widths_m=dz,
        layer_ids=layer_ids,
        n_total=col.n_grid_total,
    )
