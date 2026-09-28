import argparse
from pathlib import Path
from statistics import median

from .config import COMPLEXES
from .output.pack import SLOT_NAMES, PackSection, derive_basis, read_pack_file, same_session

MAJORS = (("TL", 2), ("DP", 11), ("CW", 0), ("PW", 1))
RANK_GROUPS = {"GD": slice(3, 7), "C": slice(7, 11)}
CASH_SYMBOLS = [s for c in COMPLEXES for s in (c.index, c.etf)]
ETF_SYMBOLS = {c.etf for c in COMPLEXES}
TRADED = ("SPX", "SPY")


def _fmt(v: float) -> str:
    return "0" if v == 0 else f"{v:g}" if v == int(v) else f"{v:.2f}"


def _pct(ours: float, vendor: float) -> float:
    return 100 * (ours - vendor) / vendor


def major_levels(vendor: PackSection, ours: PackSection) -> str:
    """TL, DP, CW, PW per cash symbol, plus median TL/DP error overall and for SPX/SPY."""
    lines = [f"MAJOR LEVELS  vendor [{vendor.date_text}] vs ours [{ours.date_text}]"]
    errors: dict[str, dict[str, float]] = {"TL": {}, "DP": {}}
    for sym in CASH_SYMBOLS:
        v, o = vendor.blocks.get(sym), ours.blocks.get(sym)
        if v is None or o is None:
            lines.append(f"  {sym:<4} not in {'vendor' if v is None else 'our'} pack")
            continue
        cells = []
        for name, i in MAJORS:
            if not (v[i] and o[i]):
                cells.append(f"{name} {_fmt(v[i])} -> {_fmt(o[i])}")
                continue
            cells.append(f"{name} {_fmt(v[i])} -> {_fmt(o[i])} ({o[i] - v[i]:+.1f}, {_pct(o[i], v[i]):+.2f}%)")
            if name in errors:
                errors[name][sym] = abs(_pct(o[i], v[i]))
        lines.append(f"  {sym:<4} " + "   ".join(cells))

    for name, errs in errors.items():
        if not errs:
            continue
        traded = [e for s, e in errs.items() if s in TRADED]
        line = f"  median |{name} error|: {median(errs.values()):.2f}% across {len(errs)} symbols"
        if traded:
            line += f", {median(traded):.2f}% for SPX/SPY"
        lines.append(line)
    return "\n".join(lines)


def minor_levels(vendor: PackSection, ours: PackSection, tolerance_pct: float = 0.002) -> str:
    """Full 12-slot grid plus how many vendor GD/C levels appear anywhere in our same-group ranks."""
    lines = [
        "MINOR / FULL GRID  (GD is box-resistant by design, so it won't track the vendor's put-side ranks;",
        "                    vendor ETF C levels are converted index levels, so ETF C overlap is n/a)",
    ]
    for c in COMPLEXES:
        fut = c.futures[0]
        if all(s in b.blocks for b in (vendor, ours) for s in (fut, c.index)):
            lines.append(
                f"basis {fut}-{c.index}: vendor {derive_basis(vendor.blocks[fut], vendor.blocks[c.index]):+.2f}"
                f"  ours {derive_basis(ours.blocks[fut], ours.blocks[c.index]):+.2f}"
            )

    lines.append("         " + "".join(f"{n:>10}" for n in SLOT_NAMES))
    for sym, v in vendor.blocks.items():
        o = ours.blocks.get(sym)
        if o is None:
            lines.append(f"{sym:<8} not in our pack")
            continue
        diff = ["-" if 0 in (a, b) else f"{b - a:+.1f}" for a, b in zip(v, o)]
        overlaps = []
        for group, sl in RANK_GROUPS.items():
            if group == "C" and sym in ETF_SYMBOLS:
                overlaps.append("C n/a")
                continue
            ours_ranks = [x for x in o[sl] if x]
            vendor_ranks = [y for y in v[sl] if y]
            hits = sum(any(abs(x - y) <= tolerance_pct * y for x in ours_ranks) for y in vendor_ranks)
            overlaps.append(f"{group} {hits}/{len(vendor_ranks)}")
        lines += [
            f"{sym:<8} " + "".join(f"{_fmt(x):>10}" for x in v) + "   vendor",
            f"{'':<8} " + "".join(f"{_fmt(x):>10}" for x in o) + "   ours",
            f"{'':<8} " + "".join(f"{d:>10}" for d in diff) + "   ours - vendor   " + ", ".join(overlaps),
        ]
    return "\n".join(lines)


def level_changes(old: PackSection | None, new: PackSection) -> list[str]:
    """Cash-symbol slots that moved since the previous pack for the same session."""
    if old is None or not same_session(old, new):
        return [f"first pack for session {new.date_text}"]
    lines = []
    for sym in CASH_SYMBOLS:
        before, after = old.blocks.get(sym), new.blocks.get(sym)
        if before is None or after is None:
            continue
        moved = [
            f"{name} {_fmt(a)}→{_fmt(b)}"
            for name, a, b in zip(SLOT_NAMES, before, after) if round(a, 2) != round(b, 2)
        ]
        if moved:
            lines.append(f"{sym:<4} " + "  ".join(moved))
    return lines or ["no level changes"]


def drift_report(base: PackSection, new: PackSection) -> list[str]:
    """Major levels (TL, DP, CW, PW) per cash symbol that differ from the pre-market pack, with signed deltas."""
    if base is new:
        return []
    lines = []
    for sym in CASH_SYMBOLS:
        before, after = base.blocks.get(sym), new.blocks.get(sym)
        if before is None or after is None:
            continue
        moved = [
            f"{name} {_fmt(after[i])} (pre {_fmt(before[i])}, {after[i] - before[i]:+.1f})"
            for name, i in MAJORS if round(after[i], 2) != round(before[i], 2) and before[i] and after[i]
        ]
        if moved:
            lines.append(f"{sym:<4} " + "  ".join(moved))
    return lines


def compare_sections(vendor: PackSection, ours: PackSection) -> str:
    return major_levels(vendor, ours) + "\n\n" + minor_levels(vendor, ours)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare a vendor gex_pack.txt against ours.")
    parser.add_argument("vendor", type=Path)
    parser.add_argument("ours", type=Path)
    parser.add_argument("--vendor-section", choices=("pack", "prior"), default="pack")
    parser.add_argument("--ours-section", choices=("pack", "prior"), default="pack")
    args = parser.parse_args()

    vendor = read_pack_file(args.vendor.read_text(encoding="utf-8"))
    ours = read_pack_file(args.ours.read_text(encoding="utf-8"))
    for w in vendor.warnings:
        print(f"vendor file warning: {w}")
    print(compare_sections(getattr(vendor, args.vendor_section), getattr(ours, args.ours_section)))


if __name__ == "__main__":
    main()
