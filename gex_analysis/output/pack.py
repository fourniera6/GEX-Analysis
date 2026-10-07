"""gex_pack.txt writer and a mirror of GexLevelsPastePack.cs's reader. Contract: docs/GEX-PACK-SPEC.md."""

import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from statistics import median

from ..config import COMPLEXES
from .analysis import GammaPeak, GexAnalysis

SLOT_COUNT = 12
SLOT_NAMES = ("CW", "PW", "TL", "GD1", "GD2", "GD3", "GD4", "C1", "C2", "C3", "C4", "DP")
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_REF_SEPARATORS = re.compile(r"[\s,|=]+")
_SYMBOL_TAIL = re.compile(r"[A-Za-z0-9]+$")


@dataclass
class PackSection:
    date_text: str
    blocks: dict[str, list[float]]


@dataclass
class HistoryEntry:
    written: datetime  # when this pack was written (ET): from then on it was on the chart
    blocks: dict[str, list[float]]


@dataclass
class PackFile:
    refs: dict[str, float] = field(default_factory=dict)
    pack: PackSection | None = None
    prior: PackSection | None = None
    premarket: PackSection | None = None  # service extension; the indicator ignores unknown sections
    history: list[HistoryEntry] = field(default_factory=list)  # service extension: every pack this session
    warnings: list[str] = field(default_factory=list)


def pack_levels(a: GexAnalysis) -> list[float]:
    def ranks(peaks: list[GammaPeak]) -> list[float]:
        top = [p.strike for p in peaks[:4]]
        return top + [0.0] * (4 - len(top))

    return [
        a.call_wall.peak.strike if a.call_wall else 0.0,
        a.put_wall.peak.strike if a.put_wall else 0.0,
        a.transition_level or 0.0,
        *ranks(a.support_peaks),
        *ranks(a.resistance_peaks),
        a.zero_gamma or 0.0,
    ]


def build_pack(levels: dict[str, list[float]], bases: dict[str, float], session: date) -> PackSection:
    """levels: 12 slots per index and ETF symbol. Futures blocks are the index block + one basis (Invariant B)."""
    blocks: dict[str, list[float]] = {}
    for c in COMPLEXES:
        shifted = [v + bases[c.index] if v else 0.0 for v in levels[c.index]]
        for fut in c.futures:
            blocks[fut] = shifted
    for c in COMPLEXES:
        blocks[c.index] = levels[c.index]
        blocks[c.etf] = levels[c.etf]
    return PackSection(format_pack_date(session), blocks)


def build_refs(spots: dict[str, float], bases: dict[str, float]) -> dict[str, float]:
    refs = {}
    for c in COMPLEXES:
        refs[c.etf] = spots[c.etf]
        for fut in c.futures:
            refs[fut] = round(spots[c.index] + bases[c.index], 2)
    return refs


