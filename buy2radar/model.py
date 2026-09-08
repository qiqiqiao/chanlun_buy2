from __future__ import annotations

import enum
from dataclasses import dataclass, field


class InstType(enum.Enum):
    SPOT = "SPOT"
    SWAP = "SWAP"


class StructureStatus(enum.Enum):
    CONFIRMED = "CONFIRMED"
    VALID = "VALID"
    WEAKENING = "WEAKENING"
    INVALID = "INVALID"


class TradingValueStatus(enum.Enum):
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    WATCH = "WATCH"
    EXTENDED = "EXTENDED"
    NO_VALUE = "NO_VALUE"


class Stage(enum.Enum):
    DISCOVERED = "DISCOVERED"
    CONFIRMED = "CONFIRMED"
    IN_ZONE = "IN_ZONE"
    CANDIDATE = "CANDIDATE"
    TOP10 = "TOP10"
    REMOVED = "REMOVED"


@dataclass
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    vol: float
    vol_ccy: float
    closed: bool = True


@dataclass
class MBar:
    ts0: int
    ts1: int
    raw0: int
    raw1: int
    high: float
    low: float
    high_raw: int
    low_raw: int
    n: int
    dir: int = 0


@dataclass
class Fractal:
    kind: str
    m_idx: int
    raw_idx: int
    confirm_raw_idx: int
    ts: int
    price: float
    m_low: float
    m_high: float


@dataclass
class Bi:
    up: bool
    m0: int
    m1: int
    raw0: int
    raw1: int
    confirm_raw0: int
    confirm_raw1: int
    ts0: int
    ts1: int
    low: float
    high: float
    start_price: float
    end_price: float


@dataclass
class Pivot:
    zg: float
    zd: float
    m0: int
    m1: int
    n: int = 3
    state: str = "forming"


@dataclass
class Buy1:
    m_idx: int
    raw_idx: int
    confirm_raw_idx: int
    ts: int
    confirm_ts: int
    low: float
    seg_start_top: int
    seg_start_top_ts: int
    seg_start_top_price: float
    macd_divergence: bool = False


@dataclass
class Buy2Candidate:
    inst_id: str
    b1: Buy1
    m_idx: int
    raw_idx: int
    confirm_raw_idx: int
    ts: int
    confirm_ts: int
    low: float
    high_after_b1: float
    high_after_b1_m: int
    confirmed_at_raw: int = -1
    invalid_at_raw: int = -1
    extra: dict = field(default_factory=dict)


@dataclass
class CandlesAndMeta:
    candles: list[Candle]
    ms: list[MBar]
    fractals: list[Fractal]
    bis: list[Bi]
    pivots: list[Pivot]
    dif: list[float]
    dea: list[float]
    hist: list[float]
    atr: list[float]
    ema_20: list[float]
    max_raw_idx_closed: int


@dataclass
class AnalysisResult:
    inst_id: str
    inst_type: InstType
    base: str
    quote: str
    ok: bool
    reason: str = ""
    turnover24h: float = 0.0
    last_price: float = 0.0
    last_close: float = 0.0
    ts: int = 0
    anchor_atr: float = 0.0
    current_atr: float = 0.0
    event: Buy2Candidate | None = None
    structure_status: StructureStatus = StructureStatus.INVALID
    trading_value_status: TradingValueStatus = TradingValueStatus.NO_VALUE
    stage: Stage = Stage.DISCOVERED
    age_days: float = 0.0
    distance_atr: float = float("nan")
    distance_anchor_atr: float = float("nan")
    gain_pct: float = float("nan")
    runup_anchor_atr: float = float("nan")
    runup_current_atr: float = float("nan")
    consumed: float = float("nan")
    sub_scores: dict = field(default_factory=dict)
    buy2_score: float = 0.0
    liquidity_score: float = 0.0
    final_score: float = 0.0
    volume_ratio: float = 0.0
    breakdown: dict = field(default_factory=dict)
