from __future__ import annotations

import logging

from .chan.core import build_bis, build_pivots, find_fractals, merge_bars
from .chan.detect import detect_buy2
from .chan.status import structure_status
from .indicators import atr as atr_indicator
from .indicators import macd
from .model import (
    AnalysisResult,
    Candle,
    InstType,
    StructureStatus,
    TradingValueStatus,
)
from .scoring import (
    distance_score,
    freshness_score,
    gain_score,
    liquidity_score,
    metrics_from_event,
    structure_score,
    trading_value_status,
    volume_ratio_score,
)
from .util import clamp, safe_div

DAY_MS = 86400000

log = logging.getLogger("analysis")


def analyze_instrument(
    inst_id: str,
    inst_type: InstType,
    base: str,
    quote: str,
    candles: list[Candle],
    live_price: float | None,
    turnover24h: float,
    today_start_ms: int,
    cfg: dict,
) -> AnalysisResult:
    res = AnalysisResult(
        inst_id=inst_id,
        inst_type=inst_type,
        base=base,
        quote=quote,
        ok=False,
        turnover24h=turnover24h,
        ts=0,
    )
    chan_cfg = cfg.get("chan", {})
    buy2_cfg = cfg.get("buy2", {})
    score_cfg = cfg.get("score", {})
    n = len(candles)
    min_candles = int(cfg["data"].get("minCandles", 220))
    if n < min_candles:
        res.reason = "insufficient_candles"
        return res

    last_close = candles[-1].close
    price = live_price if live_price is not None and live_price > 0 else last_close
    res.last_price = price
    res.last_close = last_close

    require_div = bool(chan_cfg.get("requireBuy1Divergence", False))
    if require_div:
        closes = [c.close for c in candles]
        dif, dea, hist = macd(
            closes,
            chan_cfg.get("macdFast", 12),
            chan_cfg.get("macdSlow", 26),
            chan_cfg.get("macdSignal", 9),
        )
    else:
        # 背驰开关关闭时跳过 closes 物化 + MACD 全量计算（detect 内直接视为通过）
        dif, dea, hist = [], [], []
    atr_arr = atr_indicator(candles, int(chan_cfg.get("atrPeriod", 14)))

    ms = merge_bars(candles)
    fr = find_fractals(ms, bool(chan_cfg.get("fractalConfirmerClosed", True)))
    bis = build_bis(ms, fr, int(chan_cfg.get("biMinMergedGap", 2)))
    pivots = build_pivots(ms, bis)

    if len(bis) < 4:
        res.reason = "insufficient_structure"
        return res

    last_idx = n - 1
    min_after = int(buy2_cfg.get("minAnchorBarsAfter", 1))
    detect_cfg = dict(chan_cfg)
    detect_cfg["maxConfirmRaw"] = last_idx - min_after
    detect_cfg["atr"] = atr_arr
    for _hk in ("maxBarsB1toB2", "maxB2GapAtr", "requireConfirmCloseAboveB1"):
        if _hk in buy2_cfg:
            detect_cfg[_hk] = buy2_cfg[_hk]

    event, emit_meta = detect_buy2(ms, bis, candles, hist, detect_cfg, atr_arr)
    if event is None:
        res.reason = "no_buy2"
        return res
    res.event = event

    if event.confirm_raw_idx + min_after > last_idx:
        res.reason = "buy2_unconfirmed_pending"
        return res

    atr_anchor = (
        atr_arr[event.raw_idx] if 0 <= event.raw_idx < len(atr_arr) else 0.0
    )
    atr_current = atr_arr[last_idx] if atr_arr else 0.0
    res.anchor_atr = atr_anchor
    res.current_atr = atr_current
    res.ts = candles[last_idx].ts

    age_days = max(0.0, (today_start_ms - event.confirm_ts) / DAY_MS)
    res.age_days = age_days
    max_age = float(buy2_cfg.get("maxAgeDays", 60))
    if age_days > max_age:
        res.reason = "too_old"
        return res

    st, st_meta = structure_status(
        event,
        candles,
        last_idx,
        dip_atr_cap=buy2_cfg.get("maxDipRecoverAtr"),
        anchor_atr=atr_anchor,
    )
    res.structure_status = st

    if st == StructureStatus.INVALID:
        res.reason = "structure_invalid"
        return res

    if atr_anchor <= 0 or atr_current <= 0:
        res.reason = "no_atr"
        return res

    metrics = metrics_from_event(event, atr_current, atr_anchor, price)
    res.distance_atr = metrics["distance_current_atr"]
    res.distance_anchor_atr = metrics["distance_anchor_atr"]
    res.gain_pct = metrics["runup_pct"]
    res.runup_anchor_atr = metrics["runup_anchor_atr"]
    res.runup_current_atr = metrics["runup_current_atr"]
    res.consumed = metrics["consumed"]

    baseline = _baseline_turnover(candles, int(score_cfg.get("baselineDays", 5)))
    ratio = safe_div(turnover24h, baseline, default=1.0)
    res.volume_ratio = ratio

    tables = score_cfg.get("tables", {})
    extra = {"macd_divergence": event.b1.macd_divergence}
    sub = {
        "structure": structure_score(st.name, extra),
        "distance": distance_score(
            metrics["distance_current_atr"], tables.get("distancePoints")
        ),
        "gain": gain_score(metrics["consumed"], tables.get("gainPoints")),
        "freshness": freshness_score(
            age_days,
            score_cfg.get(
                "ageScorePoints",
                tables.get("ageScorePoints", [[0, 100], [15, 100], [30, 70]]),
            ),
        ),
        "liquidity": liquidity_score(
            turnover24h,
            float(score_cfg.get("liquidityTurnMin", 2e6)),
            float(score_cfg.get("liquidityTurnMax", 5e9)),
        ),
        "volumeChange": volume_ratio_score(ratio, tables.get("volumePoints")),
    }
    res.sub_scores = sub

    weights = score_cfg.get("weights", {})
    wsum = sum(float(w) for w in weights.values()) or 1.0
    total = 0.0
    for k, w in weights.items():
        total += sub.get(k, 0.0) * float(w)
    buy2_score = clamp(total / wsum, 0.0, 100.0)
    res.buy2_score = buy2_score
    res.liquidity_score = sub["liquidity"]

    rank_cfg = cfg.get("rank", {})
    lw = float(rank_cfg.get("liquidityWeight", 0.2))
    res.final_score = clamp(
        (1.0 - lw) * buy2_score + lw * res.liquidity_score, 0.0, 100.0
    )

    vt = trading_value_status(
        metrics["distance_current_atr"],
        buy2_score,
        {
            "zones": cfg.get("zones", {}),
            "tradingValueMinScore": score_cfg.get("tradingValueMinScore", 40.0),
        },
    )
    runup_cap = float(buy2_cfg.get("runupValueCapPct", 30.0))
    if metrics["runup_pct"] > runup_cap:
        vt = TradingValueStatus.NO_VALUE
    if st == StructureStatus.WEAKENING and vt in (
        TradingValueStatus.EXCELLENT,
        TradingValueStatus.GOOD,
    ):
        vt = TradingValueStatus.WATCH
    res.trading_value_status = vt

    res.breakdown = {
        "b1_low": event.b1.low,
        "b1_ts": event.b1.ts,
        "b1_confirm_ts": event.b1.confirm_ts,
        "b2_low": event.low,
        "b2_ts": event.ts,
        "b2_confirm_ts": event.confirm_ts,
        "b2_raw": event.raw_idx,
        "b2_confirm_raw": event.confirm_raw_idx,
        "ref_high": event.high_after_b1,
        "anchor_atr": atr_anchor,
        "current_atr": atr_current,
        "upside_est": metrics["upside_est"],
        "consumed": metrics["consumed"],
        "runup_range": metrics["runup_range"],
        "divergence": event.b1.macd_divergence,
        "pivots_total": len(pivots),
        "status_reason": st_meta,
    }

    res.ok = True
    res.reason = "ok"
    return res


def _baseline_turnover(candles: list[Candle], days: int) -> float:
    seg = candles[-days:]
    vals = [c.vol_ccy for c in seg if c.vol_ccy > 0]
    if not vals:
        return 0.0
    return sum(vals) / len(vals)
