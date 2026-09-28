from dataclasses import dataclass, field
from datetime import time
from typing import Literal


@dataclass(frozen=True)
class Complex:
    index: str
    etf: str
    futures: tuple[str, ...]


# Pack block order follows the vendor's: all futures first, then each index with its ETF.
COMPLEXES = (
    Complex("SPX", "SPY", ("ES", "MES")),
    Complex("NDX", "QQQ", ("NQ", "MNQ")),
    Complex("RUT", "IWM", ("RTY",)),
)


@dataclass
class GexConfig:
    # Standard GEX convention: customers sell calls and buy puts, so dealers are long calls and short puts.
    dealer_long_calls: bool = True
    dealer_short_puts: bool = True

    # Calendar-day DTE windows, inclusive. Structural levels (walls, ranks, DP) use the wide window;
    # the transition level (TL) uses short-dated options only. Chosen against the vendor's 25 Sep 2026 pack.
    structural_window_dte: tuple[int, int] = (0, 365)
    transition_window_dte: tuple[int, int] = (0, 7)
    # Floors time-to-expiry so 0DTE gamma doesn't explode in the final minutes of the session.
    min_time_to_expiry_minutes: float = 15.0

    risk_free_rate: float = 0.04
    dividend_yields: dict[str, float] = field(default_factory=lambda: {
        "SPX": 0.0, "NDX": 0.0, "RUT": 0.0, "SPY": 0.012, "QQQ": 0.006, "IWM": 0.012,
    })

    # Which per-strike series ranks the support (GD1-4) and resistance (C1-4) levels. "net" ignores strikes
    # whose call and put gamma offset (box/conversion trades), which "put" would rank as strong support.
    support_rank_source: Literal["put", "net"] = "net"
    resistance_rank_source: Literal["call", "net"] = "net"
    peak_relative_threshold: float = 0.20
    peak_min_separation_pct: float = 0.0025
    wall_dominance_threshold: float = 1.5

    zg_search_range_pct: float = 0.10
    zg_grid_points: int = 161

    regime_battleground_normalized: float = 0.5
    regime_high_confidence_normalized: float = 2.0

    combined_strike_step: float = 5.0

    # Futures basis = index options' implied forward at the front quarterly expiry, minus spot.
    futures_roll_days: int = 8
    basis_strike_range_pct: float = 0.03

    # --intraday schedule (ET). The feed is 15 min delayed, so 09:45 sees the open and 15:45 keeps every
    # snapshot before the 16:00 close, which would otherwise roll the pack to the next session.
    intraday_start: time = time(9, 45)
    intraday_end: time = time(15, 45)
    intraday_every_minutes: int = 30
