"""可比开拍样本与价格区间证据。

用过去同类货在不同底价上的实际开拍机会做比较：
- 等级组合（混合果/单一优级）分层，价格归一到库边净价，按底价区间汇总；
- 未开拍不算开拍机会；库主自定底价的场次单独标注；
- 同一货流（流拍-重挂-拆并）里的同一买家不按独立需求重复计；
- 样本不足时保留区间或标明无法比较，不跨区间拼最有利值。
"""
from __future__ import annotations

import math

from .evidence import (BELOW, CANCELED, NO_BID, SOLD_FULL, outcome, visible_bids,
                       yard_price)
from .packet import as_range, parse_date, week_of

MIXED = "混合果"
PREMIUM = "单一优级"
INTERVAL_WIDTH = 0.5
MIN_SAMPLE = 5


def composition_group(parts):
    """按等级组合分层：纯优级为单一优级，其余为混合果。"""
    grades = {g for part in parts for g in part["grade_kg"]}
    return PREMIUM if grades == {"优"} else MIXED


def offer_group(offer):
    return composition_group(offer["segments"])


def premium_share(parts):
    """优级占比（等级区间取上限的名义口径），用于核对标的条件差异。"""
    total = sum(as_range(v)[1] for p in parts for v in p["grade_kg"].values())
    if total <= 0:
        return None
    top = sum(as_range(p["grade_kg"].get("优", 0))[1] for p in parts)
    return top / total


def comparable_lots(packet, group, as_of):
    """同等级组合、截至可知、实际开拍的标的（未开拍不算开拍机会）。"""
    out = []
    for lot_id, lot in packet.lots.items():
        round_row = packet.rounds.get(lot_id)
        if not round_row or round_row["group"] != group:
            continue
        if parse_date(lot["known"]) > as_of:
            continue
        if not lot["opened"]:
            continue
        out.append(lot_id)
    return sorted(out, key=lambda i: (packet.lots[i]["date"], i))


def interval_of(price, width=INTERVAL_WIDTH):
    lo = math.floor(float(price) / width + 1e-9) * width
    return (round(lo, 4), round(lo + width, 4))


def wilson(k, n, z=1.96):
    """二项比例的 Wilson 区间：样本少时区间自然变宽。"""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def interval_stats(packet, lot_ids, as_of):
    """按归一底价区间汇总开拍机会、锤定、实收净价、报价下限与买家。"""
    buckets = {}
    for lot_id in lot_ids:
        oc = outcome(packet, lot_id, as_of)
        key = interval_of(oc.reserve_yard)
        b = buckets.setdefault(key, {
            "lots": [], "n": 0, "hammered": 0, "delivered": 0, "full": 0,
            "canceled": 0, "no_bid": 0, "below": 0,
            "nets": [], "hammers": [], "standing": [], "buyers": set(),
            "assignments": set(), "weeks": {},
        })
        b["lots"].append(lot_id)
        b["n"] += 1
        b["assignments"].add(oc.assignment)
        week = packet.lots[lot_id]["week"]
        week_row = b["weeks"].setdefault(week, [0, 0])
        week_row[0] += 1
        if oc.hammered:
            week_row[1] += 1
        if oc.hammered:
            b["hammered"] += 1
        if oc.category == CANCELED:
            b["canceled"] += 1
        elif oc.category == NO_BID:
            b["no_bid"] += 1
        elif oc.category == BELOW:
            b["below"] += 1
            if oc.best_bid_yard is not None:
                b["standing"].append(oc.best_bid_yard)
        if oc.delivered_kg > 0:
            b["delivered"] += 1
            b["nets"].append(oc.net_per_kg)
            if oc.category == SOLD_FULL:
                b["full"] += 1
        if oc.hammer_yard is not None:
            b["hammers"].append(oc.hammer_yard)
        b["buyers"].update(oc.bidders)
    return buckets


def remaining_limit(packet, week, buyer, as_of):
    """该周采购限额扣除已交割与已签收留货；未登记限额的买家返回 None。"""
    key = (week, buyer)
    if key not in packet.buyer_limits:
        return None
    used = 0.0
    for d in packet.deliveries:
        if (d["buyer"] == buyer and parse_date(d["date"]) <= as_of
                and week_of(d["date"]).isoformat() == week):
            used += sum(float(kg) for kg in d["grade_kg"].values())
    for h in packet.holds:
        if (h["buyer"] == buyer and parse_date(h["accepted"]) <= as_of
                and week_of(h["accepted"]).isoformat() == week):
            used += sum(float(kg) for kg in h["grade_kg"].values())
    return packet.buyer_limits[key] - used


def buyer_pool(packet, group, reserve_yard, as_of, week):
    """同组历史开拍中有效报价（库边归一）达到底价的可核对买家。

    同一买家只计一次；不同报价不保证来自独立买家，实名核验过的匿名号
    相同即为同一人。交割买家同样可核对。
    """
    pool = {}
    for lot_id in comparable_lots(packet, group, as_of):
        lot = packet.lots[lot_id]
        for bid in visible_bids(packet, lot_id, as_of):
            if not bid.get("qualified"):
                continue
            if yard_price(lot, bid["price"]) >= reserve_yard - 1e-9:
                pool.setdefault(bid["buyer"], set()).add(lot_id)
        oc = outcome(packet, lot_id, as_of)
        if oc.hammer_yard is not None and oc.hammer_yard >= reserve_yard - 1e-9:
            for d in packet.deliveries_by_lot.get(lot_id, []):
                pool.setdefault(d["buyer"], set()).add(lot_id)
    rows = []
    for buyer, lots in sorted(pool.items()):
        rows.append({"buyer": buyer, "lots": sorted(lots),
                     "remaining": remaining_limit(packet, week, buyer, as_of)})
    return rows


def lineage_root(packet, lot_id):
    """货流根标的：沿最早的父标的回溯（拆分、合并、重挂同属一个货流）。"""
    cur = lot_id
    seen = set()
    while True:
        parents = packet.lots[cur]["parents"]
        if not parents:
            return cur
        nxt = sorted(parents, key=lambda p: (packet.lots[p]["date"], p))[0]
        if nxt in seen:
            return cur
        seen.add(cur)
        cur = nxt


def repeat_buyers_in_lineages(packet, group, as_of):
    """在同一货流中多次出现的买家：重挂里可能是同一批买家，不重复计需求。"""
    roots = {}
    for lot_id in comparable_lots(packet, group, as_of):
        roots.setdefault(lineage_root(packet, lot_id), []).append(lot_id)
    repeats = set()
    for members in roots.values():
        if len(members) < 2:
            continue
        seen = set()
        for lot_id in members:
            oc = outcome(packet, lot_id, as_of)
            buyers = set(oc.bidders)
            buyers.update(d["buyer"] for d in packet.deliveries_by_lot.get(lot_id, []))
            repeats.update(buyers & seen)
            seen.update(buyers)
    return sorted(repeats)
