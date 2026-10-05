"""카트 대조: 계획한 판매 건을 비회원 카트에 담고, 카트가 계산한 금액을 읽는다.

중고 판매 건 상세 페이지(/product/goods/{판매건})에는 카트 스크립트(order_payment)가 실리지 않아
버튼이 동작하지 않는다(2026-10-05 확인). 그래서 사람이 하듯 허브 목록에서 해당 판매 건의 '카트에 넣기'를 누른다.
결제·주문 단계로는 가지 않는다.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .yes24 import BASE, Offer

CART_URL = "https://ssl.yes24.com/Cart/Cart"


@dataclass
class CartResult:
    added: list[int]
    missing: list[int]
    goods_total: int | None
    extra_total: int | None   # 배송비 등 추가금액
    pay_total: int | None     # 최종 결제금액
    screenshot: Path | None


def verify(picks: list[Offer], screenshot: Path | None = None, max_pages: int = 10) -> CartResult:
    from playwright.sync_api import sync_playwright

    proxy = {"server": os.environ["HTTPS_PROXY"]} if os.environ.get("HTTPS_PROXY") else None
    added, missing = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(proxy=proxy)
        page = browser.new_context(locale="ko-KR", viewport={"width": 1400, "height": 1000}).new_page()
        page.on("dialog", lambda d: d.dismiss())
        for o in picks:
            for n in range(1, max_pages + 1):
                page.goto(f"{BASE}/Product/UsedShopHub/Hub/{o.goods_no}?delvType=&pageNumber={n}"
                          "&sortGb=01&levelGb=00&saleTypeGb=00", wait_until="networkidle", timeout=60000)
                li = page.locator(f"li[data-goods-no='{o.item_no}']")
                if li.count():
                    with page.expect_response(lambda r: "AjaxPage/Cart/Cart" in r.url):
                        li.get_by_role("link", name="카트에 넣기").first.click()
                    added.append(o.item_no)
                    break
                if not page.locator(".yesUI_pagen a.num", has_text=str(n + 1)).count():
                    break
            if o.item_no not in added:
                missing.append(o.item_no)
        page.goto(CART_URL, wait_until="networkidle", timeout=60000)
        text = re.sub(r"\s+", " ", page.inner_text("body"))
        if screenshot:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
        browser.close()
    goods, extra, pay = parse_summary(text)
    return CartResult(added=added, missing=missing, goods_total=goods, extra_total=extra,
                      pay_total=pay, screenshot=screenshot)


def parse_summary(text: str) -> tuple[int | None, int | None, int | None]:
    """카트 하단 요약 '총 상품금액 총 추가금액 총 할인금액 최종 결제금액 A원 + B원 + C원 + D원' → (A, B, D)."""
    m = re.search(r"최종 결제금액\s*([\d,]+)원\s*\+\s*([\d,]+)원\s*\+\s*([\d,]+)원\s*\+\s*([\d,]+)원", text)
    if not m:
        return None, None, None
    a, b, _, d = (int(g.replace(",", "")) for g in m.groups())
    return a, b, d
