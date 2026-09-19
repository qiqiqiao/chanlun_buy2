from __future__ import annotations

"""技术指标：口径冻结声明。

- EMA(period): seed = 前 period 个值的 SMA，落在下标 period-1；
  下标 < period-1 用 seed 填充（与“递归 EMA 从第一根开始、初值取首值”
  的实现前 ~3×period 根会有小数差异，之后收敛到可忽略）。
- ATR(period): Wilder 平滑 ``(prev*(n-1)+tr)/n`` + SMA seed，同上填充。
  即 Wilder 原定义（TA-Lib 的 ATR 同族），不是 RMA-EMA 变体。
- MACD(fast,slow,signal): 标准 EMA 差值 DIF、DIF 的 signal-EMA 为 DEA，
  HIST = 2×(DIF-DEA)。
- warm-up：`indicator_warmup()` 给出建议有效根数（3×slow+signal，保守值）；
  默认 minCandles=220 远大于它（26→87），实盘/回测不受 seed 污染。
  换库（TA-Lib/pandas-ta/TV）对账以 warm-up 之后的序列为准。
"""

from .model import Candle
from .util import safe_div

_BAR_RE = None
try:
    import re as _re

    _BAR_RE = _re.compile(r"^(\d+)(m|H|D|W|M)")
except ImportError:  # pragma: no cover
    _BAR_RE = None

_BAR_MINUTES_CACHE: dict[str, int] = {}


def indicator_warmup(
    macd_slow: int = 26, macd_signal: int = 9, atr_period: int = 14
) -> int:
    """建议的最小有效K根数（保守）：3×macd_slow + macd_signal，至少覆盖ATR。"""
    return max(3 * macd_slow + macd_signal, 3 * atr_period)


def ema(values: list[float], period: int) -> list[float]:
    """SMA-seeded EMA：seed=SMA(values[:period]) @ period-1，前段填 seed。"""
    n = len(values)
    out = [0.0] * n
    if n == 0 or period <= 0:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    one_minus_k = 1 - k
    for i in range(period, n):
        prev = values[i] * k + prev * one_minus_k
        out[i] = prev
    for i in range(period - 1):
        out[i] = seed
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    ef = ema(closes, fast)
    es = ema(closes, slow)
    dif = [f - s for f, s in zip(ef, es)]
    dea = ema(dif, signal)
    hist = [(d - e) * 2.0 for d, e in zip(dif, dea)]
    return dif, dea, hist


def true_range(candles: list[Candle]) -> list[float]:
    out = []
    for i, c in enumerate(candles):
        if i == 0:
            out.append(c.high - c.low)
        else:
            pc = candles[i - 1].close
            out.append(max(c.high - c.low, abs(c.high - pc), abs(c.low - pc)))
    return out


def atr(candles: list[Candle], period: int = 14) -> list[float]:
    """Wilder ATR：seed=SMA(前period个TR)，后续 (prev*(n-1)+tr)/n，前段填 seed。"""
    trs = true_range(candles)
    out = [0.0] * len(candles)
    n = len(candles)
    if n == 0 or period <= 0:
        return out
    seed = sum(trs[:period]) / period
    out[period - 1] = seed
    prev = seed
    rp = period - 1
    for i in range(period, n):
        prev = (prev * rp + trs[i]) / period
        out[i] = prev
    for i in range(period - 1):
        out[i] = seed
    return out


def daily_turnovers(candles: list[Candle]) -> list[float]:
    out = []
    for c in candles:
        out.append(max(0.0, c.vol_ccy))
    return out


_BAR_MINUTES = {"m": 1, "H": 60, "D": 24 * 60, "W": 7 * 24 * 60, "M": 30 * 24 * 60}


def bar_minutes(bar: str) -> int:
    """OKX bar（如 15m/1H/4H/1Dutc/1W）→ 单根K分钟数。未知格式按日线兜底。"""
    key = (bar or "").strip()
    hit = _BAR_MINUTES_CACHE.get(key)
    if hit is not None:
        return hit
    m = _BAR_RE.match(key) if _BAR_RE is not None else None
    if not m:
        _BAR_MINUTES_CACHE[key] = 24 * 60
        return 24 * 60
    val = max(1, int(m.group(1)) * _BAR_MINUTES[m.group(2)])
    _BAR_MINUTES_CACHE[key] = val
    return val


def turnover_24h_at(
    candles: list[Candle], idx: int, bar: str = "1Dutc", is_swap: bool = False
) -> float:
    """idx 处过去 24h 成交额（报价币）。

    按 bar 周期向前累计 N=ceil(1440/bar分钟) 根：1D→当日1根，4H→6根。
    SWAP 沿用实盘口径——基础币量×收盘价的估算值（与 ticker 24h 同口径，
    比 K 线精确 volCcy 更贴近实盘量能分）；SPOT 累加精确 volCcy。
    只用 [0..idx] 数据，无未来函数。
    """
    if not candles or idx < 0:
        return 0.0
    idx = min(idx, len(candles) - 1)
    need = max(1, -(-24 * 60 // bar_minutes(bar)))
    lo = max(0, idx - need + 1)
    total = 0.0
    if is_swap:
        for c in candles[lo : idx + 1]:
            total += max(0.0, c.vol) * max(0.0, c.close)
    else:
        for c in candles[lo : idx + 1]:
            total += max(0.0, c.vol_ccy)
    return total
