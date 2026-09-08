import sys
import unittest

sys.path.insert(0, ".")
from buy2radar.config import DEFAULT_CONFIG as C
from buy2radar.model import AnalysisResult, InstType, Stage, StructureStatus
from buy2radar.pool import plan_pool

HOUR = 3600000


def mk(inst, base, score, dist, turn=3e6, struct=StructureStatus.VALID):
    r = AnalysisResult(
        inst_id=inst,
        inst_type=InstType.SPOT,
        base=base,
        quote="USDT",
        ok=True,
        turnover24h=turn,
        buy2_score=score,
        final_score=score * 0.8,
        distance_atr=dist,
        structure_status=struct,
    )
    r.event = object()
    return r


class PoolHysteresisTest(unittest.TestCase):
    def setUp(self):
        self.cfg = C
        self.t0 = 1_700_000_000_000

    def test_enter_remove_cooldown_reenter(self):
        cfg = self.cfg
        res = mk("AAA-USDT", "AAA", 85, 0.5)
        rows, ordered = plan_pool({}, [res], self.t0, cfg)
        a = [c for c in ordered if c.res.inst_id == "AAA-USDT"][0]
        self.assertTrue(a.is_member)
        self.assertEqual(a.res.stage, Stage.TOP10)

        res2 = mk("AAA-USDT", "AAA", 60, 2.5)
        rows2, ordered2 = plan_pool({r["inst_id"]: r for r in rows}, [res2], self.t0, cfg)
        a2 = [c for c in ordered2 if c.res.inst_id == "AAA-USDT"][0]
        self.assertFalse(a2.is_member)
        row2 = [r for r in rows2 if r["inst_id"] == "AAA-USDT"][0]
        self.assertEqual(row2["removed_ms"], self.t0)

        res3 = mk("AAA-USDT", "AAA", 90, 0.4)
        rows3, ordered3 = plan_pool(
            {r["inst_id"]: r for r in rows2}, [res3], self.t0, cfg
        )
        a3 = [c for c in ordered3 if c.res.inst_id == "AAA-USDT"][0]
        self.assertFalse(a3.is_member)
        self.assertEqual(a3.wait_reason, "cooldown")

        rows4, ordered4 = plan_pool(
            {r["inst_id"]: r for r in rows3},
            [res3],
            self.t0 + int(cfg["pool"]["removeCooldownHours"]) * HOUR + 1,
            cfg,
        )
        a4 = [c for c in ordered4 if c.res.inst_id == "AAA-USDT"][0]
        self.assertTrue(a4.is_member)
        self.assertEqual(a4.res.stage, Stage.TOP10)

    def test_score_below_exit_removed(self):
        res = mk("BBB-USDT", "BBB", 80, 0.8)
        rows, _ = plan_pool({}, [res], self.t0, self.cfg)
        res2 = mk("BBB-USDT", "BBB", 40, 0.8)
        rows2, ordered2 = plan_pool(
            {r["inst_id"]: r for r in rows}, [res2], self.t0, self.cfg
        )
        a = [c for c in ordered2 if c.res.inst_id == "BBB-USDT"][0]
        self.assertFalse(a.is_member)
        self.assertEqual(a.res.stage, Stage.REMOVED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
