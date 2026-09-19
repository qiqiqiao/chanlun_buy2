from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from statistics import mean

from .analysis import analyze_instrument
from .indicators import turnover_24h_at

log = logging.getLogger("backtest")


def _candles_to_raw(candles) -> list[tuple]:
    """Candle 对象 → 轻量 tuple，进程池 pickle 体积减半（仅回测传输用）。"""
    return [
        (c.ts, c.open, c.high, c.low, c.close, c.vol, c.vol_ccy) for c in candles
    ]


def _candles_from_raw(raw: list[tuple]):
    from .model import Candle

    return [Candle(ts=t, open=o, high=h, low=l, close=c, vol=v, vol_ccy=q) for t, o, h, l, c, v, q in raw]


def _walk_instrument(job: dict) -> tuple[list[dict], int, list[dict]]:
    """单币 walk-forward（顶层函数，供进程池 pickle）。

    返回 (signal_events, inst_days, daily_snaps)：snaps 覆盖 warmup..max_d 每一天
    （含无信号日），供 Pool/Portfolio 二级回测做逐日横截面。
    """
    from .model import InstType

    raw = job.get("candles")
    if raw is None:
        raw = job.get("candles_raw")
    if raw and isinstance(raw[0], (list, tuple)) and not hasattr(raw[0], "close"):
        candles = _candles_from_raw(raw)
    else:
        candles = raw
    cfg = job["cfg"]
    fwd_days = job["fwd_days"]
    warmup = job["warmup"]
    bar = cfg.get("data", {}).get("bar", "1Dutc")
    is_swap = bool(job["is_swap"])
    total = len(candles)
    max_fwd = max(fwd_days)
    max_d = total - 1 - max_fwd
    if total <= warmup + 2 or max_d < warmup:
        return [], 0, []
    closes = [c.close for c in candles]
    inst_days = max_d - warmup + 1
    events: list[dict] = []
    snaps: list[dict] = []
    inst_type = InstType(job["inst_type"])
    for d in range(warmup, max_d + 1):
        sub = candles[: d + 1]
        turnover_bt = turnover_24h_at(sub, d, bar, is_swap)
        res = analyze_instrument(
            job["inst_id"],
            inst_type,
            job["base"],
            job["quote"],
            sub,
            live_price=closes[d],
            turnover24h=turnover_bt,
            today_start_ms=sub[-1].ts + 86400000,
            cfg=cfg,
        )
        snaps.append(
            {
                "ts": sub[-1].ts,
                "close": closes[d],
                "high": sub[-1].high,
                "low": sub[-1].low,
                "turnover": turnover_bt,
                "ok": res.ok,
                "has_event": res.event is not None,
                "score": res.buy2_score,
                "dist": res.distance_atr,
                "gain": res.gain_pct,
                "final": res.final_score,
                "struct": res.structure_status.value if res.structure_status else "",
                "value": res.trading_value_status.value
                if res.trading_value_status
                else "",
            }
        )
        if not res.ok:
            continue
        fwd = {}
        horizon_high = []
        horizon_low = []
        for k in fwd_days:
            fwd[k] = None
            if d + k < total:
                fwd[k] = (closes[d + k] - closes[d]) / closes[d] * 100.0
        for j in range(d + 1, min(d + max_fwd + 1, total)):
            horizon_high.append(candles[j].high)
            horizon_low.append(candles[j].low)
        events.append(
            {
                "d": d,
                "inst": job["inst_id"],
                "p0": closes[d],
                "fwd": fwd,
                "score": res.buy2_score,
                "dist": res.distance_atr,
                "struct": res.structure_status.value,
                "hh": horizon_high,
                "ll": horizon_low,
            }
        )
    return events, inst_days, snaps


