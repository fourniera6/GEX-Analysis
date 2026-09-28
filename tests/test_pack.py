from datetime import date
from pathlib import Path

import pytest

from gex_analysis.output.analysis import GammaPeak, GexAnalysis, WallInfo
from gex_analysis.output.pack import (
    PackSection,
    build_pack,
    build_refs,
    derive_basis,
    derive_ratio,
    etf_to_futures,
    format_pack_date,
    format_pack_file,
    pack_levels,
    parse_pack_date,
    read_pack_file,
    write_pack_file,
)

REFERENCE_PACKS = Path(__file__).parent.parent / "validation" / "reference_packs"
VENDOR_PACK = REFERENCE_PACKS / "2026-09-25_vendor.txt"
SYMBOLS = ("SPX", "SPY", "NDX", "QQQ", "RUT", "IWM")
BASES = {"SPX": 62.65, "NDX": 288.49, "RUT": 22.56}


def _levels(offset: float) -> list[float]:
    return [7800 + offset, 7500 + offset, 7710.15 + offset, 8000, 7700, 7000, 0.0, 7800, 7900, 8000, 8100, 7668.83]


def _section(session: date) -> PackSection:
    return build_pack({s: _levels(i) for i, s in enumerate(SYMBOLS)}, BASES, session)


def _peak(strike: float) -> GammaPeak:
    return GammaPeak(strike, 1.0, 1.0, 0.0, 0.0, 0.0, "Support")


def test_pack_levels_slot_order_and_padding():
    a = GexAnalysis(
        timestamp="", underlying="SPX", spot=7743.0, expected_move_1d=43.5, total_gex_per_pct=1.0,
        zero_gamma=7668.8, transition_level=7710.1, distance_to_zg_pts=None, distance_to_zg_normalized=None,
        regime="Positive", regime_confidence=1.0,
        call_wall=WallInfo(_peak(7800), 1.0), put_wall=None,
        support_peaks=[_peak(8000), _peak(7700)],
        resistance_peaks=[_peak(k) for k in (7800, 7900, 8000, 8100, 8200)],
        gamma_profile={},
    )
    assert pack_levels(a) == [7800, 0.0, 7710.1, 8000, 7700, 0.0, 0.0, 7800, 7900, 8000, 8100, 7668.8]


def test_futures_blocks_follow_invariants_a_and_b():
    section = _section(date(2026, 9, 28))
    parsed = read_pack_file(format_pack_file({}, section, None)).pack
    assert list(parsed.blocks) == ["ES", "MES", "NQ", "MNQ", "RTY", "SPX", "SPY", "NDX", "QQQ", "RUT", "IWM"]
    assert parsed.blocks["ES"] == parsed.blocks["MES"]
    assert parsed.blocks["NQ"] == parsed.blocks["MNQ"]
    for fut, index in (("ES", "SPX"), ("NQ", "NDX"), ("RTY", "RUT")):
        diffs = [f - i for f, i in zip(parsed.blocks[fut], parsed.blocks[index]) if i]
        assert diffs == pytest.approx([BASES[index]] * len(diffs))
        assert derive_basis(parsed.blocks[fut], parsed.blocks[index]) == pytest.approx(BASES[index])
        assert parsed.blocks[fut][6] == 0.0  # an unavailable slot stays 0, never shifted


def test_round_trip():
    section = _section(date(2026, 9, 28))
    text = format_pack_file({"SPY": 771.35, "MES": 7806.06}, section, None, ["header"])
    parsed = read_pack_file(text)
    assert parsed.warnings == []
    assert parsed.refs == {"SPY": 771.35, "MES": 7806.06}
    assert parsed.pack.date_text == "28 SEP 2026"
    for sym, values in section.blocks.items():
        assert parsed.pack.blocks[sym] == pytest.approx(values, abs=1e-6)
        assert len(parsed.pack.blocks[sym]) == 12


def test_refs_make_simultaneous_pairs():
    refs = build_refs({"SPX": 7743.41, "SPY": 771.35, "NDX": 30608.13, "QQQ": 745.4, "RUT": 2837.55,
                       "IWM": 281.97}, BASES)
    assert refs["ES"] == refs["MES"] == pytest.approx(7806.06)
    assert 10.0 <= derive_ratio(refs["MES"], BASES["SPX"], refs["SPY"]) <= 10.05
    assert 40 <= derive_ratio(refs["MNQ"], BASES["NDX"], refs["QQQ"]) <= 42


@pytest.mark.parametrize("text, expected", [
    ("25 september 2026", date(2026, 9, 25)),
    ("4 SEP 2026", date(2026, 9, 4)),
    ("04 SEP 2026", date(2026, 9, 4)),
    ("2026-09-04", date(2026, 9, 4)),
    ("not a date", None),
])
def test_parse_pack_date(text, expected):
    assert parse_pack_date(text) == expected


def test_format_pack_date():
    assert format_pack_date(date(2026, 9, 4)) == "4 SEP 2026"


