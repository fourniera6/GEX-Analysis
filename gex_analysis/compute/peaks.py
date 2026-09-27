import numpy as np


def find_peaks(strikes: np.ndarray, magnitude: np.ndarray, rel_threshold: float, min_separation: float) -> list[int]:
    """Indices of peaks, strongest first, via greedy suppression of anything within min_separation of a stronger peak."""
    top = magnitude.max(initial=0.0)
    if top <= 0:
        return []
    candidates = np.flatnonzero((magnitude >= rel_threshold * top) & (magnitude > 0))
    candidates = candidates[np.argsort(-magnitude[candidates], kind="stable")]
    kept: list[int] = []
    for i in candidates:
        if all(abs(strikes[i] - strikes[j]) >= min_separation for j in kept):
            kept.append(int(i))
    return kept


def half_width(strikes: np.ndarray, magnitude: np.ndarray, i: int) -> float:
    """Half the price span over which magnitude stays at or above 50% of the peak at index i."""
    half = magnitude[i] / 2
    lo = hi = i
    while lo > 0 and magnitude[lo - 1] >= half:
        lo -= 1
    while hi < len(magnitude) - 1 and magnitude[hi + 1] >= half:
        hi += 1
    return float(strikes[hi] - strikes[lo]) / 2
