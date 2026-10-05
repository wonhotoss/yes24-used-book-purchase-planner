"""yes24 중고샵 수집: 검색(책 → 허브 번호)과 허브 판매 목록(허브 번호 → 판매 건).

로그인 없이 정적 HTML만 쓴다. 허브 번호 = 새책 상품번호(goodsNo). Open API의 itemId와 같다.
"""
from __future__ import annotations

import re
import time
import urllib.parse
from dataclasses import dataclass, field, asdict
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

BASE = "https://www.yes24.com"
UA = "Mozilla/5.0 (personal used-book planner; low rate)"
DELAY = 1.0  # 요청 간 최소 간격(초)

# 예스24배송(직배송 중고)은 판매자 표기가 "유료 (… 15,000원 이상 무료)"뿐이라 금액을 상수로 둔다.
# 출처: https://m.yes24.com/Goods/Popup?viewName=DeliveryFee_Popup (2026-10-05 확인)
YES24_FEE = 2500
YES24_FREE_OVER = 15000

GRADES = {"01": "최상", "02": "상", "03": "중", "04": "하"}
CHANNELS = {"C2B": "예스24배송", "OFFSTORE": "매장ON", "C2C": "판매자 배송"}


@dataclass
class Candidate:
    """검색 결과의 책 하나(판본 단위)."""
    goods_no: int
    title: str
    subtitle: str = ""
    author: str = ""
    publisher: str = ""
    date: str = ""
    counts: dict = field(default_factory=dict)  # 채널 → 판매 건수


@dataclass
class Offer:
    """판매 건 하나."""
    goods_no: int        # 허브(책) 번호
    item_no: int         # 판매 건 상품번호 — 카트에 넣는 번호
    price: int
    grade: str           # 최상/상/중/하
    channel: str         # 예스24배송/매장ON/판매자 배송
    seller_id: str       # 배송비 묶음 단위
    seller_name: str
    fee: int             # 기본 배송비
    free_over: int | None  # 이 금액 이상이면 무료. None = 무조건 유료
    deli_text: str = ""
    rating: int | None = None
    rating_n: int | None = None
    business: bool = False  # 사업자 판매자


class Client:
    def __init__(self, cache_dir: Path | None = None, delay: float = DELAY):
        self.http = httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=20)
        self.cache_dir = cache_dir
        self.delay = delay
        self._last = 0.0

    def get(self, path: str, params: dict) -> str:
        url = BASE + path + "?" + urllib.parse.urlencode(params)
        cache = None
        if self.cache_dir:
            key = re.sub(r"[^0-9A-Za-z]+", "_", path + "_" + urllib.parse.urlencode(params))[:180]
            cache = self.cache_dir / f"{key}.html"
            if cache.exists():
                return cache.read_text(encoding="utf-8")
        wait = self._last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        r = self.http.get(url)
        self._last = time.monotonic()
        r.raise_for_status()
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(r.text, encoding="utf-8")
        return r.text

    def search(self, query: str) -> list[Candidate]:
        html = self.get("/product/search", {"query": query, "domain": "USED_GOODS"})
        return parse_search(html)

    def offers(self, goods_no: int, channel: str = "") -> list[Offer]:
        """허브의 판매 목록 전 페이지. channel='' 이면 전 채널."""
        out: list[Offer] = []
        page = 1
        while True:
            html = self.get("/Product/UsedShopHub/GoodsList", {
                "goodsNo": goods_no, "masterGoodsNo": goods_no, "delvType": channel,
                "pageNumber": page, "sortGb": "01", "levelGb": "00", "saleTypeGb": "00",
            })
            got = parse_offers(html, goods_no)
            out += got
            if not got or not has_next_page(html, page):
                return out
            page += 1


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def _int(s: str) -> int:
    return int(re.sub(r"[^0-9]", "", s))


def parse_search(html: str) -> list[Candidate]:
    soup = BeautifulSoup(html, "lxml")
    seen: dict[int, Candidate] = {}
    for a in soup.select("a.gd_name[href*='UsedShopHub/Hub/']"):
        m = re.search(r"Hub/(\d+)", a["href"])
        goods_no = int(m.group(1))
        if goods_no in seen:
            continue
        info = a.find_parent(class_="item_info")
        c = Candidate(goods_no=goods_no, title=_text(a), subtitle=_text(info.select_one(".gd_nameE")))
        c.author = _text(info.select_one(".info_auth"))
        c.publisher = _text(info.select_one(".info_pub"))
        c.date = _text(info.select_one(".info_date"))
        for b in info.select("a[href*='delvType=']"):
            ch = re.search(r"delvType=(\w+)", b["href"]).group(1)
            n = b.find("strong")
            if n:
                c.counts[CHANNELS.get(ch, ch)] = _int(n.get_text())
        seen[goods_no] = c
    return list(seen.values())


def parse_fee(text: str, channel: str) -> tuple[int, int | None]:
    """배송비 문구 → (기본 배송비, 무료 기준). 모르는 문구는 ValueError."""
    t = re.sub(r"\s+", " ", text).strip()
    if channel == "예스24배송":
        return YES24_FEE, YES24_FREE_OVER
    m = re.match(r"배송비 : 무료", t)
    if m:
        return 0, 0
    m = re.match(r"배송비 : ([\d,]+)원, .+?에서 ([\d,]+)원 이상 구매 시 무료배송", t)
    if m:
        return _int(m.group(1)), _int(m.group(2))
    m = re.match(r"배송비 : ([\d,]+)원, .+?에서 직접 배송", t)
    if m:
        return _int(m.group(1)), None
    raise ValueError(f"배송비 문구 해석 불가: {t!r}")


def parse_offers(html: str, goods_no: int) -> list[Offer]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for li in soup.select("li[salePrice][data-goods-no]"):
        channel = _text(li.select_one(".info_usedIco .txt"))
        deli = _text(li.select_one(".info_deli"))
        deli = deli.replace("예스24 배송비 무료 안내", "").strip()
        fee, free_over = parse_fee(deli, channel)
        store = li.select_one(".store_name a")
        href = store.get("href", "") if store else ""
        m = re.search(r"/mall/([^/]+)/", href)
        seller_id = m.group(1) if m else ("yes24" if channel == "예스24배송" else _text(store))
        rating = li.select_one(".store_rating .rating")
        rn = re.search(r"rating_(\d)", " ".join(rating.get("class", []))) if rating else None
        cnt = re.search(r"\(([\d,]+)명", _text(li.select_one(".store_txt")))
        out.append(Offer(
            goods_no=goods_no,
            item_no=int(li["data-goods-no"]),
            price=int(li["saleprice"]),
            grade=GRADES.get(li.get("levelgb", ""), li.get("levelgb", "")),
            channel=channel,
            seller_id=seller_id,
            seller_name=_text(store),
            fee=fee,
            free_over=free_over,
            deli_text=deli,
            rating=int(rn.group(1)) if rn else None,
            rating_n=_int(cnt.group(1)) if cnt else None,
            business=li.select_one(".item_storeCol .isSeller") is not None,
        ))
    return out


def has_next_page(html: str, page: int) -> bool:
    soup = BeautifulSoup(html, "lxml")
    pages = [int(a.get_text()) for a in soup.select(".yesUI_pagen a.num") if a.get_text().strip().isdigit()]
    return any(p > page for p in pages)


def offer_dict(o: Offer) -> dict:
    return asdict(o)
