import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import requests

from .base import Chain, DataSource

ET = ZoneInfo("America/New_York")
URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{path}.json"
INDEX_UNDERLYINGS = {"SPX", "NDX", "RUT", "VIX", "XSP", "DJX"}
# Monthly index options on these roots settle at the open; weeklies (SPXW, NDXP, RUTW) settle at the close.
AM_SETTLED_ROOTS = {"SPX", "NDX", "RUT"}
SECONDS_PER_YEAR = 365 * 24 * 3600


def parse_occ(symbol: str) -> tuple[str, date, bool, float]:
    root = symbol[:-15]
    expiry = datetime.strptime(symbol[-15:-9], "%y%m%d").date()
    return root, expiry, symbol[-9] == "C", int(symbol[-8:]) / 1000


def parse_payload(underlying: str, raw: bytes) -> Chain:
    data = json.loads(raw)["data"]
    asof = datetime.fromisoformat(data["last_trade_time"]).replace(tzinfo=ET)

    rows = []
    for o in data["options"]:
        root, expiry, is_call, strike = parse_occ(o["option"])
        settle = time(9, 30) if root in AM_SETTLED_ROOTS else time(16, 0)
        t_years = (datetime.combine(expiry, settle, ET).timestamp() - asof.timestamp()) / SECONDS_PER_YEAR
        rows.append((
            root, expiry, t_years, is_call, strike,
            *(float(o.get(k) or 0.0) for k in ("iv", "open_interest", "volume", "bid", "ask")),
        ))

    root, expiry, t_years, is_call, strike, iv, oi, volume, bid, ask = zip(*rows)
    return Chain(
        underlying=underlying,
        spot=float(data["current_price"]),
        close=float(data.get("close") or data["current_price"]),
        asof=asof,
        root=np.array(root),
        expiry_date=np.array(expiry, dtype="datetime64[D]"),
        t_years=np.array(t_years),
        is_call=np.array(is_call),
        strike=np.array(strike),
        iv=np.array(iv),
        open_interest=np.array(oi),
        volume=np.array(volume),
        bid=np.array(bid),
        ask=np.array(ask),
        raw=raw,
    )


class CboeDelayedSource(DataSource):
    """CBOE's public 15-minute-delayed quotes feed. Unofficial and undocumented: no SLA."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def fetch_chain(self, underlying: str) -> Chain:
        path = f"_{underlying}" if underlying in INDEX_UNDERLYINGS else underlying
        resp = requests.get(URL.format(path=path), headers={"User-Agent": "Mozilla/5.0"}, timeout=self.timeout)
        resp.raise_for_status()
        return parse_payload(underlying, resp.content)
