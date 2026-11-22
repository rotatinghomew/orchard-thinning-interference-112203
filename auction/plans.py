"""挂牌方案评估：库存可交付性、书面兜底、可比证据与承诺口径。

- 承诺口径只含买家书面兜底与可交付货量；同一买家该周各笔兜底共用
  一个限额，几个标的不能各自按“可成交”重复占用同一额度。
- 历史模型支持的涨幅只用于比较待挂方案，不写入承诺。
- 成交概率与净价在同一底价区间内联合取值，不跨区间各取最有利。
- 等级复核使兜底条件不再适用时，说明未结清部分应暂停还是仍可依
  原条件交割；已完成的交割与到账照原单可查。
"""
from __future__ import annotations

import itertools
from datetime import timedelta

from . import comparables, inventory
from .comparables import (buyer_pool, interval_of, interval_stats, offer_group,
                          premium_share, remaining_limit, repeat_buyers_in_lineages,
                          wilson)
from .packet import parse_date, week_of


def plan_week(plan):
    """方案周：最迟取货日所在的交割周。"""
    return week_of(plan["collect_by"]).isoformat()


def evaluate_offer(packet, plan, offer, as_of):
    """单个待挂标的的库存核对、可比证据与潜在买家。"""
    group = offer_group(offer)
    segments = inventory.check_offer(packet, offer, as_of)
    deliv_lo, deliv_hi = inventory.offer_deliverable(packet, offer, as_of)
    lots = comparables.comparable_lots(packet, group, as_of)
    stats = interval_stats(packet, lots, as_of)
    key = interval_of(offer["reserve"])
    week = plan_week(plan)
    return {
        "as_of": as_of,
        "plan_id": plan["id"],
        "name": offer["name"],
        "group": group,
        "reserve": float(offer["reserve"]),
        "kg_offered": sum(float(v) for s in offer["segments"] for v in s["grade_kg"].values()),
        "deliv_lo": deliv_lo,
        "deliv_hi": deliv_hi,
        "kg_lo": sum(deliv_lo.values()),
        "kg_hi": sum(deliv_hi.values()),
        "segments": segments,
        "premium_share": premium_share(offer["segments"]),
        "interval": key,
        "bucket": stats.get(key),
        "lots": lots,
        "buyers": buyer_pool(packet, group, float(offer["reserve"]), as_of, week),
        "repeat_buyers": repeat_buyers_in_lineages(packet, group, as_of),
    }


def guarantee_valid(packet, plan, guarantee, as_of):
    """书面兜底在该方案下是否仍有效；返回 (是否有效, 提示)。"""
    if parse_date(guarantee["until"]) < as_of:
        return False, [f"{guarantee['id']}: 已过截止日 {guarantee['until']}"]
    notes = []
    if guarantee["week"] != plan_week(plan):
        notes.append(f"{guarantee['id']}: 兜底周 {guarantee['week']} 与方案周不一致")
    if parse_date(plan["collect_by"]) > parse_date(guarantee["until"]):
        notes.append(f"{guarantee['id']}: 最迟取货日晚于兜底截止日")
    return True, notes


def guarantee_applicable(guarantee, deliv_grades):
    """门槛须由可保证交付的各等级重量同时满足（不是逐档各报一遍最高价）。"""
    return all(deliv_grades.get(grade, 0.0) >= float(kg) - 1e-9
               for grade, kg in guarantee["need"].items())


def match_guarantees(offer_evals, guarantees, limit_of):
    """共享限额与可交付量约束下，承诺回款最大的兜底分配。

    同一买家该周全部保证共用 cap；每段可交付重量只计一次，
    不能把每个标的各自可成交当成整笔回款有保证。
    """
    n_g, n_o = len(guarantees), len(offer_evals)
    best_value, best_alloc = -1.0, None
    for assign in itertools.product(range(n_o + 1), repeat=n_g):
        order = sorted(range(n_g), key=lambda i: -guarantees[i]["net_per_kg"])
        cap_used, kg_used = {}, [0.0] * n_o
        value = 0.0
        alloc = []
        for gi in order:
            oi = assign[gi]
            if oi >= n_o:
                continue
            guarantee = guarantees[gi]
            oe = offer_evals[oi]
            if not guarantee_applicable(guarantee, oe["deliv_lo"]):
                continue
            buyer = guarantee["buyer"]
            limit = limit_of(buyer)
            eff_cap = float(guarantee["cap"])
            if limit is not None:
                eff_cap = min(eff_cap, limit)
            covered = min(eff_cap - cap_used.get(buyer, 0.0),
                          oe["kg_lo"] - kg_used[oi])
            if covered <= 1e-9:
                continue
            amount = covered * float(guarantee["net_per_kg"])
            value += amount
            cap_used[buyer] = cap_used.get(buyer, 0.0) + covered
            kg_used[oi] += covered
            alloc.append({"guarantee": guarantee["id"], "buyer": buyer,
                          "offer": oe["name"], "covered_kg": covered,
                          "net_per_kg": float(guarantee["net_per_kg"]),
                          "amount": amount})
        if value > best_value:
            best_value, best_alloc = value, alloc
    return max(best_value, 0.0), best_alloc or []


