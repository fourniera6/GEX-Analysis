import json
import math
from datetime import date, datetime

import pytest

from gex_analysis.compute.basis import front_quarterly_expiry, futures_basis, implied_forward, third_friday
from gex_analysis.data_sources.cboe import ET, SECONDS_PER_YEAR, parse_payload


def test_third_friday():
    assert third_friday(2026, 12) == date(2026, 12, 18)
    assert third_friday(2027, 3) == date(2027, 3, 19)
    assert third_friday(2026, 5) == date(2026, 5, 15)  # month starting on a Friday


@pytest.mark.parametrize("on, expected", [
    (date(2026, 9, 25), date(2026, 12, 18)),
    (date(2026, 9, 10), date(2026, 9, 18)),    # 8 days out: still front
    (date(2026, 9, 11), date(2026, 12, 18)),   # 7 days out: rolled
    (date(2026, 12, 11), date(2027, 3, 19)),
])
def test_front_quarterly_expiry(on, expected):
    assert front_quarterly_expiry(on, roll_days=8) == expected


def _parity_chain(forward: float, r: float):
    """SPX chain at the Dec expiry whose call/put mids satisfy put-call parity for `forward`."""
    asof = datetime(2026, 9, 25, 16, 0, tzinfo=ET)
    t = (datetime(2026, 12, 18, 9, 30, tzinfo=ET).timestamp() - asof.timestamp()) / SECONDS_PER_YEAR
    options = []
    for k in (7700, 7750, 7800, 7850, 7900):
        put_mid = 100.0
        call_mid = put_mid + math.exp(-r * t) * (forward - k)
        for cp, mid in (("C", call_mid), ("P", put_mid)):
            options.append({"option": f"SPX261218{cp}{k * 1000:08d}", "iv": 0.15, "open_interest": 10,
                            "volume": 0, "bid": mid - 0.5, "ask": mid + 0.5})
    # A far strike outside the ±3% window with inconsistent prices must be ignored.
    options.append({"option": "SPX261218C06000000", "bid": 1.0, "ask": 2.0})
    options.append({"option": "SPX261218P06000000", "bid": 1.0, "ask": 2.0})
    raw = json.dumps({"data": {"current_price": 7743.41, "last_trade_time": "2026-09-25T16:00:00",
                               "options": options}}).encode()
    return parse_payload("SPX", raw)


def test_implied_forward_recovers_parity_forward():
    chain = _parity_chain(forward=7806.08, r=0.04)
    assert implied_forward(chain, date(2026, 12, 18), 0.04, 0.03) == pytest.approx(7806.08, abs=1e-6)


def test_futures_basis():
    chain = _parity_chain(forward=7806.08, r=0.04)
    assert futures_basis(chain, r=0.04, roll_days=8, strike_range_pct=0.03) == pytest.approx(62.67)


def test_implied_forward_requires_quotes():
    chain = _parity_chain(forward=7806.08, r=0.04)
    with pytest.raises(ValueError):
        implied_forward(chain, date(2027, 3, 19), 0.04, 0.03)
