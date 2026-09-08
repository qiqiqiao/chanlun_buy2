from __future__ import annotations

import random

from buy2radar.model import Candle


def candles_from_path(path: list[float], seed: int = 7, start_ts: int = 1700000000000) -> list[Candle]:
    rnd = random.Random(seed)
    out: list[Candle] = []
    prev = path[0] * 0.998
    for i, p in enumerate(path):
        o = prev
        h = max(o, p) * (1 + abs(rnd.gauss(0, 0.004)))
        l = min(o, p) * (1 - abs(rnd.gauss(0, 0.004)))
        if h < max(o, p):
            h = max(o, p)
        if l > min(o, p):
            l = min(o, p)
        v = rnd.uniform(0.5, 3.0)
        out.append(Candle(start_ts + i * 86400000, o, h, l, p, v, v * p))
        prev = p
    return out


def pattern_path() -> list[float]:
    rnd = random.Random(7)
    p = 100.0
    path: list[float] = []
    phases = [-0.6, -0.9, 1.2, 0.2, -0.35, 1.4]
    for ph in phases:
        for _ in range(16):
            p = p * (1 + ph * 0.008 + rnd.gauss(0, 0.012))
            path.append(p)
    return path


def random_path(n: int, seed: int, start: float = 50.0) -> list[float]:
    rnd = random.Random(seed)
    p = start
    out: list[float] = []
    for _ in range(n):
        p = p * (1 + rnd.gauss(0, 0.025))
        out.append(p)
    return out
