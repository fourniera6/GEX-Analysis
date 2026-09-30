import json

import numpy as np
import pytest

from gex_analysis.compute.expected_move import expected_move_1d
from gex_analysis.compute.per_strike import ContractSet
from gex_analysis.config import GexConfig
from gex_analysis.data_sources.cboe import parse_payload


def _option(symbol, iv=0.2, oi=100.0, bid=1.0, ask=1.2):
    return {"option": symbol, "iv": iv, "open_interest": oi, "volume": 0, "bid": bid, "ask": ask}


def _payload(options):
    return json.dumps({"data": {
        "current_price": 7700.0,
        "last_trade_time": "2026-09-25T16:00:00",  # Friday close, ET
        "options": options,
    }}).encode()


def test_parse_payload_expiry_times_and_filtering():
    chain = parse_payload("SPX", _payload([
        _option("SPXW260925C07700000"),          # expired at today's close
        _option("SPXW260928C07700000"),          # Monday PM: 3 days
        _option("SPX261016P07700000"),           # AM-settled monthly: settles 09:30
        _option("SPXW261231C07700000"),          # outside 30 DTE
        _option("SPXW260928P07600000", oi=0.0),  # no open interest
        _option("SPXW261106C07700000"),          # crosses the 1 Nov DST change
    ]))
    assert chain.t_years[0] == pytest.approx(0.0)
    assert chain.t_years[1] * 365 == pytest.approx(3.0)
    assert chain.t_years[2] * 365 == pytest.approx(21 - 6.5 / 24)
    assert chain.t_years[5] * 365 == pytest.approx(42 + 1 / 24)
    assert list(chain.root[:3]) == ["SPXW", "SPXW", "SPX"]
    assert chain.close == 7700.0  # no "close" field in the payload: falls back to current_price

    contracts = ContractSet.from_chain(chain, GexConfig(), (0, 30))
    assert list(contracts.strike) == [7700.0, 7700.0]
    assert list(contracts.is_call) == [True, False]


def test_parse_payload_tolerates_null_fields():
    opt = _option("SPXW260928C07700000")
    opt["iv"] = None
    chain = parse_payload("SPX", _payload([opt]))
    assert chain.iv[0] == 0.0


def test_expected_move_uses_nearest_two_sided_expiry():
    chain = parse_payload("SPX", _payload([
        _option("SPXW260928C07700000", bid=0.0, ask=0.0),  # Monday call unquoted -> skip Monday
        _option("SPXW260928P07700000", bid=20.0, ask=22.0),
        _option("SPXW260930C07700000", bid=30.0, ask=32.0),  # Wednesday: 3 trading days from Friday
        _option("SPXW260930P07700000", bid=29.0, ask=31.0),
        _option("SPXW260930C07800000", bid=5.0, ask=6.0),
    ]))
    assert expected_move_1d(chain) == pytest.approx(61.0 / np.sqrt(3))


def test_expired_contracts_drop_out_after_the_close():
    # ETF quotes stop at 15:59:59, one second before the 16:00 expiry. A 07:29 run the next morning must not
    # treat that day's expired 0DTE options as live (they'd get the 15-minute time floor and phantom gamma).
    raw = json.dumps({"timestamp": "2026-09-29 11:29:00", "data": {
        "current_price": 280.06, "last_trade_time": "2026-09-28T15:59:59",
        "options": [
            _option("IWM260928C00280000", bid=0.05, ask=0.25),   # expired yesterday
            _option("IWM260928P00280000", bid=0.05, ask=0.25),
            _option("IWM260929C00280000", bid=1.2, ask=1.3),     # today's 0DTE
            _option("IWM260929P00280000", bid=1.1, ask=1.2),
        ],
    }}).encode()
    chain = parse_payload("IWM", raw)
    assert f"{chain.quote_time:%Y-%m-%d %H:%M}" == "2026-09-29 07:29"
    assert list(chain.alive) == [False, False, True, True]
    assert list(ContractSet.from_chain(chain, GexConfig(), (0, 7)).strike) == [280.0, 280.0]
    assert expected_move_1d(chain) == pytest.approx(2.4)       # today's straddle, not yesterday's 0.30


def test_quote_time_never_precedes_the_last_trade():
    raw = json.dumps({"timestamp": "2026-09-26 03:44:18", "data": {  # a stale file stamp
        "current_price": 2837.5, "last_trade_time": "2026-09-28T16:09:55", "options": [_option("RUTW260929C02840000")]}}).encode()
    chain = parse_payload("RUT", raw)
    assert chain.quote_time == chain.asof
