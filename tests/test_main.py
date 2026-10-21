import csv
import gzip
import json
import math
from datetime import date, datetime, time, timedelta

import requests

import gex_analysis.main as main
from gex_analysis.config import GexConfig
from gex_analysis.data_sources.base import DataSource
from gex_analysis.data_sources.cboe import ET, SECONDS_PER_YEAR, parse_payload
from gex_analysis.main import (
    append_pivot_log,
    describe_open_interest,
    intraday_slots,
    previous_open_interest,
    run,
    run_intraday,
)


def _archive(raw_dir, name, open_interest):
    options = [{"option": "SPXW261016C07700000", "iv": 0.2, "open_interest": oi} for oi in open_interest]
    payload = {"data": {"current_price": 7700.0, "last_trade_time": "2026-09-25T16:00:00", "options": options}}
    (raw_dir / name).write_bytes(gzip.compress(json.dumps(payload).encode()))


def test_previous_open_interest_picks_latest_archive_for_symbol(tmp_path):
    assert previous_open_interest("SPX", tmp_path) is None
    _archive(tmp_path, "SPX_2026-09-25_1802.json.gz", [100, 50])
    _archive(tmp_path, "SPX_2026-09-26_0845.json.gz", [120, 60])
    _archive(tmp_path, "SPY_2026-09-27_0845.json.gz", [1, 1])
    assert previous_open_interest("SPX", tmp_path) == ("2026-09-26_0845", 180.0)


def test_describe_open_interest_lines_and_staleness():
    current = {"SPX": 180.0, "SPY": 20.0}
    lines, stale = describe_open_interest(
        current, {"SPX": ("2026-09-25_1802", 180.0), "SPY": ("2026-09-25_1802", 10.0)}, date(2026, 9, 28))
    assert "unchanged vs archive 2026-09-25_1802" in lines[0]
    assert "+100.00% vs archive" in lines[1]
    assert stale == ["SPX"]  # SPY refreshed, SPX didn't (like RUT on 28 Sep)

    same = {"SPX": ("2026-09-25_1802", 180.0), "SPY": ("2026-09-25_1802", 20.0)}
    assert describe_open_interest(current, same, date(2026, 9, 28))[1] == ["SPX", "SPY"]
    assert describe_open_interest(current, same, date(2026, 9, 25))[1] == []  # same-day rerun
    lines, stale = describe_open_interest(current, {"SPX": None, "SPY": None}, date(2026, 9, 28))
    assert "no earlier archive" in lines[0] and stale == []


def test_append_pivot_log_writes_header_once(tmp_path):
    path = tmp_path / "data" / "pivot_log.csv"
    row = {"fetched": "2026-09-28 08:45", "session": "2026-09-28", "complex": "SPX", "tl_combined": 7711.3}
    append_pivot_log(path, [row])
    append_pivot_log(path, [{**row, "complex": "NDX", "dp_etf_only": None}])
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["complex"] for r in rows] == ["SPX", "NDX"]
    assert rows[0]["tl_combined"] == "7711.3"
    assert rows[1]["dp_etf_only"] == ""
    assert path.read_text().count("fetched") == 1


SPOTS = {"SPX": 7700.0, "SPY": 767.0, "NDX": 30600.0, "QQQ": 745.0, "RUT": 2830.0, "IWM": 282.0}
ROOTS = {"SPX": ("SPXW", "SPX"), "NDX": ("NDXP", "NDX"), "RUT": ("RUTW", "RUT")}


def _synthetic_payload(symbol: str, shift: float = 0.0) -> bytes:
    """A small parity-consistent chain: a near (Mon) and a Dec expiry, 11 strikes around spot."""
    spot, asof = SPOTS[symbol] * (1 + shift), datetime(2026, 9, 25, 16, 0, tzinfo=ET)
    near_root, far_root = ROOTS.get(symbol, (symbol, symbol))
    far_settle = time(9, 30) if far_root in ("SPX", "NDX", "RUT") else time(16, 0)
    options = []
    for root, code, expiry in [
        (near_root, "260928", datetime(2026, 9, 28, 16, 0, tzinfo=ET)),
        (far_root, "261218", datetime.combine(date(2026, 12, 18), far_settle, ET)),
    ]:
        t = (expiry.timestamp() - asof.timestamp()) / SECONDS_PER_YEAR
        forward = spot * math.exp(0.02 * t)
        for i in range(-5, 6):
            k = round(spot * (1 + 0.01 * i))
            put = 0.06 * spot
            call = put + math.exp(-0.04 * t) * (forward - k)
            for cp, mid, oi in (("C", call, 100 + 20 * i), ("P", put, 100 - 20 * i)):
                options.append({"option": f"{root}{code}{cp}{k * 1000:08d}", "iv": 0.2, "open_interest": oi,
                                "volume": 0, "bid": mid * 0.99, "ask": mid * 1.01})
    data = {"current_price": spot, "close": spot, "last_trade_time": "2026-09-25T16:00:00", "options": options}
    return json.dumps({"data": data}).encode()


