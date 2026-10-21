"""Research only: which contract set reproduces the vendor's DP?

The vendor's DP sits below ours on the index chains on most days (SPX +20 to +43, NDX +216 on 13 Oct) while the
ETF DPs mostly match. This recomputes DP from a saved raw snapshot under several variants of which contracts
feed it, and scores each against a vendor pack. It changes nothing in the pack.

    python -m gex_analysis.research.dp_variants --vendor "$NT8/gex_pack.txt" [--stamp 2026-10-13_0912]
"""

import argparse
import gzip
import re
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median

import numpy as np

from ..compute.basis import underlying_level
from ..compute.per_strike import ContractSet
from ..compute.zero_gamma import find_zero_gamma
from ..config import COMPLEXES, GexConfig
from ..data_sources.base import Chain
from ..data_sources.cboe import AM_SETTLED_ROOTS, SECONDS_PER_YEAR, parse_payload
from ..output.pack import read_pack_file

INDEXES = [c.index for c in COMPLEXES]
ETFS = [c.etf for c in COMPLEXES]
SYMBOLS = [s for c in COMPLEXES for s in (c.index, c.etf)]
DP_SLOT = 11
_ARCHIVE = re.compile(r"^([A-Z]+)_(\d{4}-\d{2}-\d{2}_\d{4})\.json\.gz$")


@dataclass(frozen=True)
class Variant:
    name: str
    window: tuple[int, int] = (0, 365)
    band_pct: float | None = None   # keep strikes within this fraction of spot
    weeklies_only: bool = False     # drop the AM-settled monthly roots (SPX, NDX, RUT)
    snapshot_clock: bool = False    # measure time to expiry from the snapshot, not the underlying's last trade


BASELINE = "DTE 0-365 (ours)"
VARIANTS = [
    *(Variant(f"DTE 0-{hi}", (0, hi)) for hi in (7, 14, 30, 45, 60, 90, 180)),
    Variant(BASELINE),
    Variant("DTE 1-365 (no 0DTE)", (1, 365)),
    *(Variant(f"strikes ±{int(b * 100)}%", band_pct=b) for b in (0.05, 0.10, 0.20)),
    Variant("weeklies only", weeklies_only=True),
    Variant("snapshot clock", snapshot_clock=True),
]


def stamps(raw_dir: Path) -> list[str]:
    """Fetch stamps with an archive for every symbol, oldest first."""
    found: dict[str, set[str]] = {}
    for path in raw_dir.glob("*.json.gz"):
        if m := _ARCHIVE.match(path.name):
            found.setdefault(m.group(2), set()).add(m.group(1))
    return sorted(stamp for stamp, syms in found.items() if set(SYMBOLS) <= syms)


def load_snapshot(raw_dir: Path, stamp: str, cfg: GexConfig) -> dict[str, Chain]:
    """The six chains of one run, each priced at the spot implied by its own quotes, as main.run() does."""
    chains = {}
    for sym in SYMBOLS:
        chain = parse_payload(sym, gzip.decompress((raw_dir / f"{sym}_{stamp}.json.gz").read_bytes()))
        spot, _ = underlying_level(chain, cfg.risk_free_rate, cfg.futures_roll_days, cfg.basis_strike_range_pct)
        chains[sym] = replace(chain, spot=spot)
    return chains


def variant_dp(chain: Chain, variant: Variant, cfg: GexConfig) -> float | None:
    if variant.snapshot_clock:
        elapsed = max(0.0, (chain.quote_time - chain.asof).total_seconds()) / SECONDS_PER_YEAR
        chain = replace(chain, t_years=chain.t_years - elapsed, asof=chain.quote_time)
    mask = np.ones(len(chain.strike), dtype=bool)
    if variant.band_pct is not None:
        mask &= np.abs(chain.strike / chain.spot - 1) <= variant.band_pct
    if variant.weeklies_only:
        mask &= ~np.isin(chain.root, list(AM_SETTLED_ROOTS))
    contracts = ContractSet.from_chain(chain, cfg, variant.window, mask)
    if len(contracts.strike) == 0:
        return None
    return find_zero_gamma(contracts.total_gex, chain.spot, cfg.zg_search_range_pct, cfg.zg_grid_points)


