from __future__ import annotations

from ..model import Buy2Candidate, Candle, StructureStatus


def structure_status(
    event: Buy2Candidate,
    candles: list[Candle],
    last_closed_idx: int,
    allow_dip_recover: bool = True,
    dip_atr_cap: float | None = None,
    anchor_atr: float | None = None,
) -> tuple[StructureStatus, dict]:
    b1_low = event.b1.low
    b2_low = event.low
    ref_high = event.high_after_b1

    if event.b1.raw_idx == event.raw_idx:
        return StructureStatus.INVALID, {"reason": "anchor_collision"}

    # 扫描起点：确认K（含）之后。raw+1..confirm-1 是分型形成区间的中间K，
    # 其影线发生在信号确认之前，不应计为信号后的 dip/破位（无未来函数对齐）。
    start_idx = max(event.raw_idx + 1, event.confirm_raw_idx)
    after = candles[start_idx : last_closed_idx + 1]
    min_low_after = min((c.low for c in after), default=b2_low)
    cur = candles[last_closed_idx].close

    if min_low_after <= b1_low:
        return StructureStatus.INVALID, {
            "reason": "broke_buy1_low",
            "min_low_after": min_low_after,
        }

    if cur < b2_low:
        return StructureStatus.WEAKENING, {
            "reason": "below_buy2_low",
            "cur": cur,
        }

    dipped, dip_meta = _dip_after_b2(event, candles, last_closed_idx, ref_high)
    if dipped:
        dip_atr = 0.0
        if dip_atr_cap is not None and anchor_atr and anchor_atr > 0:
            dip_atr = dip_meta["dip"] / anchor_atr
        recovered = allow_dip_recover and dip_meta["recovered"]
        if recovered and (dip_atr_cap is None or dip_atr <= dip_atr_cap):
            return StructureStatus.CONFIRMED, {
                "reason": "dipped_then_recovered",
                **dip_meta,
                "dip_atr": dip_atr,
            }
        return StructureStatus.WEAKENING, {
            "reason": "dipped_below_buy2_low",
            **dip_meta,
            "dip_atr": dip_atr,
        }

    if cur >= ref_high:
        return StructureStatus.CONFIRMED, {
            "reason": "broke_ref_high",
            "cur": cur,
            "ref_high": ref_high,
        }

    return StructureStatus.VALID, {
        "reason": "holding_above_buy2_low",
        "cur": cur,
        "ref_high": ref_high,
    }


def _dip_after_b2(
    event: Buy2Candidate, candles: list[Candle], last_closed_idx: int, ref_high: float
) -> tuple[bool, dict]:
    b2_low = event.low
    dipped = False
    max_after_dip = None
    min_dip = None
    dip_idx = -1
    start_idx = max(event.raw_idx + 1, event.confirm_raw_idx)
    for i in range(start_idx, last_closed_idx + 1):
        c = candles[i]
        if not dipped and c.low < b2_low:
            dipped = True
            dip_idx = i
        if dipped:
            if max_after_dip is None:
                max_after_dip = c.high
                min_dip = c.low
            else:
                if c.high > max_after_dip:
                    max_after_dip = c.high
                if min_dip is None or c.low < min_dip:
                    min_dip = c.low
    if not dipped:
        return False, {}
    recovered = max_after_dip is not None and max_after_dip >= ref_high
    return True, {
        "dip": b2_low - min_dip,
        "min_dip": min_dip,
        "dip_idx": dip_idx,
        "max_high_after_dip": max_after_dip,
        "recovered": recovered,
    }
