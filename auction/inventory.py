"""可重挂库存台账。

同一批货可以经历流拍、重挂、拆单和合并，只有实际交割与已签收留货
才从可重新挂牌的重量里扣去；流拍、成交后取消、未开拍都不扣。
等级复核自其可知时点起用于现行交割复核，不改写已完成的交割单。
"""
from __future__ import annotations

from .packet import as_range, parse_date

DELIVERABLE = "deliverable"  # 拟交量不超过余量下限，可保证交付
UNCERTAIN = "uncertain"      # 拟交量落在余量区间内，不能全额保证
EXCEEDS = "exceeds"          # 拟交量超出余量上限，实存解释不了


def grade_ranges_at(packet, stock_id, as_of):
    """截至 as_of 有效的等级范围：入库记录被已可知的等级复核更正。"""
    grades = {g: as_range(v) for g, v in packet.stock[stock_id]["grade_kg"].items()}
    for check in packet.grade_checks:
        if check["stock"] == stock_id and parse_date(check["known"]) <= as_of:
            grades = {g: as_range(v) for g, v in check["grade_kg"].items()}
    return grades


def delivered_by_stock(packet, as_of=None):
    """把每笔交割按标的构成顺序归属到入库批：{入库批: {等级: 千克}}。

    交割重量必须能由标的构成解释，否则视为台账不一致。
    """
    out = {}
    for row in sorted(packet.deliveries, key=lambda r: (r["date"], r["id"])):
        if as_of is not None and parse_date(row["date"]) > as_of:
            continue
        lot = packet.lots[row["lot"]]
        todo = {g: float(kg) for g, kg in row["grade_kg"].items()}
        for part in lot["composition"]:
            for grade, value in part["grade_kg"].items():
                if grade not in todo:
                    continue
                take = min(as_range(value)[1], todo[grade])
                if take > 0:
                    slot = out.setdefault(part["stock"], {})
                    slot[grade] = slot.get(grade, 0.0) + take
                    todo[grade] -= take
        leftover = {g: kg for g, kg in todo.items() if kg > 1e-9}
        if leftover:
            raise ValueError(
                f"交割 {row['id']} 的重量无法由标的 {row['lot']} 的构成解释: {leftover}")
    return out


def held_by_stock(packet, as_of=None):
    """已签收但尚未实际交割的留货：{入库批: {等级: 千克}}。"""
    out = {}
    for row in packet.holds:
        if as_of is not None and parse_date(row["accepted"]) > as_of:
            continue
        slot = out.setdefault(row["stock"], {})
        for grade, kg in row["grade_kg"].items():
            slot[grade] = slot.get(grade, 0.0) + float(kg)
    return out


def remaining(packet, as_of):
    """各入库批各等级的可重挂余量 (下限, 上限)。"""
    delivered = delivered_by_stock(packet, as_of)
    held = held_by_stock(packet, as_of)
    out = {}
    for stock_id in packet.stock:
        row = {}
        for grade, (lo, hi) in grade_ranges_at(packet, stock_id, as_of).items():
            used = (delivered.get(stock_id, {}).get(grade, 0.0)
                    + held.get(stock_id, {}).get(grade, 0.0))
            row[grade] = (lo - used, hi - used)
        out[stock_id] = row
    return out


def check_offer(packet, offer, as_of):
    """逐段核对拟交等级重量能否由同一批实存余量解释。"""
    rem = remaining(packet, as_of)
    segments = []
    for seg in offer["segments"]:
        stock_id = seg["stock"]
        for grade, kg in seg["grade_kg"].items():
            lo, hi = rem.get(stock_id, {}).get(grade, (0.0, 0.0))
            kg = float(kg)
            if kg <= lo + 1e-9:
                status = DELIVERABLE
            elif kg <= hi + 1e-9:
                status = UNCERTAIN
            else:
                status = EXCEEDS
            segments.append({"stock": stock_id, "grade": grade, "offered": kg,
                             "lo": lo, "hi": hi, "status": status})
    return segments


def offer_deliverable(packet, offer, as_of):
    """按等级汇总可交付量：(可保证交付, 可能交付)，分别按余量下限、上限口径。"""
    rem = remaining(packet, as_of)
    lo_sum, hi_sum = {}, {}
    for seg in offer["segments"]:
        for grade, kg in seg["grade_kg"].items():
            lo, hi = rem.get(seg["stock"], {}).get(grade, (0.0, 0.0))
            kg = float(kg)
            lo_sum[grade] = lo_sum.get(grade, 0.0) + min(kg, max(lo, 0.0))
            hi_sum[grade] = hi_sum.get(grade, 0.0) + min(kg, max(hi, 0.0))
    return lo_sum, hi_sum


def plan_conflicts(packet, plan_ids=None):
    """未定方案两两之间是否拟卖同一段重量（尚未生效不能同时对外出售）。"""
    ids = plan_ids or list(packet.proposals)
    conflicts = []
    for i, id_a in enumerate(ids):
        for id_b in ids[i + 1:]:
            shared = []
            for offer_a in packet.proposals[id_a]["offers"]:
                for offer_b in packet.proposals[id_b]["offers"]:
                    for seg_a in offer_a["segments"]:
                        for seg_b in offer_b["segments"]:
                            if seg_a["stock"] != seg_b["stock"]:
                                continue
                            for grade in sorted(set(seg_a["grade_kg"]) & set(seg_b["grade_kg"])):
                                shared.append((seg_a["stock"], grade,
                                               offer_a["name"], offer_b["name"]))
            if shared:
                conflicts.append((id_a, id_b, shared))
    return conflicts
