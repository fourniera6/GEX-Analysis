"""Session timeline: how the levels moved through one session, as a self-contained HTML page.

Built from the pack copies in data/packs/<session>_<HHMM>.txt (one per run) and each run's implied spot in
data/pivot_log.csv, so it works for any session already recorded. Levels are placed at the time their pack was
written, i.e. when they reached the chart; their data is about 15 minutes older (CBOE's delay).
"""

import argparse
import csv
import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from math import floor, log10
from pathlib import Path
from statistics import median

from .compare import CASH_SYMBOLS, drift_report, level_changes
from .config import COMPLEXES
from .output.pack import (
    SLOT_NAMES,
    HistoryEntry,
    PackSection,
    derive_basis,
    format_pack_date,
    format_pack_file,
    read_pack_file,
)

MAJOR_SERIES = (("TL", 2), ("DP", 11), ("CW", 0), ("PW", 1))  # draw order; colors in CSS --tl/--dp/--cw/--pw
WALLS = ("CW", "PW")
MINOR_SLOTS = range(3, 11)
NEAR_SPOT_PCT = 0.02  # walls and ranks within this of spot set the y-range; farther ones get an edge note
DAY_START, CLOSE = time(8, 0), time(16, 0)
_PACK_COPY = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{4})$")

W, H = 880, 300
ML, MR, MT, MB = 60, 124, 16, 28  # plot margins; the right one holds the end labels


@dataclass
class Run:
    written: datetime  # ET, naive
    blocks: dict[str, list[float]]
    spots: dict[str, float] = field(default_factory=dict)
    source: Path | None = None  # the pack copy it came from


@dataclass
class SessionTimeline:
    session: date
    runs: list[Run]
    premarket: dict[str, list[float]] | None


def available_sessions(packs_dir: Path) -> list[date]:
    found = {m.group(1) for p in packs_dir.glob("*.txt") if (m := _PACK_COPY.match(p.stem))}
    return sorted(date.fromisoformat(s) for s in found)


def _pivot_spots(pivot_log: Path, session: date) -> dict[str, tuple[datetime, dict[str, float]]]:
    """HHMM -> (fetch time, {symbol: implied spot}) for this session's runs."""
    out: dict[str, tuple[datetime, dict[str, float]]] = {}
    if not pivot_log.exists():
        return out
    complexes = {c.index: c for c in COMPLEXES}
    with pivot_log.open(newline="") as f:
        for row in csv.DictReader(f):
            c = complexes.get(row.get("complex", ""))
            if row.get("session") != session.isoformat() or c is None:
                continue
            fetched = datetime.fromisoformat(row["fetched"])
            _, spots = out.setdefault(f"{fetched:%H%M}", (fetched, {}))
            for sym, key in ((c.index, "index_spot"), (c.etf, "etf_spot")):
                try:
                    spots[sym] = float(row[key])
                except (KeyError, TypeError, ValueError):
                    pass
    return out


def load_session(session: date, packs_dir: Path, pivot_log: Path) -> SessionTimeline:
    pivots = _pivot_spots(pivot_log, session)
    loaded = []
    for path in packs_dir.glob(f"{session.isoformat()}_*.txt"):
        m = _PACK_COPY.match(path.stem)
        pack_file = read_pack_file(path.read_text(encoding="utf-8")) if m else None
        if pack_file is None or pack_file.pack is None:
            continue
        hhmm = m.group(2)
        # The pivot log has the full fetch time, so an evening run that feeds the next session sorts first.
        written, spots = pivots.get(hhmm, (datetime.combine(session, time(int(hhmm[:2]), int(hhmm[2:]))), {}))
        loaded.append((Run(written, pack_file.pack.blocks, spots, path), pack_file.premarket))
    loaded.sort(key=lambda item: item[0].written)
    premarket = next((pm.blocks for _, pm in reversed(loaded) if pm is not None), None)
    return SessionTimeline(session, [run for run, _ in loaded], premarket)


