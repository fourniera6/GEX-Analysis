import json
import math
from datetime import date, datetime

import pytest

from gex_analysis.compute.basis import front_quarterly_expiry, implied_forward, third_friday, underlying_level
from gex_analysis.data_sources.cboe import ET, SECONDS_PER_YEAR, parse_payload

ASOF = datetime(2026, 9, 25, 16, 0, tzinfo=ET)
EXPIRIES = {"SPXW260928": datetime(2026, 9, 28, 16, 0, tzinfo=ET), "SPX261218": datetime(2026, 12, 18, 9, 30, tzinfo=ET)}


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


def _parity_chain(spot: float, carry: float, r: float, quoted_price: float = 7743.41):
    """SPX chain whose call/put mids satisfy put-call parity for forwards F = spot * e^(carry * T) at a near
    (Mon) and a far (Dec) expiry. The quote's own price field is `quoted_price`, e.g. a stale prior close."""
    options = []
    for prefix, expiry in EXPIRIES.items():
        t = (expiry.timestamp() - ASOF.timestamp()) / SECONDS_PER_YEAR
        forward = spot * math.exp(carry * t)
        for k in (7650, 7700, 7750, 7800, 7850):
            put_mid = 100.0
            call_mid = put_mid + math.exp(-r * t) * (forward - k)
            for cp, mid in (("C", call_mid), ("P", put_mid)):
                options.append({"option": f"{prefix}{cp}{k * 1000:08d}", "iv": 0.15, "open_interest": 10,
                                "volume": 0, "bid": mid - 0.5, "ask": mid + 0.5})
    # A far strike outside the ±3% window with inconsistent prices must be ignored.
    options.append({"option": "SPX261218C06000000", "bid": 1.0, "ask": 2.0})
    options.append({"option": "SPX261218P06000000", "bid": 1.0, "ask": 2.0})
    raw = json.dumps({"data": {"current_price": quoted_price, "close": quoted_price,
                               "last_trade_time": "2026-09-25T16:00:00", "options": options}}).encode()
    return parse_payload("SPX", raw)


def test_implied_forward_recovers_parity_forward():
    chain = _parity_chain(spot=7717.02, carry=0.028, r=0.04)
    t = (EXPIRIES["SPX261218"].timestamp() - ASOF.timestamp()) / SECONDS_PER_YEAR
    forward, years = implied_forward(chain, date(2026, 12, 18), 0.04, 0.03)
    assert forward == pytest.approx(7717.02 * math.exp(0.028 * t), abs=1e-6)
    assert years == pytest.approx(t)


def test_underlying_level_ignores_stale_quote():
    # Pre-market: the quote still shows Friday's 7743.41, but the options price SPX at 7717.02.
    chain = _parity_chain(spot=7717.02, carry=0.028, r=0.04, quoted_price=7743.41)
    spot, far_forward = underlying_level(chain, r=0.04, roll_days=8, strike_range_pct=0.03)
    t_far = (EXPIRIES["SPX261218"].timestamp() - ASOF.timestamp()) / SECONDS_PER_YEAR
    assert spot == pytest.approx(7717.02, abs=1e-6)
    assert far_forward - spot == pytest.approx(7717.02 * (math.exp(0.028 * t_far) - 1), abs=1e-6)


def test_implied_forward_requires_quotes():
    chain = _parity_chain(spot=7717.02, carry=0.028, r=0.04)
    with pytest.raises(ValueError):
        implied_forward(chain, date(2027, 3, 19), 0.04, 0.03)
