import json
from dataclasses import asdict, dataclass


@dataclass
class GammaPeak:
    strike: float
    magnitude_signed: float  # dealer $ gamma per 1% move at this strike, from the ranking series
    magnitude_normalized: float  # 0..1 relative to the strongest peak in the same ranking
    half_width_pts: float
    distance_to_spot_pts: float  # strike - spot: positive = level above spot
    distance_to_spot_normalized: float  # as a multiple of expected_move_1d
    role: str  # Support / Resistance


@dataclass
class WallInfo:
    peak: GammaPeak
    confidence: float


@dataclass
class GexAnalysis:
    timestamp: str
    underlying: str
    spot: float
    expected_move_1d: float
    total_gex_per_pct: float
    zero_gamma: float | None  # DP: all structural expiries
    transition_level: float | None  # TL: short-dated expiries only
    distance_to_zg_pts: float | None  # spot - zero_gamma: positive = spot above ZG
    distance_to_zg_normalized: float | None
    regime: str
    regime_confidence: float
    call_wall: WallInfo | None  # largest positive net GEX strike
    put_wall: WallInfo | None  # largest negative net GEX strike
    support_peaks: list[GammaPeak]  # GD1-4 in the pack
    resistance_peaks: list[GammaPeak]  # C1-4 in the pack
    gamma_profile: dict[str, float]  # strike -> net GEX normalized to [-1, 1] by max abs

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)
