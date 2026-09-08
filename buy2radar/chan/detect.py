from __future__ import annotations

import enum

from ..model import Bi, Buy1, Buy2Candidate, Candle


class B2State(enum.Enum):
    """Buy2 波内状态机（单波单候选，行为见各状态注释）。"""

    SEARCH_LOW = "SEARCH_LOW"  # A: 寻找/跟踪本波最低点 B1
    AWAIT_BOUNCE = "AWAIT_BOUNCE"  # B: B1 已定，等待反弹达标
    AWAIT_PULLBACK = "AWAIT_PULLBACK"  # C: 反弹达标，等待更高低点 B2
    EMITTED = "EMITTED"  # D: 二买确认（终端，本波不再锚定）
    EXPIRED = "EXPIRED"  # E: 瞬态——波超时作废，隨即回到 SEARCH_LOW 重找


def swings_from_bis(bis: list[Bi], candles: list[Candle]) -> list[dict]:
    pts: list[dict] = []
    if not bis:
        return pts
    kinds: list[str] = []
    kinds.append("bottom" if bis[0].up else "top")
    for b in bis:
        kinds.append("top" if b.up else "bottom")

    def push(raw, confirm_raw, kind, m_idx):
        if 0 <= raw < len(candles) and 0 <= confirm_raw < len(candles):
            pts.append(
                {
                    "kind": kind,
                    "price": candles[raw].low if kind == "bottom" else candles[raw].high,
                    "raw": raw,
                    "confirm_raw": confirm_raw,
                    "m_idx": m_idx,
                    "ts": candles[raw].ts,
                    "confirm_ts": candles[confirm_raw].ts,
                }
            )

    for i in range(len(bis) + 1):
        if i == 0:
            b0 = bis[0]
            push(b0.raw0, b0.confirm_raw0, kinds[0], b0.m0)
            continue
        b = bis[i - 1]
        push(b.raw1, b.confirm_raw1, kinds[i], b.m1)
    return pts


def _down_bi_at(bis: list[Bi], end_raw: int, hist: list[float]) -> dict | None:
    for b in bis:
        if not b.up and b.raw1 == end_raw:
            lo = max(0, min(b.raw0, b.raw1))
            hi = max(b.raw0, b.raw1)
            seg = hist[lo : hi + 1]
            return {"raw0": lo, "raw1": hi, "min_hist": min(seg) if seg else 0.0}
    return None


def _prev_down_bi(bis: list[Bi], before_raw: int, hist: list[float]) -> dict | None:
    for b in reversed(bis):
        if b.up:
            continue
        if b.raw1 < before_raw:
            lo = max(0, min(b.raw0, b.raw1))
            hi = max(b.raw0, b.raw1)
            seg = hist[lo : hi + 1]
            return {"raw0": lo, "raw1": hi, "min_hist": min(seg) if seg else 0.0}
    return None


