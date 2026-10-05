import itertools
import random

from ubc.optimize import Book, Filters, naive, shipping_fee, solve
from ubc.yes24 import Offer

SELLERS = {  # id → (fee, free_over)
    "a": (3300, 20000), "b": (2500, None), "c": (6000, None), "d": (3000, 15000), "e": (0, 0),
}


def offer(book, seller, price, grade="상"):
    fee, free = SELLERS[seller]
    return Offer(goods_no=book, item_no=hash((book, seller, price)) & 0xFFFFFF, price=price, grade=grade,
                 channel="판매자 배송", seller_id=seller, seller_name=seller, fee=fee, free_over=free)


def brute(books):
    best = None
    for combo in itertools.product(*[b.offers for b in books]):
        sub = {}
        for o in combo:
            sub[o.seller_id] = sub.get(o.seller_id, 0) + o.price
        rep = {o.seller_id: o for o in combo}
        total = sum(sub.values()) + sum(shipping_fee(rep[s], v) for s, v in sub.items())
        best = total if best is None else min(best, total)
    return best


def test_threshold_pulls_books_together():
    # a에서 둘 다 사면 20,000 넘어 무료. 따로 사면 b가 싸 보이지만 배송비가 두 번.
    books = [Book("x", [1], [offer(1, "a", 11000), offer(1, "b", 9000)]),
             Book("y", [2], [offer(2, "a", 9500), offer(2, "c", 8000)])]
    plan = solve(books)
    assert plan.total == 20500
    assert {o.seller_id for o in plan.picks.values()} == {"a"}
    assert naive(books).total > plan.total


def test_matches_brute_force():
    rng = random.Random(7)
    for _ in range(40):
        books = []
        for i in range(rng.randint(2, 5)):
            sellers = rng.sample(sorted(SELLERS), rng.randint(1, 4))
            books.append(Book(f"b{i}", [i], [offer(i, s, rng.randrange(1000, 15000, 100)) for s in sellers]))
        assert solve(books).total == brute(books)


def test_filters():
    books = [Book("x", [1], [offer(1, "a", 1000, "하"), offer(1, "b", 5000, "상")])]
    assert solve(books, Filters(min_grade="상")).picks["x"].seller_id == "b"
    assert solve(books, Filters(exclude_sellers={"a"})).picks["x"].seller_id == "b"
