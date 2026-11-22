"""可比样本：分组分层、时点过滤、区间汇总与买家去重。"""
from datetime import date
from pathlib import Path
import unittest

from auction import comparables
from auction.packet import Packet


class ComparablesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = Packet.load(Path("资料/演练.json"))
        cls.as_of = cls.packet.latest_known()  # 2026-11-14

    def test_not_opened_lots_are_not_opportunities(self):
        lots = comparables.comparable_lots(self.packet, comparables.MIXED, self.as_of)
        self.assertNotIn("L02-03", lots)
        self.assertNotIn("L06-03", lots)
        self.assertNotIn("L09-03", lots)
        self.assertEqual(len(lots), 37)  # 36 混合果开拍 + H1..H4 − 3 未开拍

    def test_groups_are_separated(self):
        mixed = comparables.comparable_lots(self.packet, comparables.MIXED, self.as_of)
        premium = comparables.comparable_lots(self.packet, comparables.PREMIUM, self.as_of)
        self.assertEqual(len(premium), 24)
        self.assertTrue(set(mixed).isdisjoint(premium))

    def test_point_in_time_sample(self):
        # 第一次开拍日前的依据不能混入后来的重挂结果
        lots = comparables.comparable_lots(self.packet, comparables.MIXED, date(2026, 11, 9))
        self.assertNotIn("H2", lots)   # 11-10 才可知
        self.assertNotIn("H4", lots)   # 11-12 才可知
        self.assertIn("H1", lots)
        self.assertEqual(len(lots), 34)

    def test_interval_stats(self):
        lots = comparables.comparable_lots(self.packet, comparables.MIXED, self.as_of)
        stats = comparables.interval_stats(self.packet, lots, self.as_of)
        bucket = stats[(4.5, 5.0)]
        self.assertEqual(bucket["n"], 8)
        self.assertEqual(bucket["hammered"], 6)
        self.assertEqual(bucket["canceled"], 3)
        self.assertEqual(bucket["no_bid"], 1)
        # 区间键按库边归一底价：delivered 条款减承运费后入区间
        self.assertIn("L03-02", bucket["lots"])  # 底价 5.0 含运费 → 4.75
        # 分周归一：各周机会数加总等于区间总机会
        self.assertEqual(sum(n for n, _ in bucket["weeks"].values()), bucket["n"])

    def test_wilson_interval_widens_on_small_samples(self):
        lo, hi = comparables.wilson(2, 6)
        self.assertAlmostEqual(lo, 0.097, places=3)
        self.assertAlmostEqual(hi, 0.700, places=3)
        lo1, hi1 = comparables.wilson(0, 1)
        self.assertLess(lo1, lo)
        self.assertGreater(hi1, hi)

    def test_buyer_pool_dedups_and_nets_weekly_limits(self):
        pool = comparables.buyer_pool(self.packet, comparables.MIXED, 5.0,
                                      self.as_of, "2026-11-09")
        buyers = {row["buyer"]: row["remaining"] for row in pool}
        # 同一买家只出现一次；该周限额扣已交割与留货
        self.assertEqual(buyers["B01"], 90.0)    # 160 − 交割45 − 留货25
        self.assertEqual(buyers["B02"], 100.0)   # 180 − 留货80
        self.assertEqual(buyers["B03"], 200.0)
        self.assertIsNone(buyers["B04"])         # 该周未登记限额

    def test_repeat_buyers_in_relist_lineage(self):
        repeats = comparables.repeat_buyers_in_lineages(
            self.packet, comparables.MIXED, self.as_of)
        # H1→H2,H3→H4 同一货流中重复出现的买家不按独立需求重复计
        self.assertIn("B01", repeats)
        self.assertIn("B02", repeats)


if __name__ == "__main__":
    unittest.main()