def _fmt(v: float) -> str:
    return f"{v:g}" if v == int(v) else f"{v:.2f}"


def _nice_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    raw = (hi - lo) / target
    magnitude = 10 ** floor(log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    first = (lo // step + 1) * step
    return [first + i * step for i in range(int((hi - first) / step) + 1)]


class _Panel:
    """Geometry for one symbol's chart."""

    def __init__(self, timeline: SessionTimeline, sym: str):
        self.sym = sym
        self.runs = timeline.runs
        self.pre = (timeline.premarket or {}).get(sym)
        on_day = [r.written for r in self.runs if r.written.date() == timeline.session]
        self.t0 = min([datetime.combine(timeline.session, DAY_START), *on_day]).replace(minute=0)
        self.t1 = max([datetime.combine(timeline.session, CLOSE), *on_day])

        spots = [r.spots[sym] for r in self.runs if sym in r.spots]
        pivots = [v for r in self.runs for v in (r.blocks[sym][2], r.blocks[sym][11]) if v]
        self.center = median(spots or pivots or [1.0])
        core = spots + pivots + [self.pre[i] for i in (2, 11) if self.pre and self.pre[i]]
        others = [r.blocks[sym][i] for r in self.runs for i in (0, 1, *MINOR_SLOTS)]
        others += [self.pre[i] for i in (0, 1)] if self.pre else []
        values = core + [v for v in others if self.near(v)]
        lo, hi = min(values, default=self.center), max(values, default=self.center)
        pad = max((hi - lo) * 0.08, self.center * 0.0008)
        self.lo, self.hi = lo - pad, hi + pad

    def near(self, v: float) -> bool:
        return bool(v) and abs(v / self.center - 1) <= NEAR_SPOT_PCT

    def in_range(self, v: float) -> bool:
        return bool(v) and self.lo <= v <= self.hi

    def x(self, t: datetime) -> float:
        t = min(max(t, self.t0), self.t1)
        return ML + (W - ML - MR) * (t - self.t0).total_seconds() / (self.t1 - self.t0).total_seconds()

    def y(self, v: float) -> float:
        return MT + (H - MT - MB) * (self.hi - v) / (self.hi - self.lo)

    def spans(self) -> list[tuple[float, float]]:
        """Each run's x extent: from its write time until the next run (the last one holds to the close)."""
        xs = [self.x(r.written) for r in self.runs]
        return list(zip(xs, xs[1:] + [self.x(self.t1)]))


def _step_path(panel: _Panel, values: list[float]) -> str:
    parts, drawing = [], False
    for (x0, x1), v in zip(panel.spans(), values):
        if not panel.in_range(v):
            drawing = False
            continue
        y = panel.y(v)
        parts.append(f"{'L' if drawing else 'M'}{x0:.1f},{y:.1f} H{x1:.1f}")
        drawing = True
    return " ".join(parts)


def _end_labels(items: list[tuple[float, str, str]]) -> str:
    """Right-margin labels with leader lines; nudged apart rather than overlapping."""
    items = sorted(items)
    placed, gap = [], 15.0
    for y, _, _ in items:
        placed.append(max(y, placed[-1] + gap) if placed else y)
    overflow = (placed[-1] if placed else 0) - (H - MB)
    if overflow > 0:
        placed = [p - overflow for p in placed]
        for i in range(len(placed) - 2, -1, -1):
            placed[i] = min(placed[i], placed[i + 1] - gap)
    x_line, x_text = W - MR, W - MR + 18
    out = []
    for (y, cls, text), ly in zip(items, placed):
        out.append(f'<path class="leader" d="M{x_line:.1f},{y:.1f} L{x_text - 4:.1f},{ly:.1f}"/>')
        out.append(f'<circle class="key {cls}" cx="{x_text + 2:.1f}" cy="{ly:.1f}" r="3"/>')
        out.append(f'<text class="end" x="{x_text + 9:.1f}" y="{ly + 4:.1f}">{html.escape(text)}</text>')
    return "".join(out)


def _panel_svg(panel: _Panel) -> str:
    sym, runs = panel.sym, panel.runs
    spans = panel.spans()
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{sym} levels through the session">']

    for tick in _nice_ticks(panel.lo, panel.hi):
        y = panel.y(tick)
        out.append(f'<line class="grid" x1="{ML}" x2="{W - MR}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="axis" x="{ML - 8}" y="{y + 4:.1f}" text-anchor="end">{_fmt(tick)}</text>')
    hour = panel.t0
    while hour <= panel.t1:
        x = panel.x(hour)
        out.append(f'<line class="tick" x1="{x:.1f}" x2="{x:.1f}" y1="{H - MB}" y2="{H - MB + 4}"/>')
        out.append(f'<text class="axis" x="{x:.1f}" y="{H - 8}" text-anchor="middle">{hour:%H:%M}</text>')
        hour += timedelta(hours=1)
    out.append(f'<line class="baseline" x1="{ML}" x2="{W - MR}" y1="{H - MB}" y2="{H - MB}"/>')

    # Minor ranks (GD1-4, C1-4): faint ticks per run.
    for (x0, x1), run in zip(spans, runs):
        for i in MINOR_SLOTS:
            v = run.blocks[sym][i]
            if panel.in_range(v) and x1 > x0:
                out.append(f'<line class="minor" x1="{x0:.1f}" x2="{x1:.1f}" y1="{panel.y(v):.1f}" y2="{panel.y(v):.1f}"/>')

    # Pre-market references (dashed), then the majors as step lines.
    if panel.pre:
        for name, i in MAJOR_SERIES:
            v = panel.pre[i]
            if panel.in_range(v):
                out.append(f'<line class="pre {name.lower()}" x1="{ML}" x2="{W - MR}" y1="{panel.y(v):.1f}" '
                           f'y2="{panel.y(v):.1f}"><title>pre-market {name} {_fmt(v)}</title></line>')
    for name, i in MAJOR_SERIES:
        d = _step_path(panel, [r.blocks[sym][i] for r in runs])
        if d:
            out.append(f'<path class="level {name.lower()}" d="{d}"/>')

    # Spot at each update (implied by the options, at the data time).
    points = [(panel.x(r.written), panel.y(r.spots[sym])) for r in runs
              if sym in r.spots and panel.in_range(r.spots[sym])]
    if points:
        out.append('<polyline class="spot" points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in points) + '"/>')
        out += [f'<circle class="spot-dot" cx="{x:.1f}" cy="{y:.1f}" r="4"/>' for x, y in points]

    # Wall jumps: a marker at each, and a label where it doesn't collide with an earlier one (the table has all).
    taken: list[tuple[float, float, float]] = []
    for name, i in MAJOR_SERIES:
        if name not in WALLS:
            continue
        for k in range(1, len(runs)):
            before, after = runs[k - 1].blocks[sym][i], runs[k].blocks[sym][i]
            if not (before and after and before != after and panel.in_range(after)):
                continue
            x, y = spans[k][0], panel.y(after)
            text = f"{name} {_fmt(before)}→{_fmt(after)} {runs[k].written:%H:%M}"
            width = 6.8 * len(text)
            left = x - 6 - width if x + 6 + width > W - MR else x + 6
            out.append(f'<circle class="jump {name.lower()}" cx="{x:.1f}" cy="{y:.1f}" r="4">'
                       f'<title>{html.escape(text)}</title></circle>')
            if all(left + width < l0 or left > l1 or abs(y - ly) > 13 for l0, l1, ly in taken):
                taken.append((left, left + width, y))
                out.append(f'<text class="note" x="{left:.1f}" y="{y - 8:.1f}">{html.escape(text)}</text>')

    # End labels for the last run.
    if runs:
        last = runs[-1]
        items = [(panel.y(last.blocks[sym][i]), name.lower(), f"{name} {_fmt(last.blocks[sym][i])}")
                 for name, i in MAJOR_SERIES if panel.in_range(last.blocks[sym][i])]
        if sym in last.spots and panel.in_range(last.spots[sym]):
            items.append((panel.y(last.spots[sym]), "spot-key", f"spot {last.spots[sym]:.2f}"))
        out.append(_end_labels(items))

    out.append(f'<line class="crosshair" x1="0" x2="0" y1="{MT}" y2="{H - MB}" visibility="hidden"/>')
    out.append(f'<rect class="hit" x="{ML}" y="{MT}" width="{W - ML - MR}" height="{H - MT - MB}"/>')
    out.append("</svg>")
    return "".join(out)


def _off_chart(panel: _Panel) -> str:
    """Walls too far from spot to plot, with when they held."""
    notes = []
    for name, i in MAJOR_SERIES:
        if name not in WALLS:
            continue
        seen: dict[float, list[datetime]] = {}
        for run in panel.runs:
            v = run.blocks[panel.sym][i]
            if v and not panel.in_range(v):
                seen.setdefault(v, []).append(run.written)
        for v, times in seen.items():
            if len(times) == len(panel.runs):
                when = "all session"
            elif len(times) == 1:
                when = f"{times[0]:%H:%M}"
            else:
                when = f"{times[0]:%H:%M}–{times[-1]:%H:%M}"
            notes.append(f"{name} {_fmt(v)} {'↑' if v > panel.hi else '↓'} ({when})")
    return f'<p class="off">Off the chart: {html.escape(" · ".join(notes))}</p>' if notes else ""


def _table(timeline: SessionTimeline, sym: str) -> str:
    head = "".join(f"<th>{h}</th>" for h in ("Written", "Spot", *SLOT_NAMES, "Changes"))
    rows = []
    previous = None
    for run in timeline.runs:
        section = PackSection(format_pack_date(timeline.session), run.blocks)
        changes = [line[4:].strip() for line in level_changes(previous, section) if line.startswith(f"{sym:<4} ")]
        previous = section
        spot = f"{run.spots[sym]:.2f}" if sym in run.spots else "–"
        cells = "".join(f"<td>{_fmt(v) if v else '–'}</td>" for v in run.blocks[sym])
        rows.append(f"<tr><td>{run.written:%a %H:%M}</td><td>{spot}</td>{cells}"
                    f"<td class=\"changes\">{html.escape('  '.join(changes))}</td></tr>")
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _hover_data(panel: _Panel) -> list[dict]:
    data = []
    for (x0, x1), run in zip(panel.spans(), panel.runs):
        levels = run.blocks[panel.sym]
        data.append({
            "x0": round(x0, 1), "x1": round(x1, 1), "t": f"{run.written:%H:%M}",
            "spot": round(run.spots[panel.sym], 2) if panel.sym in run.spots else None,
            "majors": [[name, levels[i]] for name, i in MAJOR_SERIES],
            "gd": [v for v in levels[3:7] if v], "c": [v for v in levels[7:11] if v],
        })
    return data


def render_html(timeline: SessionTimeline, prev_href: str | None = None, next_href: str | None = None) -> str:
    runs = timeline.runs
    title = f"GEX timeline {timeline.session:%d %b %Y}"
    if runs:
        last = runs[-1].blocks
        bases = "  ·  ".join(f"{c.futures[0]}−{c.index} {derive_basis(last[c.futures[0]], last[c.index]):+.2f}"
                             for c in COMPLEXES if c.futures[0] in last and c.index in last)
        sub = (f"{len(runs)} update{'s' if len(runs) != 1 else ''}, {runs[0].written:%a %H:%M} to "
               f"{runs[-1].written:%H:%M} ET. Each level is placed when its pack was written (when it reached the "
               f"chart); its data is about 15 minutes older. Futures levels are the index levels plus the basis "
               f"(last update: {bases}).")
    else:
        sub = "No pack copies found for this session."

    nav = []
    if prev_href:
        nav.append(f'<a href="{html.escape(prev_href)}">← previous session</a>')
    if next_href:
        nav.append(f'<a href="{html.escape(next_href)}">next session →</a>')

    panels, hover = [], {}
    pre_section = PackSection(format_pack_date(timeline.session), timeline.premarket) if timeline.premarket else None
    for sym in CASH_SYMBOLS:
        if not runs or any(sym not in r.blocks for r in runs):
            continue
        panel = _Panel(timeline, sym)
        hover[sym] = _hover_data(panel)
        drift = ""
        if pre_section:
            last = PackSection(pre_section.date_text, runs[-1].blocks)
            lines = [line[4:].strip() for line in drift_report(pre_section, last) if line.startswith(f"{sym:<4} ")]
            drift = f"vs pre-market: {lines[0]}" if lines else "no major level differs from pre-market"
        panels.append(
            f'<section class="panel" data-sym="{sym}"><header><h2>{sym}</h2><p>{html.escape(drift)}</p></header>'
            f'<div class="chart">{_panel_svg(panel)}<div class="tip" hidden></div></div>{_off_chart(panel)}'
            f'<details><summary>Table and changes</summary>{_table(timeline, sym)}</details></section>'
        )

    legend = "".join(
        f'<span><svg width="22" height="10"><line class="{cls}" x1="1" x2="21" y1="5" y2="5"/></svg>{label}</span>'
        for cls, label in (("level tl", "TL"), ("level dp", "DP"), ("level cw", "CW"), ("level pw", "PW"),
                           ("spot", "Spot"), ("pre tl", "Pre-market (dashed)"), ("minor", "GD/C 1–4"))
    )
    return _PAGE.format(
        title=html.escape(title), sub=html.escape(sub), nav=" ".join(nav), legend=legend,
        panels="".join(panels), data=json.dumps(hover).replace("</", "<\\/"),
    )


def write_timeline(session: date, packs_dir: Path, pivot_log: Path, out_dir: Path) -> Path:
    timeline = load_session(session, packs_dir, pivot_log)
    sessions = available_sessions(packs_dir)
    earlier = [s for s in sessions if s < session]
    later = [s for s in sessions if s > session]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"timeline_{session}.html"
    path.write_text(render_html(
        timeline,
        f"timeline_{earlier[-1]}.html" if earlier else None,
        f"timeline_{later[0]}.html" if later else None,
    ), encoding="utf-8")
    return path


def pack_with_history(session: date, packs_dir: Path, pivot_log: Path) -> str:
    """The session's last pack with [HISTORY] rebuilt from its pack copies: for testing the NT8 rendering
    on sessions recorded before the service wrote [HISTORY] itself."""
    runs = load_session(session, packs_dir, pivot_log).runs
    if not runs:
        raise SystemExit(f"no pack copies for {session} in {packs_dir}")
    f = read_pack_file(runs[-1].source.read_text(encoding="utf-8"))
    history = [HistoryEntry(r.written.replace(second=0, microsecond=0), r.blocks) for r in runs]
    return format_pack_file(f.refs, f.pack, f.prior, [f"{session} rebuilt with [HISTORY]"], f.premarket,
                            history)


def main() -> None:
    parser = argparse.ArgumentParser(description="Draw how the levels moved through a session (HTML page).")
    parser.add_argument("session", nargs="?", type=date.fromisoformat, help="YYYY-MM-DD (default: latest)")
    parser.add_argument("--packs-dir", type=Path, default=Path("data/packs"))
    parser.add_argument("--pivot-log", type=Path, default=Path("data/pivot_log.csv"))
    parser.add_argument("--out-dir", type=Path, default=Path("output"))
    parser.add_argument("--all", action="store_true", help="write a page for every recorded session")
    parser.add_argument("--pack-with-history", type=Path, metavar="PATH",
                        help="also write the session's last pack with [HISTORY] rebuilt (to test NT8 rendering)")
    args = parser.parse_args()

    sessions = available_sessions(args.packs_dir)
    if not sessions:
        raise SystemExit(f"no pack copies in {args.packs_dir}")
    for session in sessions if args.all else [args.session or sessions[-1]]:
        path = write_timeline(session, args.packs_dir, args.pivot_log, args.out_dir)
        print(f"Wrote {path}")
    print(f'Open it from WSL with: explorer.exe "$(wslpath -w {path})"')
    if args.pack_with_history:
        session = args.session or sessions[-1]
        args.pack_with_history.write_text(pack_with_history(session, args.packs_dir, args.pivot_log), encoding="utf-8")
        print(f"Wrote {args.pack_with_history} ({session} with [HISTORY])")


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --ring: rgba(11,11,11,0.10);
  --tl: #2a78d6; --dp: #eb6834; --cw: #1baf7a; --pw: #eda100;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
    --tl: #3987e5; --dp: #d95926; --cw: #199e70; --pw: #c98500;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
  --tl: #3987e5; --dp: #d95926; --cw: #199e70; --pw: #c98500;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--page); color: var(--ink); font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1680px; margin: 0 auto; padding: 20px 16px 40px; }}
h1 {{ font-size: 20px; margin: 0 0 4px; }}
.sub {{ color: var(--ink-2); margin: 0 0 8px; max-width: 900px; }}
nav {{ display: flex; gap: 16px; margin-bottom: 12px; }}
nav a {{ color: var(--ink-2); }}
.legend {{ display: flex; flex-wrap: wrap; gap: 6px 18px; color: var(--ink-2); font-size: 13px; margin-bottom: 16px; }}
.legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
.grid-panels {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 620px), 1fr)); gap: 16px; }}
.panel {{ background: var(--surface); border: 1px solid var(--ring); border-radius: 10px; padding: 12px 12px 8px; min-width: 0; }}
.panel header {{ display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }}
.panel h2 {{ font-size: 16px; margin: 0; }}
.panel header p {{ margin: 0; color: var(--ink-2); font-size: 13px; }}
.chart {{ position: relative; }}
.chart svg {{ display: block; width: 100%; height: auto; }}
.legend svg {{ flex: none; }}
.off {{ margin: 4px 0 0; color: var(--ink-2); font-size: 12px; }}
.grid {{ stroke: var(--grid); stroke-width: 1; }}
.baseline, .tick {{ stroke: var(--axis); stroke-width: 1; }}
.axis {{ fill: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }}
.level {{ fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }}
.pre {{ stroke-width: 1.5; stroke-dasharray: 5 4; opacity: 0.6; }}
.tl {{ stroke: var(--tl); fill: var(--tl); }} .dp {{ stroke: var(--dp); fill: var(--dp); }}
.cw {{ stroke: var(--cw); fill: var(--cw); }} .pw {{ stroke: var(--pw); fill: var(--pw); }}
path.level, line.level, line.pre {{ fill: none; }}
.minor {{ stroke: var(--muted); stroke-width: 1; opacity: 0.45; }}
.spot {{ fill: none; stroke: var(--ink-2); stroke-width: 1.5; stroke-dasharray: 1 3; stroke-linecap: round; }}
.spot-dot {{ fill: var(--ink-2); stroke: var(--surface); stroke-width: 2; }}
.spot-key {{ fill: var(--ink-2); }}
.jump {{ stroke: var(--surface); stroke-width: 2; }}
.note {{ fill: var(--ink-2); font-size: 12px; paint-order: stroke; stroke: var(--surface); stroke-width: 3px; stroke-linejoin: round; }}
.end {{ fill: var(--ink); font-size: 13px; font-variant-numeric: tabular-nums; }}
.leader {{ stroke: var(--axis); stroke-width: 1; fill: none; }}
.key {{ stroke: none; }}
.crosshair {{ stroke: var(--muted); stroke-width: 1; }}
.hit {{ fill: transparent; cursor: crosshair; }}
.tip {{ position: absolute; pointer-events: none; background: var(--surface); border: 1px solid var(--ring);
  border-radius: 8px; padding: 8px 10px; font-size: 12px; box-shadow: 0 4px 16px rgba(0,0,0,0.12); min-width: 150px; }}
