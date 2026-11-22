"""单场结果解释：归一价格、场次规则、截至可知的报价证据。"""
from datetime import date
from pathlib import Path
import unittest

from auction import evidence
from auction.packet import Packet


class EvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = Packet.load(Path("资料/演练.json"))
        cls.as_of = cls.packet.latest_known()

    def test_delivered_term_normalizes_to_yard_price(self):
        oc = evidence.outcome(self.packet, "L01-02", self.as_of)
        self.assertAlmostEqual(oc.reserve_yard, 4.25)   # 4.5 − 0.25 承运费
        self.assertAlmostEqual(oc.hammer_yard, 4.81)    # 5.06 − 0.25

    def test_not_opened_is_not_a_failure(self):
        oc = evidence.outcome(self.packet, "L02-03", self.as_of)
        self.assertEqual(oc.category, evidence.NOT_OPENED)

    def test_ascending_below_keeps_standing_lower_bound(self):
        oc = evidence.outcome(self.packet, "L01-03", self.as_of)
        self.assertEqual(oc.category, evidence.BELOW)
        self.assertAlmostEqual(oc.best_bid_yard, 5.24)  # 买家至少愿意出到 5.24
        self.assertAlmostEqual(oc.reserve_yard, 5.5)

    def test_sealed_result_only_hides_bids_until_release(self):
        # L03-02 密封仅结果：揭封前（08-22 前）有效价不可知
        oc = evidence.outcome(self.packet, "L03-02", date(2026, 8, 21))
        self.assertEqual(oc.hidden_bids, 4)
        oc = evidence.outcome(self.packet, "L03-02", date(2026, 8, 22))
        self.assertEqual(oc.hidden_bids, 0)
        self.assertAlmostEqual(oc.best_bid_yard, 6.19)  # 6.44 − 0.25

    def test_sealed_result_only_never_released_stays_unknown(self):
        oc = evidence.outcome(self.packet, "L04-04", self.as_of)
        self.assertEqual(oc.category, evidence.SOLD_PARTIAL)  # 实交 108/120
        self.assertEqual(oc.hidden_bids, 1)  # 密封仅结果且未揭封，价格不可知

    def test_h1_bids_visible_only_after_release(self):
        self.assertEqual(len(evidence.visible_bids(self.packet, "H1", date(2026, 11, 12))), 0)
        self.assertEqual(len(evidence.visible_bids(self.packet, "H1", date(2026, 11, 13))), 2)

    def test_canceled_has_hammer_but_no_recovery(self):
        oc = evidence.outcome(self.packet, "L01-01", self.as_of)
        self.assertEqual(oc.category, evidence.CANCELED)
        self.assertAlmostEqual(oc.hammer_yard, 5.16)
        self.assertEqual(oc.delivered_kg, 0.0)

    def test_partial_settlement_is_not_full_delivery(self):
        oc = evidence.outcome(self.packet, "L03-05", self.as_of)
        self.assertEqual(oc.category, evidence.SOLD_PARTIAL)
        self.assertEqual(oc.delivered_kg, 128.0)
        self.assertEqual(oc.lot_kg, 140.0)
        self.assertAlmostEqual(oc.net_per_kg, 853.76 / 128.0)

    def test_no_bid_has_distinct_meaning(self):
        oc = evidence.outcome(self.packet, "L10-04", self.as_of)
        self.assertEqual(oc.category, evidence.NO_BID)
        self.assertIsNone(oc.best_bid_yard)


if __name__ == "__main__":
    unittest.main()
