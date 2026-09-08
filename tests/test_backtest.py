"""二级回测 + turnover_24h_at：用合成数据验证统计链路不断、口径正确。"""

import copy
import sys
import time
import unittest

sys.path.insert(0, ".")

from buy2radar.backtest import _run_pool_backtest, _walk_instrument
from buy2radar.config import DEFAULT_CONFIG as CFG
from buy2radar.indicators import bar_minutes, turnover_24h_at
from buy2radar.model import Candle
from tests.gen import candles_from_path, random_path

DAY = 86400000


def _c(ts, vol, vol_ccy, close):
    return Candle(ts, close, close * 1.01, close * 0.99, close, vol, vol_ccy)


class Turnover24hTest(unittest.TestCase):
    def setUp(self):
        self.cs = [_c(i * DAY, 10.0, 1000.0 + i, 100.0) for i in range(10)]

    def test_daily_bar_single_candle(self):
        self.assertAlmostEqual(turnover_24h_at(self.cs, 5, "1Dutc"), 1005.0)
        self.assertAlmostEqual(
            turnover_24h_at(self.cs, 5, "1Dutc", is_swap=True), 10.0 * 100.0
        )

    def test_intraday_bar_accumulates(self):
        self.assertEqual(bar_minutes("4H"), 240)
        self.assertEqual(bar_minutes("1Dutc"), 1440)
        # 4H → 最近6根
        expect = sum(1000.0 + i for i in range(4, 10))
        self.assertAlmostEqual(turnover_24h_at(self.cs, 9, "4H"), expect)

    def test_no_lookahead_and_bounds(self):
        a = turnover_24h_at(self.cs, 5, "4H")
        mutated = list(self.cs)
        mutated[6] = _c(6 * DAY, 999.0, 999999.0, 500.0)
        self.assertAlmostEqual(turnover_24h_at(mutated, 5, "4H"), a)
        self.assertEqual(turnover_24h_at([], 0, "1Dutc"), 0.0)
        self.assertEqual(turnover_24h_at(self.cs, -1, "1Dutc"), 0.0)


class PoolBacktestTest(unittest.TestCase):
    def test_signal_to_portfolio_pipeline(self):
        cfg = copy.deepcopy(CFG)
        cfg["pool"]["minTurnoverPool"] = 0.0
        cfg["score"]["enterScore"] = 55.0
        cfg["score"]["exitScore"] = 40.0
        now = int(time.time() * 1000)
        snaps_by_inst, metas = {}, {}
        for seed in range(3):
            cs = candles_from_path(
                random_path(300, seed), seed=seed, start_ts=now - 300 * DAY
            )
            iid = "S%d-SPOT" % seed
            job = {
                "candles": cs,
                "cfg": cfg,
                "fwd_days": [5],
                "warmup": 220,
                "inst_id": iid,
                "inst_type": "SPOT",
                "base": "S%d" % seed,
                "quote": "USDT",
                "is_swap": False,
            }
            _, _, snaps = _walk_instrument(job)
            self.assertTrue(len(snaps) > 0)
            snaps_by_inst[iid] = snaps
            metas[iid] = {"inst_type": "SPOT", "base": "S%d" % seed}
        m = _run_pool_backtest(cfg, snaps_by_inst, metas)
        for k in (
            "dates",
            "pool_days",
            "entries",
            "ever_top",
            "trades",
            "equity",
            "max_dd",
            "cost_drag",
        ):
            self.assertIn(k, m)
        self.assertGreater(m["dates"], 0)
        self.assertGreater(m["entries"], 0)
        self.assertGreater(len(m["trades"]), 0)
        self.assertGreater(m["equity"], 0.0)
        self.assertGreaterEqual(m["max_dd"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