def format_pack_date(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def parse_pack_date(text: str) -> date | None:
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    return None


def format_number(v: float) -> str:
    return f"{v:.10g}"


def _format_blocks(blocks: dict[str, list[float]]) -> list[str]:
    return [f"{sym}=" + "|".join(format_number(v) for v in vals) for sym, vals in blocks.items()]


def _format_section(name: str, section: PackSection) -> list[str]:
    blocks = _format_blocks(section.blocks)
    return [f"[{name}]", section.date_text, *(b + ";" for b in blocks[:-1]), blocks[-1], ""]


def _format_history(history: list[HistoryEntry]) -> list[str]:
    # One line per pack: its write time, then its blocks. Not space-joined like [PACK]: each line stands alone.
    return ["[HISTORY]", *(f"{h.written:%Y-%m-%dT%H:%M} " + ";".join(_format_blocks(h.blocks)) for h in history), ""]


def format_pack_file(
    refs: dict[str, float],
    pack: PackSection,
    prior: PackSection | None,
    header_lines: list[str] = (),
    premarket: PackSection | None = None,
    history: list[HistoryEntry] = (),
) -> str:
    lines = [f"# {h}" for h in header_lines]
    lines += ["[REFS]", *(f"{sym} {format_number(p)}" for sym, p in refs.items()), ""]
    lines += _format_section("PACK", pack)
    if prior is not None:
        lines += _format_section("PRIOR", prior)
    if premarket is not None:
        lines += _format_section("PREMARKET", premarket)
    if history:
        lines += _format_history(history)
    return "\n".join(lines)


def write_pack_file(
    path: Path,
    refs: dict[str, float],
    pack: PackSection,
    header_lines: list[str] = (),
    premarket: PackSection | None = None,
    history: list[HistoryEntry] = (),
) -> None:
    """Atomic write. The existing [PACK] rolls into [PRIOR] when the session date changes."""
    prior = None
    if path.exists():
        existing = read_pack_file(path.read_text(encoding="utf-8"))
        prior = existing.pack if existing.pack and not same_session(existing.pack, pack) else existing.prior
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(format_pack_file(refs, pack, prior, header_lines, premarket, history), encoding="utf-8")
    _replace(tmp, path)


def same_session(a: PackSection, b: PackSection) -> bool:
    da, db = parse_pack_date(a.date_text), parse_pack_date(b.date_text)
    if da and db:
        return da == db
    return a.date_text.strip().lower() == b.date_text.strip().lower()


def _replace(src: Path, dst: Path, attempts: int = 5) -> None:
    # On Windows os.replace fails while NT8 holds the file open for its (brief) read.
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.2)


def read_pack_file(text: str) -> PackFile:
    sections: dict[str, list[str]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().upper()
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)

    result = PackFile()
    for line in sections.get("REFS", []):
        parts = [p for p in _REF_SEPARATORS.split(line) if p]
        try:
            result.refs[parts[0].upper()] = float(parts[1])
        except (IndexError, ValueError):
            result.warnings.append(f"[REFS] unparseable line: {line!r}")
    for name in ("PACK", "PRIOR", "PREMARKET"):
        if name in sections:
            section, warnings = parse_pack_string(" ".join(sections[name]))
            setattr(result, name.lower(), section)
            result.warnings += [f"[{name}] {w}" for w in warnings]
    for line in sections.get("HISTORY", []):
        stamp, _, rest = line.partition(" ")
        try:
            written = datetime.fromisoformat(stamp)
        except ValueError:
            result.warnings.append(f"[HISTORY] bad time in {line[:40]!r}")
            continue
        section, warnings = parse_pack_string(rest)
        result.history.append(HistoryEntry(written, section.blocks))
        result.warnings += [f"[HISTORY] {stamp} {w}" for w in warnings]
    return result


def parse_pack_string(s: str) -> tuple[PackSection, list[str]]:
    blocks: dict[str, list[float]] = {}
    warnings: list[str] = []
    date_text = None
    for chunk in s.split(";"):
        if "=" not in chunk:
            continue
        left, right = chunk.split("=", 1)
        match = _SYMBOL_TAIL.search(left)
        if match is None:
            warnings.append(f"no symbol before '=' in {chunk.strip()!r}")
            continue
        if date_text is None:
            date_text = left[: match.start()].strip()
        symbol = match.group().upper()
        try:
            values = [float(v) for v in right.split("|")]
        except ValueError:
            warnings.append(f"{symbol}: non-numeric value")
            continue
        if len(values) != SLOT_COUNT:
            warnings.append(f"{symbol}: {len(values)} values, expected {SLOT_COUNT}")
            continue
        blocks[symbol] = values
    return PackSection(date_text or "", blocks), warnings


# Mirrors of the indicator's consumption math (spec §3), for tests and comparisons.

def derive_basis(futures_block: list[float], index_block: list[float]) -> float:
    return median(f - i for f, i in zip(futures_block, index_block))


def derive_ratio(futures_ref: float, basis: float, etf_ref: float) -> float:
    return (futures_ref - basis) / etf_ref


def etf_to_futures(value: float, ratio: float, basis: float, tick: float) -> float:
    return math.floor((value * ratio + basis) / tick + 0.5) * tick
