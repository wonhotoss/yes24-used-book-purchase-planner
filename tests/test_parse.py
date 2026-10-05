from pathlib import Path

import pytest

from ubc.yes24 import YES24_FEE, YES24_FREE_OVER, has_next_page, parse_fee, parse_offers, parse_search

FX = Path(__file__).parent / "fixtures"


def load(name):
    return (FX / name).read_text(encoding="utf-8")


def test_search_isbn():
    [c] = parse_search(load("search_9788966260959.html"))
    assert c.goods_no == 11681152
    assert c.publisher.startswith("인사이트")
    assert c.counts == {"예스24배송": 2, "매장ON": 9, "판매자 배송": 50}


def test_offers_seller_delivery():
    html = load("goodslist_11681152_C2C_p1.html")
    offs = parse_offers(html, 11681152)
    assert len(offs) == 24
    assert has_next_page(html, 1)
    o = offs[0]
    assert (o.item_no, o.price, o.grade, o.channel) == (193993793, 1400, "중", "판매자 배송")
    assert (o.seller_id, o.seller_name, o.fee, o.free_over) == ("goodkmh", "작은곰", 3300, 23000)
    assert (o.rating, o.rating_n, o.business) == (4, 53, True)
    direct = offs[1]
    assert (direct.seller_id, direct.fee, direct.free_over) == ("healthwan", 3300, None)


def test_offers_yes24_and_store():
    [a, b] = parse_offers(load("hub_11681152_C2B.html"), 11681152)
    assert a.channel == b.channel == "예스24배송"
    assert a.seller_id == b.seller_id == "yes24"
    assert (a.fee, a.free_over) == (YES24_FEE, YES24_FREE_OVER)
    offs = parse_offers(load("hub_11681152_OFFSTORE.html"), 11681152)
    assert len(offs) == 9
    assert {o.channel for o in offs} == {"매장ON"}
    assert offs[0].seller_id == "yes24off02" and offs[0].seller_name == "YES24 목동점"
    assert (offs[0].fee, offs[0].free_over) == (2500, 20000)


def test_offers_last_page():
    html = load("goodslist_11681152_C2C_p3.html")
    assert len(parse_offers(html, 11681152)) == 2  # 50건 = 24 + 24 + 2
    assert not has_next_page(html, 3)


@pytest.mark.parametrize("text,want", [
    ("배송비 : 3,300원, 작은곰에서 23,000원 이상 구매 시 무료배송", (3300, 23000)),
    ("배송비 : 6,000원, 여백의 서재에서 직접 배송", (6000, None)),
    ("배송비 : 무료", (0, 0)),
    ("배송비 : 무료배송, 누구에서 무료배송", (0, 0)),
])
def test_parse_fee(text, want):
    assert parse_fee(text, "판매자 배송") == want


def test_parse_fee_unknown():
    with pytest.raises(ValueError):
        parse_fee("배송비 : 착불", "판매자 배송")


def test_cart_summary():
    from ubc.cart import parse_summary
    t = "총 상품금액 총 추가금액 총 할인금액 최종 결제금액 32,650원 + 3,000원 + 0원 + 35,650원 정가 47,100원에서"
    assert parse_summary(t) == (32650, 3000, 35650)
    assert parse_summary("카트에 담긴 상품이 없습니다") == (None, None, None)
