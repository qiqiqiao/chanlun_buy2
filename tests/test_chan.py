import sys
import unittest

sys.path.insert(0, ".")
from buy2radar.indicators import atr as atr_indicator, macd
from buy2radar.chan.core import build_bis, find_fractals, merge_bars
from buy2radar.chan.detect import detect_buy2
from tests.gen import candles_from_path, pattern_path, random_path


def analyze(cs, require_div=False):
    closes = [c.close for c in cs]
    dif, dea, hist = macd(closes)
    atr = atr_indicator(cs)
    ms = merge_bars(cs)
    fr = find_fractals(ms)
    bis = build_bis(ms, fr, 2)
    ev, meta = detect_buy2(
        ms,
        bis,
        cs,
        hist,
        {
            "maxAnchorRaw": len(cs) - 2,
            "atr": atr,
            "waveReboundFrac": 0.5,
            "minBounceAtr": 0.6,
            "requireBuy1Divergence": require_div,
        },
    )
    return ev, ms, fr, bis, atr


class ChanCoreTest(unittest.TestCase):
    def test_merge_containment(self):
        path = [100] * 12
        cs = candles_from_path(path, seed=1)
        ms = merge_bars(cs)
        self.assertLessEqual(len(ms), len(cs))
        for m in ms:
            self.assertGreaterEqual(m.high, m.low)

    def test_bis_alternate(self):
        cs = candles_from_path(random_path(200, 4))
        ms = merge_bars(cs)
        fr = find_fractals(ms)
        bis = build_bis(ms, fr, 2)
        kinds = [b.up for b in bis]
        for a, b in zip(kinds, kinds[1:]):
            self.assertNotEqual(a, b)

    def test_pattern_yields_buy2_higher_low(self):
        cs = candles_from_path(pattern_path(), seed=7)
        ev, *_ = analyze(cs)
        self.assertIsNotNone(ev)
        self.assertGreater(ev.low, ev.b1.low)
        self.assertLess(ev.raw_idx, len(cs) - 1)
        self.assertGreater(ev.b1.raw_idx, 0)

    def test_random_walk_no_crash(self):
        for seed in range(10):
            cs = candles_from_path(random_path(180, seed))
            ev, *_ = analyze(cs)
            if ev is not None:
                self.assertGreater(ev.low, ev.b1.low)
                self.assertLess(ev.raw_idx, len(cs) - 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
