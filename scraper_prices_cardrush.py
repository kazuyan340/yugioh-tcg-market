"""カードラッシュ(www.cardrush.jp)から遊戯王OCGの価格を取得し price_history に
保存するモジュール。

【重要】ワンピース版(onepiece-card-game/scraper_prices_cardrush.py)はカードラッシュ
「メディア」側(cardrush.media)のNext.js製カード図鑑JSON APIを叩いていたが、
cardrush.mediaのカード図鑑機能はポケモン・ワンピースのみが対象で、
`/yugioh/cards` 系のパスは404になり遊戯王には使えないことを確認した
(2026-09時点)。カードラッシュの遊戯王専門店は別ドメインの
`www.cardrush.jp`(タイトル: 「【カードラッシュ】遊戯王が日本最大級の通販サイト」)
であり、これはカードラボ・竜のしっぽ・まんぞく屋と同じ系列のECカート
(Ocnk系)で運営されている。そのため本スクレイパーは、cardrush.mediaのJSON APIでは
なく、他5店と同じ「product-list HTMLページを直接スクレイピングする」方式で実装する。

www.cardrush.jpは遊戯王カード専門サイトなので、カードラボのような
「キーワードで他ジャンルから絞り込む」処理は不要で、`/product-list`を
そのままページングするだけで全商品(2026-09時点で実測89,095件、
num=120でページネーションリンク上の最終ページは891)を横断できる。

商品名(`.goods_name`)は「{カード名}(注記等)【{レアリティ}】{{カード番号}}
《{カードタイプ}》」という形式(例: "〔PSA10鑑定済〕青眼の白龍【レリーフ】{SM-51}
《モンスター》")。カード番号は{}で囲まれており、値が"{-}"(例: 詰め合わせ・
スリーブ等カード番号を持たない商品)の場合は単品カードとして特定できないため
除外する。
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

SITE_ROOT = "https://www.cardrush.jp"
SEARCH_URL = "https://www.cardrush.jp/product-list"
PAGE_SIZE = 120

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_TIMEOUT = 30
REQUEST_DELAY_SEC = 30
MAX_PAGES = 950  # 安全のための上限(実際の最終ページはparse_last_pageから算出)
MAX_RETRIES = 4
RETRY_BACKOFF_SEC = 10

# 例: "〔PSA10鑑定済〕青眼の白龍【レリーフ】{SM-51}《モンスター》"
NAME_PATTERN = re.compile(r"【([^】]+)】\{([^{}]+)\}《[^》]*》\s*$")
CARD_NUM_PATTERN = re.compile(r"^[A-Za-z0-9]{2,6}-[A-Za-z0-9]{2,6}$")
PAGE_LINK_PATTERN = re.compile(r"[?&](?:amp;)?page=(\d+)")

logger = logging.getLogger(__name__)


def fetch_page(page: int) -> str:
    params = {"num": PAGE_SIZE, "page": page}
    resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def parse_items(html: str) -> list[tuple[str, str, int | None, str, str | None, str | None]]:
    """(card_num, rarity, price, 商品名, 商品画像URL, 商品ページURL) のリストを返す。
    カード番号を持たない商品(詰め合わせ・スリーブ・保護用品等、"{-}")や、
    在庫切れの商品は除外する。
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
        m = NAME_PATTERN.search(name_text)
        if not m:
            continue
        rarity, card_num = m.group(1), m.group(2)
        if not CARD_NUM_PATTERN.match(card_num):
            continue

        price_el = li.select_one(".price .figure")
        if not price_el:
            continue
        price_text = price_el.get_text().split("円")[0].replace(",", "").strip()
        try:
            price = int(price_text)
        except ValueError:
            continue

        img_el = li.select_one(".global_photo img")
        image_url = img_el.get("src") if img_el else None
        link_el = li.select_one("a.item_data_link")
        product_url = urljoin(SITE_ROOT, link_el.get("href")) if link_el and link_el.get("href") else None

        results.append((card_num, rarity, price, name_text.strip(), image_url, product_url))
    return results


def parse_last_page(html: str) -> int:
    """ページネーションのリンクに現れる最大のpage番号を最終ページとする
    (count_numberの件数表示から算出すると実際のページ数とずれることがあるため使わない)。"""
    numbers = [int(n) for n in PAGE_LINK_PATTERN.findall(html)]
    return max(numbers) if numbers else 1


def sync_prices(conn=None, delay: float = REQUEST_DELAY_SEC, progress_callback=None) -> dict:
    """カードラッシュ(www.cardrush.jp)から遊戯王OCGの価格を取得し price_history に
    保存する。

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

            html = None
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    html = fetch_page(page)
                    break
                except requests.RequestException as exc:
                    if attempt >= MAX_RETRIES:
                        logger.warning(
                            "カードラッシュの取得に失敗 (page=%d, %d回リトライ後): %s",
                            page, attempt, exc,
                        )
                    else:
                        logger.info(
                            "カードラッシュ page=%d 取得失敗 (試行%d/%d): %s - %d秒後に再試行",
                            page, attempt, MAX_RETRIES, exc, RETRY_BACKOFF_SEC * attempt,
                        )
                        time.sleep(RETRY_BACKOFF_SEC * attempt)
            if html is None:
                if page == 1:
                    # 1ページ目(総ページ数の判定に必須)が取れなければこれ以上進めない。
                    break
                # それ以外のページは諦めて次に進む(一時的な読み込み遅延のたびに
                # 891ページ全体のクロールを諦めるよりは、部分的にでも進めたい)。
                page += 1
                continue

            if page == 1:
                last_page = parse_last_page(html)

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
                conn, card_id, "カードラッシュ", min(prices),
                recorded_at=run_recorded_at, sample_count=len(prices),
            )

        write_unresolved("カードラッシュ", unresolved_entries)

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
