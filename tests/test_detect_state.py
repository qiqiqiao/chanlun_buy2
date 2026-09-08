"""Buy2 状态机：默认行为（钩全关）= 历史逻辑；开钩只会更严格。"""

import sys
import unittest

sys.path.insert(0, ".")

from buy2radar.chan.core import build_bis, find_fractals, merge_bars
from buy2radar.chan.detect import B2State, detect_buy2
from buy2radar.indicators import atr as atr_indicator
from tests.gen import candles_from_path, pattern_path


def _setup():
    cs = candles_from_path(pattern_path(), seed=7)
    ms = merge_bars(cs)
    bis = build_bis(ms, find_fractals(ms), 2)
    base = {
        "maxConfirmRaw": len(cs) - 2,
        "waveReboundFrac": 0.5,
        "minBounceAtr": 0.6,
        "requireBuy1Divergence": False,
        "atr": atr_indicator(cs),
    }
    return cs, ms, bis, base


class StateMachineTest(unittest.TestCase):
    def test_default_walks_search_bounce_pullback_emitted(self):
        cs, ms, bis, base = _setup()
        ev, meta = detect_buy2(ms, bis, cs, [0.0] * len(cs), dict(base))
        self.assertIsNotNone(ev)
        self.assertGreater(ev.low, ev.b1.low)
        states = [t["s"] for t in meta.get("state_trace", [])]
        for want in (
            B2State.AWAIT_BOUNCE.value,
            B2State.AWAIT_PULLBACK.value,
            B2State.EMITTED.value,
        ):
            self.assertIn(want, states)
        self.assertEqual(meta.get("final_state"), B2State.EMITTED.value)

    def test_timeout_expires_wave(self):
        cs, ms, bis, base = _setup()
        ev, meta = detect_buy2(ms, bis, cs, [0.0] * len(cs), dict(base, maxBarsB1toB2=1))
        self.assertIsNone(ev)
        states = [t["s"] for t in meta.get("state_trace", [])]
        self.assertIn(B2State.EXPIRED.value, states)

    def test_gap_hook_can_veto(self):
        cs, ms, bis, base = _setup()
        ev, _ = detect_buy2(ms, bis, cs, [0.0] * len(cs), dict(base, maxB2GapAtr=1e-9))
        self.assertIsNone(ev)


if __name__ == "__main__":
    unittest.main(verbosity=2)
