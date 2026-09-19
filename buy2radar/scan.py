from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .analysis import analyze_instrument
from .model import InstType, Stage
from .okx import parse_candle_row, ticker_turnover
from .pool import plan_pool
from .store import Store
from .util import day_start_ms, now_ms

DAY_MS = 86400000

log = logging.getLogger("scan")


class Instrument:
    def __init__(self, inst_id: str, inst_type: str, base: str, quote: str):
        self.inst_id = inst_id
        self.inst_type = InstType(inst_type)
        self.base = base
        self.quote = quote
        self.turnover24h = 0.0
        self.last_px = 0.0

    @property
    def is_swap(self) -> bool:
        return self.inst_type == InstType.SWAP


class ScanEngine:
    def __init__(self, client, store: Store, cfg: dict):
        self.client = client
        self.store = store
        self.cfg = cfg
        self.universe: list[Instrument] = []
        self._candles: dict[str, list] = {}
        self._ticker_map: dict[str, dict] = {}
        self._mem_rows: list[dict] | None = None
        self._bar = cfg["data"]["bar"]
        self._history_bars = int(cfg["data"].get("historyBars", 320))
        self._min_candles = int(cfg["data"].get("minCandles", 220))
        self._candle_limit = min(
            self._history_bars, int(cfg["data"].get("maxCandlesPerReq", 300))
        )
        self._per_req = max(1, min(int(cfg["data"].get("maxCandlesPerReq", 300)), 300))
        state_cfg = cfg.get("state", {})
        self._candle_keep = max(
            self._history_bars, int(state_cfg.get("candleLimit", 1000))
        )
        self._last_ordered: list = []

    def fetch_tickers(self) -> dict:
        tm: dict[str, dict] = {}
        for typ in self.cfg["universe"]["instTypes"]:
            for t in self.client.tickers(typ):
                tm[t["instId"]] = t
        self._ticker_map = tm
        return tm

    def build_universe(self) -> list[Instrument]:
        uni = self.cfg["universe"]
        stable = set(uni.get("stableBases", []))
        exclude = set(uni.get("excludeBases", []))
        force = set(uni.get("forceBases", []))
        quote = uni.get("quoteCcy", "USDT")
        insts: list[Instrument] = []
        for typ in uni["instTypes"]:
            for it in self.client.instruments(typ):
                if it.get("state") != "live":
                    continue
                base = it.get("baseCcy", "")
                q = it.get("quoteCcy", "")
                if typ == "SWAP":
                    if it.get("ctType") != "linear":
                        continue
                    if it.get("settleCcy") != quote:
                        continue
                if q != quote:
                    continue
                if not base:
                    continue
                if base in stable or base in exclude:
                    continue
                insts.append(Instrument(it["instId"], typ, base, q))

        tm = self.fetch_tickers()
        min_turn = {
            "SPOT": float(uni.get("minSpotTurnover24h", 3e5)),
            "SWAP": float(uni.get("minSwapTurnover24h", 5e5)),
        }
        keep: list[Instrument] = []
        for ins in insts:
            t = tm.get(ins.inst_id)
            if t is None:
                continue
            ins.turnover24h = ticker_turnover(t, ins.inst_type.value)
            ins.last_px = float(t.get("last", 0.0) or 0.0)
            if ins.base in force:
                keep.append(ins)
                continue
            if ins.turnover24h >= min_turn.get(ins.inst_type.value, 0.0):
                keep.append(ins)
        keep.sort(key=lambda x: x.turnover24h, reverse=True)
        max_n = int(uni.get("maxInstruments", 500))
        self.universe = keep[:max_n]
        return self.universe

    def _store_instrument(self, ins: Instrument) -> None:
        self.store.save_instrument(
            ins.inst_id,
            {
                "inst_type": ins.inst_type.value,
                "base": ins.base,
                "quote": ins.quote,
                "last_px": ins.last_px,
                "turnover24h": ins.turnover24h,
                "ts": now_ms(),
            },
        )

    def fetch_closed(self, ins: Instrument, have: int) -> list:
        """纯网络拉取（无 DB 写，可放心放线程池）：分页回补已收盘K。

        首次拉满 historyBars；增量时往旧页翻到与本地重叠，
        停机多天也不留缺口。翻页守卫防 API 语义不符死循环。
        """
        today_start = day_start_ms()
        collected: dict[int, object] = {}
        after = None
        for _ in range(6):
            rows = self.client.candles(
                ins.inst_id, self._bar, limit=self._per_req, after=after
            )
            if not rows:
                break
            batch = []
            for r in rows:
                c = parse_candle_row(r)
                if c.ts < today_start:
                    batch.append(c)
            if not batch:
                ts_list = [int(r[0]) for r in rows]
                new_after = min(ts_list)
                if after is not None and new_after >= after:
                    break
                after = new_after
                continue
            for c in batch:
                collected.setdefault(c.ts, c)
            oldest = min(c.ts for c in batch)
            if have == 0:
                if len(collected) >= self._history_bars:
                    break
            elif oldest <= have:
                break
            if after is not None and oldest >= after:
                break
            after = oldest
        return sorted(collected.values(), key=lambda x: x.ts)

    def _remember(self, ins: Instrument) -> bool:
        today_start = day_start_ms()
        lim = self._history_bars
        candles = self.store.load_candles(
            ins.inst_id, self._bar, limit=lim, closed_before_ms=None
        )
        candles = [c for c in candles if c.ts < today_start][-lim:]
        self._candles[ins.inst_id] = candles
        return len(candles) >= self._min_candles

    @staticmethod
    def _db_rows(closed: list) -> list[tuple]:
        return [
            (c.ts, c.open, c.high, c.low, c.close, c.vol, c.vol_ccy) for c in closed
        ]

    def _instrument_info(self, ins: Instrument) -> dict:
        return {
            "inst_type": ins.inst_type.value,
            "base": ins.base,
            "quote": ins.quote,
            "last_px": ins.last_px,
            "turnover24h": ins.turnover24h,
            "ts": now_ms(),
        }

    def sync_candles(self, ins: Instrument) -> bool:
        have = self.store.latest_ts(ins.inst_id, self._bar)
        closed = self.fetch_closed(ins, have)
        if not closed:
            return False
        if have == 0 and len(closed) < self._min_candles:
            return False
        self.store.apply_sync_bundle(
            {ins.inst_id: self._db_rows(closed)},
            self._bar,
            [(ins.inst_id, self._instrument_info(ins))],
            keep=self._candle_keep,
        )
        return self._remember(ins)

    def sync_all(self) -> None:
        ins = self.universe
        workers = max(1, int(self.cfg["data"].get("threads", 8)))
        log.info("syncing candles for %d instruments (workers=%d)", len(ins), workers)
        by_id = {it.inst_id: it for it in ins}
        latest = self.store.latest_map([it.inst_id for it in ins], self._bar)
        haves = {it.inst_id: latest.get(it.inst_id, 0) for it in ins}
        fetched: dict[str, list] = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {
                ex.submit(self.fetch_closed, it, haves[it.inst_id]): it.inst_id
                for it in ins
            }
            for fut in as_completed(futs):
                inst_id = futs[fut]
                try:
                    fetched[inst_id] = fut.result()
                except Exception:
                    log.exception("sync failed: %s", inst_id)
        bundle = {}
        infos = []
        for inst_id, closed in fetched.items():
            if not closed:
                continue
            if haves[inst_id] == 0 and len(closed) < self._min_candles:
                continue
            infos.append((inst_id, self._instrument_info(by_id[inst_id])))
            rows = self._db_rows(closed)
            have = haves[inst_id]
            if have > 0 and rows:
                # 增量过滤：DB 已有区间重复 upsert 是纯浪费（稳态每轮 500×320
                # 行 INSERT OR IGNORE），只保留真正的新 K。
                rows = [r for r in rows if r[0] > have]
            if rows:
                bundle[inst_id] = rows
        if bundle or infos:
            # 全批一次提交：500币×(upsert+prune+instrument)合并为一个事务
            self.store.apply_sync_bundle(
                bundle, self._bar, infos, keep=self._candle_keep
            )
        ok_n = 0
        for inst_id, _info in infos:
            if self._remember(by_id[inst_id]):
                ok_n += 1
        log.info("candle sync done: %d/%d ok", ok_n, len(ins))

    def _candles_for(self, ins: Instrument):
        cands = self._candles.get(ins.inst_id)
        if cands:
            return cands
        today_start = day_start_ms()
        cands = self.store.load_candles(
            ins.inst_id, self._bar, limit=self._history_bars, closed_before_ms=today_start
        )
        self._candles[ins.inst_id] = cands
        return cands

    def analyze_instruments(self, use_tickers: bool = True) -> list:
        today_start = day_start_ms()
        results = []
        for ins in self.universe:
            candles = self._candles_for(ins)
            if len(candles) < self._min_candles:
                continue
            t = self._ticker_map.get(ins.inst_id)
            if t is not None:
                ins.turnover24h = ticker_turnover(t, ins.inst_type.value)
                ins.last_px = float(t.get("last", 0.0) or 0.0)
            if not use_tickers and ins.turnover24h == 0:
                pass
            res = analyze_instrument(
                ins.inst_id,
                ins.inst_type,
                ins.base,
                ins.quote,
                candles,
                ins.last_px if ins.last_px > 0 else None,
                ins.turnover24h,
                today_start,
                self.cfg,
            )
            if res.ok:
                results.append(res)
        return results

    def run_full(self) -> tuple[list, list[dict], list]:
        self.build_universe()
        self.sync_all()
        return self.compute_pool(persist=True)

    def compute_pool(self, persist: bool = False, log_scan_id: str = "") -> tuple[list, list[dict], list]:
        self.fetch_tickers()
        results = self.analyze_instruments(use_tickers=True)
        if persist or self._mem_rows is None:
            prev_state = self.store.get_pool()
        else:
            prev_state = {r["inst_id"]: r for r in self._mem_rows}
        rows, ordered = plan_pool(prev_state, results, now_ms(), self.cfg)
        self._mem_rows = rows
        self._last_ordered = ordered
        if persist:
            self.store.save_pool(rows)
            nw = now_ms()
            ev_rows = []
            for r in results:
                if r.event is None:
                    continue
                bd = r.breakdown or {}
                ev_rows.append(
                    (
                        r.inst_id,
                        {
                            "anchor_ts": bd.get("b2_ts", r.event.ts),
                            "b1_ts": bd.get("b1_ts"),
                            "b1_low": bd.get("b1_low"),
                            "b2_low": bd.get("b2_low"),
                            "status": r.structure_status.value if r.structure_status else "",
                            "ts": nw,
                            "payload": {
                                "score": r.buy2_score,
                                "dist": r.distance_atr,
                                "value": r.trading_value_status.value if r.trading_value_status else "",
                                "stage": r.stage.value if r.stage else "",
                            },
                        },
                    )
                )
            if ev_rows:
                self.store.log_events(ev_rows)
        top = [c for c in ordered if c.in_top]
        cand = [c for c in ordered if not c.in_top and c.res.ok and c.res.event is not None]
        if persist and log_scan_id:
            payload = []
            for c in ordered:
                r = c.res
                payload.append(_result_payload(r, c))
            self.store.log_scan(log_scan_id, "full", payload)
        return results, rows, ordered

    def light_subset_ids(self) -> set[str] | None:
        """轻刷子集：成员全刷 + 候选按 final_score 取前 candidateRefresh 个。

        返回 None 表示尚无历史（首轮），调用方应全量计算。
        新突破信号在轻刷周期可能漏掉，最晚下个全量周期补上
        （fullScanMinutes 上限延迟，日线策略可接受）。
        """
        if not self._last_ordered and not self._mem_rows:
            return None
        ids: set[str] = set()
        for r in self._mem_rows or []:
            if r.get("is_member"):
                ids.add(r["inst_id"])
        cap = int(self.cfg.get("monitor", {}).get("candidateRefresh", 40) or 0)
        n = 0
        for c in self._last_ordered or []:
            iid = c.res.inst_id
            if c.in_top:
                ids.add(iid)
                continue
            try:
                is_cand = c.res.stage == Stage.CANDIDATE
            except AttributeError:
                is_cand = False
            if is_cand and c.res.ok and c.res.event is not None:
                if cap > 0 and n >= cap:
                    continue
                ids.add(iid)
                n += 1
        return ids


def _result_payload(r, c) -> dict:
    return {
        "inst_id": r.inst_id,
        "inst_type": r.inst_type.value,
        "base": r.base,
        "turnover24h": r.turnover24h,
        "price": r.last_price,
        "buy2_low": r.breakdown.get("b2_low") if r.breakdown else None,
        "buy2_ts": r.breakdown.get("b2_ts") if r.breakdown else None,
        "distance_atr": r.distance_atr,
        "gain_pct": r.gain_pct,
        "age_days": r.age_days,
        "struct": r.structure_status.value if r.structure_status else "",
        "value": r.trading_value_status.value if r.trading_value_status else "",
        "stage": r.stage.value if r.stage else "",
        "score": r.buy2_score,
        "final": r.final_score,
        "rank": c.rank,
        "is_member": c.is_member,
        "in_top": c.in_top,
        "change": c.change,
    }
