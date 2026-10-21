from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import numpy as np


@dataclass
class Chain:
    underlying: str
    spot: float
    close: float  # the quote's last official close; index and ETF closes are a simultaneous pair
    asof: datetime  # the underlying's last trade; t_years is measured from here
    quote_time: datetime  # when the snapshot was taken (ETF last trades stop at 15:59:59 on the expiry day)
    root: np.ndarray
    expiry_date: np.ndarray  # datetime64[D]
    t_years: np.ndarray
    is_call: np.ndarray
    strike: np.ndarray
    iv: np.ndarray
    open_interest: np.ndarray
    volume: np.ndarray
    bid: np.ndarray
    ask: np.ndarray
    raw: bytes = b""

    @property
    def alive(self) -> np.ndarray:
        """Contracts that hadn't expired when the snapshot was taken."""
        elapsed = max(0.0, (self.quote_time - self.asof).total_seconds()) / (365 * 24 * 3600)
        return self.t_years > elapsed


class DataSource(ABC):
    @abstractmethod
    def fetch_chain(self, underlying: str) -> Chain: ...
