import argparse
import csv
import gzip
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import sleep

import numpy as np
import requests

from .compare import level_changes
from .compute.basis import underlying_level
from .compute.combine import combine_profiles
from .compute.expected_move import expected_move_1d
from .compute.peaks import find_peaks, half_width
from .compute.per_strike import ContractSet, StrikeProfile
from .compute.regime import classify_regime
from .compute.walls import wall_confidence
from .compute.zero_gamma import find_zero_gamma
from .config import COMPLEXES, Complex, GexConfig
from .data_sources.base import Chain, DataSource
from .data_sources.cboe import ET, CboeDelayedSource, parse_payload
from .output.analysis import GammaPeak, GexAnalysis, WallInfo
from .output.pack import build_pack, build_refs, pack_levels, read_pack_file, write_pack_file

MARKET_CLOSE = time(16, 0)
PIVOT_LOG_FIELDS = [
    "fetched", "session", "complex", "index_spot", "etf_spot", "ratio", "basis",
    "tl_index_only", "tl_etf_only", "tl_combined", "dp_index_only", "dp_etf_only", "dp_combined",
]


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


def _summed_gex(index_set: ContractSet, etf_set: ContractSet, ratio: float) -> Callable[[float], float]:
    return lambda s: index_set.total_gex(s) + etf_set.total_gex(s / ratio)


def pivot_row(
    fetched: datetime, session: date, c: Complex, chains: dict[str, Chain],
    analyses: dict[str, GexAnalysis], combined: GexAnalysis, basis: float,
) -> dict:
    """Index-only, ETF-only (on the index scale) and combined TL/DP for one complex."""
    ratio = chains[c.index].close / chains[c.etf].close
    index, etf = analyses[c.index], analyses[c.etf]

    def scaled(v: float | None) -> float | None:
        return None if v is None else round(v * ratio, 2)

    def rounded(v: float | None) -> float | None:
        return None if v is None else round(v, 2)

    return {
        "fetched": f"{fetched:%Y-%m-%d %H:%M}", "session": session.isoformat(), "complex": c.index,
        "index_spot": chains[c.index].spot, "etf_spot": chains[c.etf].spot, "ratio": round(ratio, 5), "basis": basis,
        "tl_index_only": rounded(index.transition_level), "tl_etf_only": scaled(etf.transition_level),
        "tl_combined": rounded(combined.transition_level),
        "dp_index_only": rounded(index.zero_gamma), "dp_etf_only": scaled(etf.zero_gamma),
        "dp_combined": rounded(combined.zero_gamma),
    }


def append_pivot_log(path: Path, rows: list[dict]) -> None:
    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PIVOT_LOG_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def previous_open_interest(symbol: str, raw_dir: Path) -> tuple[str, float] | None:
    """(fetch label, total OI) of the latest archived snapshot for symbol, if any."""
    archives = sorted(raw_dir.glob(f"{symbol}_*.json.gz"))
    if not archives:
        return None
    label = archives[-1].name[len(symbol) + 1: -len(".json.gz")]
    chain = parse_payload(symbol, gzip.decompress(archives[-1].read_bytes()))
    return label, float(chain.open_interest.sum())


def describe_open_interest(
    current: dict[str, float], previous: dict[str, tuple[str, float] | None], today: date
) -> tuple[list[str], list[str]]:
    """One line per symbol, and the symbols whose OI is unchanged since an earlier day's archive (likely stale)."""
    lines, stale = [], []
    for sym, total in current.items():
        prev = previous.get(sym)
        if prev is None:
            lines.append(f"{sym:<4} OI {total:>12,.0f}  (no earlier archive)")
            continue
        label, prev_total = prev
        change = "unchanged" if total == prev_total else f"{100 * (total / prev_total - 1):+.2f}%"
        lines.append(f"{sym:<4} OI {total:>12,.0f}  ({change} vs archive {label})")
        if total == prev_total and date.fromisoformat(label[:10]) < today:
            stale.append(sym)
    return lines, stale


@dataclass
class RunResult:
    analyses: dict[str, GexAnalysis]
    bases: dict[str, float]
    session: date
    pack_path: Path
    pivot_rows: list[dict]
    open_interest_lines: list[str]
    stale_open_interest: list[str]
    changes: list[str]
    history_path: Path


