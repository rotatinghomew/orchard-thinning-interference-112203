"""命令行冒烟测试：四个子命令都能出报告且关键口径在文内。"""
from contextlib import redirect_stdout
from io import StringIO
import unittest

from auction.cli import main


def run(*argv):
    out = StringIO()
    with redirect_stdout(out):
        main(list(argv))
    return out.getvalue()


class CliTest(unittest.TestCase):
    def test_inventory(self):
        text = run("inventory")
        self.assertIn("可重挂余量", text)
        self.assertIn("QC1", text)

    def test_week_dedups_lineage(self):
        text = run("week", "2026-11-09")
        self.assertIn("不能按 4 笔独立需求计", text)
        self.assertIn("周报口径（仅已交割）", text)
        self.assertIn("流拍证据", text)

    def test_plans(self):
        text = run("plans")
        self.assertIn("承诺口径最低回款 525.00 元", text)
        self.assertIn("不能同时对外出售同一段重量", text)
        self.assertIn("应暂停", text)

    def test_ladder(self):
        text = run("ladder", "混合果")
        self.assertIn("分价格区间证据", text)
        self.assertIn("[4.5,5.0)", text)
        self.assertIn("报价下限", text)

    def test_diff_across_dates_attributes_to_bid_releases(self):
        text = run("diff", "P2:混合@2026-11-10", "P2:混合@2026-11-14")
        self.assertIn("一、标的条件", text)
        self.assertIn("二、样本选择", text)
        self.assertIn("三、有效出价", text)
        self.assertIn("H4-B01", text)  # 揭封新增的可见报价

    def test_diff_across_offers_attributes_to_conditions(self):
        text = run("diff", "P1:整批", "P2:优级")
        self.assertIn("组别不同", text)


if __name__ == "__main__":
    unittest.main()
