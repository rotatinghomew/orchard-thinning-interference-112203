"""资料包读取与索引。

所有业务记录都带“可知时点”（known 字段或揭封确认时点）。本模块只负责
读取、索引和可见性判断，不做业务解释；业务口径见其余各模块。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path


def parse_date(value):
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def week_of(day):
    """交割周：以周一为起点的周，返回周一日期。"""
    day = parse_date(day)
    return day - timedelta(days=day.weekday())


def as_range(value):
    """等级重量：确定千克数或 [下限, 上限] 区间，统一为 (下限, 上限)。"""
    if isinstance(value, (list, tuple)):
        lo, hi = value
        return (float(lo), float(hi))
    return (float(value), float(value))


def read_packet(path):
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or any(not isinstance(v, (list, dict)) for v in data.values()):
        raise ValueError("资料包必须是由记录列表或参数对象组成的 JSON 对象")
    return data


class Packet:
    """加索引的资料包，所有记录保持原始字段。"""

    def __init__(self, data):
        self.data = data
        self.stock = {r["id"]: r for r in data.get("stock", [])}
        self.lots = {r["id"]: r for r in data.get("lots", [])}
        self.rounds = {r["lot"]: r for r in data.get("rounds", [])}
        self.bids = {r["id"]: r for r in data.get("bids", [])}
        self.releases = {r["bid"]: parse_date(r["known"]) for r in data.get("bid_releases", [])}
        self.deliveries = list(data.get("deliveries", []))
        self.holds = list(data.get("holds", []))
        self.grade_checks = sorted(data.get("grade_checks", []), key=lambda r: r["known"])
        self.guarantees = list(data.get("guarantees", []))
        self.proposals = {r["id"]: r for r in data.get("proposals", [])}
        self.buyer_limits = {(r["week"], r["buyer"]): float(r["kg"])
                             for r in data.get("buyer_limits", [])}
        self.bids_by_lot = {}
        for bid in self.bids.values():
            self.bids_by_lot.setdefault(bid["lot"], []).append(bid)
        self.deliveries_by_lot = {}
        for row in self.deliveries:
            self.deliveries_by_lot.setdefault(row["lot"], []).append(row)

    @classmethod
    def load(cls, path):
        return cls(read_packet(path))

    def bid_known_date(self, bid):
        """有效价的可知时点：直接记录的 known，或密封全量复核的揭封确认。

        两者都没有（密封仅结果且未揭封）时返回 None，表示市场台账不可知，
        不能当作买家不存在。
        """
        if bid.get("known"):
            return parse_date(bid["known"])
        return self.releases.get(bid["id"])

    def latest_known(self):
        """资料包内最晚的可知时点，作为默认的“今天”。"""
        dates = [r["known"] for r in self.stock.values()]
        dates += [r["known"] for r in self.lots.values()]
        dates += [g["known"] for g in self.grade_checks]
        dates += [d["date"] for d in self.deliveries]
        dates += [h["accepted"] for h in self.holds]
        dates += [k.isoformat() for k in self.releases.values()]
        dates += [b["known"] for b in self.bids.values() if b.get("known")]
        return max(parse_date(d) for d in dates)