def run_backtest(cfg: dict, args) -> int:
    from .okx import OkxClient
    from .scan import ScanEngine
    from .store import Store

    limit = int(args.limit or 80)
    engine = ScanEngine(OkxClient(cfg), Store(cfg), cfg)
    uni_cfg = cfg["universe"]
    engine.build_universe()
    use = engine.universe[:limit]
    engine.universe = use
    log.info("backtest universe: %d instruments", len(use))
    engine.sync_all()

    fwd_days = list(args.fwd) if args.fwd else cfg["backtest"]["forwardDays"]
    min_score_grid = (
        list(args.min_score) if args.min_score else cfg["backtest"]["minScoreGrid"]
    )
    dist_grid = (
        list(args.max_dist) if args.max_dist else cfg["backtest"]["distanceGrid"]
    )
    warmup = int(args.warmup or cfg["data"].get("minCandles", 220))

    events: list[dict] = []
    snaps_by_inst: dict[str, list[dict]] = {}
    metas: dict[str, dict] = {}
    inst_days = 0
    t0 = time.time()
    jobs = []
    for ins in use:
        candles = engine._candles_for(ins)
        if args.days and int(args.days) > 0:
            candles = candles[-int(args.days) :]
        metas[ins.inst_id] = {"inst_type": ins.inst_type.value, "base": ins.base}
        jobs.append(
            {
                "candles_raw": _candles_to_raw(candles),
                "cfg": cfg,
                "fwd_days": fwd_days,
                "warmup": warmup,
                "inst_id": ins.inst_id,
                "inst_type": ins.inst_type.value,
                "base": ins.base,
                "quote": ins.quote,
                "is_swap": ins.is_swap,
            }
        )
    n_workers = int(getattr(args, "workers", 0) or 0)
    if n_workers <= 0:
        n_workers = int(cfg.get("backtest", {}).get("workers", 0) or 0)
    if n_workers <= 0:
        n_workers = min(4, os.cpu_count() or 2, len(jobs) or 1)
    if n_workers <= 1 or len(jobs) <= 1:
        for j, job in enumerate(jobs):
            evs, dd, snaps = _walk_instrument(job)
            events.extend(evs)
            snaps_by_inst[job["inst_id"]] = snaps
            inst_days += dd
            if (j + 1) % 10 == 0:
                log.info("walked %d/%d (%.0fs)", j + 1, len(jobs), time.time() - t0)
    else:
        log.info("walk-forward workers=%d", n_workers)
        with ProcessPoolExecutor(max_workers=n_workers) as ex:
            futs = {ex.submit(_walk_instrument, job): i for i, job in enumerate(jobs)}
            done = 0
            for fut in as_completed(futs):
                evs, dd, snaps = fut.result()
                events.extend(evs)
                snaps_by_inst[jobs[futs[fut]]["inst_id"]] = snaps
                inst_days += dd
                done += 1
                if done % 10 == 0 or done == len(jobs):
                    log.info(
                        "walked %d/%d (%.0fs)", done, len(jobs), time.time() - t0
                    )
    log.info(
        "collected %d signal-days across %d instrument-days", len(events), inst_days
    )
    _report(events, fwd_days, min_score_grid, dist_grid)
    if bool(getattr(args, "pool", True)):
        _report_pool(_run_pool_backtest(cfg, snaps_by_inst, metas), len(events))
    return 0


