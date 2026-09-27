import argparse
import gzip
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

import numpy as np

from .compute.basis import futures_basis
from .compute.combine import combine_profiles
from .compute.expected_move import expected_move_1d
from .compute.peaks import find_peaks, half_width
from .compute.per_strike import ContractSet, StrikeProfile
from .compute.regime import classify_regime
from .compute.walls import wall_confidence
from .compute.zero_gamma import find_zero_gamma
from .config import COMPLEXES, GexConfig
from .data_sources.base import DataSource
from .data_sources.cboe import ET, CboeDelayedSource
from .output.analysis import GammaPeak, GexAnalysis, WallInfo
from .output.pack import build_pack, build_refs, pack_levels, write_pack_file

MARKET_CLOSE = time(16, 0)


def session_date(asof: datetime) -> date:
    """The session a snapshot feeds: the same day during the session, the next weekday after the close.
    Exchange holidays aren't modeled."""
    offset = 1 if asof.time() >= MARKET_CLOSE else 0
    return np.busday_offset(np.datetime64(asof.date(), "D"), offset, roll="forward").item()


def build_analysis(
    name: str,
    spot: float,
    asof: datetime,
    profile: StrikeProfile,
    structural_gex: Callable[[float], float],
    transition_gex: Callable[[float], float],
    expected_move: float,
    cfg: GexConfig,
) -> GexAnalysis:
    zero_gamma = find_zero_gamma(structural_gex, spot, cfg.zg_search_range_pct, cfg.zg_grid_points)
    transition = find_zero_gamma(transition_gex, spot, cfg.zg_search_range_pct, cfg.zg_grid_points)
    dist = None if zero_gamma is None else spot - zero_gamma
    dist_norm = None if dist is None else dist / expected_move
    gex_at_spot = structural_gex(spot)
    regime, confidence = classify_regime(gex_at_spot, dist_norm, cfg)

    def ranked(values: np.ndarray) -> list[GammaPeak]:
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

    net = profile.net
    positive_net, negative_net = ranked(np.maximum(net, 0)), ranked(np.minimum(net, 0))
    support = ranked(profile.put) if cfg.support_rank_source == "put" else negative_net
    resistance = ranked(profile.call) if cfg.resistance_rank_source == "call" else positive_net
    scale = float(np.abs(net).max()) or 1.0
    return GexAnalysis(
        timestamp=asof.isoformat(),
        underlying=name,
        spot=spot,
        expected_move_1d=expected_move,
        total_gex_per_pct=gex_at_spot,
        zero_gamma=zero_gamma,
        transition_level=transition,
        distance_to_zg_pts=dist,
        distance_to_zg_normalized=dist_norm,
        regime=regime.value,
        regime_confidence=confidence,
        call_wall=wall(positive_net),
        put_wall=wall(negative_net),
        support_peaks=support,
        resistance_peaks=resistance,
        gamma_profile={f"{k:g}": round(float(v) / scale, 4) for k, v in zip(profile.strikes, net)},
    )


@dataclass
class RunResult:
    analyses: dict[str, GexAnalysis]
    bases: dict[str, float]
    session: date
    pack_path: Path


