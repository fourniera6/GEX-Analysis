from enum import Enum

from ..config import GexConfig


class GammaRegime(str, Enum):
    POSITIVE = "Positive"
    NEGATIVE = "Negative"
    BATTLEGROUND = "Battleground"


def classify_regime(
    total_gex_at_spot: float, distance_to_zg_normalized: float | None, cfg: GexConfig
) -> tuple[GammaRegime, float]:
    sign_regime = GammaRegime.POSITIVE if total_gex_at_spot >= 0 else GammaRegime.NEGATIVE
    if distance_to_zg_normalized is None:  # no zero crossing inside the search range
        return sign_regime, 1.0
    d = abs(distance_to_zg_normalized)
    bg, hi = cfg.regime_battleground_normalized, cfg.regime_high_confidence_normalized
    if d < bg:
        return GammaRegime.BATTLEGROUND, 0.0
    return sign_regime, min(1.0, (d - bg) / (hi - bg))
