from gex_analysis.compare import major_levels, minor_levels
from gex_analysis.output.pack import PackSection


def _block(cw, pw, tl, dp, gd=(0, 0, 0, 0), c=(0, 0, 0, 0)):
    return [cw, pw, tl, *gd, *c, dp]


def _sections():
    vendor = PackSection("25 september 2026", {
        "SPX": _block(7800, 7500, 7710, 7659, gd=(8000, 7700, 7000, 7800), c=(7797, 7897, 7997, 7504)),
        "SPY": _block(785, 760, 767, 765, c=(777, 787, 797, 757)),
        "NDX": _block(29475, 29500, 29470, 29947),
        "QQQ": _block(760, 730, 738, 734),
    })
    ours = PackSection("28 SEP 2026", {
        "SPX": _block(7800, 7500, 7710.15, 7668.83, gd=(7500, 7650, 7600, 7400), c=(7800, 7900, 8000, 8100)),
        "SPY": _block(785, 750, 768.99, 0, c=(785, 780, 775, 800)),   # DP not found
        "NDX": _block(29475, 29500, 30248.31, 29600.31),
        "RUT": _block(2855, 2820, 2929.97, 2952.70),
    })
    return vendor, ours


def test_major_levels_table_and_summary():
    text = major_levels(*_sections())
    lines = text.splitlines()
    assert lines[0] == "MAJOR LEVELS  vendor [25 september 2026] vs ours [28 SEP 2026]"
    spx = next(line for line in lines if line.strip().startswith("SPX"))
    assert "TL 7710 -> 7710.15 (+0.1, +0.00%)" in spx
    assert "DP 7659 -> 7668.83 (+9.8, +0.13%)" in spx
    assert "CW 7800 -> 7800 (+0.0, +0.00%)" in spx
    spy = next(line for line in lines if line.strip().startswith("SPY"))
    assert "DP 765 -> 0" in spy and "DP 765 -> 0 (" not in spy   # missing level: shown, not scored
    assert "QQQ  not in our pack" in text
    assert "RUT  not in vendor pack" in text
    # TL errors: SPX 0.002%, SPY 0.26%, NDX 2.64% -> median 0.26%; SPX/SPY median 0.13%.
    assert "median |TL error|: 0.26% across 3 symbols, 0.13% for SPX/SPY" in text
    assert "median |DP error|:" in text and "across 2 symbols" in text


def test_minor_levels_rank_overlap():
    text = minor_levels(*_sections())
    rows = text.splitlines()
    spx_diff = rows[rows.index(next(r for r in rows if r.startswith("SPX "))) + 2]
    assert spx_diff.endswith("GD 0/4, C 3/4")   # 7797/7897/7997 are within 0.2% of 7800/7900/8000
    spy_diff = rows[rows.index(next(r for r in rows if r.startswith("SPY "))) + 2]
    assert spy_diff.endswith("C n/a")
    assert "QQQ      not in our pack" in text
