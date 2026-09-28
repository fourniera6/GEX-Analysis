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


def sticky_peaks(
    strikes: np.ndarray,
    magnitude: np.ndarray,
    ranked: list[int],
    incumbents: list[float],
    margin: float,
    rel_threshold: float,
    min_separation: float,
) -> list[int]:
    """Re-rank `ranked` so slot k keeps incumbents[k] unless the best available challenger is more than
    `margin` stronger. Incumbents that vanished or fell under the relative threshold give way."""
    top = magnitude.max(initial=0.0)
    index_of = {float(k): i for i, k in enumerate(strikes)}
    chosen: list[int] = []

    def fits(i: int) -> bool:
        return i not in chosen and all(abs(strikes[i] - strikes[j]) >= min_separation for j in chosen)

    for strike in incumbents:
        challenger = next((i for i in ranked if fits(i)), None)
        held = index_of.get(float(strike))
        holds = (
            held is not None and fits(held) and magnitude[held] > 0 and magnitude[held] >= rel_threshold * top
            and (challenger is None or magnitude[challenger] <= magnitude[held] * (1 + margin))
        )
        pick = held if holds else challenger
        if pick is None:
            break
        chosen.append(pick)
    return chosen + [i for i in ranked if fits(i)]
