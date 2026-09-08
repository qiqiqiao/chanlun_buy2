"""指标口径锁定：换实现/换库时这些值不许悄悄漂移。"""

import sys
import unittest

sys.path.insert(0, ".")

from buy2radar.config import DEFAULT_CONFIG as CFG
from buy2radar.indicators import atr as atr_indicator, ema, indicator_warmup
from buy2radar.model import Candle


class EmaAtrConventionTest(unittest.TestCase):
    def test_ema_sma_seed(self):
        # period=3: seed=(1+2+3)/3=2 @idx2, k=0.5 → idx3=3.0, idx4=4.0
        self.assertEqual(ema([1, 2, 3, 4, 5], 3), [2, 2, 2, 3.0, 4.0])

    def test_atr_wilder_flat(self):
        cs = [Candle(i, 10.5, 11.0, 10.0, 10.5, 1.0, 10.0) for i in range(6)]
        self.assertEqual(atr_indicator(cs, 3), [1.0] * 6)

    def test_atr_wilder_step(self):
        # TR: c0=2, c1=max(2,|12-10|=2,|10-10|=0)=2, c2: close1=12,h14 l12 → max(2,2,0)=2
        cs = [
            Candle(0, 10, 11, 9, 10, 1, 1),
            Candle(1, 10, 12, 10, 12, 1, 1),
            Candle(2, 12, 14, 12, 14, 1, 1),
        ]
        out = atr_indicator(cs, 2)
        # seed=(2+2)/2=2 @idx1；idx2=(2*1+2)/2=2
        self.assertEqual(out, [2.0, 2.0, 2.0])

    def test_warmup_covered_by_min_candles(self):
        need = indicator_warmup(
            CFG["chan"]["macdSlow"], CFG["chan"]["macdSignal"], CFG["chan"]["atrPeriod"]
        )
        self.assertLessEqual(need, CFG["data"]["minCandles"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
