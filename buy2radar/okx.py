from __future__ import annotations

import http.client
import json
import random
import threading
import time
import urllib.parse

from .model import Candle

BASE_URL = "https://www.okx.com"
OKX_HOST = "www.okx.com"


def parse_candle_row(row: list) -> Candle:
    quote = float(row[7]) if len(row) > 7 else float(row[6])
    return Candle(
        ts=int(row[0]),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        vol=float(row[5]),
        vol_ccy=quote,
    )


def ticker_turnover(t: dict, inst_type: str) -> float:
    if inst_type == "SPOT":
        return float(t.get("volCcy24h", 0.0) or 0.0)
    last = float(t.get("last", 0.0) or 0.0)
    base = float(t.get("volCcy24h", 0.0) or 0.0)
    return base * last


RETRY_OKX_CODES = {"50011", "50010"}


class _RateLimit(RuntimeError):
    def __init__(self, code: str, msg: str):
        super().__init__(f"okx rate limit code={code} {msg}")


class OkxError(RuntimeError):
    pass


class OkxClient:
    def __init__(self, cfg: dict, logger=None):
        self.cfg = cfg.get("data", {})
        self._interval = float(self.cfg.get("requestIntervalSec", 0.06))
        self._retries = int(self.cfg.get("retries", 3))
        self._timeout = float(self.cfg.get("timeoutSec", 20))
        self._lock = threading.Lock()
        self._last_ts = 0.0
        # 线程本地长连接：同一线程复用一条 HTTPS 连接，避免每次
        # urlopen 重建 TCP+TLS（首轮 500 币 ~1000 请求的主要开销）。
        # http.client 连接非线程安全，故按线程隔离而非全局共享。
        self._local = threading.local()
        if logger is not None:
            self._log = logger
        else:
            import logging

            self._log = logging.getLogger("okx")

    def _throttle(self):
        with self._lock:
            wait = self._interval - (time.monotonic() - self._last_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_ts = time.monotonic()

    def _backoff(self, attempt: int, rate_limited: bool) -> None:
        # 指数退避 + 抖动：多线程同时撞限流时错开重试，防“惊群”齐步踩线。
        # 正常限流(429/50011/50010): 0.5/1/2/4…上限8s；网络抖动类上限4s。
        cap = 8.0 if rate_limited else 4.0
        base = min(cap, (0.5 if rate_limited else 0.3) * (2**attempt))
        time.sleep(base + random.uniform(0.0, base * 0.5))

    def _get_conn(self) -> http.client.HTTPSConnection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = http.client.HTTPSConnection(OKX_HOST, timeout=self._timeout)
            self._local.conn = conn
        return conn

    def _drop_conn(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
            self._local.conn = None

    def get(self, path: str, params: dict | None = None) -> list:
        query = ""
        if params:
            query = "?" + urllib.parse.urlencode(
                {k: str(v) for k, v in params.items() if v is not None}
            )
        full_path = path + query
        url = BASE_URL + path + query
        last_err: Exception | None = None
        for attempt in range(self._retries + 1):
            self._throttle()
            try:
                conn = self._get_conn()
                conn.request(
                    "GET", full_path, headers={"User-Agent": "buy2radar/1.0"}
                )
                resp = conn.getresponse()
                raw = resp.read().decode("utf-8")
                if resp.status == 429:
                    last_err = _RateLimit("429", "http too many requests")
                    try:
                        ra = resp.getheader("Retry-After")
                        if ra is not None:
                            time.sleep(min(30.0, float(ra)))
                            continue
                    except (TypeError, ValueError):
                        pass
                    self._backoff(attempt, rate_limited=True)
                    continue
                if resp.status != 200:
                    last_err = OkxError(f"http status={resp.status}")
                    self._drop_conn()
                    self._backoff(attempt, rate_limited=False)
                    continue
                data = json.loads(raw)
                if data.get("code") != "0":
                    code = str(data.get("code"))
                    if code in RETRY_OKX_CODES:
                        raise _RateLimit(code, str(data.get("msg")))
                    raise OkxError(f"code={code} msg={data.get('msg')}")
                return data.get("data", [])
            except _RateLimit as e:
                last_err = e
                self._backoff(attempt, rate_limited=True)
            except (http.client.HTTPException, OSError, json.JSONDecodeError) as e:
                last_err = e
                self._drop_conn()
                self._backoff(attempt, rate_limited=False)
            except OkxError:
                raise
        hint = ""
        if isinstance(last_err, OSError):
            import errno

            code = getattr(last_err, "errno", None)
            if code in (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ECONNREFUSED):
                hint = "；本机网络不可达，检查外网/代理/防火墙"
            elif "Name or service" in str(last_err):
                hint = "；DNS 解析失败，当前环境可能无外网访问（沙箱/容器常见）"
        raise OkxError(f"GET failed {url} : {last_err}{hint}")

    def instruments(self, inst_type: str) -> list[dict]:
        return self.get(
            "/api/v5/public/instruments", {"instType": inst_type, "instId": None}
        )

    def tickers(self, inst_type: str) -> list[dict]:
        return self.get("/api/v5/market/tickers", {"instType": inst_type})

    def candles(
        self,
        inst_id: str,
        bar: str,
        limit: int = 300,
        after: int | None = None,
        before: int | None = None,
    ) -> list[list]:
        raw = self.get(
            "/api/v5/market/candles",
            {"instId": inst_id, "bar": bar, "limit": limit, "after": after, "before": before},
        )
        return raw
