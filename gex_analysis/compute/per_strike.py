from dataclasses import dataclass

import numpy as np

from ..config import GexConfig
from ..data_sources.base import Chain
from .greeks import bs_gamma

CONTRACT_MULTIPLIER = 100
MINUTES_PER_YEAR = 365 * 24 * 60


@dataclass
class StrikeProfile:
    strikes: np.ndarray
    call: np.ndarray
    put: np.ndarray

    @property
    def net(self) -> np.ndarray:
        return self.call + self.put


@dataclass
class ContractSet:
    strike: np.ndarray
    t: np.ndarray
    iv: np.ndarray
    oi: np.ndarray
    is_call: np.ndarray
    sign: np.ndarray
    r: float
    q: float

    @classmethod
    def from_chain(cls, chain: Chain, cfg: GexConfig, window_dte: tuple[int, int]) -> "ContractSet":
        lo, hi = window_dte
        dte = (chain.expiry_date - np.datetime64(chain.asof.date(), "D")).astype(int)
        keep = (
            (chain.t_years > 0) & (dte >= lo) & (dte <= hi)
            & (chain.open_interest > 0) & (chain.iv > 0)
        )
        is_call = chain.is_call[keep]
        call_sign = 1.0 if cfg.dealer_long_calls else -1.0
        put_sign = -1.0 if cfg.dealer_short_puts else 1.0
        return cls(
            strike=chain.strike[keep],
            t=np.maximum(chain.t_years[keep], cfg.min_time_to_expiry_minutes / MINUTES_PER_YEAR),
            iv=chain.iv[keep],
            oi=chain.open_interest[keep],
            is_call=is_call,
            sign=np.where(is_call, call_sign, put_sign),
            r=cfg.risk_free_rate,
            q=cfg.dividend_yields.get(chain.underlying, 0.0),
        )

    def gex(self, spot: float) -> np.ndarray:
        # Dealer dollar gamma per 1% move in the underlying, per contract.
        gamma = bs_gamma(spot, self.strike, self.t, self.iv, self.r, self.q)
        return gamma * self.oi * CONTRACT_MULTIPLIER * spot * spot * 0.01 * self.sign

    def total_gex(self, spot: float) -> float:
        return float(self.gex(spot).sum())

    def profile(self, spot: float) -> StrikeProfile:
        gex = self.gex(spot)
        strikes, idx = np.unique(self.strike, return_inverse=True)
        n = len(strikes)
        call = np.bincount(idx, weights=np.where(self.is_call, gex, 0.0), minlength=n)
        put = np.bincount(idx, weights=np.where(self.is_call, 0.0, gex), minlength=n)
        return StrikeProfile(strikes, call, put)
