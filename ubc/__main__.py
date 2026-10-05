"""사용법: python -m ubc 목록.txt [--min-grade 상] [--min-rating 4] [--exclude 판매자ID ...] [-o plan.md] [--verify-cart]

목록 파일 한 줄 = 책 한 권.
  클린 코드 인사이트          검색어 — 중고샵 검색 1순위 판본을 쓴다
  9788966260959              ISBN도 검색어로 통한다
  데미안 = 176787 8156036     판본 고정 — '=' 뒤 상품번호 중 어느 것이든 하나면 된다
  # 주석
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

from .optimize import Book, Filters, Plan, naive, solve
from .yes24 import Client

HERE = Path(__file__).resolve().parent.parent


def read_list(path: Path) -> list[tuple[str, list[int] | None]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            label, nums = line.split("=", 1)
            out.append((label.strip(), [int(n) for n in nums.replace(",", " ").split()]))
        else:
            out.append((line, None))
    return out


def hub(goods_no: int) -> str:
    return f"https://www.yes24.com/Product/UsedShopHub/Hub/{goods_no}"


def resolve(c: Client, items) -> tuple[list[Book], list[str]]:
    """검색어 → 1순위 판본. 보고서용으로 판본 후보 표를 함께 만든다."""
    books, notes = [], []
    for label, nums in items:
        if nums:
            books.append(Book(label=label, goods_nos=nums))
            notes += [f"### {label}", "", "판본 고정: " + ", ".join(f"[`{n}`]({hub(n)})" for n in nums), ""]
            continue
        cands = c.search(label)
        if not cands:
            notes += [f"### {label}", "", "중고샵 검색 결과 없음 — 건너뜀", ""]
            continue
        books.append(Book(label=label, goods_nos=[cands[0].goods_no]))
        notes += [f"### {label}", "",
                  "| | 상품번호 | 제목 | 출판사 · 출간 | 판매 건 |", "|---|---|---|---|---|"]
        for i, a in enumerate(cands[:6]):
            title = a.title + (f" — {a.subtitle}" if a.subtitle and len(a.subtitle) < 40 else "")
            counts = " · ".join(f"{k} {v}" for k, v in a.counts.items())
            notes.append(f"| {'**사용**' if i == 0 else ''} | [`{a.goods_no}`]({hub(a.goods_no)}) | {title} "
                         f"| {a.publisher} · {a.date} | {counts} |")
        if len(cands) > 6:
            notes.append(f"| | | … 외 {len(cands) - 6}건 | | |")
        notes += ["", f"다른 판본을 쓰려면 목록에 `{label} = 상품번호`로 적는다.", ""]
    return books, notes


def won(n: int) -> str:
    return f"{n:,}원"


def render(plan: Plan, base: Plan, books: list[Book], notes: list[str], f: Filters,
           grades: dict[str, Plan | None], cart=None) -> str:
    L = [f"# 중고책 구매 계획 ({dt.date.today()})", ""]
    L += [f"조건: 등급 {f.min_grade} 이상"
          + (f", 판매자 만족도 {f.min_rating} 이상" if f.min_rating else "")
          + (f", 제외 {sorted(f.exclude_sellers)}" if f.exclude_sellers else ""), ""]
    L += [f"**총액 {won(plan.total)}** = 책값 {won(plan.book_sum)} + 배송비 {won(sum(plan.shipping.values()))}"
          f" · 주문처 {len(plan.shipping)}곳 · 최적화 {plan.status}", ""]
    L += [f"비교: 책마다 따로 최저가(배송비 포함)로 사면 {won(base.total)} (주문처 {len(base.shipping)}곳)"
          f" → **{won(base.total - plan.total)} 절약**", ""]

    if cart:
        ok = cart.pay_total == plan.total and not cart.missing
        L += ["## 카트 대조", "",
              f"비회원 카트에 {len(cart.added)}건을 담았다. 카트 계산: 상품 {won(cart.goods_total or 0)}"
              f" + 추가(배송비) {won(cart.extra_total or 0)} = **최종 {won(cart.pay_total or 0)}**"
              f" → 계획과 {'**일치**' if ok else '**불일치 — 확인 필요**'}"
              + (f". 못 담은 판매 건: {cart.missing}" if cart.missing else ""), ""]
        if cart.screenshot:
            L += [f"![카트]({cart.screenshot.name})", ""]

    L += ["## 주문처별", ""]
    by_seller: dict[str, list] = {}
    for label, o in plan.picks.items():
        by_seller.setdefault(o.seller_id, []).append((label, o))
    for s, rows in sorted(by_seller.items(), key=lambda kv: -sum(o.price for _, o in kv[1])):
        o0 = rows[0][1]
        sub = sum(o.price for _, o in rows)
        L += [f"### {o0.seller_name} ({o0.channel}) — 책 {won(sub)} + 배송 {won(plan.shipping[s])}", ""]
        L += [f"배송 조건: {o0.deli_text}" + (f" · 만족도 {o0.rating}점({o0.rating_n}명)" if o0.rating else ""), ""]
        L += ["| 책 | 등급 | 가격 | 판매 건 |", "|---|---|---:|---|"]
        for label, o in rows:
            L += [f"| {label} | {o.grade} | {won(o.price)} | [{o.item_no}](https://www.yes24.com/product/goods/{o.item_no}) |"]
        L += [""]

    L += ["## 등급 조건별 최적 총액", "", "| 최저 등급 | 총액 | 주문처 | 구성 |", "|---|---:|---:|---|"]
    for g, gp in grades.items():
        if gp is None:
            L.append(f"| {g} | — | — | 조건에 맞는 판매 건이 없는 책이 있음 |")
            continue
        desc = "; ".join(f"{o.seller_name}: " + ", ".join(f"{k.split()[-1]}({o.grade} {o.price:,})"
                         for k, o in gp.picks.items() if o.seller_id == s)
                         for s, o in {o.seller_id: o for o in gp.picks.values()}.items())
        L.append(f"| {g} 이상 | {won(gp.total)} | {len(gp.shipping)} | {desc} |")
    L += [""]

    L += ["## 판본", ""] + notes
    L += ["## 참고", "",
          "- 가격·재고는 조회 시점 기준. 결제 전 카트 금액으로 확인할 것",
          "- 도서산간 추가 배송비, 쿠폰·포인트는 반영하지 않았다", ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ubc", description="yes24 중고책 구매처 계획")
    ap.add_argument("list", type=Path)
    ap.add_argument("--min-grade", default="하", choices=["최상", "상", "중", "하"])
    ap.add_argument("--min-rating", type=int)
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--cache", type=Path, help="HTML 캐시 디렉터리(같은 날 재실행용)")
    ap.add_argument("--verify-cart", action="store_true", help="계획대로 비회원 카트에 담아 금액 대조(Playwright)")
    a = ap.parse_args(argv)

    c = Client(cache_dir=a.cache)
    books, notes = resolve(c, read_list(a.list))
    for b in books:
        for g in b.goods_nos:
            b.offers += c.offers(g)
        print(f"{b.label}: 판매 건 {len(b.offers)}", file=sys.stderr)

    f = Filters(min_grade=a.min_grade, min_rating=a.min_rating, exclude_sellers=set(a.exclude))
    plan, base = solve(books, f), naive(books, f)
    grades = {}
    for g in ["하", "중", "상", "최상"]:
        try:
            grades[g] = solve(books, replace(f, min_grade=g))
        except ValueError:
            grades[g] = None
    cart = None
    if a.verify_cart:
        from .cart import verify
        shot = a.out.with_suffix(".cart.png") if a.out else None
        cart = verify(list(plan.picks.values()), screenshot=shot)
    md = render(plan, base, books, notes, f, grades, cart)
    if a.out:
        a.out.write_text(md, encoding="utf-8")
        snap = a.out.with_suffix(".offers.json")
        snap.write_text(json.dumps({b.label: [asdict(o) for o in b.offers] for b in books},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
