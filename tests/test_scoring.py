import sys
import unittest

sys.path.insert(0, ".")
from buy2radar.model import Candle, StructureStatus, TradingValueStatus
from buy2radar.scoring import (
    metrics_from_event,
    structure_score,
    trading_value_status,
)
from tests.gen import candles_from_path, pattern_path
from tests.test_chan import analyze
from buy2radar.chan.status import structure_status


def _event():
    cs = candles_from_path(pattern_path(), seed=7)
    ev, *_ = analyze(cs)
    return ev, cs


class ScoringTest(unittest.TestCase):
    def test_structure_score_map(self):
        self.assertEqual(structure_score("CONFIRMED", {}), 100.0)
        self.assertEqual(structure_score("VALID", {}), 78.0)
        self.assertEqual(structure_score("WEAKENING", {}), 40.0)
        self.assertEqual(structure_score("INVALID", {}), 0.0)

    def test_value_status_by_distance(self):
        cfg = {
            "zones": {"optimalAtr": 0.5, "goodAtr": 1.0, "watchAtr": 1.5, "maxAtr": 2.0},
            "tradingValueMinScore": 40,
        }
        self.assertEqual(
            trading_value_status(0.3, 80, cfg), TradingValueStatus.EXCELLENT
        )
        self.assertEqual(trading_value_status(0.7, 80, cfg), TradingValueStatus.GOOD)
        self.assertEqual(trading_value_status(1.2, 80, cfg), TradingValueStatus.WATCH)
        self.assertEqual(
            trading_value_status(1.8, 80, cfg), TradingValueStatus.EXTENDED
        )
        self.assertEqual(trading_value_status(2.5, 80, cfg), TradingValueStatus.NO_VALUE)
        self.assertEqual(trading_value_status(0.5, 20, cfg), TradingValueStatus.NO_VALUE)


class StatusTest(unittest.TestCase):
    def test_invalid_when_buy1_broken(self):
        ev, cs = _event()
        if ev is None:
            self.skipTest("no event in pattern")
        broken = cs + [
            Candle(cs[-1].ts + i * 86400000, 1, 2, ev.b1.low * 0.97, ev.b1.low * 0.98, 1, 1)
            for i in range(1, 4)
        ]
        st, meta = structure_status(ev, broken, len(broken) - 1)
        self.assertEqual(st, StructureStatus.INVALID)

    def test_confirm_above_ref(self):
        ev, cs = _event()
        if ev is None:
            self.skipTest("no event in pattern")
        hi = ev.high_after_b1
        extra = []
        p = hi * 1.02
        for i in range(1, 5):
            extra.append(
                Candle(
                    cs[-1].ts + i * 86400000,
                    p * 0.99,
                    p * 1.02,
                    p * 0.98,
                    p,
                    1,
                    1,
                )
            )
        st, meta = structure_status(ev, cs + extra, len(cs) + 3)
        self.assertEqual(st, StructureStatus.CONFIRMED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
