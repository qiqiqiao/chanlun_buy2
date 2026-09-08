from __future__ import annotations

import math

from .model import Buy2Candidate, TradingValueStatus
from .util import clamp, piecewise_lookup, safe_div


def metrics_from_event(
    event: Buy2Candidate,
    atr_current: float,
    atr_anchor: float,
    price: float,
) -> dict:
    l2 = event.low
    run = max(price - l2, 0.0)
    g_pct = safe_div(run, l2) * 100.0
    ups = event.high_after_b1
    pullback_range = max(ups - l2, 1e-12)
    upside_est = ups + pullback_range
    consumed = clamp(safe_div(run, upside_est - l2, default=1.0), 0.0, 3.0)
    return {
        "distance_current_atr": safe_div(price - l2, atr_current),
        "distance_anchor_atr": safe_div(price - l2, atr_anchor),
        "runup_pct": g_pct,
        "runup_current_atr": safe_div(run, atr_current),
        "runup_anchor_atr": safe_div(run, atr_anchor),
        "runup_range": safe_div(run, pullback_range),
        "upside_est": upside_est,
        "pullback_range": pullback_range,
        "consumed": consumed,
        "l2": l2,
    }


def distance_score(distance_atr: float, table: list[list[float]]) -> float:
    return clamp(piecewise_lookup(distance_atr, table), 0.0, 100.0)


def gain_score(consumed: float, table: list[list[float]]) -> float:
    return clamp(piecewise_lookup(consumed, table), 0.0, 100.0)


def freshness_score(age_days: float, points: list[list[float]]) -> float:
    if not points:
        return 100.0
    return clamp(piecewise_lookup(max(age_days, 0.0), points), 0.0, 100.0)


def liquidity_score(turnover24h: float, lo: float, hi: float) -> float:
    if turnover24h <= 0:
        return 0.0
    if turnover24h <= lo:
        return 20.0 * safe_div(turnover24h, lo)
    return clamp(log_scale(turnover24h, lo, hi), 0.0, 100.0)


def log_scale(value: float, lo: float, hi: float) -> float:
    a = math.log10(max(value, 1e-6))
    b = math.log10(lo)
    c = math.log10(hi)
    if c <= b:
        return 100.0
    return 100.0 * (a - b) / (c - b)


def volume_ratio_score(ratio: float, table: list[list[float]]) -> float:
    return clamp(piecewise_lookup(ratio, table), 0.0, 100.0)


def structure_score(status_name: str, extra: dict) -> float:
    base = {
        "CONFIRMED": 100.0,
        "VALID": 78.0,
        "WEAKENING": 40.0,
        "INVALID": 0.0,
    }.get(status_name, 0.0)
    bonus = 0.0
    if extra.get("macd_divergence"):
        bonus += 3.0
    return clamp(base + bonus, 0.0, 100.0)


def trading_value_status(
    distance_current_atr: float, buy2_score: float, cfg: dict
) -> TradingValueStatus:
    vt = cfg.get("zones", {})
    d_optimal = float(vt.get("optimalAtr", 0.5))
    d_good = float(vt.get("goodAtr", 1.0))
    d_watch = float(vt.get("watchAtr", 1.5))
    d_max = float(vt.get("maxAtr", 2.0))
    low_cut = float(cfg.get("tradingValueMinScore", 40.0))
    if buy2_score < low_cut:
        return TradingValueStatus.NO_VALUE
    if distance_current_atr <= d_optimal:
        return TradingValueStatus.EXCELLENT
    if distance_current_atr <= d_good:
        return TradingValueStatus.GOOD
    if distance_current_atr <= d_watch:
        return TradingValueStatus.WATCH
    if distance_current_atr <= d_max:
        return TradingValueStatus.EXTENDED
    return TradingValueStatus.NO_VALUE