def model_scenarios(bucket, kg):
    """同一底价区间内联合取值的成交可能×净价情景（审慎/基准/乐观）。

    概率与净价都来自该底价自己的区间，不跨区间拼最有利值；
    没有同区间开拍机会时返回 None，由报告标明无法比较。
    """
    if not bucket or bucket["n"] == 0:
        return None
    p_lo, p_hi = wilson(bucket["hammered"], bucket["n"])
    p_mid = bucket["hammered"] / bucket["n"]
    nets = sorted(bucket["nets"] or bucket["hammers"])
    if not nets:
        return None
    q_lo, q_mid, q_hi = nets[0], nets[len(nets) // 2], nets[-1]
    return {
        "p": (p_lo, p_mid, p_hi),
        "net": (q_lo, q_mid, q_hi),
        "total": (p_lo * q_lo * kg, p_mid * q_mid * kg, p_hi * q_hi * kg),
        "source": "实收净价" if bucket["nets"] else "锤价（无实收样本）",
        "small_sample": bucket["n"] < comparables.MIN_SAMPLE,
    }


def evaluate_plan(packet, plan, as_of):
    """整套方案：逐标的评估 + 兜底分配 + 等级复核影响 + 交割口径说明。"""
    offers = [evaluate_offer(packet, plan, o, as_of) for o in plan["offers"]]
    week = plan_week(plan)
    valid, g_notes = [], []
    for guarantee in packet.guarantees:
        ok, notes = guarantee_valid(packet, plan, guarantee, as_of)
        if ok:
            valid.append(guarantee)
        g_notes.extend(notes)
    limit_of = lambda buyer: remaining_limit(packet, week, buyer, as_of)
    committed, alloc = match_guarantees(offers, valid, limit_of)

    # 等级复核影响：复核前仍适用、复核后不再适用的兜底
    plan_stocks = {s["stock"] for o in plan["offers"] for s in o["segments"]}
    qc_dates = [parse_date(c["known"]) for c in packet.grade_checks
                if parse_date(c["known"]) <= as_of and c["stock"] in plan_stocks]
    qc_notes = []
    if qc_dates:
        pre = max(qc_dates) - timedelta(days=1)
        pre_offers = [evaluate_offer(packet, plan, o, pre) for o in plan["offers"]]
        for guarantee in valid:
            for pre_oe, oe in zip(pre_offers, offers):
                if (guarantee_applicable(guarantee, pre_oe["deliv_lo"])
                        and not guarantee_applicable(guarantee, oe["deliv_lo"])):
                    need = "、".join(f"{g}{kg:g}千克" for g, kg in guarantee["need"].items())
                    qc_notes.append(
                        f"兜底 {guarantee['id']}（{guarantee['buyer']}，门槛 {need}）"
                        f"因等级复核后对「{oe['name']}」不再适用")

    # 未结清部分的交割口径：可保证交付的可依原条件，超出的应暂停
    statements = []
    for oe in offers:
        bad = [s for s in oe["segments"] if s["status"] != inventory.DELIVERABLE]
        if not bad:
            statements.append(f"「{oe['name']}」拟交量可保证交付，仍可依原条件交割")
        else:
            desc = "、".join(
                f"{s['stock']} {s['grade']} 拟交 {s['offered']:g} 千克"
                f"（复核后余量 {s['lo']:g}–{s['hi']:g}）" for s in bad)
            statements.append(
                f"「{oe['name']}」{desc}：超出可保证余量，应暂停该部分或改按可交付量重报")

    stocks = plan_stocks
    completed = [d["id"] for d in packet.deliveries
                 if any(p["stock"] in stocks for p in packet.lots[d["lot"]]["composition"])]
    return {
        "plan": plan,
        "week": week,
        "offers": offers,
        "committed": committed,
        "alloc": alloc,
        "minimum_net": float(plan["minimum_net"]),
        "committed_ok": committed >= float(plan["minimum_net"]) - 1e-9,
        "qc_notes": qc_notes,
        "g_notes": g_notes,
        "statements": statements,
        "completed": completed,
    }
