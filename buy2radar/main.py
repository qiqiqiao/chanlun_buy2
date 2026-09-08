from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from .config import DEFAULT_CONFIG, load_config, save_config
from .util import init_logging, now_ms

log = logging.getLogger("main")


def _engine(cfg: dict):
    from .okx import OkxClient
    from .scan import ScanEngine
    from .store import Store

    client = OkxClient(cfg)
    store = Store(cfg)
    return ScanEngine(client, store, cfg), store


def _run_once(cfg: dict, args) -> int:
    from .dashboard import render_dashboard

    engine, _ = _engine(cfg)
    results, rows, ordered = engine.run_full()
    n_cand = sum(1 for c in ordered if c.res.ok and c.res.event and not c.in_top)
    meta = {
        "ts_text": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "n_inst": len(engine.universe),
        "n_ok": len(results),
        "n_cand": n_cand,
    }
    print(render_dashboard(ordered, cfg, meta))
    return 0


def _inspect(cfg: dict, inst_id: str) -> int:
    from .dashboard import render_detail
    from .okx import OkxClient, ticker_turnover
    from .scan import ScanEngine, Instrument
    from .store import Store

    client = OkxClient(cfg)
    store = Store(cfg)
    uni_cfg = cfg["universe"]
    rows = None
    for typ in uni_cfg["instTypes"]:
        for it in client.instruments(typ):
            if it["instId"] == inst_id:
                rows = it
                break
        if rows:
            break
    if not rows:
        print("unknown instrument", inst_id)
        return 1
    ins = Instrument(inst_id, rows.get("instType"), rows.get("baseCcy"), rows.get("quoteCcy"))
    tm = client.tickers(ins.inst_type.value)
    tmap = {t["instId"]: t for t in tm}
    t = tmap.get(inst_id)
    if t:
        ins.turnover24h = ticker_turnover(t, ins.inst_type.value)
        ins.last_px = float(t.get("last", 0.0) or 0.0)
    engine = ScanEngine(client, store, cfg)
    engine.universe = [ins]
    ok = engine.sync_candles(ins)
    if not ok:
        print("insufficient candles")
        return 1
    engine._ticker_map = tmap
    res = engine.analyze_instruments(use_tickers=True)
    if not res:
        print("no valid buy2 candidate:", inst_id)
        return 0
    print(render_detail(res[0], cfg))
    return 0


def _watch(cfg: dict, args) -> int:
    from .dashboard import render_dashboard

    engine, store = _engine(cfg)
    interval = int(args.interval or cfg["monitor"].get("lightRefreshSeconds", 60))
    full_minutes = float(cfg["monitor"].get("fullScanMinutes", 15))
    quiet = bool(cfg["monitor"].get("quiet", False))
    once = bool(getattr(args, "once", False))
    last_full = 0.0
    engine.build_universe()
    log.info("universe=%d", len(engine.universe))
    cycle = 0
    while True:
        now = time.time()
        if now - last_full >= full_minutes * 60 or cycle == 0:
            log.info("full scan (cycle %d)", cycle)
            engine.build_universe()
            log.info("universe=%d", len(engine.universe))
            engine.sync_all()
            results, rows, ordered = engine.compute_pool(persist=True)
            last_full = now
        else:
            # 轻刷：只重算成员+高优候选（新信号最多延迟一个全量周期）
            sub = engine.light_subset_ids()
            if sub:
                full_uni = engine.universe
                by_id = {it.inst_id: it for it in full_uni}
                engine.universe = [by_id[i] for i in sub if i in by_id] or full_uni
                try:
                    results, rows, ordered = engine.compute_pool(persist=False)
                finally:
                    engine.universe = full_uni
            else:
                results, rows, ordered = engine.compute_pool(persist=False)
        n_ok = len(results)
        n_cand = sum(1 for c in ordered if c.res.ok and c.res.event and not c.in_top)
        meta = {
            "ts_text": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "n_inst": len(engine.universe),
            "n_ok": n_ok,
            "n_cand": n_cand,
        }
        text = render_dashboard(ordered, cfg, meta)
        if not quiet:
            sys.stdout.write("\x1b[H\x1b[2J" + text + "\n")
            sys.stdout.flush()
        cycle += 1
        if once:
            return 0
        time.sleep(interval)


def _backtest(cfg: dict, args) -> int:
    from .backtest import run_backtest

    return run_backtest(cfg, args)


def _write_example_config(cfg: dict, path: str) -> None:
    save_config(cfg, path)
    print("config written:", path)


def _config_path(path: str | None) -> str:
    if path:
        return path
    return os.path.join(os.getcwd(), "data", "config.json")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="buy2radar",
        description="OKX daily Chan-theory Buy-2 radar: scan, score, monitor top pool",
    )
    p.add_argument("-c", "--config", default=None, help="config json path")
    p.add_argument("--log", default="INFO", help="log level")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("config", help="write default config file")
    sp.add_argument("path", nargs="?", default=None)

    sp = sub.add_parser("scan", help="one-shot full scan and print dashboard")
    sp.add_argument("--insts", type=int, default=None)

    sp = sub.add_parser("watch", help="continuous monitor loop")
    sp.add_argument("--interval", type=int, default=None)
    sp.add_argument("--once", action="store_true", help="single pass then exit")

    sp = sub.add_parser("inspect", help="inspect one instrument")
    sp.add_argument("inst", help="instId e.g. BTC-USDT-SWAP")

    sp = sub.add_parser("backtest", help="replay thresholds over history")
    sp.add_argument("--limit", type=int, default=80, help="max instruments")
    sp.add_argument("--days", type=int, default=0, help="max candles to consider")
    sp.add_argument("--warmup", type=int, default=0)
    sp.add_argument("--workers", type=int, default=0, help="walk workers (0=auto)")
    sp.add_argument("--pool", action=argparse.BooleanOptionalAction, default=True)
    sp.add_argument("--min-score", type=float, nargs="*", default=None)
    sp.add_argument("--max-dist", type=float, nargs="*", default=None)
    sp.add_argument("--fwd", type=int, nargs="*", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    init_logging(args.log)
    cfg = load_config(args.config)
    if not args.cmd:
        build_parser().print_help()
        return 1
    if args.cmd == "config":
        path = args.path or _config_path(args.config)
        _write_example_config(DEFAULT_CONFIG, path)
        return 0
    if args.cmd == "scan":
        return _run_once(cfg, args)
    if args.cmd == "watch":
        return _watch(cfg, args)
    if args.cmd == "inspect":
        return _inspect(cfg, args.inst)
    if args.cmd == "backtest":
        return _backtest(cfg, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
