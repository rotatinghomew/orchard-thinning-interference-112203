"""文字报告：每个结论都能回到台账记录核对。"""
from __future__ import annotations

from . import comparables, evidence, inventory, plans
from .packet import parse_date, week_of

ASSIGN_TEXT = {"balanced": "事先平衡", "owner_hold": "库主定"}
METHOD_TEXT = {"ascending": "公开升价", "sealed": "密封"}
COVERAGE_TEXT = {"all": "全部报价", "standing": "站立价", "result_only": "仅结果"}
STATUS_TEXT = {
    inventory.DELIVERABLE: "可交付",
    inventory.UNCERTAIN: "区间内，不能全额保证",
    inventory.EXCEEDS: "超出实存",
}


def fmt_range(bounds):
    lo, hi = bounds
    return f"{lo:g}" if abs(lo - hi) < 1e-9 else f"[{lo:g},{hi:g}]"


def fmt_list(values):
    return "、".join(f"{v:.2f}" for v in sorted(values))


# ---------------------------------------------------------------- 库存

def inventory_report(packet, as_of):
    rem = inventory.remaining(packet, as_of)
    delivered = inventory.delivered_by_stock(packet, as_of)
    held = inventory.held_by_stock(packet, as_of)
    lines = [f"可重挂余量（截至 {as_of}）",
             "只有实际交割与已签收留货扣减；流拍、成交后取消、未开拍都不扣。"]
    for stock_id in sorted(rem):
        parts = []
        for grade, (lo, hi) in sorted(rem[stock_id].items()):
            if hi <= 1e-9:
                continue
            base = inventory.grade_ranges_at(packet, stock_id, as_of)[grade]
            seg = f"{grade} 入库{fmt_range(base)}"
            used_d = delivered.get(stock_id, {}).get(grade, 0.0)
            used_h = held.get(stock_id, {}).get(grade, 0.0)
            if used_d:
                seg += f" − 交割{used_d:g}"
            if used_h:
                seg += f" − 留货{used_h:g}"
            seg += f" = 余量{fmt_range((lo, hi))}"
            parts.append(seg)
        if parts:
            lines.append(f"{stock_id}：" + "；".join(parts))
    for check in packet.grade_checks:
        if parse_date(check["known"]) <= as_of:
            lines.append(
                f"等级复核 {check['id']}（{check['stock']}，可知 {check['known']}）"
                "自可知时点起用于现行交割复核；此前已完成的交割与到账照原单可查。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 周报

def week_report(packet, week, as_of):
    monday = parse_date(week)
    lots = [l for l in packet.lots.values() if l["week"] == monday.isoformat()]
    if not lots:
        return f"第 {week} 周没有挂牌记录"
    lots.sort(key=lambda l: (l["date"], l["id"]))
    lines = [f"第 {week} 周开拍证据（截至 {as_of}）"]
    ocs = []
    for lot in lots:
        oc = evidence.outcome(packet, lot["id"], as_of)
        ocs.append(oc)
        desc = evidence.CATEGORY_TEXT[oc.category]
        detail = ""
        if oc.category == evidence.BELOW:
            if oc.best_bid_yard is not None and oc.method == "ascending":
                detail = f"站立价 {oc.best_bid_yard:.2f}：买家至少愿意出到此价"
            elif oc.best_bid_yard is not None:
                detail = f"可见有效价最高 {oc.best_bid_yard:.2f}（{'、'.join(oc.bidders)}）"
            elif oc.hidden_bids:
                detail = f"密封仅公布结果：{oc.hidden_bids} 笔有效报价未揭封，不能当作无买家"
        elif oc.hammered:
            detail = f"锤价 {oc.hammer_yard:.2f}"
            if oc.delivered_kg > 0:
                detail += (f"；实交 {oc.delivered_kg:g}/{oc.lot_kg:g} 千克，"
                           f"入账 {oc.delivered_amount:.2f} 元（{oc.net_per_kg:.2f}/千克）")
            elif oc.category == evidence.CANCELED:
                detail += "；合同取消，未回款"
            else:
                detail += "；尚未交割"
        head = (f"{lot['id']} {oc.group} {ASSIGN_TEXT.get(oc.assignment, oc.assignment)} "
                f"{METHOD_TEXT.get(oc.method, oc.method)}/{COVERAGE_TEXT.get(oc.coverage, oc.coverage)} "
                f"底价 {oc.reserve_yard:.2f}（库边）")
        lines.append(f"{head} {desc}{('：' + detail) if detail else ''}")

    n_open = sum(1 for l in lots if l["opened"])
    hammered = [o for o in ocs if o.hammered]
    canceled = [o for o in ocs if o.category == evidence.CANCELED]
    lines.append(f"开拍机会 {n_open} 次（未开拍 {len(lots) - n_open} 笔不计入）；"
                 f"锤定 {len(hammered)}（其中成交后取消 {len(canceled)}）")

    week_deliveries = [d for d in packet.deliveries
                       if week_of(d["date"]).isoformat() == monday.isoformat()
                       and parse_date(d["date"]) <= as_of]
    if week_deliveries:
        kg = sum(sum(map(float, d["grade_kg"].values())) for d in week_deliveries)
        amt = sum(float(d["amount"]) for d in week_deliveries)
        lines.append(f"周报口径（仅已交割）：{len(week_deliveries)} 笔，均价 {amt / kg:.2f} 元/千克"
                     "——不含流拍与取消，单看会高估高底价的稳妥程度")

    for oc in ocs:
        if oc.category != evidence.BELOW:
            continue
        if oc.best_bid_yard is not None:
            lines.append(f"流拍证据：{oc.lot_id} 底价 {oc.reserve_yard:.2f} 未成交，"
                         f"但可核对买家至少愿意出到 {oc.best_bid_yard:.2f}（{len(oc.bidders)} 人），"
                         "降一点价有没有新买家可看此下限")
        elif oc.hidden_bids:
            lines.append(f"流拍证据：{oc.lot_id} 密封场有 {oc.hidden_bids} 笔有效报价未揭封，"
                         "买家存在但价格未知")

    roots = {}
    for lot in lots:
        if lot["opened"]:
            roots.setdefault(comparables.lineage_root(packet, lot["id"]), []).append(lot["id"])
    for members in roots.values():
        if len(members) < 2:
            continue
        buyers = []
        for lot_id in members:
            oc = evidence.outcome(packet, lot_id, as_of)
            for buyer in oc.bidders + [d["buyer"] for d in packet.deliveries_by_lot.get(lot_id, [])]:
                if buyer not in buyers:
                    buyers.append(buyer)
        lines.append(f"货流去重：{'→'.join(members)} 是同一批货的流拍/重挂/拆并，"
                     f"独立买家 {len(buyers)} 位（{'、'.join(buyers)}），"
                     f"不能按 {len(members)} 笔独立需求计")

    for h in packet.holds:
        if week_of(h["accepted"]).isoformat() == monday.isoformat():
            grades = "、".join(f"{g}{kg}千克" for g, kg in h["grade_kg"].items())
            lines.append(f"签收留货 {h['id']}：{h['stock']} {grades} 已确认未提，占用可重挂重量")
    return "\n".join(lines)


# ---------------------------------------------------------------- 价格阶梯

def ladder_report(packet, group, as_of):
    """分价格区间的成交可能、潜在买家范围和能收回的净款。"""
    lots = comparables.comparable_lots(packet, group, as_of)
    stats = comparables.interval_stats(packet, lots, as_of)
    lines = [f"{group} 分价格区间证据（截至 {as_of}，库边归一，共 {len(lots)} 次开拍机会）",
             "价格区间      机会  锤定  成交可能      实收净价（元/千克）  可核对买家  场次构成"]
    for key in sorted(stats):
        b = stats[key]
        p_lo, p_hi = comparables.wilson(b["hammered"], b["n"])
        nets = f"{min(b['nets']):.2f}–{max(b['nets']):.2f}（{len(b['nets'])} 笔）" if b["nets"] else "—"
        assigns = "、".join(
            f"{ASSIGN_TEXT.get(a, a)}{sum(1 for l in b['lots'] if packet.rounds[l]['assignment'] == a)}"
            for a in sorted(b["assignments"]))
        sample_note = "（样本不足）" if b["n"] < comparables.MIN_SAMPLE else ""
        pct = f"{p_lo * 100:.0f}%–{p_hi * 100:.0f}%"
        lines.append(f"[{key[0]:.1f},{key[1]:.1f})    {b['n']:>3}  {b['hammered']:>3}   "
                     f"{pct:<11}   {nets:<18}  "
                     f"{len(b['buyers']):>3} 位     {assigns}{sample_note}")
    notes = [f"报价下限：{key[0]:.1f}–{key[1]:.1f} 区间未达底价但买家出到 {fmt_list(b['standing'])}"
             for key, b in sorted(stats.items()) if b["standing"]]
    if notes:
        lines.append("未达底价区间的可见报价下限（降一点价可能接到的需求）：")
        lines.extend("  " + n for n in notes)
    return "\n".join(lines)


# ---------------------------------------------------------------- 方案

def _offer_lines(oe):
    share = oe["premium_share"]
    lines = [f"  标的「{oe['name']}」（{oe['group']}，优级占比 {share * 100:.0f}%）："
             f"底价 {oe['reserve']:.2f} 元/千克（库边口径），拟交 {oe['kg_offered']:g} 千克"]
    for s in oe["segments"]:
        lines.append(f"    库存核对：{s['stock']} {s['grade']} 拟交 {s['offered']:g}，"
                     f"余量 {fmt_range((s['lo'], s['hi']))} → {STATUS_TEXT[s['status']]}")
    b = oe["bucket"]
    if not b:
        lines.append(f"    可比证据：底价区间 {oe['interval'][0]:.1f}–{oe['interval'][1]:.1f} "
                     "无可比开拍机会 → 无法比较")
    else:
        p_lo, p_hi = comparables.wilson(b["hammered"], b["n"])
        assigns = "、".join(sorted(ASSIGN_TEXT.get(a, a) for a in b["assignments"]))
        line = (f"    可比证据：同区间开拍机会 {b['n']} 次（{assigns}），"
                f"锤定 {b['hammered']}（成交可能 {p_lo * 100:.0f}%–{p_hi * 100:.0f}%）")
        if b["canceled"]:
            line += f"，含取消 {b['canceled']}"
        if b["n"] < comparables.MIN_SAMPLE:
            line += "；样本不足，区间保留仅供参考"
        lines.append(line)
        weeks = "、".join(f"{w[5:]} {n}/{h}" for w, (n, h) in sorted(b["weeks"].items()))
        lines.append(f"    分周机会/锤定：{weeks}")
        if b["nets"]:
            lines.append(f"    实收净价样本 {len(b['nets'])} 笔：{fmt_list(b['nets'])} 元/千克")
        if b["standing"]:
            lines.append(f"    未达底价的可见报价下限：{fmt_list(b['standing'])}"
                         "（降价可能接到的需求）")
    if oe["buyers"]:
        parts = []
        for row in oe["buyers"]:
            rem = row["remaining"]
            parts.append(f"{row['buyer']}（余 {rem:g} 千克）" if rem is not None
                         else f"{row['buyer']}（未登记限额）")
        lines.append(f"    潜在买家 {len(oe['buyers'])} 位：{'、'.join(parts)}")
    else:
        lines.append("    潜在买家：该底价上没有可核对的有效报价买家")
    if oe["repeat_buyers"]:
        lines.append(f"    重挂重复买家（不按独立需求重复计）：{'、'.join(oe['repeat_buyers'])}")
    model = plans.model_scenarios(oe["bucket"], oe["kg_hi"])
    if model is None:
        lines.append("    模型口径：无法比较（该底价区间无可用的实收样本）")
    else:
        t = model["total"]
        note = "；样本不足" if model["small_sample"] else ""
        lines.append(f"    模型口径（仅比较用，{model['source']}，按可能交付 {oe['kg_hi']:g} 千克{note}）："
                     f"审慎 {t[0]:.2f} / 基准 {t[1]:.2f} / 乐观 {t[2]:.2f} 元")
    return lines


def plans_report(packet, as_of, plan_ids=None):
    ids = plan_ids or list(packet.proposals)
    lines = [f"挂牌方案评估（截至 {as_of}）",
             "承诺口径只含买家书面兜底与可交付货量；模型口径仅用于比较待挂方案，不作承诺。"]
    for pid in ids:
        plan = packet.proposals[pid]
        ev = plans.evaluate_plan(packet, plan, as_of)
        lines.append("")
        lines.append(f"方案 {pid}（最迟取货 {plan['collect_by']}，"
                     f"回款底线 {ev['minimum_net']:.2f} 元，方案周 {ev['week']}）")
        for oe in ev["offers"]:
            lines.extend(_offer_lines(oe))
        for a in ev["alloc"]:
            lines.append(f"  兜底 {a['guarantee']}（{a['buyer']}）→「{a['offer']}」："
                         f"{a['covered_kg']:g} 千克 × {a['net_per_kg']:.2f} = {a['amount']:.2f} 元")
        verdict = ("达标" if ev["committed_ok"]
                   else f"缺口 {ev['minimum_net'] - ev['committed']:.2f} 元，须由市场竞价补足（不属承诺）")
        lines.append(f"  承诺口径最低回款 {ev['committed']:.2f} 元，"
                     f"底线 {ev['minimum_net']:.2f} 元 → {verdict}")
        for note in ev["g_notes"] + ev["qc_notes"]:
            lines.append(f"  提示：{note}")
        for s in ev["statements"]:
            lines.append(f"  交割口径：{s}")
        if ev["completed"]:
            lines.append(f"  已完成的交割与到账照原单可查：{'、'.join(ev['completed'])}")
        lines.append("  未售余量保留一次到期前重挂的选择；重挂机会不计入承诺回款。")
    conflicts = inventory.plan_conflicts(packet, ids)
    if conflicts:
        lines.append("")
        lines.append("方案间冲突（尚未生效的方案不能同时对外出售同一段重量）：")
        for id_a, id_b, shared in conflicts:
            desc = "、".join(f"{s[0]} {s[1]}（{s[2]}↔{s[3]}）" for s in shared)
            lines.append(f"  {id_a} 与 {id_b}：共用 {desc}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 价差核对

def _resolve_offer(packet, spec, default_as_of):
    if "@" in spec:
        base, day = spec.rsplit("@", 1)
        as_of = parse_date(day)
    else:
        base, as_of = spec, default_as_of
    plan_id, name = base.split(":", 1)
    plan = packet.proposals[plan_id]
    for offer in plan["offers"]:
        if offer["name"] == name:
            return plan, offer, as_of
    raise ValueError(f"方案 {plan_id} 没有标的「{name}」")


def _sample(packet, group, as_of):
    """可比样本及被排除的同名组标的（附原因）。"""
    included = comparables.comparable_lots(packet, group, as_of)
    excluded = []
    for lot_id, lot in packet.lots.items():
        round_row = packet.rounds.get(lot_id)
        if not round_row or round_row["group"] != group or lot_id in included:
            continue
        if not lot["opened"]:
            reason = "未开拍（有意留库或安排未成行，不计入）"
        else:
            reason = f"截至 {as_of} 不可知"
        excluded.append((lot_id, reason))
    return included, excluded


def _interval_bids(packet, oe):
    """该标的可比区间内的可见有效报价：{报价号: (买家, 库边价, 可知时点)}。"""
    rows = {}
    bucket = oe["bucket"]
    if not bucket:
        return rows
    for lot_id in bucket["lots"]:
        lot = packet.lots[lot_id]
        for bid in evidence.visible_bids(packet, lot_id, oe["as_of"]):
            if bid.get("qualified"):
                rows[bid["id"]] = (bid["buyer"], evidence.yard_price(lot, bid["price"]),
                                   packet.bid_known_date(bid))
    return rows


def diff_report(packet, spec_a, spec_b, as_of):
    plan_a, offer_a, date_a = _resolve_offer(packet, spec_a, as_of)
    plan_b, offer_b, date_b = _resolve_offer(packet, spec_b, as_of)
    ea = plans.evaluate_offer(packet, plan_a, offer_a, date_a)
    eb = plans.evaluate_offer(packet, plan_b, offer_b, date_b)
    lines = [f"价差来源核对：{plan_a['id']}:{offer_a['name']}（截至 {date_a}）"
             f" vs {plan_b['id']}:{offer_b['name']}（截至 {date_b}）"]

    lines.append("一、标的条件")
    lines.append(f"  构成：{plan_a['id']}:{offer_a['name']} {ea['kg_offered']:g} 千克、"
                 f"优级占比 {ea['premium_share'] * 100:.0f}%、组别 {ea['group']}；"
                 f"{plan_b['id']}:{offer_b['name']} {eb['kg_offered']:g} 千克、"
                 f"优级占比 {eb['premium_share'] * 100:.0f}%、组别 {eb['group']}")
    lines.append(f"  底价（库边口径）：{ea['reserve']:.2f} vs {eb['reserve']:.2f}；"
                 f"方案周 {plans.plan_week(plan_a)} vs {plans.plan_week(plan_b)}")

    lines.append("二、样本选择（重挂样本与场次规则）")
    inc_a, exc_a = _sample(packet, ea["group"], date_a)
    inc_b, exc_b = _sample(packet, eb["group"], date_b)
    lines.append(f"  可比开拍：{len(inc_a)} 次（{ea['group']}）vs {len(inc_b)} 次（{eb['group']}）")
    same_group = ea["group"] == eb["group"]
    only_a = set(inc_a) - set(inc_b)
    only_b = set(inc_b) - set(inc_a)
    if not same_group:
        lines.append("  组别不同，两侧样本不重叠：价差首先来自标的条件本身")
    else:
        for lot_id in sorted(only_a):
            lines.append(f"  仅左侧样本 {lot_id}：截至 {date_b} 不可知")
        for lot_id in sorted(only_b):
            lines.append(f"  仅右侧样本 {lot_id}：截至 {date_a} 不可知")
    skip_a = only_b if same_group else set()
    skip_b = only_a if same_group else set()
    for lot_id, reason in exc_a:
        if lot_id not in skip_a:
            lines.append(f"  左侧排除 {lot_id}：{reason}")
    for lot_id, reason in exc_b:
        if lot_id not in dict(exc_a) and lot_id not in skip_b:
            lines.append(f"  右侧排除 {lot_id}：{reason}")

    lines.append("三、有效出价")
    for tag, oe in (("左", ea), ("右", eb)):
        bids = _interval_bids(packet, oe)
        b = oe["bucket"]
        if not b:
            lines.append(f"  {tag}侧区间 {oe['interval'][0]:.1f}–{oe['interval'][1]:.1f}：无开拍机会")
            continue
        top = max((v[1] for v in bids.values()), default=None)
        top_text = f"，可见有效价最高 {top:.2f}" if top is not None else ""
        lines.append(f"  {tag}侧区间 {oe['interval'][0]:.1f}–{oe['interval'][1]:.1f}："
                     f"机会 {b['n']}、锤定 {b['hammered']}、可见有效报价 {len(bids)} 笔{top_text}")
    bids_a, bids_b = _interval_bids(packet, ea), _interval_bids(packet, eb)
    if not same_group:
        lines.append("  组别不同，有效出价集合不重叠，不逐笔列示")
    else:
        for bid_id in sorted(set(bids_a) - set(bids_b)):
            buyer, price, known = bids_a[bid_id]
            reason = (f"右侧截至 {date_b} 前未揭封" if known and known > date_b
                      else "不属右侧底价区间")
            lines.append(f"  仅左侧可见 {bid_id}（{buyer} {price:.2f}，可知 {known}）：{reason}")
        for bid_id in sorted(set(bids_b) - set(bids_a)):
            buyer, price, known = bids_b[bid_id]
            reason = (f"左侧截至 {date_a} 前未揭封" if known and known > date_a
                      else "不属左侧底价区间")
            lines.append(f"  仅右侧可见 {bid_id}（{buyer} {price:.2f}，可知 {known}）：{reason}")
    return "\n".join(lines)
