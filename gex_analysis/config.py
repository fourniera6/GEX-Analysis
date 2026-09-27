from dataclasses import dataclass, field


@dataclass
class GexConfig:
    # Standard GEX convention: customers sell calls and buy puts, so dealers are long calls and short puts.
    dealer_long_calls: bool = True
    dealer_short_puts: bool = True

    # Calendar-day DTE window, inclusive.
    expiry_window_dte: tuple[int, int] = (0, 30)
    # Floors time-to-expiry so 0DTE gamma doesn't explode in the final minutes of the session.
    min_time_to_expiry_minutes: float = 15.0

    risk_free_rate: float = 0.04
    dividend_yields: dict[str, float] = field(default_factory=lambda: {"SPX": 0.0, "SPY": 0.012})

    peak_relative_threshold: float = 0.20
    peak_min_separation_pct: float = 0.0025
    wall_dominance_threshold: float = 1.5

    zg_search_range_pct: float = 0.10
    zg_grid_points: int = 161

    regime_battleground_normalized: float = 0.5
    regime_high_confidence_normalized: float = 2.0

    combined_strike_step: float = 5.0
