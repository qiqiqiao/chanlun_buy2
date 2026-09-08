from __future__ import annotations

import datetime
import os
import sys

from .model import Stage, StructureStatus, TradingValueStatus

ANSI = os.environ.get("NO_COLOR") is None and sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    if not ANSI or not code:
        return s
    return f"\x1b[{code}m{s}\x1b[0m"


def struct_dot(s: StructureStatus) -> str:
    m = {
        StructureStatus.CONFIRMED: "\U0001f7e2",
        StructureStatus.VALID: "\U0001f7e2",
        StructureStatus.WEAKENING: "\U0001f7e1",
        StructureStatus.INVALID: "\U0001f534",
    }
    return m.get(s, "\u26aa")


def value_dot(v: TradingValueStatus) -> str:
    m = {
        TradingValueStatus.EXCELLENT: "\U0001f7e2",
        TradingValueStatus.GOOD: "\U0001f7e2",
        TradingValueStatus.WATCH: "\U0001f7e1",
        TradingValueStatus.EXTENDED: "\U0001f7e0",
        TradingValueStatus.NO_VALUE: "\U0001f534",
    }
    return m.get(v, "\u26aa")


_STRUCT_TAG = {
    StructureStatus.CONFIRMED: "CONF",
    StructureStatus.VALID: "VAL",
    StructureStatus.WEAKENING: "WEAK",
    StructureStatus.INVALID: "INV",
}
_VALUE_TAG = {
    TradingValueStatus.EXCELLENT: "EXC",
    TradingValueStatus.GOOD: "GOOD",
    TradingValueStatus.WATCH: "WATCH",
    TradingValueStatus.EXTENDED: "EXT",
    TradingValueStatus.NO_VALUE: "NONE",
}


def _p(v, nd: int = 2) -> str:
    try:
        return f"{float(v):,.{nd}f}"
    except (TypeError, ValueError):
        return "-"


