import gzip
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import numpy as np

from .compute.combine import combine_profiles
from .compute.expected_move import expected_move_1d
from .compute.peaks import find_peaks, half_width
from .compute.per_strike import ContractSet, StrikeProfile
from .compute.regime import classify_regime
from .compute.walls import wall_confidence
from .compute.zero_gamma import find_zero_gamma
from .config import GexConfig
from .data_sources.base import DataSource
from .data_sources.cboe import CboeDelayedSource
from .output.analysis import GammaPeak, GexAnalysis, WallInfo

UNDERLYINGS = ("SPX", "SPY")


def build_analysis(
    name: str,
    spot: float,
    asof: datetime,
    profile: StrikeProfile,
    total_gex: Callable[[float], float],
    expected_move: float,
    cfg: GexConfig,
) -> GexAnalysis:
    zero_gamma = find_zero_gamma(total_gex, spot, cfg.zg_search_range_pct, cfg.zg_grid_points)
    dist = None if zero_gamma is None else spot - zero_gamma
    dist_norm = None if dist is None else dist / expected_move
    gex_at_spot = total_gex(spot)
    regime, confidence = classify_regime(gex_at_spot, dist_norm, cfg)

    def side_peaks(values: np.ndarray) -> list[GammaPeak]:
        magnitude = np.abs(values)
        idxs = find_peaks(profile.strikes, magnitude, cfg.peak_relative_threshold, cfg.peak_min_separation_pct * spot)
        peaks = []
        for i in idxs:
            strike = float(profile.strikes[i])
            peaks.append(GammaPeak(
                strike=strike,
                magnitude_signed=float(values[i]),
                magnitude_normalized=float(magnitude[i] / magnitude[idxs[0]]),
                half_width_pts=half_width(profile.strikes, magnitude, i),
                distance_to_spot_pts=strike - spot,
                distance_to_spot_normalized=(strike - spot) / expected_move,
                role="Resistance" if strike > spot else "Support",
            ))
        return peaks

    def wall(peaks: list[GammaPeak]) -> WallInfo | None:
        if not peaks:
            return None
        magnitudes = [abs(p.magnitude_signed) for p in peaks]
        return WallInfo(peaks[0], wall_confidence(magnitudes, cfg.wall_dominance_threshold))

    call_peaks, put_peaks = side_peaks(profile.call), side_peaks(profile.put)
    net = profile.net
    scale = float(np.abs(net).max()) or 1.0
    return GexAnalysis(
        timestamp=asof.isoformat(),
        underlying=name,
        spot=spot,
        expected_move_1d=expected_move,
        total_gex_per_pct=gex_at_spot,
        zero_gamma=zero_gamma,
        distance_to_zg_pts=dist,
        distance_to_zg_normalized=dist_norm,
        regime=regime.value,
        regime_confidence=confidence,
        call_wall=wall(call_peaks),
        put_wall=wall(put_peaks),
        call_peaks=call_peaks,
        put_peaks=put_peaks,
        gamma_profile={f"{k:g}": round(float(v) / scale, 4) for k, v in zip(profile.strikes, net)},
    )


def run(
    cfg: GexConfig | None = None,
    source: DataSource | None = None,
    out_dir: Path = Path("output"),
    raw_dir: Path = Path("data/raw"),
) -> list[GexAnalysis]:
    cfg = cfg or GexConfig()
    source = source or CboeDelayedSource()
    chains = {sym: source.fetch_chain(sym) for sym in UNDERLYINGS}
    session = chains["SPX"].asof.date().isoformat()

    raw_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    for sym, chain in chains.items():
        (raw_dir / f"{sym}_{session}.json.gz").write_bytes(gzip.compress(chain.raw))

    sets = {sym: ContractSet.from_chain(chain, cfg) for sym, chain in chains.items()}
    moves = {sym: expected_move_1d(chain) for sym, chain in chains.items()}
    profiles = {sym: sets[sym].profile(chain.spot) for sym, chain in chains.items()}

    analyses = [
        build_analysis(sym, chain.spot, chain.asof, profiles[sym], sets[sym].total_gex, moves[sym], cfg)
        for sym, chain in chains.items()
    ]

    spx, spy = chains["SPX"], chains["SPY"]
    ratio = spx.spot / spy.spot
    combined = combine_profiles(profiles["SPX"], profiles["SPY"], ratio, cfg.combined_strike_step)
    analyses.append(build_analysis(
        "SPX_SPY", spx.spot, spx.asof, combined,
        lambda s: sets["SPX"].total_gex(s) + sets["SPY"].total_gex(s / ratio),
        moves["SPX"], cfg,
    ))

    for a in analyses:
        (out_dir / f"analysis_{a.underlying}_{session}.json").write_text(a.to_json())
    return analyses


def summarize(a: GexAnalysis) -> str:
    def fmt_peaks(peaks: list[GammaPeak]) -> str:
        return ", ".join(f"{p.strike:g} ({p.magnitude_normalized:.2f})" for p in peaks[:5]) or "none"

    def fmt_wall(w: WallInfo | None) -> str:
        return "none" if w is None else f"{w.peak.strike:g} (conf {w.confidence:.2f})"

    zg = "none in range" if a.zero_gamma is None else (
        f"{a.zero_gamma:.1f} (spot {a.distance_to_zg_pts:+.1f} pts, {a.distance_to_zg_normalized:+.2f} EM)"
    )
    return "\n".join([
        f"{a.underlying}  spot {a.spot:.2f}  as of {a.timestamp}",
        f"  1D expected move  ±{a.expected_move_1d:.2f}",
        f"  total GEX         ${a.total_gex_per_pct / 1e9:+.2f}B per 1% move",
        f"  zero gamma        {zg}",
        f"  regime            {a.regime} (confidence {a.regime_confidence:.2f})",
        f"  call wall         {fmt_wall(a.call_wall)}",
        f"  put wall          {fmt_wall(a.put_wall)}",
        f"  call peaks        {fmt_peaks(a.call_peaks)}",
        f"  put peaks         {fmt_peaks(a.put_peaks)}",
    ])


def main() -> None:
    for a in run():
        print(summarize(a), end="\n\n")


if __name__ == "__main__":
    main()