def test_write_rolls_prior_on_new_session_and_keeps_it_on_rerun(tmp_path):
    path = tmp_path / "gex_pack.txt"
    day1, day2 = _section(date(2026, 9, 25)), _section(date(2026, 9, 28))
    day2.blocks["SPX"] = [x + 1 if x else 0.0 for x in day2.blocks["SPX"]]

    write_pack_file(path, {}, day1)
    assert read_pack_file(path.read_text()).prior is None

    write_pack_file(path, {}, day2)
    written = read_pack_file(path.read_text())
    assert written.pack.date_text == "28 SEP 2026"
    assert written.prior.date_text == "25 SEP 2026"
    assert written.prior.blocks["SPX"] == pytest.approx(day1.blocks["SPX"])

    write_pack_file(path, {}, day2)  # same-session rerun
    assert read_pack_file(path.read_text()).prior.date_text == "25 SEP 2026"
    assert [p.name for p in tmp_path.iterdir()] == ["gex_pack.txt"]


def test_spec_worked_example():
    """GEX-PACK-SPEC.md §4, Sep 4 2026, as the indicator derives it on an MES chart."""
    text = """
[REFS]
SPY 774.09
MES 7762.0
[PACK]
4 SEP 2026
MES=7810|7510|7730|8010|7710|7010|7810|7812|7835|7913|7789|7654;
SPX=7800|7500|7720|8000|7700|7000|7800|7802|7825|7903|7779|7644;
SPY=780|760|772|770|775|765|760|778|748|788|758|771
"""
    f = read_pack_file(text)
    mes, spx, spy = (f.pack.blocks[s] for s in ("MES", "SPX", "SPY"))
    basis = derive_basis(mes, spx)
    ratio = derive_ratio(f.refs["MES"], basis, f.refs["SPY"])
    assert basis == 10
    assert ratio == pytest.approx(10.01434, abs=1e-5)

    tick = 0.25
    converted = {name: etf_to_futures(spy[i], ratio, basis, tick) for name, i in
                 (("PW", 1), ("GD1", 3), ("TL", 2), ("C1", 7), ("CW", 0), ("DP", 11))}
    assert converted == {"PW": 7621.00, "GD1": 7721.00, "TL": 7741.00, "C1": 7801.25, "CW": 7821.25, "DP": 7731.00}
    assert abs(converted["DP"] - mes[2]) <= 8 * tick  # suppressed; native TL gets the +SPY DP tag


def test_reads_vendor_pack_and_flags_bad_ref():
    f = read_pack_file(VENDOR_PACK.read_text(encoding="utf-8"))
    expected = {"ES", "MES", "NQ", "MNQ", "RTY", "SPX", "SPY", "NDX", "QQQ", "IWM"}
    assert set(f.pack.blocks) == expected and set(f.prior.blocks) == expected
    assert parse_pack_date(f.pack.date_text) == date(2026, 9, 25)
    assert parse_pack_date(f.prior.date_text) == date(2026, 9, 24)
    assert f.refs == {"SPY": 770.31, "MES": 7793.75, "MNQ": 30926.5}
    assert f.warnings == ["[REFS] unparseable line: 'QQQ 7.45.4'"]
    assert f.pack.blocks["ES"] == f.pack.blocks["MES"]
    assert derive_basis(f.pack.blocks["ES"], f.pack.blocks["SPX"]) == 63


@pytest.mark.parametrize("day", [21, 22, 23, 25])
def test_all_vendor_reference_packs_parse(day):
    f = read_pack_file((REFERENCE_PACKS / f"2026-09-{day}_vendor.txt").read_text(encoding="utf-8"))
    assert set(f.pack.blocks) == {"ES", "MES", "NQ", "MNQ", "RTY", "SPX", "SPY", "NDX", "QQQ", "IWM"}
    assert parse_pack_date(f.pack.date_text) == date(2026, 9, day)
    assert f.pack.blocks["ES"] == f.pack.blocks["MES"]
    assert f.pack.blocks["NQ"] == f.pack.blocks["MNQ"]
    assert not [w for w in f.warnings if w.startswith("[PACK]")]


def test_premarket_section_round_trips_without_disturbing_pack_and_prior():
    pack, prior, pre = _section(date(2026, 9, 28)), _section(date(2026, 9, 25)), _section(date(2026, 9, 28))
    pre.blocks["SPX"] = [x - 1 if x else 0.0 for x in pre.blocks["SPX"]]
    parsed = read_pack_file(format_pack_file({"SPY": 771.35}, pack, prior, ["h"], pre))
    assert parsed.warnings == []
    assert parsed.pack.date_text == "28 SEP 2026" and parsed.prior.date_text == "25 SEP 2026"
    assert parsed.premarket.blocks["SPX"] == pytest.approx(pre.blocks["SPX"])
    assert parsed.pack.blocks["SPX"] == pytest.approx(pack.blocks["SPX"])
