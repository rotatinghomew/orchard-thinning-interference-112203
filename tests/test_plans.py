"""方案评估：兜底共享限额、承诺口径、等级复核影响与模型口径边界。"""
from datetime import date
from pathlib import Path
import unittest

from auction import comparables, plans
from auction.packet import Packet

AS_OF = date(2026, 11, 14)


class PlansTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = Packet.load(Path("资料/演练.json"))
        cls.evals = {pid: plans.evaluate_plan(cls.packet, cls.packet.proposals[pid], AS_OF)
                     for pid in ("P1", "P2", "P3")}

    def test_committed_minimum_uses_guarantees_and_deliverable_only(self):
        self.assertAlmostEqual(self.evals["P1"]["committed"], 525.0)   # G2: 105 × 5.0
        self.assertAlmostEqual(self.evals["P2"]["committed"], 457.5)   # G2 300 + G3 157.5
        self.assertAlmostEqual(self.evals["P3"]["committed"], 300.0)   # G2: 60 × 5.0
        self.assertTrue(self.evals["P1"]["committed_ok"])
        self.assertTrue(self.evals["P2"]["committed_ok"])
        self.assertTrue(self.evals["P3"]["committed_ok"])

    def test_shared_cap_is_not_double_counted(self):
        # B03 的 G1、G2 共用 cap 105：整批 105 千克只能按一笔计，
        # 不能 G1(105×4.2) 与 G2(105×5.0) 同时占满
        alloc = self.evals["P1"]["alloc"]
        covered = sum(a["covered_kg"] for a in alloc if a["buyer"] == "B03")
        self.assertLessEqual(covered, 105.0)

    def test_grade_check_voids_backstop_condition(self):
        # QC1 后 RX 优级可保证余量 15，G1 门槛 优30 对「混合」不再适用
        oe = self.evals["P2"]["offers"][0]
        g1 = next(g for g in self.packet.guarantees if g["id"] == "G1")
        self.assertFalse(plans.guarantee_applicable(g1, oe["deliv_lo"]))
        self.assertTrue(any("G1" in note for note in self.evals["P2"]["qc_notes"]))
        # 复核前（11-13）仍适用
        pre = plans.evaluate_plan(self.packet, self.packet.proposals["P2"], date(2026, 11, 13))
        self.assertFalse(any("G1" in note for note in pre["qc_notes"]))

    def test_unsettled_part_gets_pause_or_deliver_statement(self):
        statements = self.evals["P1"]["statements"]
        self.assertTrue(any("应暂停" in s for s in statements))
        statements = self.evals["P3"]["statements"]
        self.assertTrue(any("仍可依原条件交割" in s for s in statements))
        # 已完成的交割照原单可查
        self.assertIn("DH2", self.evals["P1"]["completed"])

    def test_model_estimate_stays_within_one_interval(self):
        # 成交概率与净价取自同一底价区间，不跨区间拼最有利值
        oe = plans.evaluate_offer(self.packet, self.packet.proposals["P2"],
                                  self.packet.proposals["P2"]["offers"][0], AS_OF)
        self.assertEqual(oe["interval"], (4.5, 5.0))
        model = plans.model_scenarios(oe["bucket"], oe["kg_hi"])
        bucket = oe["bucket"]
        p_lo, p_hi = comparables.wilson(bucket["hammered"], bucket["n"])
        self.assertAlmostEqual(model["p"][0], p_lo)
        self.assertAlmostEqual(model["p"][2], p_hi)
        self.assertIn(model["net"][0], bucket["nets"])
        self.assertIn(model["net"][2], bucket["nets"])

    def test_missing_comparables_allow_incomparable(self):
        model = plans.model_scenarios(None, 100.0)
        self.assertIsNone(model)

    def test_reserve_without_comparables_is_incomparable(self):
        # 底价区间没有可比开拍机会：保留判断为无法比较，不硬给数字
        plan = {"id": "PX", "collect_by": "2026-11-15", "minimum_net": 0,
                "offers": [{"name": "高价优级", "reserve": 9.9,
                            "segments": [{"stock": "RY", "grade_kg": {"优": 60}}]}]}
        oe = plans.evaluate_offer(self.packet, plan, plan["offers"][0], AS_OF)
        self.assertIsNone(oe["bucket"])
        self.assertIsNone(plans.model_scenarios(oe["bucket"], oe["kg_hi"]))

    def test_guarantee_validity_window(self):
        g1 = next(g for g in self.packet.guarantees if g["id"] == "G1")
        plan = self.packet.proposals["P1"]
        ok, _ = plans.guarantee_valid(self.packet, plan, g1, date(2026, 11, 14))
        self.assertTrue(ok)
        ok, _ = plans.guarantee_valid(self.packet, plan, g1, date(2026, 11, 16))
        self.assertFalse(ok)  # 截止 2026-11-15

    def test_minimum_net_gap_is_not_hidden(self):
        plan = dict(self.packet.proposals["P3"], minimum_net=9999.0)
        ev = plans.evaluate_plan(self.packet, plan, AS_OF)
        self.assertFalse(ev["committed_ok"])
        self.assertAlmostEqual(ev["minimum_net"] - ev["committed"], 9999.0 - 300.0)


if __name__ == "__main__":
    unittest.main()
