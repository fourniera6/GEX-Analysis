def wall_confidence(peak_magnitudes: list[float], dominance_threshold: float) -> float:
    """1.0 when the top peak is at least dominance_threshold times the runner-up, scaling down to 0 at parity."""
    if len(peak_magnitudes) < 2:
        return 1.0
    ratio = peak_magnitudes[0] / peak_magnitudes[1]
    # A held wall (intraday hysteresis) can be weaker than the runner-up; that reads as 0.
    return max(0.0, min(1.0, (ratio - 1) / (dominance_threshold - 1)))
