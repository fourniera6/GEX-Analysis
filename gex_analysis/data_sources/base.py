from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import numpy as np


@dataclass
class Chain:
    underlying: str
    spot: float
    asof: datetime
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


class DataSource(ABC):
    @abstractmethod
    def fetch_chain(self, underlying: str) -> Chain: ...