def _run_pool_backtest(cfg: dict, snaps_by_inst: dict, metas: dict) -> dict:
    """二级回测：逐日横截面跑真实 plan_pool（去重/TopN/冷却/排名全生效），
    持仓 = 当日 in_top，等权、每日收盘再平衡。

    缺席某日的币（list 前/掉线）视为按最后价离场，不虚构数据。
    """
    from .model import AnalysisResult, InstType, Stage, StructureStatus, TradingValueStatus
    from .pool import plan_pool

    fee_side = float(cfg.get("backtest", {}).get("feeBpsPerSide", 10.0)) / 10000.0
    by_date: dict[int, dict[str, dict]] = {}
    for iid, snaps in snaps_by_inst.items():
        for sn in snaps:
            by_date.setdefault(sn["ts"], {})[iid] = sn
    dates = sorted(by_date)
    prev: dict = {}
    open_pos: dict[str, dict] = {}
    trades: list[dict] = []
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    prev_hold: dict[str, float] = {}
    n_entries = 0
    cand_days = 0
    ever_top: set[str] = set()
    pool_days = 0
    cost_drag = 0.0
    for ts in dates:
        day = by_date[ts]
        results = []
        for iid, sn in day.items():
            meta = metas.get(iid, {})
            try:
                itype = InstType(meta.get("inst_type", "SPOT"))
            except ValueError:
                itype = InstType.SPOT
            r = AnalysisResult(
                inst_id=iid,
                inst_type=itype,
                base=meta.get("base", ""),
                quote="USDT",
                ok=bool(sn["ok"]),
                turnover24h=sn["turnover"] or 0.0,
                buy2_score=sn["score"] or 0.0,
                final_score=sn["final"] or 0.0,
                distance_atr=sn["dist"],
                gain_pct=sn["gain"],
                last_price=sn["close"],
                last_close=sn["close"],
            )
            r.event = object() if sn["has_event"] else None
            try:
                r.structure_status = (
                    StructureStatus(sn["struct"])
                    if sn["struct"]
                    else StructureStatus.INVALID
                )
            except ValueError:
                r.structure_status = StructureStatus.INVALID
            try:
                r.trading_value_status = (
                    TradingValueStatus(sn["value"])
                    if sn["value"]
                    else TradingValueStatus.NO_VALUE
                )
            except ValueError:
                r.trading_value_status = TradingValueStatus.NO_VALUE
            results.append(r)
        rows, ordered = plan_pool(prev, results, ts, cfg)
        prev = {rw["inst_id"]: rw for rw in rows}
        hold: dict[str, float] = {}
        for c in ordered:
            if c.in_top:
                ever_top.add(c.res.inst_id)
                if c.res.inst_id in day:
                    hold[c.res.inst_id] = day[c.res.inst_id]["close"]
            elif c.res.stage == Stage.CANDIDATE:
                cand_days += 1
        if hold:
            pool_days += 1
        old_ids = set(prev_hold)
        new_ids = set(hold)
        exits = old_ids - new_ids
        entries = new_ids - old_ids
        n_entries += len(entries)
        n_old = max(1, len(old_ids))
        day_ret_sum = 0.0
        for iid in old_ids:
            px_now = day.get(iid, {}).get("close", prev_hold[iid])
            day_ret_sum += px_now / prev_hold[iid] - 1.0
            if iid in open_pos:
                pos = open_pos[iid]
                pos["max_high"] = max(
                    pos["max_high"], day.get(iid, {}).get("high", pos["max_high"])
                )
                pos["min_low"] = min(
                    pos["min_low"], day.get(iid, {}).get("low", pos["min_low"])
                )
        port_ret = day_ret_sum / n_old if old_ids else 0.0
        cost = 0.0
        if entries or exits:
            cost = (
                len(entries) / max(1, len(new_ids)) + len(exits) / n_old
            ) * fee_side
        cost_drag += cost
        equity *= 1.0 + port_ret - cost
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
        for iid in exits:
            pos = open_pos.pop(iid, None)
            if pos is None:
                continue
            exit_px = day.get(iid, {}).get("close", prev_hold[iid])
            gross = exit_px / pos["entry_px"] - 1.0
            trades.append(
                {
                    "hold_days": (ts - pos["entry_ts"]) / 86400000.0,
                    "gross": gross * 100.0,
                    "net": (gross - 2.0 * fee_side) * 100.0,
                    "mfe": (pos["max_high"] / pos["entry_px"] - 1.0) * 100.0,
                    "mae": (pos["min_low"] / pos["entry_px"] - 1.0) * 100.0,
                }
            )
        for iid in entries:
            open_pos[iid] = {
                "entry_ts": ts,
                "entry_px": hold[iid],
                "max_high": hold[iid],
                "min_low": hold[iid],
            }
        prev_hold = dict(hold)
    if dates:
        last_ts = dates[-1]
        for iid, pos in open_pos.items():
            exit_px = prev_hold.get(iid, pos["entry_px"])
            gross = exit_px / pos["entry_px"] - 1.0
            trades.append(
                {
                    "hold_days": (last_ts - pos["entry_ts"]) / 86400000.0,
                    "gross": gross * 100.0,
                    "net": (gross - 2.0 * fee_side) * 100.0,
                    "mfe": (pos["max_high"] / pos["entry_px"] - 1.0) * 100.0,
                    "mae": (pos["min_low"] / pos["entry_px"] - 1.0) * 100.0,
                }
            )
    return {
        "dates": len(dates),
        "pool_days": pool_days,
        "cand_days": cand_days,
        "entries": n_entries,
        "ever_top": len(ever_top),
        "trades": trades,
        "equity": equity,
        "max_dd": max_dd * 100.0,
        "cost_drag": cost_drag * 100.0,
        "fee_bps": fee_side * 10000.0,
    }