def variant_dps(chains: dict[str, Chain], cfg: GexConfig) -> dict[str, dict[str, float | None]]:
    return {v.name: {sym: variant_dp(chains[sym], v, cfg) for sym in SYMBOLS} for v in VARIANTS}


def _pct(ours: float | None, vendor: float | None) -> float | None:
    if not ours or not vendor:
        return None
    return 100 * (ours - vendor) / vendor


def _median_abs(values: list[float | None]) -> float | None:
    present = [abs(v) for v in values if v is not None]
    return median(present) if present else None


def report(
    results: dict[str, dict[str, float | None]], vendor: dict[str, float], spots: dict[str, float], title: str,
) -> str:
    def cell(v: float | None, sym: str) -> str:
        if v is None:
            return "-"
        diff = f" ({v - vendor[sym]:+.1f})" if vendor.get(sym) else ""
        return f"{v:.1f}{diff}"

    width = 20
    lines = [title, f"{'':<22}" + "".join(f"{s:>{width}}" for s in SYMBOLS)]
    lines.append(f"{'spot':<22}" + "".join(f"{spots[s]:>{width}.1f}" for s in SYMBOLS))
    lines.append(f"{'vendor DP':<22}" + "".join(f"{(f'{vendor[s]:g}' if vendor.get(s) else '-'):>{width}}" for s in SYMBOLS))
    for name, dps in results.items():
        lines.append(f"{name:<22}" + "".join(f"{cell(dps[s], s):>{width}}" for s in SYMBOLS))

    scored = []
    for name, dps in results.items():
        idx = _median_abs([_pct(dps[s], vendor.get(s)) for s in INDEXES])
        etf = _median_abs([_pct(dps[s], vendor.get(s)) for s in ETFS])
        scored.append((name, idx, etf))

    def fmt(v: float | None) -> str:
        return "   -  " if v is None else f"{v:5.2f}%"

    lines += ["", "Median |DP error| vs the vendor, best index match first (indexes / ETFs):"]
    for name, idx, etf in sorted(scored, key=lambda r: (r[1] is None, r[1] or 0)):
        marker = "   <- ours" if name == BASELINE else ""
        lines.append(f"  {name:<22} {fmt(idx)} / {fmt(etf)}{marker}")
    lines.append("A variant is only interesting if it improves the indexes without breaking the ETFs, "
                 "and does so on several days.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Recompute DP under contract-set variants and score vs a vendor pack.")
    parser.add_argument("--vendor", type=Path, required=True, help="vendor gex_pack.txt")
    parser.add_argument("--vendor-section", choices=("pack", "prior"), default="pack")
    parser.add_argument("--stamp", help="raw archive stamp, e.g. 2026-10-13_0912 (default: latest complete)")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    args = parser.parse_args()

    available = stamps(args.raw_dir)
    if not available:
        raise SystemExit(f"no complete set of raw archives in {args.raw_dir}")
    stamp = args.stamp or available[-1]
    if stamp not in available:
        raise SystemExit(f"no complete set of archives for {stamp}; latest ones: {', '.join(available[-5:])}")

    section = getattr(read_pack_file(args.vendor.read_text(encoding="utf-8")), args.vendor_section)
    if section is None:
        raise SystemExit(f"no [{args.vendor_section.upper()}] in {args.vendor}")
    vendor = {s: section.blocks[s][DP_SLOT] for s in SYMBOLS if s in section.blocks}

    cfg = GexConfig()
    chains = load_snapshot(args.raw_dir, stamp, cfg)
    print(report(variant_dps(chains, cfg), vendor, {s: c.spot for s, c in chains.items()},
                 f"DP variants  snapshot {stamp}  vs vendor [{section.date_text}]"))


if __name__ == "__main__":
    main()
