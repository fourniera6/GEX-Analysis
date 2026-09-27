import math
from datetime import date, timedelta

import numpy as np

from ..data_sources.base import Chain


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def front_quarterly_expiry(on: date, roll_days: int) -> date:
    """Front equity-index futures expiry (third Friday of Mar/Jun/Sep/Dec), rolling roll_days early."""
    year, month = on.year, on.month
    while True:
        if month % 3 == 0:
            expiry = third_friday(year, month)
            if (expiry - on).days >= roll_days:
                return expiry
        month += 1
        if month > 12:
            year, month = year + 1, 1


def implied_forward(chain: Chain, expiry: date, r: float, strike_range_pct: float) -> float:
    """Median put-call-parity forward, F = K + e^(rT)(C - P), over near-the-money strikes at one expiry."""
    quoted = (chain.expiry_date == np.datetime64(expiry, "D")) & (chain.bid > 0) & (chain.ask > 0)
    quoted &= np.abs(chain.strike / chain.spot - 1) <= strike_range_pct
    calls, puts = {}, {}
    for i in np.flatnonzero(quoted):
        side = calls if chain.is_call[i] else puts
        side[(chain.root[i], chain.strike[i])] = ((chain.bid[i] + chain.ask[i]) / 2, chain.t_years[i])
    forwards = [
        key[1] + math.exp(r * t) * (call_mid - puts[key][0])
        for key, (call_mid, t) in calls.items() if key in puts
    ]
    if not forwards:
        raise ValueError(f"no two-sided quotes to imply a {expiry} forward for {chain.underlying}")
    return float(np.median(forwards))


def futures_basis(chain: Chain, r: float, roll_days: int, strike_range_pct: float) -> float:
    expiry = front_quarterly_expiry(chain.asof.date(), roll_days)
    return round(implied_forward(chain, expiry, r, strike_range_pct) - chain.spot, 2)
