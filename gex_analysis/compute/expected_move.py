import numpy as np

from ..data_sources.base import Chain


def expected_move_1d(chain: Chain) -> float:
    """ATM straddle mid at the nearest expiry with two-sided quotes, scaled to one trading day. In points."""
    quoted = (chain.t_years > 0) & (chain.bid > 0) & (chain.ask > 0)
    asof_day = np.datetime64(chain.asof.date(), "D")
    for expiry in np.unique(chain.expiry_date[quoted]):
        in_expiry = quoted & (chain.expiry_date == expiry)
        mids = {}
        for side in (True, False):
            m = in_expiry & (chain.is_call == side)
            mids[side] = dict(zip(chain.strike[m], (chain.bid[m] + chain.ask[m]) / 2))
        common = mids[True].keys() & mids[False].keys()
        if not common:
            continue
        atm = min(common, key=lambda k: abs(k - chain.spot))
        straddle = mids[True][atm] + mids[False][atm]
        trading_days = max(1, int(np.busday_count(asof_day, expiry)))
        return float(straddle / np.sqrt(trading_days))
    raise ValueError(f"no two-sided ATM quotes for {chain.underlying}")
