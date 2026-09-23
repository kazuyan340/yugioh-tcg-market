"""カードショップわいTV(cardshop-waitv.net)から遊戯王OCGの価格を取得し
price_history に保存するモジュール。

【重要な注記(2026-09時点で確認)】わいTVは現状、遊戯王OCGの在庫を一切持って
いない。サイト内検索(`/product-list?keyword=...`)で「遊戯王」「遊戯」
「OCG」「ブラックマジシャン」等どのキーワードで検索しても0件になる一方、
「ポケモン」では71件ヒットする(検索機能自体は正常)ため、キーワード表記の
問題ではなく単純に取扱いが無いと判断できる。ワンピースカードゲーム
(`/product-list/1`)・名探偵コナンTCG(`/product-list/110`)は取り扱いがある
ことを確認済みで、同じECカート(Ocnk系)上に「{カード名}（{パック略称}
{カード番号} {レアリティ}） 状態{状態ランク}」という商品名形式で並んでいる
(例: "ロックス・D・ジーベック（OP-17 OP17-118 SEC） 状態A-")。

そのため本スクレイパーは、わいTVが将来的に遊戯王OCGを取り扱い始めた場合に
備えてワンピース版と同じ解析ロジック(キーワード検索+括弧内トークン解析)を
遊戯王のカード番号形式向けに移植したものだが、現時点では実行しても
0件(対象商品なし)で終わる。定期実行フローに組み込む際は、この0件が
「スクレイパーの不具合」ではなく「現状の仕様」であることに注意すること。

売り切れの商品は<li>に"list_item_soldout"クラスが付いたまま一覧に残り続け、
価格欄には最後に売れた時の値段が表示されたままになることがある
(名探偵コナンTCG版わいTVスクレイパーでの実例: SR中森銀三)。これを除外しないと
売り切れなのに古い価格が最新の相場として記録され続けてしまうため、必ず除外する。
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

SEARCH_URL = "https://www.cardshop-waitv.net/product-list"
SITE_ROOT = "https://www.cardshop-waitv.net"
KEYWORD = "遊戯王"
PAGE_SIZE = 120

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_TIMEOUT = 15
REQUEST_DELAY_SEC = 30
MAX_PAGES = 30

PAREN_CONTENT = re.compile(r"（([^（）]*)）")
# 遊戯王のカード番号: 例 LOB-JP001, 20TH-JPC02, 26LP-JP008, SD42-JPS02
CARD_NUM_PATTERN = re.compile(r"^([A-Z0-9]{2,6}-[A-Za-z]{0,3}\d{2,4})$")
PACK_CODE_PATTERN = re.compile(r"^[A-Z0-9]{1,4}-\d{1,3}$")

logger = logging.getLogger(__name__)


def fetch_page(page: int) -> str:
    params = {"keyword": KEYWORD, "num": PAGE_SIZE, "page": page}
    resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _parse_name(name_text: str) -> tuple[str | None, str | None]:
    """商品名から(card_num, rarity)を取り出す。括弧内のトークンのうち、カード番号の
    形をしたものをcard_num、パック略称でもcard_numでもないものをrarityとする。
    """
    m = PAREN_CONTENT.search(name_text)
    if not m:
        return None, None
    card_num = None
    rarity = None
    for token in m.group(1).split():
        if CARD_NUM_PATTERN.match(token):
            card_num = token
        elif not PACK_CODE_PATTERN.match(token):
            rarity = token
    return card_num, rarity


def parse_items(html: str) -> list[tuple[str, str | None, int, str, str | None, str | None]]:
    """(card_num, rarity, price, 商品名, 商品画像URL, 商品ページURL) のリストを返す。
    売り切れ(list_item_soldout)の商品は古い価格が残ったままのことがあるため除外する。
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for li in soup.select("li.list_item_cell"):
        if "list_item_soldout" in (li.get("class") or []):
            continue

        name_el = li.select_one(".goods_name")
        if not name_el:
            continue

        name_text = name_el.get_text()
        card_num, rarity = _parse_name(name_text)
        if not card_num:
            continue

        price_el = li.select_one(".price .figure")
        if not price_el:
            continue
        price_text = price_el.get_text().replace("¥", "").replace(",", "").strip()
        try:
            price = int(price_text)
        except ValueError:
            continue

        photo_el = li.select_one(".global_photo")
        image_url = photo_el.get("data-src") if photo_el else None
        link_el = li.select_one("a.item_data_link")
        product_url = urljoin(SITE_ROOT, link_el.get("href")) if link_el and link_el.get("href") else None

        results.append((card_num, rarity, price, name_text.strip(), image_url, product_url))
    return results


def parse_total_count(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    el = soup.select_one(".count_number .number")
    if not el:
        return 0
    return int(el.get_text().replace(",", ""))


def sync_prices(conn=None, delay: float = REQUEST_DELAY_SEC, progress_callback=None) -> dict:
    """わいTVから遊戯王OCGの価格を取得し price_history に保存する。現状わいTVは
    遊戯王を取り扱っていないため(モジュールdocstring参照)、通常はmatched_cards=0の
    まま正常終了する。

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
                logger.warning("わいTVの取得に失敗 (page=%d): %s", page, exc)
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
                conn, card_id, "わいTV", min(prices),
                recorded_at=run_recorded_at, sample_count=len(prices),
            )

        write_unresolved("わいTV", unresolved_entries)

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
