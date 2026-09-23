"""カードラボ(c-labo-online.jp)から遊戯王OCGの価格を取得し price_history に保存する
モジュール。

カードラボの検索結果には goods_name というクラスの要素に
"【遊戯】{カード名}【{レアリティ}/{カードタイプ}】{カード番号}" という形式で
カード番号がそのまま残っている(例: "【遊戯】王の舞台【ノーマル/魔法】DBMF-JP034"、
"【遊戯】王のしもべ-ブラック・マジシャン【プリズマティックシークレット/効果】LOCH-JP001")。
末尾のスラッシュ以降(魔法/罠/効果/モンスター種族等)はレアリティではなくカード
タイプの注記なので切り捨てる。キーワード「遊戯」1つでワンピース版の「OP」と同様に
遊戯王カテゴリ全体を横断検索できる(2026-09時点で実測約10,000件・num=120で84ページ)。

同じcard_numに対して複数レアリティ(再録・パラレル違い等)が存在するケースは、
公式サイト側データでも絵違いまで区別できないことがある。そのような場合は自動で
決め打ちせず、price_matching.apply_resolution()経由でunresolvedとして記録し、
管理ページ(site/admin-unofficial-cards.html)でユーザーに選んでもらう。
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

SEARCH_URL = "https://www.c-labo-online.jp/product-list/"
BASE_URL = "https://www.c-labo-online.jp"
KEYWORD = "遊戯"
PAGE_SIZE = 120

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_TIMEOUT = 15
REQUEST_DELAY_SEC = 30
MAX_PAGES = 120  # 安全のための上限(実際の最終ページはparse_last_pageから算出)

# 例: "【遊戯】王の舞台【ノーマル/魔法】DBMF-JP034"
#     "【遊戯】王のしもべ-ブラック・マジシャン【プリズマティックシークレット/効果】LOCH-JP001"
NAME_PATTERN = re.compile(
    r"^【遊戯】(.+?)【([^/】]+)(?:/[^】]*)?】([A-Za-z0-9]+-[A-Za-z0-9]+)\s*$"
)
PAGE_LINK_PATTERN = re.compile(r"[?&](?:amp;)?page=(\d+)")

logger = logging.getLogger(__name__)


def fetch_search_page(page: int) -> str:
    params = {"keyword": KEYWORD, "num": PAGE_SIZE, "page": page}
    resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def parse_items(html: str) -> list[tuple[str, str, int | None, str | None, str | None, str | None]]:
    """(card_num, rarity, price, 商品名, 商品画像URL, 商品ページURL) のリストを返す。
    在庫切れの場合はpriceがNoneになる。
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for li in soup.select("li.list_item_cell"):
        name_el = li.select_one(".goods_name")
        if not name_el:
            continue

        name_text = name_el.get_text()
        m = NAME_PATTERN.match(name_text)
        if not m:
            continue
        product_name, rarity, card_num = m.group(1), m.group(2), m.group(3)

        photo_el = li.select_one(".global_photo")
        image_url = photo_el.get("data-src") if photo_el else None
        link_el = li.select_one("a.item_data_link")
        product_url = urljoin(BASE_URL, link_el.get("href")) if link_el and link_el.get("href") else None

        if "list_item_soldout" in (li.get("class") or []):
            results.append((card_num, rarity, None, product_name, image_url, product_url))
            continue

        price_el = li.select_one(".price .figure")
        if not price_el:
            continue

        price_text = price_el.get_text().split("円")[0].replace(",", "").strip()
        try:
            price = int(price_text)
        except ValueError:
            continue

        results.append((card_num, rarity, price, product_name, image_url, product_url))
    return results


def parse_last_page(html: str) -> int:
    """ページネーションのリンクに現れる最大のpage番号を最終ページとする。
    count_numberの件数表示は上限(10,000件)でカンストするため、これをPAGE_SIZEで
    割って最終ページを算出すると実際より少なく見積もる恐れがあり使わない。
    """
    numbers = [int(n) for n in PAGE_LINK_PATTERN.findall(html)]
    return max(numbers) if numbers else 1


def sync_prices(conn=None, delay: float = REQUEST_DELAY_SEC, progress_callback=None) -> dict:
    """カードラボから遊戯王OCGの価格を取得し price_history に保存する。

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
                html = fetch_search_page(page)
            except requests.RequestException as exc:
                logger.warning("カードラボの取得に失敗 (page=%d): %s", page, exc)
                break

            if page == 1:
                last_page = parse_last_page(html)

            for card_num, rarity, price, product_name, image_url, product_url in parse_items(html):
                if price is None:
                    continue
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
                conn, card_id, "カードラボ", min(prices),
                recorded_at=run_recorded_at, sample_count=len(prices),
            )

        write_unresolved("カードラボ", unresolved_entries)

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