class FakeSource(DataSource):
    def __init__(self, shift: float = 0.0):
        self.shift = shift  # move every spot by this fraction, e.g. an intraday selloff

    def fetch_chain(self, underlying):
        return parse_payload(underlying, _synthetic_payload(underlying, self.shift))


def test_run_without_archive_still_writes_pack_history_and_log(tmp_path):
    raw = tmp_path / "data" / "raw"
    kwargs = dict(source=FakeSource(), out_dir=tmp_path / "output", raw_dir=raw,
                  pivot_log=tmp_path / "data" / "pivot_log.csv")

    first = run(archive=False, **kwargs)
    assert list(raw.iterdir()) == []
    assert first.changes == ["first pack for session 28 SEP 2026"]
    assert first.history_path.parent == tmp_path / "data" / "packs"
    assert first.history_path.name.startswith("2026-09-28_")
    assert first.history_path.read_text() == first.pack_path.read_text()
    assert len((tmp_path / "data" / "pivot_log.csv").read_text().splitlines()) == 1 + 3

    second = run(archive=True, **kwargs)
    assert len(list(raw.iterdir())) == 6
    assert second.changes == ["no level changes"]


def test_premarket_is_set_before_the_open_and_kept_through_the_session(tmp_path):
    from gex_analysis.output.pack import read_pack_file

    kwargs = dict(out_dir=tmp_path / "output", raw_dir=tmp_path / "data" / "raw",
                  pivot_log=tmp_path / "data" / "pivot_log.csv", archive=False)
    morning = run(source=FakeSource(), now=datetime(2026, 9, 28, 8, 45, tzinfo=ET), **kwargs)
    pre = read_pack_file(morning.pack_path.read_text())
    assert pre.premarket.blocks == pre.pack.blocks and morning.drift == []
    assert [h.written for h in pre.history] == [datetime(2026, 9, 28, 8, 45)]
    assert morning.timeline_path == tmp_path / "output" / "timeline_2026-09-28.html"

    midday = run(source=FakeSource(shift=-0.01), now=datetime(2026, 9, 28, 11, 45, tzinfo=ET),
                 hold_levels=True, **kwargs)
    after = read_pack_file(midday.pack_path.read_text())
    assert after.premarket.blocks == pre.pack.blocks            # kept from the morning
    assert after.pack.blocks != pre.pack.blocks                 # while the live levels moved
    assert midday.history_path.name == "2026-09-28_1145.txt"

    assert after.pack.blocks["SPY"][0] == pre.pack.blocks["SPY"][0]  # CW held: the shifted data alone picks 775
    assert [(h.written.hour, h.blocks) for h in after.history] == [(8, pre.pack.blocks), (11, after.pack.blocks)]
    assert "11:45" in midday.timeline_path.read_text()

    rerun = run(source=FakeSource(shift=-0.01), now=datetime(2026, 9, 28, 9, 10, tzinfo=ET), **kwargs)
    reread = read_pack_file(rerun.pack_path.read_text())
    assert reread.premarket.blocks == reread.pack.blocks != pre.pack.blocks  # a pre-open rerun resets it
    assert reread.pack.blocks["SPY"][0] == 775.0                              # and isn't held
    assert [h.written.strftime("%H%M") for h in reread.history] == ["0845", "1145", "0910"]  # in write order

    path = rerun.pack_path
    path.write_text(path.read_text().replace("28 SEP 2026", "25 SEP 2026"))  # as if left over from the last session
    nextday = read_pack_file(run(source=FakeSource(), now=datetime(2026, 9, 28, 8, 40, tzinfo=ET), **kwargs)
                             .pack_path.read_text())
    assert [h.written.strftime("%H%M") for h in nextday.history] == ["0840"]  # a new session starts a new history


def test_intraday_slots():
    def at(h, m):
        return datetime(2026, 9, 28, h, m, tzinfo=ET)

    slots = intraday_slots(at(8, 45), time(9, 45), time(15, 45), 30)
    assert len(slots) == 13 and slots[0] == at(9, 45) and slots[-1] == at(15, 45)
    assert intraday_slots(at(11, 57), time(9, 45), time(15, 45), 30)[0] == at(12, 15)
    assert intraday_slots(at(15, 45), time(9, 45), time(15, 45), 30) == []


