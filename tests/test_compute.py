import math
from datetime import date

import numpy as np
import pytest

from gex_analysis.compute.combine import combine_profiles
from gex_analysis.compute.greeks import bs_gamma
from gex_analysis.compute.peaks import find_peaks, half_width
from gex_analysis.compute.per_strike import ContractSet, StrikeProfile
from gex_analysis.compute.regime import GammaRegime, classify_regime
from gex_analysis.compute.walls import wall_confidence
from gex_analysis.compute.zero_gamma import find_zero_gamma
from gex_analysis.config import GexConfig
from gex_analysis.data_sources.cboe import parse_occ


@pytest.mark.parametrize("symbol, expected", [
    ("SPXW261009P05200000", ("SPXW", date(2026, 10, 9), False, 5200.0)),
    ("SPY261218C00787000", ("SPY", date(2026, 12, 18), True, 787.0)),
    ("SPX261016C07762500", ("SPX", date(2026, 10, 16), True, 7762.5)),
])
def test_parse_occ(symbol, expected):
    assert parse_occ(symbol) == expected


def test_bs_gamma_atm_closed_form():
    # S=K=100, sigma=0.2, T=1, r=q=0 -> d1 = 0.1
    expected = math.exp(-0.005) / math.sqrt(2 * math.pi) / 20
    assert bs_gamma(100.0, 100.0, 1.0, 0.2, 0.0, 0.0) == pytest.approx(expected, rel=1e-12)


def test_zero_gamma_symmetric_call_put_pair():
    # Long-call gamma at 110 and short-put gamma at 90 with equal size cancel where |d1| matches:
    # ln(s/110) + c = -(ln(s/90) + c), c = sigma^2 t / 2  ->  s = sqrt(9900) * exp(-c)
    iv, t = 0.2, 0.1
    contracts = ContractSet(
        strike=np.array([110.0, 90.0]), t=np.array([t, t]), iv=np.array([iv, iv]),
        oi=np.array([1000.0, 1000.0]), is_call=np.array([True, False]),
        sign=np.array([1.0, -1.0]), r=0.0, q=0.0,
    )
    zg = find_zero_gamma(contracts.total_gex, 100.0, 0.10, 161)
    assert zg == pytest.approx(math.sqrt(9900) * math.exp(-iv * iv * t / 2), rel=1e-6)


def test_zero_gamma_none_when_no_crossing():
    assert find_zero_gamma(lambda s: 1.0, 100.0, 0.10, 21) is None


def test_find_peaks_threshold_and_separation():
    strikes = np.arange(90.0, 111.0)
    magnitude = np.zeros_like(strikes)
    magnitude[5] = 10.0   # 95: strongest
    magnitude[6] = 9.0    # 96: suppressed, too close to 95
    magnitude[15] = 6.0   # 105: kept
    magnitude[18] = 1.0   # 108: below 20% threshold
    assert find_peaks(strikes, magnitude, 0.20, 3.0) == [5, 15]


def test_find_peaks_empty_profile():
    assert find_peaks(np.array([1.0, 2.0]), np.zeros(2), 0.2, 1.0) == []


def test_half_width():
    strikes = np.array([90.0, 95.0, 100.0, 105.0, 110.0])
    magnitude = np.array([1.0, 6.0, 10.0, 5.0, 1.0])
    assert half_width(strikes, magnitude, 2) == 5.0  # spans 95..105


def test_wall_confidence():
    assert wall_confidence([10.0], 1.5) == 1.0
    assert wall_confidence([15.0, 10.0], 1.5) == pytest.approx(1.0)
    assert wall_confidence([12.5, 10.0], 1.5) == pytest.approx(0.5)
    assert wall_confidence([10.0, 10.0], 1.5) == 0.0


@pytest.mark.parametrize("gex, dist_norm, expected", [
    (5.0, 0.2, (GammaRegime.BATTLEGROUND, 0.0)),
    (5.0, 1.25, (GammaRegime.POSITIVE, 0.5)),
    (-5.0, -3.0, (GammaRegime.NEGATIVE, 1.0)),
    (5.0, None, (GammaRegime.POSITIVE, 1.0)),
])
def test_classify_regime(gex, dist_norm, expected):
    regime, confidence = classify_regime(gex, dist_norm, GexConfig())
    assert (regime, confidence) == (expected[0], pytest.approx(expected[1]))


def test_combine_profiles_maps_and_conserves():
    spx = StrikeProfile(np.array([7700.0, 7710.0]), np.array([1.0, 2.0]), np.array([-1.0, 0.0]))
    spy = StrikeProfile(np.array([771.0, 772.0]), np.array([0.5, 0.25]), np.array([-0.5, -0.25]))
    combined = combine_profiles(spx, spy, ratio=10.0, step=5.0)
    assert list(combined.strikes) == [7700.0, 7710.0, 7720.0]
    assert list(combined.call) == [1.0, 2.5, 0.25]
    assert combined.net.sum() == pytest.approx(spx.net.sum() + spy.net.sum())


def test_build_analysis_net_walls_and_rank_sources():
    from datetime import datetime

    from gex_analysis.main import build_analysis

    # 8000 carries huge offsetting call and put gamma (e.g. box trades): big on each side, small net.
    profile = StrikeProfile(
        strikes=np.array([7500.0, 7700.0, 7800.0, 8000.0]),
        call=np.array([0.5, 7.0, 10.0, 13.0]),
        put=np.array([-4.5, -8.0, -1.0, -11.0]),
    )
    a = build_analysis(
        "SPX", 7743.0, datetime(2026, 9, 25, 16), profile,
        structural_gex=lambda s: s - 7650.0, transition_gex=lambda s: s - 7710.0,
        expected_move=43.5, cfg=GexConfig(peak_relative_threshold=0.0),
    )
    assert a.call_wall.peak.strike == 7800   # net +9
    assert a.put_wall.peak.strike == 7500    # net -4
    assert [p.strike for p in a.support_peaks] == [8000, 7700, 7500, 7800]    # |put| ranking
    assert [p.strike for p in a.resistance_peaks] == [7800, 8000]             # positive net ranking
    assert a.zero_gamma == pytest.approx(7650.0)
    assert a.transition_level == pytest.approx(7710.0)
    assert a.regime == "Positive"
