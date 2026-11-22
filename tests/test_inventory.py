"""库存台账：扣减口径、等级复核时点、方案间重量冲突。"""
from datetime import date
from pathlib import Path
import unittest

from auction import inventory
from auction.packet import Packet


class InventoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = Packet.load(Path("资料/演练.json"))

    def test_only_deliveries_and_holds_deduct(self):
        rem = inventory.remaining(self.packet, date(2026, 11, 13))
        # RX：入库 优[105,110] 常[110,115]，交割45（DH2）、留货 优25 常80
        self.assertEqual(rem["RX"]["优"], (35.0, 40.0))
        self.assertEqual(rem["RX"]["常"], (30.0, 35.0))
        # 流拍、取消、未开拍不扣：S01-03 流拍后仍全量可重挂
        self.assertEqual(rem["S01-03"]["优"], (53.0, 53.0))
        self.assertEqual(rem["S01-03"]["常"], (107.0, 107.0))
        # 部分结清只扣实交：S01-02 优46 实交34
        self.assertEqual(rem["S01-02"]["优"], (12.0, 12.0))

    def test_grade_check_applies_from_known_date(self):
        before = inventory.remaining(self.packet, date(2026, 11, 13))
        after = inventory.remaining(self.packet, date(2026, 11, 14))
        self.assertEqual(before["RX"]["优"], (35.0, 40.0))
        # QC1 自 2026-11-14 起：优[85,96]−70、常[124,135]−80
        self.assertEqual(after["RX"]["优"], (15.0, 26.0))
        self.assertEqual(after["RX"]["常"], (44.0, 55.0))
        # 未涉及的入库批不受影响
        self.assertEqual(after["RY"]["优"], (80.0, 80.0))

    def test_split_tiers_must_be_explained_by_same_stock(self):
        offer = self.packet.proposals["P1"]["offers"][0]
        before = {s["grade"]: s["status"]
                  for s in inventory.check_offer(self.packet, offer, date(2026, 11, 13))}
        self.assertEqual(before["优"], inventory.DELIVERABLE)
        after = inventory.check_offer(self.packet, offer, date(2026, 11, 14))
        status = {(s["stock"], s["grade"]): s["status"] for s in after}
        # 复核后 RX 优级余量上限 26，拟交 35 无法由同一批实存解释
        self.assertEqual(status[("RX", "优")], inventory.EXCEEDS)
        self.assertEqual(status[("RX", "常")], inventory.DELIVERABLE)
        self.assertEqual(status[("RY", "优")], inventory.DELIVERABLE)

    def test_pending_plans_cannot_sell_same_weight(self):
        conflicts = inventory.plan_conflicts(self.packet)
        pairs = {(a, b) for a, b, _ in conflicts}
        self.assertIn(("P1", "P2"), pairs)
        self.assertIn(("P1", "P3"), pairs)
        self.assertIn(("P2", "P3"), pairs)

    def test_delivery_beyond_composition_is_rejected(self):
        data = {"stock": [{"id": "S", "known": "2026-01-05",
                           "grade_kg": {"优": 100}, "available_kg": 100}],
                "lots": [{"id": "L", "date": "2026-01-06", "week": "2026-01-05",
                          "known": "2026-01-06", "opened": True, "term": "yard",
                          "freight_per_kg": 0, "method": "ascending", "reserve": 5,
                          "coverage": "standing",
                          "composition": [{"stock": "S", "grade_kg": {"优": 100}}],
                          "parents": [], "status": "sold", "hammer": 6}],
                "deliveries": [{"id": "D", "lot": "L", "buyer": "B",
                                "date": "2026-01-07", "grade_kg": {"优": 120},
                                "amount": 720.0}]}
        with self.assertRaises(ValueError):
            inventory.delivered_by_stock(Packet(data))


if __name__ == "__main__":
    unittest.main()
