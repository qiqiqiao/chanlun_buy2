from __future__ import annotations

import json
import os
import sqlite3
import threading

from .model import Candle
from .util import now_ms


class Store:
    def __init__(self, cfg: dict):
        state_dir = cfg["state"]["dir"]
        os.makedirs(state_dir, exist_ok=True)
        db = os.path.join(state_dir, cfg["state"]["dbFile"])
        self.conn = sqlite3.connect(db, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            c = self.conn
            c.execute(
                "CREATE TABLE IF NOT EXISTS candles("
                "inst_id TEXT NOT NULL, bar TEXT NOT NULL, ts INTEGER NOT NULL, "
                "o REAL,h REAL,l REAL,c REAL,vol REAL,vol_ccy REAL, "
                "PRIMARY KEY(inst_id,bar,ts))"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS instruments("
                "inst_id TEXT PRIMARY KEY, inst_type TEXT, base TEXT, quote TEXT, "
                "last_px REAL, turnover24h REAL, ts INTEGER)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS events("
                "inst_id TEXT NOT NULL, anchor_ts INTEGER NOT NULL, "
                "b1_ts INTEGER, b1_low REAL, b2_low REAL, "
                "status TEXT, payload TEXT, ts INTEGER, "
                "PRIMARY KEY(inst_id, anchor_ts))"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS pool_state("
                "inst_id TEXT PRIMARY KEY, is_member INTEGER DEFAULT 0, "
                "in_top INTEGER DEFAULT 0, entered_ms INTEGER, removed_ms INTEGER, "
                "last_score REAL, last_final REAL, ts INTEGER)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS scans("
                "scan_id TEXT, ts INTEGER, kind TEXT, payload TEXT)"
            )
            c.commit()

    def upsert_candles(self, inst_id: str, bar: str, rows: list[tuple]) -> None:
        if not rows:
            return
        with self._lock:
            self.conn.executemany(
                "INSERT OR IGNORE INTO candles(inst_id,bar,ts,o,h,l,c,vol,vol_ccy) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                [(inst_id, bar, *r) for r in rows],
            )
            self.conn.commit()

    def latest_ts(self, inst_id: str, bar: str) -> int:
        with self._lock:
            cur = self.conn.execute(
                "SELECT MAX(ts) AS m FROM candles WHERE inst_id=? AND bar=?",
                (inst_id, bar),
            )
            row = cur.fetchone()
            return row["m"] if row and row["m"] else 0

    def latest_map(self, inst_ids: list[str], bar: str) -> dict[str, int]:
        """一次查询多币最新 ts，避免 sync 前 500 次顺序往返。"""
        if not inst_ids:
            return {}
        with self._lock:
            out: dict[str, int] = {i: 0 for i in inst_ids}
            chunk = 200
            for i in range(0, len(inst_ids), chunk):
                part = inst_ids[i : i + chunk]
                q = (
                    "SELECT inst_id, MAX(ts) AS m FROM candles "
                    "WHERE bar=? AND inst_id IN (%s) GROUP BY inst_id"
                    % ",".join("?" * len(part))
                )
                for row in self.conn.execute(q, [bar, *part]):
                    if row["m"]:
                        out[row["inst_id"]] = row["m"]
            return out

    def load_candles(
        self, inst_id: str, bar: str, limit: int, closed_before_ms: int | None = None
    ) -> list[Candle]:
        q = "SELECT ts,o,h,l,c,vol,vol_ccy FROM candles WHERE inst_id=? AND bar=?"
        args: list = [inst_id, bar]
        if closed_before_ms is not None:
            q += " AND ts < ?"
            args.append(closed_before_ms)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            cur = self.conn.execute(q, args)
            rows = cur.fetchall()
        rows.reverse()
        return [
            Candle(
                ts=r["ts"],
                open=r["o"],
                high=r["h"],
                low=r["l"],
                close=r["c"],
                vol=r["vol"],
                vol_ccy=r["vol_ccy"],
            )
            for r in rows
        ]

    def save_instrument(self, inst_id: str, info: dict) -> None:
        self.save_instruments([(inst_id, info)])

    def save_instruments(self, items: list[tuple[str, dict]]) -> None:
        if not items:
            return
        with self._lock:
            self.conn.executemany(
                "INSERT OR REPLACE INTO instruments(inst_id,inst_type,base,quote,"
                "last_px,turnover24h,ts) VALUES(?,?,?,?,?,?,?)",
                [
                    (
                        inst_id,
                        info.get("inst_type"),
                        info.get("base"),
                        info.get("quote"),
                        info.get("last_px", 0.0),
                        info.get("turnover24h", 0.0),
                        info.get("ts", 0),
                    )
                    for inst_id, info in items
                ],
            )
            self.conn.commit()

    def log_event(self, inst_id: str, ev: dict) -> None:
        self.log_events([(inst_id, ev)])

    def log_events(self, items: list[tuple[str, dict]]) -> None:
        if not items:
            return
        with self._lock:
            self.conn.executemany(
                "INSERT OR REPLACE INTO events(inst_id,anchor_ts,b1_ts,b1_low,b2_low,"
                "status,payload,ts) VALUES(?,?,?,?,?,?,?,?)",
                [
                    (
                        inst_id,
                        ev.get("anchor_ts", 0),
                        ev.get("b1_ts"),
                        ev.get("b1_low"),
                        ev.get("b2_low"),
                        ev.get("status"),
                        json.dumps(ev.get("payload", {})),
                        ev.get("ts", 0),
                    )
                    for inst_id, ev in items
                ],
            )
            self.conn.commit()

    def apply_sync_bundle(
        self,
        candles_map: dict[str, list[tuple]],
        bar: str,
        instruments: list[tuple[str, dict]] | None = None,
        keep: int = 1000,
    ) -> None:
        """一次同步事务：多币 K 线 upsert + instrument 快照 + 裁剪，一次 commit。

        sync_all 全批（500币）只产生一次提交；线程池内只做网络拉取不碰 DB。
        """
        with self._lock:
            before = self.conn.total_changes
            if candles_map:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO candles(inst_id,bar,ts,o,h,l,c,vol,vol_ccy) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    [
                        (inst_id, bar, *r)
                        for inst_id, rows in (candles_map or {}).items()
                        for r in rows
                        if r
                    ],
                )
            inserted = self.conn.total_changes - before
            if instruments:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO instruments(inst_id,inst_type,base,quote,"
                    "last_px,turnover24h,ts) VALUES(?,?,?,?,?,?,?)",
                    [
                        (
                            inst_id,
                            info.get("inst_type"),
                            info.get("base"),
                            info.get("quote"),
                            info.get("last_px", 0.0),
                            info.get("turnover24h", 0.0),
                            info.get("ts", 0),
                        )
                        for inst_id, info in instruments
                    ],
                )
            if keep > 0 and candles_map and inserted > 0:
                # 按需裁剪：零新行时直接跳过（稳态同步多为重复 upsert）；
                # 否则先 GROUP BY 找出真正超限的币，只对它们 DELETE。
                # 原实现对 500 个币无条件逐个 DELETE...NOT IN，即使 count<=keep。
                ids = list(candles_map)
                over: set[str] = set()
                for i in range(0, len(ids), 200):
                    part = ids[i : i + 200]
                    q = (
                        "SELECT inst_id, COUNT(*) AS n FROM candles "
                        "WHERE bar=? AND inst_id IN (%s) GROUP BY inst_id"
                        % ",".join("?" * len(part))
                    )
                    for row in self.conn.execute(q, [bar, *part]):
                        if row["n"] > keep:
                            over.add(row["inst_id"])
                for inst_id in over:
                    self.conn.execute(
                        "DELETE FROM candles WHERE inst_id=? AND bar=? AND ts NOT IN "
                        "(SELECT ts FROM candles WHERE inst_id=? AND bar=? "
                        "ORDER BY ts DESC LIMIT ?)",
                        (inst_id, bar, inst_id, bar, keep),
                    )
            self.conn.commit()

    def prune_scans(self, keep: int = 20) -> None:
        if keep <= 0:
            return
        with self._lock:
            self.conn.execute(
                "DELETE FROM scans WHERE rowid NOT IN "
                "(SELECT rowid FROM scans ORDER BY ts DESC LIMIT ?)",
                (keep,),
            )
            self.conn.commit()

    def get_pool(self) -> dict:
        with self._lock:
            cur = self.conn.execute("SELECT * FROM pool_state")
            rows = cur.fetchall()
        return {r["inst_id"]: dict(r) for r in rows}

    def save_pool(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._lock:
            self.conn.executemany(
                "INSERT INTO pool_state(inst_id,is_member,in_top,entered_ms,"
                "removed_ms,last_score,last_final,ts) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(inst_id) DO UPDATE SET is_member=excluded.is_member,"
                "in_top=excluded.in_top,entered_ms=excluded.entered_ms,"
                "removed_ms=excluded.removed_ms,last_score=excluded.last_score,"
                "last_final=excluded.last_final,ts=excluded.ts",
                [
                    (
                        r["inst_id"],
                        1 if r.get("is_member") else 0,
                        1 if r.get("in_top") else 0,
                        r.get("entered_ms"),
                        r.get("removed_ms"),
                        r.get("last_score", 0.0),
                        r.get("last_final", 0.0),
                        r.get("ts", 0),
                    )
                    for r in rows
                ],
            )
            self.conn.commit()

    def log_scan(self, scan_id: str, kind: str, payload: list[dict]) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO scans(scan_id,ts,kind,payload) VALUES(?,?,?,?)",
                (scan_id, now_ms(), kind, json.dumps(payload)),
            )
            self.conn.execute(
                "DELETE FROM scans WHERE rowid NOT IN "
                "(SELECT rowid FROM scans ORDER BY ts DESC LIMIT 20)"
            )
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()
