import csv
import gzip
import json
from datetime import date

from gex_analysis.main import append_pivot_log, describe_open_interest, previous_open_interest


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