def run(
    cfg: GexConfig | None = None,
    source: DataSource | None = None,
    out_dir: Path = Path("output"),
    raw_dir: Path = Path("data/raw"),
    pack_path: Path | None = None,
    pivot_log: Path = Path("data/pivot_log.csv"),
    archive: bool = True,
) -> RunResult:
    """One full computation. Every pack written is also kept in <raw_dir>/../packs/<session>_<HHMM>.txt."""
    cfg = cfg or GexConfig()
    source = source or CboeDelayedSource()
    fetched = datetime.now(ET)
    symbols = [s for c in COMPLEXES for s in (c.index, c.etf)]
    chains = {s: source.fetch_chain(s) for s in symbols}
    session = session_date(chains["SPX"].asof)

    raw_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    oi_lines, stale_oi = describe_open_interest(
        {s: float(ch.open_interest.sum()) for s, ch in chains.items()},
        {s: previous_open_interest(s, raw_dir) for s in chains},
        fetched.date(),
    )
    if archive:
        for sym, chain in chains.items():
            (raw_dir / f"{sym}_{fetched:%Y-%m-%d_%H%M}.json.gz").write_bytes(gzip.compress(chain.raw))

    # Price everything at the level implied by each chain's own option quotes: outside regular hours the quoted
    # index level is stale while SPX options still trade, and ETF quotes move pre-market while their options don't.
    levels = {
        s: underlying_level(ch, cfg.risk_free_rate, cfg.futures_roll_days, cfg.basis_strike_range_pct)
        for s, ch in chains.items()
    }
    chains = {s: replace(ch, spot=levels[s][0]) for s, ch in chains.items()}
    bases = {c.index: round(levels[c.index][1] - levels[c.index][0], 2) for c in COMPLEXES}

    structural = {s: ContractSet.from_chain(ch, cfg, cfg.structural_window_dte) for s, ch in chains.items()}
    transition = {s: ContractSet.from_chain(ch, cfg, cfg.transition_window_dte) for s, ch in chains.items()}
    profiles = {s: structural[s].profile(ch.spot) for s, ch in chains.items()}
    moves = {s: expected_move_1d(ch) for s, ch in chains.items()}
    analyses = {
        s: build_analysis(s, ch.spot, ch.asof, profiles[s], structural[s].total_gex, transition[s].total_gex, moves[s], cfg)
        for s, ch in chains.items()
    }

    # Research only: index + ETF gamma combined. Not used in the pack.
    combined, pivot_rows = {}, []
    for c in COMPLEXES:
        index = chains[c.index]
        ratio = index.close / chains[c.etf].close
        name = f"{c.index}_{c.etf}"
        combined[name] = build_analysis(
            name, index.spot, index.asof,
            combine_profiles(profiles[c.index], profiles[c.etf], ratio, cfg.combined_strike_step),
            _summed_gex(structural[c.index], structural[c.etf], ratio),
            _summed_gex(transition[c.index], transition[c.etf], ratio),
            moves[c.index], cfg,
        )
        pivot_rows.append(pivot_row(fetched, session, c, chains, analyses, combined[name], bases[c.index]))
    append_pivot_log(pivot_log, pivot_rows)

    for a in [*analyses.values(), *combined.values()]:
        (out_dir / f"analysis_{a.underlying}_{session}.json").write_text(a.to_json())

    pack_path = pack_path or out_dir / "gex_pack.txt"
    pack = build_pack({s: pack_levels(a) for s, a in analyses.items()}, bases, session)
    previous = read_pack_file(pack_path.read_text(encoding="utf-8")).pack if pack_path.exists() else None
    write_pack_file(
        pack_path,
        build_refs({s: ch.close for s, ch in chains.items()}, bases),
        pack,
        [
            f"Generated {fetched:%Y-%m-%d %H:%M} ET by gex-analysis from CBOE delayed quotes "
            f"(SPX data as of {chains['SPX'].asof:%Y-%m-%d %H:%M} ET).",
            "Futures blocks = index levels + basis implied by index options at the front futures expiry.",
            "[REFS] ETF = its official close; futures = the index's official close + that basis, "
            "so each pair is simultaneous even when run outside regular hours.",
        ],
    )
    history_path = raw_dir.parent / "packs" / f"{session}_{fetched:%H%M}.txt"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(pack_path.read_text(encoding="utf-8"), encoding="utf-8")
    return RunResult(
        {**analyses, **combined}, bases, session, pack_path, pivot_rows, oi_lines, stale_oi,
        level_changes(previous, pack), history_path,
    )