def run(
    cfg: GexConfig | None = None,
    source: DataSource | None = None,
    out_dir: Path = Path("output"),
    raw_dir: Path = Path("data/raw"),
    pack_path: Path | None = None,
) -> RunResult:
    cfg = cfg or GexConfig()
    source = source or CboeDelayedSource()
    fetched = datetime.now(ET)
    symbols = [s for c in COMPLEXES for s in (c.index, c.etf)]
    chains = {s: source.fetch_chain(s) for s in symbols}
    session = session_date(chains["SPX"].asof)

    raw_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    for sym, chain in chains.items():
        (raw_dir / f"{sym}_{fetched:%Y-%m-%d_%H%M}.json.gz").write_bytes(gzip.compress(chain.raw))

    structural = {s: ContractSet.from_chain(ch, cfg, cfg.structural_window_dte) for s, ch in chains.items()}
    transition = {s: ContractSet.from_chain(ch, cfg, cfg.transition_window_dte) for s, ch in chains.items()}
    profiles = {s: structural[s].profile(ch.spot) for s, ch in chains.items()}
    moves = {s: expected_move_1d(ch) for s, ch in chains.items()}
    analyses = {
        s: build_analysis(s, ch.spot, ch.asof, profiles[s], structural[s].total_gex, transition[s].total_gex, moves[s], cfg)
        for s, ch in chains.items()
    }
    bases = {
        c.index: futures_basis(chains[c.index], cfg.risk_free_rate, cfg.futures_roll_days, cfg.basis_strike_range_pct)
        for c in COMPLEXES
    }

    spx, spy = chains["SPX"], chains["SPY"]
    ratio = spx.spot / spy.spot
    combined = build_analysis(
        "SPX_SPY", spx.spot, spx.asof,
        combine_profiles(profiles["SPX"], profiles["SPY"], ratio, cfg.combined_strike_step),
        lambda s: structural["SPX"].total_gex(s) + structural["SPY"].total_gex(s / ratio),
        lambda s: transition["SPX"].total_gex(s) + transition["SPY"].total_gex(s / ratio),
        moves["SPX"], cfg,
    )
    for a in [*analyses.values(), combined]:
        (out_dir / f"analysis_{a.underlying}_{session}.json").write_text(a.to_json())

    pack_path = pack_path or out_dir / "gex_pack.txt"
    write_pack_file(
        pack_path,
        build_refs({s: ch.spot for s, ch in chains.items()}, bases),
        build_pack({s: pack_levels(a) for s, a in analyses.items()}, bases, session),
        [
            f"Generated {fetched:%Y-%m-%d %H:%M} ET by gex-analysis from CBOE delayed quotes "
            f"(SPX data as of {spx.asof:%Y-%m-%d %H:%M} ET).",
            "Futures blocks = index levels + basis implied by index options at the front futures expiry.",
            "[REFS] futures = index spot + that basis, so each ETF/futures pair is one simultaneous observation.",
        ],
    )
    return RunResult({**analyses, combined.underlying: combined}, bases, session, pack_path)


def summarize(a: GexAnalysis) -> str:
    def lvl(v: float | None) -> str:
        return "-" if v is None else f"{v:g}" if v == int(v) else f"{v:.1f}"

    def ranks(peaks: list[GammaPeak]) -> str:
        return ", ".join(lvl(p.strike) for p in peaks[:4]) or "-"

    return "\n".join([
        f"{a.underlying:<8} spot {a.spot:.2f}  EM ±{a.expected_move_1d:.2f}  "
        f"GEX ${a.total_gex_per_pct / 1e9:+.2f}B/1%  {a.regime} ({a.regime_confidence:.2f})",
        f"         CW {lvl(a.call_wall and a.call_wall.peak.strike)}  PW {lvl(a.put_wall and a.put_wall.peak.strike)}  "
        f"TL {lvl(a.transition_level)}  DP {lvl(a.zero_gamma)}",
        f"         GD {ranks(a.support_peaks)}   C {ranks(a.resistance_peaks)}",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute GEX levels and write gex_pack.txt.")
    parser.add_argument("--pack-path", type=Path, help="where to write the pack (default: output/gex_pack.txt)")
    parser.add_argument("--out-dir", type=Path, default=Path("output"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    args = parser.parse_args()

    result = run(out_dir=args.out_dir, raw_dir=args.raw_dir, pack_path=args.pack_path)
    for a in result.analyses.values():
        print(summarize(a))
    bases = "  ".join(f"{c.futures[0]}-{c.index} {result.bases[c.index]:+.2f}" for c in COMPLEXES)
    print(f"\nBasis: {bases}\nPack for session {result.session} written to {result.pack_path}")


if __name__ == "__main__":
    main()
