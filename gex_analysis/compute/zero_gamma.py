from collections.abc import Callable

import numpy as np
from scipy.optimize import brentq


def find_zero_gamma(
    total_gex: Callable[[float], float], spot: float, range_pct: float, grid_points: int
) -> float | None:
    """Spot level where total dealer GEX, re-evaluated at that spot, crosses zero. Nearest crossing to spot wins."""
    grid = spot * np.linspace(1 - range_pct, 1 + range_pct, grid_points)
    values = np.array([total_gex(s) for s in grid])
    crossings = np.flatnonzero(np.sign(values[:-1]) * np.sign(values[1:]) < 0)
    if len(crossings) == 0:
        return None
    roots = [brentq(total_gex, grid[i], grid[i + 1]) for i in crossings]
    return float(min(roots, key=lambda z: abs(z - spot)))
