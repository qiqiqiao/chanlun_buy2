import sys
import unittest

sys.path.insert(0, ".")
from buy2radar.model import Buy1, Buy2Candidate, Candle, StructureStatus
from buy2radar.chan.status import structure_status
from tests.gen import candles_from_path, pattern_path
from tests.test_chan import analyze

DAY = 86400000


def _ev():
    cs = candles_from_path(pattern_path(), seed=7)
    ev, *_ = analyze(cs)
    if ev is None:
        raise AssertionError("pattern produced no event")
    return ev, cs


def _cand(ts, o, h, l, c):
    return Candle(ts, o, h, l, c, 1.0, 1.0 * c)


def _mk_event(l1: float, l2: float, ref: float, n: int = 8) -> Buy2Candidate:
    b1 = Buy1(
        m_idx=1,
        raw_idx=2,
        confirm_raw_idx=3,
        ts=1700000000000 + 2 * DAY,
        confirm_ts=1700000000000 + 3 * DAY,
        low=l1,
        seg_start_top=1,
        seg_start_top_ts=1700000000000,
        seg_start_top_price=ref * 1.3,
    )
    return Buy2Candidate(
        inst_id="",
        b1=b1,
        m_idx=3,
        raw_idx=4,
        confirm_raw_idx=5,
        ts=1700000000000 + 4 * DAY,
        confirm_ts=1700000000000 + 5 * DAY,
        low=l2,
        high_after_b1=ref,
        high_after_b1_m=2,
    )


class DipOrderingTest(unittest.TestCase):
    def _candles_and_event(self, l1=10.0, l2=12.0, ref=18.0):
        ev = _mk_event(l1, l2, ref)
        ts = 1700000000000 + 5 * DAY
        base = [
            _cand(ts - 4 * DAY, 15, 16, 14, 15),
            _cand(ts - 3 * DAY, 15, 13, 11, 12),
            _cand(ts - 2 * DAY, 12, 13, 10, 12),
            _cand(ts - DAY, 12, 13, 11.9, 12.2),
            _cand(ts, 12.2, 12.5, 12.0, 12.1),
        ]
        return ev, base, ts

    def test_recovery_only_counts_after_dip(self):
        ev, base, ts = self._candles_and_event()
        ref = 18.0
        l2 = 12.0
        seq = [
            _cand(ts + DAY, 17.9, 17.82, 11.2, 12.1),
            _cand(ts + 2 * DAY, 12.1, 17.5, 12.0, 12.2),
            _cand(ts + 3 * DAY, 12.2, 17.9, 12.1, 12.3),
        ]
        candles = base + seq
        st, meta = structure_status(ev, candles, len(candles) - 1)
        self.assertEqual(st, StructureStatus.WEAKENING)
        self.assertIn("max_high_after_dip", meta)
        self.assertLess(meta["max_high_after_dip"], ref)

    def test_recovery_after_dip_confirms(self):
        ev, base, ts = self._candles_and_event()
        l2, ref = 12.0, 18.0
        seq = [
            _cand(ts + DAY, 12.0, 12.4, 11.5, 12.05),
            _cand(ts + 2 * DAY, 12.05, 18.7, 12.0, 18.4),
        ]
        st, meta = structure_status(ev, base + seq, len(base) + len(seq) - 1)
        self.assertEqual(st, StructureStatus.CONFIRMED)
        self.assertTrue(meta.get("recovered"))
        self.assertGreater(meta["max_high_after_dip"], ref)

    def test_deep_dip_cannot_recover_with_cap(self):
        ev, base, ts = self._candles_and_event(l1=10.0, l2=12.0, ref=18.0)
        l2, ref = 12.0, 18.0
        anchor = 0.6
        seq = [
            _cand(ts + DAY, 12.0, 12.4, 10.2, 10.5),
            _cand(ts + 2 * DAY, 10.5, ref * 1.1, 10.4, ref * 1.05),
        ]
        st, meta = structure_status(
            ev,
            base + seq,
            len(base) + len(seq) - 1,
            dip_atr_cap=1.0,
            anchor_atr=anchor,
        )
        self.assertEqual(st, StructureStatus.WEAKENING)
        self.assertGreater(meta["max_high_after_dip"], ref)


class ClosedOnlyTest(unittest.TestCase):
    def test_structure_ignores_above_ref_intraday(self):
        l1, l2, ref = 10.0, 12.0, 18.0
        ev = _mk_event(l1, l2, ref)
        ts = 1700000000000 + 5 * DAY
        tail = [
            _cand(ts + DAY, 12.5, 14.0, 12.1, 13.2),
            _cand(ts + 2 * DAY, 13.2, 20.0, 12.2, 13.5),
            _cand(ts + 3 * DAY, 13.5, 14.0, 12.3, 12.9),
        ]
        candles = [_cand(ts - 3 * DAY, 12, 13, 11, 12)] + tail
        st, meta = structure_status(ev, candles, len(candles) - 1)
        self.assertEqual(st, StructureStatus.VALID)
        self.assertLess(candles[-1].close, ref)

    def test_closed_break_confirms(self):
        l1, l2, ref = 10.0, 12.0, 18.0
        ev = _mk_event(l1, l2, ref)
        ts = 1700000000000 + 5 * DAY
        candles = [
            _cand(ts + DAY, 12.5, 14.0, 12.1, 13.2),
            _cand(ts + 2 * DAY, 13.2, 14.0, 12.5, 13.8),
            _cand(ts + 3 * DAY, 13.8, 19.0, 13.7, 18.6),
        ]
        st, _ = structure_status(ev, candles, len(candles) - 1)
        self.assertEqual(st, StructureStatus.CONFIRMED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