.tip .t {{ color: var(--ink-2); margin-bottom: 4px; }}
.tip .row {{ display: flex; align-items: center; gap: 6px; font-variant-numeric: tabular-nums; }}
.tip .row b {{ min-width: 64px; }}
.tip .row i {{ display: inline-block; width: 12px; height: 2px; }}
.tip .minor-row {{ color: var(--ink-2); margin-top: 4px; }}
details {{ margin-top: 6px; color: var(--ink-2); font-size: 13px; }}
summary {{ cursor: pointer; }}
.table-wrap {{ overflow-x: auto; }}
table {{ border-collapse: collapse; margin-top: 8px; font-size: 12px; font-variant-numeric: tabular-nums; color: var(--ink); }}
th, td {{ padding: 3px 8px; text-align: right; border-bottom: 1px solid var(--grid); white-space: nowrap; }}
th {{ color: var(--ink-2); font-weight: 600; }}
td.changes {{ text-align: left; color: var(--ink-2); }}
</style>
</head>
<body>
<main>
<h1>{title}</h1>
<p class="sub">{sub}</p>
<nav>{nav}</nav>
<div class="legend">{legend}</div>
<div class="grid-panels">{panels}</div>
</main>
<script>
const DATA = {data};
const COLORS = {{ TL: "var(--tl)", DP: "var(--dp)", CW: "var(--cw)", PW: "var(--pw)" }};
const fmt = v => Number.isInteger(v) ? String(v) : v.toFixed(2);
document.querySelectorAll(".panel").forEach(panel => {{
  const runs = DATA[panel.dataset.sym];
  const svg = panel.querySelector("svg"), hit = panel.querySelector(".hit");
  const cross = panel.querySelector(".crosshair"), tip = panel.querySelector(".tip");
  if (!runs || !hit) return;
  const show = ev => {{
    const box = svg.getBoundingClientRect(), scale = {width} / box.width;
    const x = (ev.clientX - box.left) * scale;
    let run = runs.find(r => x >= r.x0 && x < r.x1) || (x < runs[0].x0 ? runs[0] : runs[runs.length - 1]);
    cross.setAttribute("x1", run.x0); cross.setAttribute("x2", run.x0); cross.setAttribute("visibility", "visible");
    tip.replaceChildren();
    const head = document.createElement("div"); head.className = "t";
    head.textContent = "Written " + run.t + (run.spot != null ? "  ·  spot " + run.spot.toFixed(2) : "");
    tip.appendChild(head);
    run.majors.forEach(([name, v]) => {{
      const row = document.createElement("div"); row.className = "row";
      const key = document.createElement("i"); key.style.background = COLORS[name];
      const val = document.createElement("b"); val.textContent = v ? fmt(v) : "–";
      row.append(key, val, document.createTextNode(name)); tip.appendChild(row);
    }});
    [["GD", run.gd], ["C", run.c]].forEach(([name, vals]) => {{
      const row = document.createElement("div"); row.className = "minor-row";
      row.textContent = name + " " + (vals.length ? vals.map(fmt).join(", ") : "–"); tip.appendChild(row);
    }});
    tip.hidden = false;
    const px = run.x0 / scale, room = box.width - px;
    tip.style.left = (room > tip.offsetWidth + 24 ? px + 12 : px - tip.offsetWidth - 12) + "px";
    tip.style.top = "8px";
  }};
  hit.addEventListener("pointermove", show);
  hit.addEventListener("pointerleave", () => {{ tip.hidden = true; cross.setAttribute("visibility", "hidden"); }});
}});
</script>
</body>
</html>
""".replace("{width}", str(W))


if __name__ == "__main__":
    main()
