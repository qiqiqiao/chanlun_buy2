from __future__ import annotations

from ..model import Bi, Candle, Fractal, MBar, Pivot


def merge_bars(candles: list[Candle]) -> list[MBar]:
    ms: list[MBar] = []
    for i, c in enumerate(candles):
        if not ms:
            ms.append(
                MBar(c.ts, c.ts, i, i, c.high, c.low, i, i, 1, 0)
            )
            continue
        last = ms[-1]
        contained = (c.high <= last.high and c.low >= last.low) or (
            c.high >= last.high and c.low <= last.low
        )
        if contained:
            if last.dir >= 0:
                if c.high > last.high:
                    last.high = c.high
                    last.high_raw = i
                if c.low > last.low:
                    last.low = c.low
                    last.low_raw = i
            else:
                if c.high < last.high:
                    last.high = c.high
                    last.high_raw = i
                if c.low < last.low:
                    last.low = c.low
                    last.low_raw = i
            last.ts1 = c.ts
            last.raw1 = i
            last.n += 1
        else:
            direction = 1 if c.high > last.high else -1
            ms.append(MBar(c.ts, c.ts, i, i, c.high, c.low, i, i, 1, direction))
    return ms


def find_fractals(
    ms: list[MBar], require_closed_confirmer: bool = True
) -> list[Fractal]:
    """左右邻确认的严格顶/底分型。

    确认语义（无未来函数的关键）：分型在 m_idx=i 处需要左邻 i-1 与右邻 i+1
    （确认组）同时存在；且确认组本身必须已收盘。
    原因：merge_bars 追加新K时只可能改变最后一个合并组——末尾组恒为开放组。
    若允许 i+1 == n-1（开放组）做确认，明天的新K可能并入该组改变极值，
    使今天的分型事后消失（隐性未来函数，test_no_lookahead 会抓到）。
    因此默认 i+1 <= n-2，多一根合并组的延迟是保稳定的代价。
    require_closed_confirmer=False 退化为“下一组出现即确认”（更灵敏但不稳定，
    仅供研究对比，实盘/回测禁止）。
    """
    fr: list[Fractal] = []
    n = len(ms)
    end = (n - 2) if require_closed_confirmer else (n - 1)
    for i in range(1, end):
        hi = ms[i].high
        if hi > ms[i - 1].high and hi > ms[i + 1].high:
            fr.append(
                Fractal(
                    kind="top",
                    m_idx=i,
                    raw_idx=ms[i].high_raw,
                    confirm_raw_idx=ms[i + 1].raw1,
                    ts=ms[i].ts0,
                    price=hi,
                    m_low=ms[i].low,
                    m_high=ms[i].high,
                )
            )
        lo = ms[i].low
        if lo < ms[i - 1].low and lo < ms[i + 1].low:
            fr.append(
                Fractal(
                    kind="bottom",
                    m_idx=i,
                    raw_idx=ms[i].low_raw,
                    confirm_raw_idx=ms[i + 1].raw1,
                    ts=ms[i].ts0,
                    price=lo,
                    m_low=ms[i].low,
                    m_high=ms[i].high,
                )
            )
    return fr


def build_bis(
    ms: list[MBar], fr: list[Fractal], min_gap: int = 2
) -> list[Bi]:
    if len(fr) < 2:
        return []
    selected: list[Fractal] = []
    i = 0
    while i < len(fr):
        f = fr[i]
        if selected:
            prev = selected[-1]
            if f.kind == prev.kind:
                better = (
                    f.price < prev.price
                    if f.kind == "bottom"
                    else f.price > prev.price
                )
                if better:
                    selected[-1] = f
                i += 1
                continue
            if f.m_idx - prev.m_idx < min_gap:
                # 与 prev 太近不成笔。直接丢 f 会漏掉“新低/新高延续”：
                # 若 f 比上一个同类分型更极端，说明中间的 prev 是毛刺，
                # 去掉 prev 后重走本轮（不推进 i），让 f 与新的 prev 比较。
                if len(selected) >= 2:
                    prev_same = selected[-2]  # 与 f 同类
                    better_than_same = (
                        f.price < prev_same.price
                        if f.kind == "bottom"
                        else f.price > prev_same.price
                    )
                    if better_than_same:
                        selected.pop()
                        continue
                i += 1
                continue
        selected.append(f)
        i += 1

    if len(selected) < 2:
        return []

    bis: list[Bi] = []
    for a, b in zip(selected, selected[1:]):
        up = a.kind == "bottom" and b.kind == "top"
        down = a.kind == "top" and b.kind == "bottom"
        if not up and not down:
            continue
        if up:
            low = a.price
            high = b.price
        else:
            high = a.price
            low = b.price
        bis.append(
            Bi(
                up=up,
                m0=a.m_idx,
                m1=b.m_idx,
                raw0=a.raw_idx,
                raw1=b.raw_idx,
                confirm_raw0=a.confirm_raw_idx,
                confirm_raw1=b.confirm_raw_idx,
                ts0=ms[a.m_idx].ts0,
                ts1=ms[b.m_idx].ts0,
                low=low,
                high=high,
                start_price=a.price,
                end_price=b.price,
            )
        )
    return bis


def build_pivots(ms: list[MBar], bis: list[Bi]) -> list[Pivot]:
    pivots: list[Pivot] = []
    for i in range(len(bis) - 2):
        seg = bis[i : i + 3]
        zg = min(b.high for b in seg)
        zd = max(b.low for b in seg)
        if zg > zd:
            pivots.append(
                Pivot(
                    zg=zg,
                    zd=zd,
                    m0=seg[0].m0,
                    m1=seg[2].m1,
                    n=3,
                    state="completed",
                )
            )
    return pivots
