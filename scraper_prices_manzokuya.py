"""カードショップ「まんぞく屋」(shopmanzokuya.com)から遊戯王OCGの価格を取得し
price_history に保存するモジュール。

対象カテゴリ(category_id=1)が「遊戯王OCG」の単品カードをまとめている
(2026-09時点で実測10,691件・pageno=1..107)。近縁カテゴリに
category_id=1009(アジア版)、1747(高額・希少品)があるが、通常のcard_num
(=公式サイトの型番)に基づくマッチングでは同じcard_numが混在しうるため、
まずは主要カテゴリ(1)のみを対象にする。

商品名は独自のブラケット表記が混在しており、ワンピース版のような単純な
「カード番号+スラッシュ区切りレアリティ」ではない
(例: "[SE] 26LP-JP008 《ＶＳ 龍帝ヴァリウス》"、
"(O-PSE) UT01-JP004 《ダーク・アームド・ドラゴン・バニッシャー》")。
カード番号直前の最も近い[]/〈〉/()内のトークンをレアリティ候補として拾うが、
これは店舗独自の略号(SE, PSE, UR等)であり、DBのrarity列(公式サイトの
正式なレアリティ名)と文字列が一致するとは限らない。一致すれば絞り込みに
使われ、一致しなければprice_matching.resolve()が自動では絞り込まず
(rarity不一致時は無視してcard_num単位の全候補に戻る)、通常通り
1件だけならresolved、複数ならambiguousとして扱われる。

"(3枚ずつセット販売)"のようなセット売り商品は単品カードとして価格比較
できないため除外する。まんぞく屋のrobots.txtは`/*.csv$`のみDisallowで
一般クローラーへの制限が無いが、他サイトと同様に安全側でリクエスト間隔
30秒を採用する。
"""
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import db
import price_matching
from unresolved_report import write_unresolved

BASE_URL = "https://shopmanzokuya.com"
LIST_URL = BASE_URL + "/products/list"
CATEGORY_ID = 1  # 遊戯王OCG(主要カテゴリ)
PAGE_SIZE = 100

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_TIMEOUT = 15
REQUEST_DELAY_SEC = 30
MAX_PAGES = 130  # 安全のための上限(実際の最終ページはparse_total_countから算出)

# カード番号: 例 "UT01-JP004", "26LP-JP008", "15AX-JPM57", "20TH-JPC02"
CARD_NUM_PATTERN = re.compile(r"\b([A-Z0-9]{2,6}-[A-Z]{1,3}\d{2,4})\b")
# 商品名の《》内がカード名
CARD_NAME_PATTERN = re.compile(r"《([^》]+)》")
# []/〈〉/()内のトークン(レアリティ候補・店舗独自略号のことが多い)
BRACKET_PATTERN = re.compile(r"\[([^\]]+)\]|〈\s*([^〉]+?)\s*〉|\(([^)]+)\)")

TOTAL_COUNT_PATTERN = re.compile(r"(\d+)件")

logger = logging.getLogger(__name__)


