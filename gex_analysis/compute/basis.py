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


def implied_forward(chain: Chain, expiry: date, r: float, strike_range_pct: float) -> tuple[float, float]:
    """(forward, years to expiry): median put-call-parity F = K + e^(rT)(C - P) over near-the-money strikes."""
    quoted = (chain.expiry_date == np.datetime64(expiry, "D")) & (chain.bid > 0) & (chain.ask > 0)
    quoted &= np.abs(chain.strike / chain.spot - 1) <= strike_range_pct
    calls, puts = {}, {}
    for i in np.flatnonzero(quoted):
        side = calls if chain.is_call[i] else puts
        side[(chain.root[i], chain.strike[i])] = ((chain.bid[i] + chain.ask[i]) / 2, chain.t_years[i])
    pairs = [(key[1] + math.exp(r * t) * (call_mid - puts[key][0]), t) for key, (call_mid, t) in calls.items() if key in puts]
    if not pairs:
        raise ValueError(f"no two-sided quotes to imply a {expiry} forward for {chain.underlying}")
    return float(np.median([f for f, _ in pairs])), float(np.median([t for _, t in pairs]))


def underlying_level(chain: Chain, r: float, roll_days: int, strike_range_pct: float) -> tuple[float, float]:
    """(spot, forward at the front futures expiry), both implied by the option quotes.

    The quoted index level is stale outside regular hours while SPX options keep trading (GTH), so the level
    must come from the same quotes as the IVs. Carry is taken from the two forwards, so no dividend yield is needed.
    """
    far = front_quarterly_expiry(chain.asof.date(), roll_days)
    f_far, t_far = implied_forward(chain, far, r, strike_range_pct)
    live = (chain.t_years > 0) & (chain.bid > 0) & (chain.ask > 0) & (chain.expiry_date < np.datetime64(far, "D"))
    for expiry in np.unique(chain.expiry_date[live]):
        try:
            f_near, t_near = implied_forward(chain, expiry.item(), r, strike_range_pct)
        except ValueError:
            continue
        carry = math.log(f_far / f_near) / (t_far - t_near)
        return f_near * math.exp(-carry * t_near), f_far
    raise ValueError(f"no near expiry with two-sided quotes for {chain.underlying}")
