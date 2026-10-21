from datetime import date

import numpy as np

from gex_analysis.config import GexConfig
from gex_analysis.data_sources.cboe import parse_payload
from gex_analysis.main import run
from gex_analysis.output.pack import build_pack, format_pack_file
from gex_analysis.research.dp_variants import (
    BASELINE,
    VARIANTS,
    Variant,
    load_snapshot,
    report,
    stamps,
    variant_dp,
    variant_dps,
)
from tests.test_main import FakeSource, _synthetic_payload


def _snapshot(tmp_path):
    raw = tmp_path / "data" / "raw"
    result = run(source=FakeSource(), out_dir=tmp_path / "output", raw_dir=raw,
                 pivot_log=tmp_path / "data" / "pivot_log.csv", archive=True)
    return raw, result


def test_baseline_variant_reproduces_the_production_dp(tmp_path):
    raw, result = _snapshot(tmp_path)
    (stamp,) = stamps(raw)
    cfg = GexConfig()
    chains = load_snapshot(raw, stamp, cfg)
    dps = variant_dps(chains, cfg)
    assert set(dps) == {v.name for v in VARIANTS}
    for sym, chain in chains.items():
        assert chain.spot == result.analyses[sym].spot
        assert dps[BASELINE][sym] == result.analyses[sym].zero_gamma
    assert all(v is None or np.isfinite(v) for row in dps.values() for v in row.values())


def test_masks_drop_monthlies_and_far_strikes():
    chain = parse_payload("SPX", _synthetic_payload("SPX"))  # near expiry on SPXW, Dec on the AM-settled SPX root
    cfg = GexConfig()
    assert set(chain.root) == {"SPXW", "SPX"}
    assert variant_dp(chain, Variant("weeklies only", weeklies_only=True), cfg) != variant_dp(chain, Variant("all"), cfg)
    assert variant_dp(chain, Variant("tiny band", band_pct=0.0001), cfg) is None  # no strike within 0.01% of spot


def test_report_ranks_variants_against_the_vendor():
    symbols = ("SPX", "SPY", "NDX", "QQQ", "RUT", "IWM")
    vendor = {"SPX": 7700.0, "SPY": 770.0, "NDX": 30000.0, "QQQ": 740.0, "IWM": 280.0}
    results = {
        BASELINE: {"SPX": 7740.0, "SPY": 771.0, "NDX": 30200.0, "QQQ": 741.0, "RUT": 2800.0, "IWM": 281.0},
        "DTE 0-30": {"SPX": 7705.0, "SPY": 776.0, "NDX": 30010.0, "QQQ": 745.0, "RUT": None, "IWM": 284.0},
    }
    text = report(results, vendor, dict.fromkeys(symbols, 1.0), "title")
    ranking = text[text.index("Median"):].splitlines()
    assert ranking[1].lstrip().startswith("DTE 0-30") and ranking[2].rstrip().endswith("<- ours")
    assert "7705.0 (+5.0)" in text and "2800.0" in text  # RUT has no vendor value: DP only


def test_cli_section_lookup_uses_the_dp_slot(tmp_path):
    from gex_analysis.output.pack import read_pack_file
    from gex_analysis.research.dp_variants import DP_SLOT

    levels = {s: [1.0] * 11 + [42.0] for s in ("SPX", "SPY", "NDX", "QQQ", "RUT", "IWM")}
    pack = build_pack(levels, {"SPX": 0.0, "NDX": 0.0, "RUT": 0.0}, date(2026, 10, 13))
    parsed = read_pack_file(format_pack_file({}, pack, None)).pack
    assert parsed.blocks["SPX"][DP_SLOT] == 42.0