def fetch_page(page: int) -> str:
    params = {"category_id": CATEGORY_ID, "pageno": page}
    resp = requests.get(LIST_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _parse_name(text: str) -> tuple[str | None, str | None, str | None]:
    """商品名から(card_num, rarity, product_name)を取り出す。rarityはcard_numの
    直前にある最も近いブラケット内トークン(なければNone)。
    """
    m_num = CARD_NUM_PATTERN.search(text)
    m_name = CARD_NAME_PATTERN.search(text)
    if not m_num or not m_name:
        return None, None, None

    best = None
    for bm in BRACKET_PATTERN.finditer(text):
        if bm.end() <= m_num.start():
            best = bm
    rarity = next((g for g in best.groups() if g), None) if best else None
    return m_num.group(1), rarity, m_name.group(1)


def parse_items(html: str) -> list[tuple[str, str | None, int, str, str | None, str | None]]:
    """(card_num, rarity, price, 商品名, 商品画像URL, 商品ページURL) のリストを返す。
    セット販売商品や、単品カードとして特定できない商品は除外する
    (在庫0件は一覧に表示されないため別途フィルタ不要)。
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for li in soup.select("li.ec-shelfGrid__item"):
        name_el = li.select_one(".ec-shelfGrid__item-text")
        if not name_el:
            continue

        product_name = name_el.get_text(strip=True)
        if "セット販売" in product_name:
            continue

        card_num, rarity, card_name = _parse_name(product_name)
        if not card_num:
            continue

        price_el = li.select_one(".price02-default")
        if not price_el:
            continue
        price_text = price_el.get_text().replace("￥", "").replace(",", "")
        price_text = re.sub(r"\(税込\)", "", price_text).strip()
        try:
            price = int(price_text)
        except ValueError:
            continue

        link_el = li.find("a", href=True)
        product_url = urljoin(BASE_URL, link_el["href"]) if link_el else None
        img_el = li.select_one(".ec-shelfGrid__item-image img")
        image_url = urljoin(BASE_URL, img_el["src"]) if img_el and img_el.get("src") else None

        results.append((card_num, rarity, price, product_name, image_url, product_url))
    return results


def parse_total_count(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    el = soup.select_one(".ec-searchnavRole__counter .ec-font-bold")
    if not el:
        return 0
    m = TOTAL_COUNT_PATTERN.search(el.get_text())
    return int(m.group(1)) if m else 0


def sync_prices(conn=None, delay: float = REQUEST_DELAY_SEC, progress_callback=None) -> dict:
    """まんぞく屋から遊戯王OCGの価格を取得し price_history に保存する。

    progress_callback(page, last_page, matched_count) が指定されていれば
    ページ取得のたびに呼び出す。
    """
    owns_conn = conn is None
    if owns_conn:
        conn = db.get_connection()
        db.init_db(conn)

    lookup = price_matching.build_lookup(conn)
    manual_resolutions = price_matching.load_manual_resolutions()
    all_prices: dict[str, list[int]] = defaultdict(list)
    unresolved_entries: list[dict] = []
    first_request = True

    try:
        page = 1
        last_page = 1
        while page <= min(last_page, MAX_PAGES):
            if not first_request:
                time.sleep(delay)
            first_request = False

            try:
                html = fetch_page(page)
            except requests.RequestException as exc:
                logger.warning("まんぞく屋の取得に失敗 (page=%d): %s", page, exc)
                break

            if page == 1:
                total = parse_total_count(html)
                last_page = max(1, -(-total // PAGE_SIZE))  # 切り上げ除算

            for card_num, rarity, price, product_name, image_url, product_url in parse_items(html):
                price_matching.apply_resolution(
                    all_prices, unresolved_entries, card_num, rarity, price,
                    lookup, manual_resolutions, product_name, image_url, product_url,
                )

            if progress_callback:
                progress_callback(page, last_page, len(all_prices))

            page += 1

        run_recorded_at = datetime.now(timezone.utc).isoformat()
        for card_id, prices in all_prices.items():
            db.insert_price(
                conn, card_id, "まんぞく屋", min(prices),
                recorded_at=run_recorded_at, sample_count=len(prices),
            )

        write_unresolved("まんぞく屋", unresolved_entries)

        summary = {"matched_cards": len(all_prices), "unresolved_listings": len(unresolved_entries)}
        logger.info(
            "完了: %d枚の価格を取得 (特定できなかった出品 %d件は管理ページへ)",
            summary["matched_cards"], summary["unresolved_listings"],
        )
        return summary
    finally:
        if owns_conn:
            conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    def _print_progress(page, last_page, matched_count):
        logger.info("ページ%d/%d取得完了 (累計マッチ %d件)", page, last_page, matched_count)

    sync_prices(progress_callback=_print_progress)
