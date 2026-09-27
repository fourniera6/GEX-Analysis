import numpy as np

from .per_strike import StrikeProfile


def combine_profiles(base: StrikeProfile, other: StrikeProfile, ratio: float, step: float) -> StrikeProfile:
    """Map `other` onto base's price scale (strike * ratio, binned to `step`) and sum dollar GEX."""
    mapped = np.round(other.strikes * ratio / step) * step
    strikes, idx = np.unique(np.concatenate([base.strikes, mapped]), return_inverse=True)
    n = len(strikes)
    call = np.bincount(idx, weights=np.concatenate([base.call, other.call]), minlength=n)
    put = np.bincount(idx, weights=np.concatenate([base.put, other.put]), minlength=n)
    return StrikeProfile(strikes, call, put)
