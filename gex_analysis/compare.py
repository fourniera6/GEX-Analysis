import argparse
from pathlib import Path

from .config import COMPLEXES
from .output.pack import SLOT_NAMES, PackSection, derive_basis, read_pack_file

RANK_GROUPS = {"GD": slice(3, 7), "C": slice(7, 11)}


def _fmt(v: float) -> str:
    return "0" if v == 0 else f"{v:g}" if v == int(v) else f"{v:.2f}"


def compare_sections(vendor: PackSection, ours: PackSection, tolerance_pct: float = 0.002) -> str:
    """Slot-by-slot diff, plus how many vendor GD/C levels appear anywhere in our same-group ranks."""
    lines = [f"vendor [{vendor.date_text}] vs ours [{ours.date_text}]"]
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
        matches = []
        for group, sl in RANK_GROUPS.items():
            ours_ranks = [x for x in o[sl] if x]
            hits = sum(any(abs(x - y) <= tolerance_pct * y for x in ours_ranks) for y in v[sl] if y)
            matches.append(f"{group} {hits}/{sum(1 for y in v[sl] if y)}")
        lines += [
            f"{sym:<8} " + "".join(f"{_fmt(x):>10}" for x in v) + "   vendor",
            f"{'':<8} " + "".join(f"{_fmt(x):>10}" for x in o) + "   ours",
            f"{'':<8} " + "".join(f"{d:>10}" for d in diff) + "   ours - vendor   " + ", ".join(matches),
        ]
    return "\n".join(lines)


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
