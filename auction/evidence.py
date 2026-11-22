"""单场开拍的结果解释与截至某日的报价证据。

口径：
- 价格统一归一到库边净价（delivered 条款的底价、锤价、报价都减承运费）。
- 未开拍不是开拍机会，不能当作卖不出。
- 流拍按场次规则解释：公开升价场的站立价只说明买家曾愿意出到至少这个价；
  密封仅结果场未揭封时，流拍不等于买家不存在。
- 部分结清只按实交重量入账，不当作标的全交割。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .packet import as_range, parse_date

NOT_OPENED = "not_opened"
NO_BID = "no_bid"
BELOW = "below"
SOLD_FULL = "sold_full"
SOLD_PARTIAL = "sold_partial"
SOLD_UNDELIVERED = "sold_undelivered"
CANCELED = "canceled"

CATEGORY_TEXT = {
    NOT_OPENED: "未开拍：不是开拍机会，不能当作卖不出",
    NO_BID: "开拍无人报有效价：该场没有可核对的需求",
    BELOW: "有有效报价未达底价",
    SOLD_FULL: "成交且全部交割",
    SOLD_PARTIAL: "成交但部分结清：只按实交重量入账",
    SOLD_UNDELIVERED: "成交但未完成交割",
    CANCELED: "成交后取消：锤价只证明当时有买家，不计回款",
}


def yard_price(lot, price):
    """归一到库边净价；delivered 条款的价格含承运费，须减去。"""
    if price is None:
        return None
    freight = float(lot.get("freight_per_kg", 0) or 0) if lot.get("term") == "delivered" else 0.0
    return round(float(price) - freight, 4)


def lot_kg(lot):
    """标的名义重量（等级区间取上限）。"""
    return sum(as_range(v)[1] for part in lot["composition"] for v in part["grade_kg"].values())


def visible_bids(packet, lot_id, as_of):
    """截至 as_of 市场台账可知（含已揭封确认）的报价。"""
    out = []
    for bid in packet.bids_by_lot.get(lot_id, []):
        known = packet.bid_known_date(bid)
        if known is not None and known <= as_of:
            out.append(bid)
    return out


@dataclass
class Outcome:
    lot_id: str
    category: str
    reserve_yard: float
    method: str
    coverage: str
    assignment: str
    group: str
    hammer_yard: float | None = None
    best_bid_yard: float | None = None   # 可见有效报价的最高值（库边归一）
    bidders: list = field(default_factory=list)  # 可见有效报价买家（按出现顺序去重）
    hidden_bids: int = 0                 # 台账不可知的有效报价数（密封未揭封）
    lot_kg: float = 0.0
    delivered_kg: float = 0.0
    delivered_amount: float = 0.0
    delivery_ids: list = field(default_factory=list)

    @property
    def net_per_kg(self):
        if self.delivered_kg > 0:
            return self.delivered_amount / self.delivered_kg
        return None

    @property
    def hammered(self):
        """锤定（含成交后取消），与是否回款分开看。"""
        return self.category in (SOLD_FULL, SOLD_PARTIAL, SOLD_UNDELIVERED, CANCELED)


def outcome(packet, lot_id, as_of):
    """单个标的截至 as_of 的可核对结果。"""
    lot = packet.lots[lot_id]
    round_row = packet.rounds.get(lot_id, {})
    bids = visible_bids(packet, lot_id, as_of)
    hidden = len(packet.bids_by_lot.get(lot_id, [])) - len(bids)
    best = None
    bidders = []
    for bid in bids:
        if not bid.get("qualified"):
            continue
        price = yard_price(lot, bid["price"])
        if best is None or price > best:
            best = price
        if bid["buyer"] not in bidders:
            bidders.append(bid["buyer"])
    deliveries = [d for d in packet.deliveries_by_lot.get(lot_id, [])
                  if parse_date(d["date"]) <= as_of]
    delivered_kg = sum(float(kg) for d in deliveries for kg in d["grade_kg"].values())
    delivered_amount = sum(float(d["amount"]) for d in deliveries)
    total = lot_kg(lot)
    if not lot["opened"]:
        category = NOT_OPENED
    else:
        status = lot["status"]
        if status == "sold":
            if delivered_kg <= 0:
                category = SOLD_UNDELIVERED
            elif delivered_kg < total - 1e-9:
                category = SOLD_PARTIAL
            else:
                category = SOLD_FULL
        elif status == "canceled":
            category = CANCELED
        elif status == "below":
            category = BELOW
        elif status == "no_bid":
            category = NO_BID
        else:
            raise ValueError(f"未知成交状态 {status!r}（标的 {lot_id}）")
    return Outcome(
        lot_id=lot_id,
        category=category,
        reserve_yard=yard_price(lot, lot["reserve"]),
        method=lot["method"],
        coverage=lot["coverage"],
        assignment=round_row.get("assignment", ""),
        group=round_row.get("group", ""),
        hammer_yard=yard_price(lot, lot.get("hammer")),
        best_bid_yard=best,
        bidders=bidders,
        hidden_bids=hidden,
        lot_kg=total,
        delivered_kg=delivered_kg,
        delivered_amount=delivered_amount,
        delivery_ids=[d["id"] for d in deliveries],
    )
