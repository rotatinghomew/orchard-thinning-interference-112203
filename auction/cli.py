"""命令行入口：挂牌决策证据工具。"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import report
from .packet import Packet, parse_date


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="auction",
        description="产地果品挂牌决策证据工具（每轮挂牌可用）")
    parser.add_argument("--packet", type=Path, default=Path("资料/演练.json"),
                        help="资料包路径（默认 资料/演练.json）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inventory", help="各入库批可重挂余量与扣减去向")
    p.add_argument("--as-of", help="截至日期，默认资料最新可知时点")

    p = sub.add_parser("week", help="一周开拍的完整证据（含流拍与取消）")
    p.add_argument("week", help="周一日期，如 2026-11-09")
    p.add_argument("--as-of", help="截至日期")

    p = sub.add_parser("plans", help="挂牌方案评估与比较")
    p.add_argument("--as-of", help="截至日期")
    p.add_argument("--plan", action="append", help="只评估指定方案（可多次给出）")

    p = sub.add_parser("ladder", help="分价格区间的成交可能、买家范围与净款")
    p.add_argument("group", choices=["混合果", "单一优级"], help="等级组合")
    p.add_argument("--as-of", help="截至日期")

    p = sub.add_parser("diff", help="两个标的报价的价差来源核对")
    p.add_argument("a", help="方案:标的[@日期]，如 P2:混合@2026-11-10")
    p.add_argument("b", help="方案:标的[@日期]")
    p.add_argument("--as-of", help="两侧默认截至日期")

    args = parser.parse_args(argv)
    packet = Packet.load(args.packet)
    as_of = parse_date(args.as_of) if getattr(args, "as_of", None) else packet.latest_known()

    if args.command == "inventory":
        print(report.inventory_report(packet, as_of))
    elif args.command == "week":
        print(report.week_report(packet, args.week, as_of))
    elif args.command == "plans":
        print(report.plans_report(packet, as_of, args.plan))
    elif args.command == "ladder":
        print(report.ladder_report(packet, args.group, as_of))
    elif args.command == "diff":
        print(report.diff_report(packet, args.a, args.b, as_of))