def detect_buy2(
    ms: list,
    bis: list[Bi],
    candles: list[Candle],
    hist: list[float],
    cfg: dict,
    atr_arr: list[float] | None = None,
) -> tuple[Buy2Candidate | None, dict]:
    max_confirm_raw = cfg.get(
        "maxConfirmRaw", cfg.get("maxAnchorRaw", len(candles) - 1)
    )
    if atr_arr is None:
        atr_arr = cfg.get("atr")
    if not bis:
        return None, {}
    pts = swings_from_bis(bis, candles)

    wave_rebound_frac = float(cfg.get("waveReboundFrac", 0.5))
    min_bounce_atr = float(cfg.get("minBounceAtr", 0.6))
    require_div = bool(cfg.get("requireBuy1Divergence", False))
    # 扩展钩（默认全部关闭 = 历史行为；打开后只会“更严格”，不会放宽）：
    max_bars_b1_b2 = int(cfg.get("maxBarsB1toB2", 0) or 0)  # B1→B2确认K超时，0=不限
    max_gap_atr = float(cfg.get("maxB2GapAtr", 0.0) or 0.0)  # B2-B1超过X*ATR视为脱钩跳过，0=不限
    req_close_above_b1 = bool(cfg.get("requireConfirmCloseAboveB1", False))

    state = B2State.SEARCH_LOW
    trace: list[dict] = []

    def _to(ns: B2State, raw: int) -> None:
        nonlocal state
        if ns is not state:
            state = ns
            trace.append({"s": ns.value, "raw": raw})

    def _rebound_needed(a_price: float, leg: float, atr_now: float) -> float:
        return a_price + max(
            wave_rebound_frac * leg, min_bounce_atr * max(atr_now, 1e-9)
        )

    def _atr_at(raw: int) -> float:
        if atr_arr and 0 <= raw < len(atr_arr):
            return atr_arr[raw]
        return 0.0

    def _check_divergence() -> bool:
        if not require_div:
            return True
        cur = _down_bi_at(bis, a_cand["raw"], hist)
        prev = _prev_down_bi(bis, a_cand["raw"], hist)
        if cur is not None and prev is not None:
            return cur["min_hist"] > prev["min_hist"]
        return True

    def _emit(bottom: dict) -> None:
        nonlocal emitted
        b1 = Buy1(
            m_idx=a_cand.get("m_idx", -1),
            raw_idx=a_cand["raw"],
            confirm_raw_idx=a_cand["confirm_raw"],
            ts=a_cand["ts"],
            confirm_ts=a_cand["confirm_ts"],
            low=a_cand["price"],
            seg_start_top=a_top["raw"],
            seg_start_top_ts=a_top["ts"],
            seg_start_top_price=a_top["price"],
            macd_divergence=_check_divergence(),
        )
        emitted = {
            "b1": b1,
            "b1_swing": a_cand,
            "bottom": bottom,
            # 快照：只含 a→b 区间已知顶，不含 b 之后的高点（无未来泄漏）
            "bounce_high": bounce_high,
            "bounce_high_m": bounce_high_m,
        }
        _to(B2State.EMITTED, bottom["raw"])

    a_cand: dict | None = None
    a_top: dict | None = None
    last_top: dict | None = None
    bounce_high = 0.0
    bounce_high_m = -1
    emitted: dict | None = None

    for p in pts:
        if p["confirm_raw"] > max_confirm_raw:
            break
        if p["kind"] == "top":
            if state is B2State.EMITTED:
                continue
            last_top = p
            if a_cand is not None and p["price"] > bounce_high:
                bounce_high = p["price"]
                bounce_high_m = p.get("m_idx", -1)
                leg = a_top["price"] - a_cand["price"] if a_top else 0.0
                if (
                    state is B2State.AWAIT_BOUNCE
                    and a_top is not None
                    and leg > 0
                    and bounce_high
                    >= _rebound_needed(a_cand["price"], leg, _atr_at(a_cand["raw"]))
                ):
                    _to(B2State.AWAIT_PULLBACK, p["raw"])
            continue

        bottom = p
        if state is B2State.EMITTED:
            # D之后本波不再锚定；但出现更低点 = 下跌延续，旧候选作废回A重找
            if a_cand is not None and bottom["price"] < a_cand["price"]:
                a_cand = bottom
                a_top = last_top
                emitted = None
                bounce_high = 0.0
                bounce_high_m = -1
                _to(B2State.AWAIT_BOUNCE, bottom["raw"])
            continue
        # A/SEARCH_LOW：无锚点或新低 →（重新）锚定 B1，下跌延续旧候选作废
        if a_cand is None or bottom["price"] < a_cand["price"]:
            a_cand = bottom
            a_top = last_top
            emitted = None
            bounce_high = 0.0
            bounce_high_m = -1
            _to(B2State.AWAIT_BOUNCE, bottom["raw"])
            continue
        if bottom["price"] <= a_cand["price"]:
            continue
        if a_top is None:
            continue

        leg = a_top["price"] - a_cand["price"]
        if leg <= 0:
            continue
        if bounce_high < _rebound_needed(
            a_cand["price"], leg, _atr_at(a_cand["raw"])
        ):
            _to(B2State.AWAIT_BOUNCE, bottom["raw"])
            continue
        _to(B2State.AWAIT_PULLBACK, bottom["raw"])

        # E: 超时作废 → 以当前底为新起点重找（下跌延续语义）
        if max_bars_b1_b2 > 0 and (
            bottom["confirm_raw"] - a_cand["confirm_raw"] > max_bars_b1_b2
        ):
            trace.append({"s": B2State.EXPIRED.value, "raw": bottom["raw"]})
            a_cand = bottom
            a_top = last_top
            emitted = None
            bounce_high = 0.0
            bounce_high_m = -1
            _to(B2State.AWAIT_BOUNCE, bottom["raw"])
            continue
        # B2 与 B1 拉开过远视为脱钩：跳过该底，继续等更近的回调
        if max_gap_atr > 0:
            atr_a = _atr_at(a_cand["raw"])
            if atr_a > 0 and (bottom["price"] - a_cand["price"]) > max_gap_atr * atr_a:
                trace.append({"s": "SKIP_FAR_B2", "raw": bottom["raw"]})
                continue
        # B2 确认K收盘必须站上 B1（可选的确认强度门）
        if req_close_above_b1:
            ci = bottom["confirm_raw"]
            if not (0 <= ci < len(candles) and candles[ci].close > a_cand["price"]):
                trace.append({"s": "SKIP_WEAK_CONFIRM", "raw": bottom["raw"]})
                continue

        _emit(bottom)

    if emitted is None:
        return None, {"state_trace": trace, "final_state": state.value}
    b1 = emitted["b1"]
    bottom = emitted["bottom"]
    idx_b = bottom["raw"]

    lo = min(b1.raw_idx, idx_b)
    hi = max(b1.raw_idx, idx_b)
    seg = candles[lo : hi + 1]
    max_hi_between = max(c.high for c in seg) if seg else b1.low

    ev = Buy2Candidate(
        inst_id="",
        b1=b1,
        m_idx=bottom.get("m_idx", -1),
        raw_idx=idx_b,
        confirm_raw_idx=bottom["confirm_raw"],
        ts=bottom["ts"],
        confirm_ts=bottom["confirm_ts"],
        low=bottom["price"],
        high_after_b1=emitted["bounce_high"],
        high_after_b1_m=emitted.get("bounce_high_m", -1),
    )
    emitted["max_hi_between"] = max_hi_between
    emitted["confirm_ref_high"] = max_hi_between
    emitted["state_trace"] = trace
    emitted["final_state"] = state.value
    return ev, emitted