def test_run_intraday_archives_hourly_survives_errors_and_skips_missed_slots(monkeypatch):
    clock = {"now": datetime(2026, 9, 28, 8, 45, tzinfo=ET)}

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    def fake_sleep(seconds):
        clock["now"] += timedelta(seconds=seconds)

    calls = []

    def run_once(archive, hold_levels):
        now = clock["now"]
        calls.append((f"{now:%H:%M}", archive, hold_levels))
        if f"{now:%H:%M}" == "11:15":
            raise requests.ConnectionError("CBOE unreachable")
        if f"{now:%H:%M}" == "12:15":
            clock["now"] += timedelta(hours=2)  # laptop slept through 12:45-14:15
        return object()

    monkeypatch.setattr(main, "datetime", FakeDateTime)
    monkeypatch.setattr(main, "sleep", fake_sleep)
    monkeypatch.setattr(main, "print_summary", lambda result: None)
    monkeypatch.setattr(main, "print_changes", lambda result: None)
    run_intraday(run_once, GexConfig())

    assert [(t, archive) for t, archive, _ in calls] == [
        ("08:45", True),
        ("09:45", True), ("10:15", True), ("10:45", False),
        ("11:15", True),            # failed, so the hour wasn't archived...
        ("11:45", True),            # ...and the next slot in that hour archives instead
        ("12:15", True),            # 12:45-13:45 are skipped: missed while asleep until 14:15
        ("14:15", True), ("14:45", False), ("15:15", True), ("15:45", False),
    ]
    assert [hold for _, _, hold in calls] == [False] + [True] * (len(calls) - 1)  # fresh first, then held


class GappySource(FakeSource):
    """CBOE's file without the Dec quotes for some symbols, for their first `bad` fetches (21 Oct: IWM)."""

    def __init__(self, bad: dict[str, int]):
        super().__init__()
        self.bad, self.fetches = dict(bad), {}

    def fetch_chain(self, underlying):
        self.fetches[underlying] = self.fetches.get(underlying, 0) + 1
        payload = json.loads(_synthetic_payload(underlying))
        if self.fetches[underlying] <= self.bad.get(underlying, 0):
            for o in payload["data"]["options"]:
                if o["option"][-15:-9] == "261218":
                    o["bid"] = o["ask"] = 0.0
        return parse_payload(underlying, json.dumps(payload).encode())


def _run_with(source, tmp_path, monkeypatch, name):
    waits = []
    monkeypatch.setattr(main, "sleep", waits.append)
    result = run(source=source, out_dir=tmp_path / name, raw_dir=tmp_path / name / "raw",
                 pivot_log=tmp_path / name / "pivot_log.csv", archive=True)
    return result, waits


def test_a_chain_missing_quotes_is_refetched(tmp_path, monkeypatch):
    clean, _ = _run_with(FakeSource(), tmp_path, monkeypatch, "clean")
    source = GappySource({"IWM": 1})
    result, waits = _run_with(source, tmp_path, monkeypatch, "gappy")
    assert waits == [60.0] and source.fetches["IWM"] == 2 and source.fetches["SPY"] == 1
    assert result.pack_path.read_text().split("[PACK]")[1] == clean.pack_path.read_text().split("[PACK]")[1]


def test_an_etf_that_stays_unpriceable_falls_back_to_its_quote(tmp_path, monkeypatch, capsys):
    clean, _ = _run_with(FakeSource(), tmp_path, monkeypatch, "clean")
    result, waits = _run_with(GappySource({"IWM": 99}), tmp_path, monkeypatch, "gappy")
    assert len(waits) == 2
    assert "WARNING: IWM" in capsys.readouterr().out
    assert result.analyses["IWM"].spot == SPOTS["IWM"]  # the quoted price
    for sym in ("SPX", "SPY", "NDX", "QQQ", "RUT"):
        assert result.analyses[sym].zero_gamma == clean.analyses[sym].zero_gamma


def test_an_index_that_stays_unpriceable_fails_cleanly(tmp_path, monkeypatch, capsys):
    import pytest

    monkeypatch.setattr(main, "sleep", lambda s: None)
    monkeypatch.setattr(main, "CboeDelayedSource", lambda: GappySource({"SPX": 99}))
    pack = tmp_path / "pack.txt"
    monkeypatch.setattr("sys.argv", ["gex", "--pack-path", str(pack), "--out-dir", str(tmp_path / "o"),
                                     "--raw-dir", str(tmp_path / "raw"), "--pivot-log", str(tmp_path / "log.csv")])
    with pytest.raises(SystemExit) as exit_info:
        main.main()
    assert "Run failed: no two-sided quotes to imply a 2026-12-18 forward for SPX after 3 tries" in str(exit_info.value)
    assert not pack.exists()
    assert len(list((tmp_path / "raw").glob("SPX_*.json.gz"))) == 1  # the bad snapshot is kept for diagnosis
