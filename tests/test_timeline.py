from datetime import date, datetime

from gex_analysis.main import PIVOT_LOG_FIELDS, append_pivot_log
from gex_analysis.output.pack import build_pack, format_pack_file
from gex_analysis.timeline import available_sessions, load_session, render_html, write_timeline

SESSION = date(2026, 10, 7)
BASES = {"SPX": 52.46, "NDX": 250.0, "RUT": 15.0}
SPOTS = {"SPX": 7790.0, "SPY": 777.0, "NDX": 31050.0, "QQQ": 759.0, "RUT": 2850.0, "IWM": 284.0}


def _levels(spot: float, pw: float, tl_shift: float = 0.0) -> list[float]:
    s = spot / 100  # 1% of spot
    return [round(spot + s), pw, spot - s / 2 + tl_shift, pw, round(spot - s), 0, 0,
            round(spot + s), round(spot + 2 * s), 0, 0, spot - s + tl_shift]


def _write_run(packs, hhmm: str, spy_pw: float, tl_shift: float, premarket=None):
    levels = {sym: _levels(spot, round(spot * 0.97), tl_shift) for sym, spot in SPOTS.items()}
    levels["SPY"][1] = levels["SPY"][3] = spy_pw
    pack = build_pack(levels, BASES, SESSION)
    (packs / f"{SESSION}_{hhmm}.txt").write_text(format_pack_file({}, pack, None, [], premarket or pack))
    return pack


def _pivot_rows(fetched: str):
    rows = []
    for index, etf in (("SPX", "SPY"), ("NDX", "QQQ"), ("RUT", "IWM")):
        row = dict.fromkeys(PIVOT_LOG_FIELDS)
        row.update(fetched=fetched, session=SESSION.isoformat(), complex=index,
                   index_spot=SPOTS[index], etf_spot=SPOTS[etf])
        rows.append(row)
    return rows


def _record(tmp_path):
    packs, log = tmp_path / "data" / "packs", tmp_path / "data" / "pivot_log.csv"
    packs.mkdir(parents=True)
    pre = _write_run(packs, "0829", 767, 0.0)
    _write_run(packs, "1012", 767, 2.0, pre)
    _write_run(packs, "1542", 777, 5.0, pre)                       # SPY PW jumps to spot late in the day
    (packs / "2026-10-06_1542.txt").write_text((packs / f"{SESSION}_0829.txt").read_text())
    append_pivot_log(log, _pivot_rows("2026-10-07 08:29") + _pivot_rows("2026-10-07 15:42"))  # no row for 10:12
    return packs, log


def test_load_session_joins_spots_and_orders_runs(tmp_path):
    packs, log = _record(tmp_path)
    assert available_sessions(packs) == [date(2026, 10, 6), SESSION]
    timeline = load_session(SESSION, packs, log)
    assert [r.written for r in timeline.runs] == [datetime(2026, 10, 7, h, m) for h, m in ((8, 29), (10, 12), (15, 42))]
    assert timeline.runs[0].spots["SPY"] == 777.0 and timeline.runs[1].spots == {}
    assert timeline.premarket["SPY"][1] == 767


def test_page_shows_panels_jumps_premarket_and_off_chart_walls(tmp_path):
    packs, log = _record(tmp_path)
    page = render_html(load_session(SESSION, packs, log), prev_href="timeline_2026-10-06.html")
    assert page.count('<section class="panel"') == 6
    assert "PW 767→777 15:42" in page                         # the late SPY put-wall jump is labeled
    assert 'class="pre pw"' in page                            # pre-market references are drawn
    assert "Off the chart: PW 7556 ↓ (all session)" in page    # SPX PW 3% below spot isn't squeezed in
    assert '<td class="changes">PW 767→777  TL 775.12→778.12  GD1 767→777' in page  # the SPY change log
    assert "previous session" in page and "next session" not in page
    assert "<h1>GEX timeline 07 Oct 2026</h1>" in page


def test_write_timeline_links_neighbours(tmp_path):
    packs, log = _record(tmp_path)
    path = write_timeline(date(2026, 10, 6), packs, log, tmp_path / "output")
    assert path.name == "timeline_2026-10-06.html"
    assert 'href="timeline_2026-10-07.html"' in path.read_text()


def test_pack_with_history_backfills_older_sessions(tmp_path):
    from gex_analysis.output.pack import read_pack_file
    from gex_analysis.timeline import pack_with_history

    packs, log = _record(tmp_path)
    rebuilt = read_pack_file(pack_with_history(SESSION, packs, log))
    original = read_pack_file((packs / f"{SESSION}_1542.txt").read_text())
    assert rebuilt.warnings == [] and rebuilt.pack == original.pack and rebuilt.premarket == original.premarket
    assert [(h.written.hour, h.blocks["SPY"][1]) for h in rebuilt.history] == [(8, 767), (10, 767), (15, 777)]