def intraday_slots(now: datetime, start: time, end: time, every_minutes: int) -> list[datetime]:
    """Scheduled run times still ahead of `now` today: start, start + every, ... up to end (ET)."""
    slot = datetime.combine(now.date(), start, ET)
    last = datetime.combine(now.date(), end, ET)
    slots = []
    while slot <= last:
        if slot > now:
            slots.append(slot)
        slot += timedelta(minutes=every_minutes)
    return slots


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


def print_changes(result: RunResult) -> None:
    print("\nChanges since the previous pack")
    for line in result.changes:
        print(f"  {line}")
    print(f"\nPack for session {result.session} written to {result.pack_path} (copy: {result.history_path})")


def print_summary(result: RunResult) -> None:
    for c in COMPLEXES:
        print(summarize(result.analyses[c.index]))
        print(summarize(result.analyses[c.etf]))

    def fmt(v: float | None) -> str:
        return "-" if v is None else f"{v:.1f}"

    print("\nPivots (research log), index scale: index-only / ETF-only / combined")
    for row in result.pivot_rows:
        print(f"  {row['complex']:<4} TL {fmt(row['tl_index_only'])} / {fmt(row['tl_etf_only'])} / {fmt(row['tl_combined'])}"
              f"   DP {fmt(row['dp_index_only'])} / {fmt(row['dp_etf_only'])} / {fmt(row['dp_combined'])}")

    print("\nOpen interest")
    for line in result.open_interest_lines:
        print(f"  {line}")
    if result.stale_open_interest:
        print(f"  WARNING: open interest unchanged since an earlier day's snapshot for "
              f"{', '.join(result.stale_open_interest)}. CBOE may not have refreshed that data yet, so those levels "
              "may be built on stale positioning. Consider re-running later.")

    bases = "  ".join(f"{c.futures[0]}-{c.index} {result.bases[c.index]:+.2f}" for c in COMPLEXES)
    print(f"\nBasis: {bases}")
    print_changes(result)


def run_intraday(run_once: Callable[[bool], RunResult], cfg: GexConfig) -> None:
    """Run now, then at each remaining slot today. Raw snapshots are archived at most once per clock hour."""
    last_archived_hour = None

    def attempt(archive: bool) -> RunResult | None:
        try:
            return run_once(archive)
        except (requests.RequestException, ValueError) as err:
            print(f"\n{datetime.now(ET):%H:%M} ET update failed, keeping the previous pack: {err}")
            return None

    start = datetime.now(ET)
    result = attempt(archive=True)
    if result is not None:
        print_summary(result)
        last_archived_hour = start.replace(minute=0, second=0, microsecond=0)

    for slot in intraday_slots(start, cfg.intraday_start, cfg.intraday_end, cfg.intraday_every_minutes):
        wait = (slot - datetime.now(ET)).total_seconds()
        if wait < -300:  # missed while the machine slept: skip rather than replay a burst
            continue
        print(f"\nNext update at {slot:%H:%M} ET (Ctrl-C to stop)")
        sleep(max(wait, 0))
        hour = slot.replace(minute=0)
        archive = hour != last_archived_hour
        result = attempt(archive)
        if result is None:
            continue
        if archive:
            last_archived_hour = hour
        print(f"\n=== {datetime.now(ET):%H:%M} ET update ({'archived' if archive else 'not archived'}) ===")
        print_changes(result)
    print("\nIntraday updates finished for the day.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute GEX levels and write gex_pack.txt.")
    parser.add_argument("--pack-path", type=Path, help="where to write the pack (default: output/gex_pack.txt)")
    parser.add_argument("--out-dir", type=Path, default=Path("output"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--pivot-log", type=Path, default=Path("data/pivot_log.csv"))
    parser.add_argument("--intraday", action="store_true",
                        help="run now, then every 30 min from 09:45 to 15:45 ET, recomputing all levels")
    args = parser.parse_args()

    cfg = GexConfig()

    def run_once(archive: bool) -> RunResult:
        return run(cfg=cfg, out_dir=args.out_dir, raw_dir=args.raw_dir, pack_path=args.pack_path,
                   pivot_log=args.pivot_log, archive=archive)

    if not args.intraday:
        print_summary(run_once(archive=True))
        return
    try:
        run_intraday(run_once, cfg)
    except KeyboardInterrupt:
        print("\nStopped. The last pack written stays in place.")


if __name__ == "__main__":
    main()