def _report_pool(m: dict, n_signals: int) -> None:
    print()
    print("pool backtest  (real plan_pool + equal-weight Top10, daily rebalance)")
    print(
        f"dates={m['dates']} pool_days={m['pool_days']} signal_days={n_signals} "
        f"cand_days={m['cand_days']} entries={m['entries']} "
        f"ever_top={m['ever_top']} round_trips={len(m['trades'])}"
    )
    tr = m["trades"]
    if not tr:
        print("no pool trades in window")
        return
    rets = [t["net"] for t in tr]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    n = len(rets)
    print(
        f"per-trade net%: mean {sum(rets)/n:6.2f}  wr {len(wins)/n*100:5.1f}%  "
        f"pf {_pf(wins, losses):5.2f}  hold {sum(t['hold_days'] for t in tr)/n:5.1f}d  "
        f"mfe {sum(t['mfe'] for t in tr)/n:6.2f}  mae {sum(t['mae'] for t in tr)/n:6.2f}"
    )
    print(
        f"portfolio: total {(m['equity']-1.0)*100:6.2f}%  maxDD {m['max_dd']:5.2f}%  "
        f"cost_drag {m['cost_drag']:5.2f}% (fee {m['fee_bps']:.0f}bps/side)"
    )
    print("notes: entry/exit at daily close; missing-data day exits at last close.")


def _report(events, fwd_days, min_score_grid, dist_grid):
    print()
    print("threshold sweep  (score>=S and dist<=D ATR, entry=signal-day close)")
    print(f"signals total: {len(events)}")
    print(
        f"{'score>=':>7} {'dist<=':>6} | "
        + " | ".join(f"K{d:<4}  n    mean  wr%   pf   mfe   mae" for d in fwd_days)
    )
    print("-" * 120)
    # 预计算每笔信号各 K 的 fwd/mfe/mae：原实现每个网格单元重复扫描
    # e["hh"]/e["ll"] 求均值，网格 6x4x3 时同一信号被扫描 72 遍。
    pre = []
    for e in events:
        fwd = e.get("fwd", {})
        row = {
            "score": e.get("score", 0.0),
            "dist": e.get("dist", float("inf")),
            "fwd": fwd,
            "mfe": {k: _mfe(e, k) for k in fwd_days},
            "mae": {k: _mae(e, k) for k in fwd_days},
        }
        pre.append(row)
    for s in min_score_grid:
        for dst in dist_grid:
            cols = []
            for k in fwd_days:
                rets, mfes, maes = [], [], []
                for e in pre:
                    if e["score"] >= s and e["dist"] <= dst:
                        r = e["fwd"].get(k)
                        if r is not None:
                            rets.append(r)
                            mfes.append(e["mfe"][k])
                            maes.append(e["mae"][k])
                if rets:
                    wins = [r for r in rets if r > 0]
                    losses = [r for r in rets if r <= 0]
                    cols.append(
                        f"{k:<4}  {len(rets):<4} {mean(rets):6.2f} "
                        f"{len(wins)/len(rets)*100:5.1f} {_pf(wins, losses):5.2f} "
                        f"{mean(mfes):6.2f} {mean(maes):6.2f}"
                    )
                else:
                    cols.append(f"{k:<4}     0     -    -    -    -    -")
            print(f"{s:>7.0f} {dst:>6.2f} | " + " | ".join(cols))
    print()
    print(
        "mfe = mean max favorable excursion(% from entry over the window); "
        "mae = mean max adverse excursion(% negative). "
        "mfe/mae reveal typical give-up before reward -> stop/scale design."
    )
    print(
        "notes: signal-day backtest (universe->ok only); no Top10 slots / "
        "per-base dedup / liquidity ranking / costs. Use to tune score & dist."
    )


def _mfe(e, k):
    seg = e["hh"][:k]
    if not seg:
        return 0.0
    return max(seg) / e["p0"] * 100.0 - 100.0


def _mae(e, k):
    seg = e["ll"][:k]
    if not seg:
        return 0.0
    return min(seg) / e["p0"] * 100.0 - 100.0


def _pf(wins: list, losses: list) -> float:
    if not wins:
        return 0.0
    gross = sum(wins)
    g_loss = -sum(losses)
    return gross / g_loss if g_loss > 0 else float("inf")
