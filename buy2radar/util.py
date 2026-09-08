from __future__ import annotations

import math
import sys
import time


def now_ms() -> int:
    return int(time.time() * 1000)


def day_start_ms(ts_ms: int | None = None, tz_off_s: int = 0) -> int:
    ts_s = (ts_ms if ts_ms is not None else now_ms()) // 1000 + tz_off_s
    return (ts_s - ts_s % 86400 - tz_off_s) * 1000


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    return default if b == 0 or b != b else a / b


def piecewise_lookup(x: float, pts: list[list[float]]) -> float:
    if not pts:
        return 0.0
    # 配置表通常已有序，O(n) 有序检查通过则跳过 O(n log n) 排序
    ordered = True
    for i in range(len(pts) - 1):
        if pts[i][0] > pts[i + 1][0]:
            ordered = False
            break
    if not ordered:
        pts = sorted(pts, key=lambda p: p[0])
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            t = (x - x0) / (x1 - x0)
            return y0 + (y1 - y0) * t
    return pts[-1][1]


def exp_decay(age: float, half_life_days: float) -> float:
    if half_life_days <= 0:
        return 100.0 if age <= 0 else 0.0
    return 100.0 * (0.5 ** (max(age, 0.0) / half_life_days))


def log_scale_score(value: float, lo: float, hi: float) -> float:
    if value <= lo:
        return 0.0
    if value >= hi:
        return 100.0
    a = math.log10(value)
    b = math.log10(lo)
    c = math.log10(hi)
    return 100.0 * (a - b) / (c - b)


def emoji_green(v: float | None, red_below: float, green_above: float) -> str:
    if v is None or v != v:
        return " "
    if v >= green_above:
        return "g"
    if v <= red_below:
        return "r"
    return "y"


def init_logging(level: str = "INFO") -> None:
    import logging

    logging.basicConfig(
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-6s %(message)s",
        datefmt="%H:%M:%S",
    )