def _px(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    if v == 0:
        return "0"
    if v < 0.01:
        nd = 8
    elif v < 1:
        nd = 6
    else:
        nd = 4
    return f"{v:,.{nd}f}"


def _money(v: float) -> str:
    if v >= 1e9:
        return f"{v/1e9:.2f}B"
    if v >= 1e6:
        return f"{v/1e6:.2f}M"
    if v >= 1e3:
        return f"{v/1e3:.1f}K"
    return f"{v:.0f}"


def _cell(s: str, w: int, color: str = "") -> str:
    s = str(s)
    if len(s) > w:
        s = s[: w - 1] + "~"
    return _c(color, s.ljust(w))


def render_dashboard(ordered, cfg, meta: dict | None = None) -> str:
    score_cfg = cfg["score"]
    pool_cfg = cfg["pool"]

    top = [c for c in ordered if c.in_top]
    rest = [
        c
        for c in ordered
        if not c.in_top
        and c.res.ok
        and c.res.event is not None
        and c.res.trading_value_status != TradingValueStatus.NO_VALUE
    ]
    ready = [c for c in rest if c.res.stage == Stage.CANDIDATE]
    watch = [c for c in rest if c.res.stage != Stage.CANDIDATE]
    watch_shown = watch[: int(pool_cfg.get("maxDisplay", 20))]

    lines: list[str] = []
    lines.append(_c("1;36", "  OKX CHAN Buy-2 Radar  (daily " + cfg["data"]["bar"] + ", quote " + cfg["universe"].get("quoteCcy", "USDT") + ")"))
    if meta:
        lines.append(
            "  " + meta.get("ts_text", "")
            + "   universe=" + str(meta.get("n_inst"))
            + "  signals=" + str(meta.get("n_ok"))
            + "  pool=" + str(len(top))
            + "  ready=" + str(len(ready))
            + "  watch=" + str(len(watch_shown))
        )
    lines.append(
        "  enter: score>=" + _p(score_cfg["enterScore"], 0)
        + " & dist<=" + _p(pool_cfg.get("enterDistanceAtr"), 2) + " ATR    "
        "keep: score>=" + _p(score_cfg["exitScore"], 0)
        + " & dist<=" + _p(pool_cfg.get("exitDistanceAtr"), 2) + " ATR"
    )
    lines.append("")

    def table_title(t: str):
        lines.append(_c("1;33", t))

    def emit(rows, rank_start: int, with_reason: bool):
        header = (
            _cell("#", 4)
            + _cell("inst", 19)
            + _cell("price", 13)
            + _cell("B2low", 13)
            + _cell("distATR", 8)
            + " " + _cell("pct", 8)
            + _cell("age d", 6)
            + _cell("struct", 8)
            + _cell("value", 8)
            + _cell("score", 7)
            + _cell("liq%", 6)
            + _cell("volR", 6)
            + ("  note" if with_reason else "")
        )
        lines.append(header)
        for i, c in enumerate(rows):
            r = c.res
            typ = "S" if r.inst_type.value == "SPOT" else "P"
            name = f"{r.base}/USDT {typ}"
            rank = rank_start + i
            lines.append(
                _cell(rank, 4, "1;37")
                + _cell(name, 19, "1;37")
                + _cell(_px(r.last_price), 13)
                + _cell(_px((r.breakdown or {}).get("b2_low")), 13)
                + _cell(_p(r.distance_atr, 2), 8)
                + " " + _cell(_p(r.gain_pct, 2) + "%", 8)
                + _cell(str(int(round(r.age_days))), 6)
                + " " + struct_dot(r.structure_status)
                + " " + _cell(_STRUCT_TAG[r.structure_status], 6)
                + " " + value_dot(r.trading_value_status)
                + " " + _cell(_VALUE_TAG[r.trading_value_status], 6)
                + _cell(_p(r.buy2_score, 1), 7)
                + _cell(_p(r.liquidity_score, 0), 6)
                + _cell(_p(r.volume_ratio, 2), 6)
                + ("  " + note(c) if with_reason else "")
            )

    table_title("  == TOP " + str(pool_cfg.get("topN")) + " POOL ==")
    emit(top, 1, False)
    if len(top) < int(pool_cfg.get("topN", 10)):
        lines.append(
            "  (only " + str(len(top)) + " setups currently pass entry bar)"
        )
    lines.append("")
    if ready:
        table_title("  == READY (合格补位候选: 满足入场线, 池位空出即补) ==")
        emit(ready[: int(pool_cfg.get("maxDisplay", 20))], len(top) + 1, True)
        lines.append("")
    if watch_shown:
        table_title("  == WATCH (有二买但未达入场线 / 需继续观察) ==")
        emit(watch_shown, len(top) + len(ready) + 1, True)
        lines.append("")
    if not ready and not watch_shown:
        lines.append(_c("33", "  no other current near-zone buy2"))
        lines.append("")

    lines.append(
        "  struct " + struct_dot(StructureStatus.CONFIRMED) + " confirmed/valid   "
        + struct_dot(StructureStatus.WEAKENING) + " weakening   "
        + struct_dot(StructureStatus.INVALID) + " invalid     "
        "value " + value_dot(TradingValueStatus.EXCELLENT) + " excellent/good   "
        + value_dot(TradingValueStatus.WATCH) + " watch   "
        + value_dot(TradingValueStatus.EXTENDED) + " extended   "
        + value_dot(TradingValueStatus.NO_VALUE) + " no-value"
    )
    return "\n".join(lines)


def note(c) -> str:
    if c.change:
        return c.change
    if c.res.stage and c.res.stage.value == "CANDIDATE":
        return "ready"
    if c.res.structure_status == StructureStatus.WEAKENING:
        return "weaken"
    w = c.wait_reason or ""
    if w == "cooldown":
        return "cooldown"
    if w.startswith("no:"):
        return {
            "struct": "struct",
            "liq": "low-liq",
            "score": "low-score",
            "runup": "runup",
            "dist": "far/edge",
        }.get(w.split(":", 1)[1], w)
    return w or "watch"


def render_detail(r, cfg) -> str:
    bd = r.breakdown or {}
    lines: list[str] = []
    lines.append(_c("1;36", f"  {r.inst_id}  ({r.inst_type.value})"))
    lines.append(f"  current price    : {_p(r.last_price, 4)}   (last close {_p(r.last_close, 4)})")
    if bd:
        lines.append(
            f"  buy1 low (L1)    : {_p(bd.get('b1_low'), 4)}   low day {_ts(bd.get('b1_ts'))}"
        )
        lines.append(
            f"  buy2 low (L2)    : {_p(bd.get('b2_low'), 4)}   low day {_ts(bd.get('b2_ts'))}"
            + f"  confirm {_ts(bd.get('b2_confirm_ts'))}"
        )
        lines.append(f"  bounce high (H1) : {_p(bd.get('ref_high'), 4)}")
        lines.append(
            f"  ATR  anchor/cur  : {_p(bd.get('anchor_atr'), 4)} / {_p(bd.get('current_atr'), 4)}"
        )
    lines.append(
        f"  dist to L2       : {r.distance_atr:+.2f} ATR(current)   "
        f"{r.distance_anchor_atr:+.2f} ATR(anchor)"
    )
    lines.append(f"  run-up           : {r.gain_pct:+.2f}%   ({r.runup_anchor_atr:+.2f} ATR anchor / {r.runup_current_atr:+.2f} ATR current)")
    if bd:
        lines.append(
            f"  structure use    : consumed {bd.get('consumed', 0):.2f} of upside_est "
            + f"{_p(bd.get('upside_est'), 4)} (range run-up {bd.get('runup_range', 0):.2f}x)"
        )
    lines.append(f"  age since confirm: {r.age_days:.1f} days")
    lines.append(
        f"  24h turnover     : {_money(r.turnover24h)} USD (est)   vol ratio {r.volume_ratio:.2f}"
    )
    st = r.structure_status
    vt = r.trading_value_status
    lines.append(f"  structure status : {struct_dot(st)} {st.value}   (closed-candle only)")
    lines.append(f"  trading value    : {value_dot(vt)} {vt.value}")
    for k, v in (r.sub_scores or {}).items():
        lines.append(f"      {k:<14}: {v:6.1f}")
    lines.append(f"  Buy2Score        : {r.buy2_score:.1f}/100")
    lines.append(f"  LiquidityScore   : {r.liquidity_score:.1f}/100")
    lines.append(f"  FinalScore       : {r.final_score:.1f}/100")
    if bd:
        lines.append(f"  status detail    : {bd.get('status_reason')}")
    return "\n".join(lines)


def _ts(ms: int | None) -> str:
    if not ms:
        return "-"
    return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")
