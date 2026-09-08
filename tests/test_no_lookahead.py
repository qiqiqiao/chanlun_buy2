"""无未来函数回归测试：未来数据不得改写已发布的历史结论。

策略承诺“只用已收盘K”——追加新K后，已确认的分型 / Buy2锚点 /
as-of分析结果必须保持不变。本文件从 chan 层到 store+analysis 全链路锁定该性质。
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, ".")

from buy2radar.analysis import analyze_instrument
from buy2radar.chan.core import build_bis, find_fractals, merge_bars
from buy2radar.chan.detect import detect_buy2
from buy2radar.config import DEFAULT_CONFIG as CFG
from buy2radar.model import InstType
from buy2radar.store import Store
from tests.gen import candles_from_path, random_path

DAY = 86400000
NOW = int(time.time() * 1000)
SEEDS = (3, 11, 42)
CUTS = (230, 260, 290)

DETECT_CFG = {
    "waveReboundFrac": 0.5,
    "minBounceAtr": 0.6,
    "requireBuy1Divergence": False,
}


def _series(seed: int, n: int = 300):
    return candles_from_path(
        random_path(n, seed), seed=seed, start_ts=NOW - n * DAY
    )


def _anchor(ev):
    if ev is None:
        return None
    return (
        ev.b1.raw_idx,
        ev.b1.confirm_raw_idx,
        round(ev.b1.low, 9),
        ev.raw_idx,
        ev.confirm_raw_idx,
        round(ev.low, 9),
        round(ev.high_after_b1, 9),
    )


class FractalPrefixStableTest(unittest.TestCase):
    def test_append_does_not_rewrite_confirmed(self):
        """追加5根K后，已确认区的分型集合必须逐字相同。"""
        checked = 0
        for seed in SEEDS:
            cs = _series(seed)
            for cut in CUTS:
                pre, full = cs[:cut], cs[: cut + 5]
                ms_pre, ms_full = merge_bars(pre), merge_bars(full)
                lim = len(ms_pre) - 2  # ms_pre 中已收盘确认组的上确界
                kp = [
                    (f.kind, f.m_idx, f.raw_idx, round(f.price, 9))
                    for f in find_fractals(ms_pre)
                    if f.m_idx + 1 <= lim
                ]
                kf = [
                    (f.kind, f.m_idx, f.raw_idx, round(f.price, 9))
                    for f in find_fractals(ms_full)
                    if f.m_idx + 1 <= lim
                ]
                self.assertEqual(kp, kf, f"seed={seed} cut={cut}")
                checked += len(kp)
        self.assertGreater(checked, 100, "vacuous: no fractals checked")


class DetectPrefixStableTest(unittest.TestCase):
    def test_buy2_anchor_stable(self):
        """同一确认上限下，Buy2锚点不受后续K线影响。"""
        seen_event = False
        for seed in SEEDS:
            cs = _series(seed)
            for cut in CUTS:
                pre, full = cs[:cut], cs[: cut + 5]
                cap = len(pre) - 15
                cfg = dict(DETECT_CFG, maxConfirmRaw=cap)
                ms_pre = merge_bars(pre)
                ev1, _ = detect_buy2(
                    ms_pre,
                    build_bis(ms_pre, find_fractals(ms_pre), 2),
                    pre,
                    [0.0] * len(pre),
                    cfg,
                )
                ms_full = merge_bars(full)
                ev2, _ = detect_buy2(
                    ms_full,
                    build_bis(ms_full, find_fractals(ms_full), 2),
                    full,
                    [0.0] * len(full),
                    cfg,
                )
                self.assertEqual(_anchor(ev1), _anchor(ev2), f"seed={seed} cut={cut}")
                seen_event = seen_event or ev1 is not None
        self.assertTrue(seen_event, "vacuous: no buy2 event found")


class AnalysisAsOfStableTest(unittest.TestCase):
    def _analyze(self, candles, today_start):
        return analyze_instrument(
            "T-USDT",
            InstType.SPOT,
            "T",
            "USDT",
            candles,
            candles[-1].close,
            candles[-1].vol_ccy,
            today_start,
            CFG,
        )

    def _snapshot(self, res):
        return (
            res.ok,
            res.reason,
            round(res.buy2_score, 9),
            str(res.structure_status),
            str(res.trading_value_status),
            res.distance_atr,
            _anchor(res.event),
        )

    def test_future_rows_in_db_do_not_leak(self):
        """生产路径（store按收盘过滤→analyze）：DB里多存未来K，
        同一as-of时点的结论必须与“DB里没有未来K”时完全一致。"""
        cs = _series(11)
        cut = 250
        today_start = cs[cut - 1].ts + DAY
        tmp1, tmp2 = tempfile.mkdtemp(), tempfile.mkdtemp()
        db_pre = Store({"state": {"dir": tmp1, "dbFile": "a.db"}})
        db_full = Store({"state": {"dir": tmp2, "dbFile": "b.db"}})
        db_pre.upsert_candles(
            "T-USDT",
            "1Dutc",
            [(c.ts, c.open, c.high, c.low, c.close, c.vol, c.vol_ccy) for c in cs[:cut]],
        )
        db_full.upsert_candles(
            "T-USDT",
            "1Dutc",
            [(c.ts, c.open, c.high, c.low, c.close, c.vol, c.vol_ccy) for c in cs],
        )
        list_pre = db_pre.load_candles("T-USDT", "1Dutc", limit=320, closed_before_ms=today_start)
        list_full = db_full.load_candles("T-USDT", "1Dutc", limit=320, closed_before_ms=today_start)
        self.assertEqual([c.ts for c in list_pre], [c.ts for c in list_full])
        self.assertEqual(len(list_pre), cut)
        res_pre = self._analyze(list_pre, today_start)
        res_full = self._analyze(list_full, today_start)
        self.assertEqual(self._snapshot(res_pre), self._snapshot(res_full))

    def test_deterministic(self):
        cs = _series(3)[:260]
        today_start = cs[-1].ts + DAY
        a = self._analyze(cs, today_start)
        b = self._analyze(cs, today_start)
        self.assertEqual(self._snapshot(a), self._snapshot(b))


if __name__ == "__main__":
    unittest.main(verbosity=2)
