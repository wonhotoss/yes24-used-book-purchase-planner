"""구매 계획 최적화: 책마다 판매 건 하나를 골라 (책값 + 판매자별 배송비) 합을 최소화.

판매자 s의 배송비 = fee_s, 단 s에서 산 책값 합이 free_over_s 이상이면 0.
무료 기준 때문에 '조금 비싼 책을 같은 판매자에게서 사서 무료배송을 넘기는' 해가 나오므로
탐욕법으로는 못 풀고 MILP(정수계획)로 푼다. 책 수십 권·판매자 수백 곳은 CBC가 즉시 푼다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pulp

from .yes24 import Offer

GRADE_RANK = {"최상": 0, "상": 1, "중": 2, "하": 3}


@dataclass
class Book:
    """사려는 책 한 권. goods_nos 중 어느 판본이든 하나면 된다."""
    label: str
    goods_nos: list[int]
    offers: list[Offer] = field(default_factory=list)


@dataclass
class Filters:
    min_grade: str = "하"          # 이 등급 이상만
    min_rating: int | None = None  # 판매자 만족도(1~5) 이상만. 매장ON·예스24배송은 통과
    exclude_sellers: set[str] = field(default_factory=set)

    def ok(self, o: Offer) -> bool:
        if GRADE_RANK.get(o.grade, 9) > GRADE_RANK[self.min_grade]:
            return False
        if o.seller_id in self.exclude_sellers:
            return False
        if self.min_rating and o.channel == "판매자 배송" and (o.rating or 0) < self.min_rating:
            return False
        return True


@dataclass
class Plan:
    picks: dict[str, Offer]            # 책 label → 고른 판매 건
    shipping: dict[str, int]           # 판매자 → 배송비
    total: int
    status: str

    @property
    def book_sum(self) -> int:
        return sum(o.price for o in self.picks.values())


def candidates(book: Book, f: Filters) -> list[Offer]:
    """필터를 통과한 판매 건 중 판매자별 최저가 하나씩(같은 판매자의 더 비싼 건은 지배당함)."""
    best: dict[str, Offer] = {}
    for o in book.offers:
        if not f.ok(o):
            continue
        cur = best.get(o.seller_id)
        if cur is None or o.price < cur.price or (o.price == cur.price and GRADE_RANK[o.grade] < GRADE_RANK[cur.grade]):
            best[o.seller_id] = o
    return list(best.values())


def solve(books: list[Book], f: Filters | None = None) -> Plan:
    f = f or Filters()
    cand = {b.label: candidates(b, f) for b in books}
    missing = [k for k, v in cand.items() if not v]
    if missing:
        raise ValueError(f"조건에 맞는 판매 건이 없는 책: {missing}")

    seller: dict[str, Offer] = {}  # 판매자 → 배송 조건 대표 판매 건
    for offs in cand.values():
        for o in offs:
            seller.setdefault(o.seller_id, o)

    prob = pulp.LpProblem("used_books", pulp.LpMinimize)
    def binary(name):
        return prob.add_variable(name, cat="Binary")

    x = {(k, o.seller_id): binary(f"x_{i}_{j}")
         for i, (k, offs) in enumerate(cand.items()) for j, o in enumerate(offs)}
    y = {s: binary(f"y_{i}") for i, s in enumerate(seller)}  # 판매자 이용
    z = {s: binary(f"z_{i}") for i, s in enumerate(seller)}  # 배송비 면제

    price = {(k, o.seller_id): o.price for k, offs in cand.items() for o in offs}
    prob += (pulp.lpSum(price[key] * v for key, v in x.items())
             + pulp.lpSum(seller[s].fee * (y[s] - z[s]) for s in seller))

    for k, offs in cand.items():
        prob += pulp.lpSum(x[k, o.seller_id] for o in offs) == 1
    for (k, s), v in x.items():
        prob += v <= y[s]
    for s, o in seller.items():
        prob += z[s] <= y[s]
        if o.free_over is None:
            prob += z[s] == 0
        elif o.free_over > 0:
            sub = pulp.lpSum(price[key] * v for key, v in x.items() if key[1] == s)
            prob += sub >= o.free_over * z[s]

    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    status = pulp.LpStatus[prob.status]

    picks = {}
    for k, offs in cand.items():
        for o in offs:
            if x[k, o.seller_id].value() > 0.5:
                picks[k] = o
    shipping = {}
    for s in {o.seller_id for o in picks.values()}:
        sub = sum(o.price for o in picks.values() if o.seller_id == s)
        shipping[s] = shipping_fee(seller[s], sub)
    total = sum(o.price for o in picks.values()) + sum(shipping.values())
    return Plan(picks=picks, shipping=shipping, total=total, status=status)


def shipping_fee(o: Offer, subtotal: int) -> int:
    if o.free_over is None:
        return o.fee
    return 0 if subtotal >= o.free_over else o.fee


def naive(books: list[Book], f: Filters | None = None) -> Plan:
    """비교 기준: 책마다 (책값 + 그 판매자 배송비)가 가장 싼 건을 따로따로 산다."""
    f = f or Filters()
    picks = {}
    for b in books:
        picks[b.label] = min(candidates(b, f), key=lambda o: o.price + shipping_fee(o, o.price))
    shipping = {}
    for s in {o.seller_id for o in picks.values()}:
        mine = [o for o in picks.values() if o.seller_id == s]
        shipping[s] = shipping_fee(mine[0], sum(o.price for o in mine))
    total = sum(o.price for o in picks.values()) + sum(shipping.values())
    return Plan(picks=picks, shipping=shipping, total=total, status="naive")
