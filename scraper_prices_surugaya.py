"""駿河屋から遊戯王OCGの価格を取得し price_history に保存するモジュール。

【実行環境についての注意】駿河屋はクラウド(GitHub Actions等)のIPからのアクセスを
403 Forbiddenで弾くことがあるため、本スクレイパーはセルフホストランナー上での
定期実行を前提とする(ワンピース版と同じ制約)。

検索結果ページの各商品は `div.item` 1個にまとまっており、その中に商品名
(`.product-name`、例: "LPG2-JP026[UR]：D-HERO Bloo-D(CLASSIC-STYLE)(Ultra BLUE Ver.)")と
価格欄(`.item_price`)が両方入っている。これを`div.item`単位でまとめてパースする。

検索は「レアリティ単位」で行う。「遊戯王カードゲーム {rarity}」ではなく
「遊戯王 {rarity}」で検索する(遊戯王は公式表記が「遊戯王OCG」だが、駿河屋の
カテゴリ表記・検索インデックスは「遊戯王」始まりのため)。ただしトークンの
緩いマッチのせいで無関係な他ジャンル商品が紛れ込む可能性があるため、
商品詳細欄のカテゴリパンくず文字列(`.item_detail .condition.background-kishu`、
例: "遊戯王/UR/効果モンスター/日本語/LIMITED　PACK　GX　-ラーイエロー-")が
"遊戯王"で始まることを実データで確認した上で、それ以外を除外する
(CATEGORY_PREFIX、ワンピース版の"ONE　PIECEカードゲーム"プレフィックス
ガードと同じ考え方)。

駿河屋のrobots.txtは `Crawl-delay: 30` を指定しているため、
リクエスト間隔は30秒を厳守する。
"""
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

import db
import price_matching
from unresolved_report import write_unresolved

SEARCH_URL = "https://www.suruga-ya.jp/search"
TOP_URL = "https://www.suruga-ya.jp/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

REQUEST_TIMEOUT = 15
REQUEST_DELAY_SEC = 30  # 駿河屋のCrawl-delay:30を厳守
MAX_PAGES_PER_RARITY = 60  # 安全のための上限(実際の最終ページはparse_last_pageで判定)

NAME_PATTERN = re.compile(r"^([^\[]+)\[([^\]]+)\](?:[:：](.*))?$")
PRICE_PATTERN = re.compile(r"[\d,]+")
PAGE_LINK_PATTERN = re.compile(r"[?&]page=(\d+)")

CATEGORY_PREFIX = "遊戯王"

logger = logging.getLogger(__name__)


def make_session() -> requests.Session:
    """トップページに先にアクセスしてCookieを取得したセッションを返す。

    検索URLへ直接アクセスすると403 Forbiddenになることがある
    (通常のブラウザ挙動に近づけることで回避を試みる)。
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    resp = session.get(TOP_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return session


def fetch_search_page(session: requests.Session, rarity: str, page: int) -> str:
    query = f"遊戯王 {rarity}"
    params = {"category": "", "search_word": query, "page": page}
    resp = session.get(
        SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT,
        headers={"Referer": TOP_URL},
    )
    resp.raise_for_status()
    return resp.text


def parse_items(html: str) -> list[tuple[str, str, int | None, str | None, str | None, str | None]]:
    """(card_num, rarity, price, 商品名, 商品画像URL, 商品ページURL) のリストを返す。
    品切れの場合はpriceがNoneになる。
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for item in soup.select("div.item"):
        name_el = item.select_one(".product-name")
        if not name_el:
            continue
        m = NAME_PATTERN.match(name_el.get_text(strip=True))
        if not m:
            continue

        category_el = item.select_one(".item_detail .condition.background-kishu")
        category_text = category_el.get_text(strip=True) if category_el else ""
        if not category_text.startswith(CATEGORY_PREFIX):
            continue

        card_num, rarity = m.group(1), m.group(2)
        product_name = m.group(3).strip() if m.group(3) else None

        thumb_link = item.select_one(".photo_box .thum a")
        product_url = thumb_link.get("href") if thumb_link else None
        img_el = item.select_one(".photo_box .thum img")
        image_url = img_el.get("src") if img_el else None

        price_el = item.select_one(".item_price")
        if not price_el:
            continue

        if "品切れ" in price_el.get_text():
            results.append((card_num, rarity, None, product_name, image_url, product_url))
            continue

        teika_el = price_el.select_one(".price_teika")
        if not teika_el:
            continue
        price_m = PRICE_PATTERN.search(teika_el.get_text())
        if not price_m:
            continue
        results.append((
            card_num, rarity, int(price_m.group(0).replace(",", "")),
            product_name, image_url, product_url,
        ))
    return results


def parse_last_page(html: str) -> int:
    """ページネーションのリンクに現れる最大のpage番号を最終ページとする
    (件数がページごとに一定でないため「24件未満なら最終ページ」という判定は
    誤判定の恐れがあり使わない)。"""
    numbers = [int(n) for n in PAGE_LINK_PATTERN.findall(html)]
    return max(numbers) if numbers else 1


def sync_prices(conn=None, delay: float = REQUEST_DELAY_SEC, progress_callback=None) -> dict:
    """遊戯王OCGの価格を駿河屋から取得し price_history に保存する。

    progress_callback(rarity, page, matched_count) が指定されていれば
    ページ取得のたびに呼び出す。
    """
    owns_conn = conn is None
    if owns_conn:
        conn = db.get_connection()
        db.init_db(conn)

    lookup = price_matching.build_lookup(conn)
    manual_resolutions = price_matching.load_manual_resolutions()
    target_rarities = db.get_distinct_values(conn, "rarity")
    logger.info("価格取得対象レアリティ: %s", ", ".join(target_rarities))

    all_prices: dict[str, list[int]] = defaultdict(list)
    unresolved_entries: list[dict] = []
    first_request = True

    try:
        try:
            session = make_session()
        except requests.RequestException as exc:
            logger.warning("駿河屋のトップページ取得に失敗: %s", exc)
            session = requests.Session()
            session.headers.update(HEADERS)

        for rarity in target_rarities:
            page = 1
            last_page = 1
            while page <= min(last_page, MAX_PAGES_PER_RARITY):
                if not first_request:
                    time.sleep(delay)
                first_request = False

                try:
                    html = fetch_search_page(session, rarity, page)
                except requests.RequestException as exc:
                    logger.warning("駿河屋の取得に失敗 (rarity=%s page=%d): %s", rarity, page, exc)
                    break

                if page == 1:
                    last_page = parse_last_page(html)

                for card_num, item_rarity, price, product_name, image_url, product_url in parse_items(html):
                    if price is None:
                        continue
                    price_matching.apply_resolution(
                        all_prices, unresolved_entries, card_num, item_rarity, price,
                        lookup, manual_resolutions, product_name, image_url, product_url,
                    )

                if progress_callback:
                    progress_callback(rarity, page, len(all_prices))

                page += 1

        # 同じ実行内では同じ時刻に揃える(グラフ上で同じ日時が複数点にずれないように)。
        run_recorded_at = datetime.now(timezone.utc).isoformat()
        for card_id, prices in all_prices.items():
            db.insert_price(
                conn, card_id, "駿河屋", min(prices),
                recorded_at=run_recorded_at, sample_count=len(prices),
            )

        write_unresolved("駿河屋", unresolved_entries)

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

    def _print_progress(rarity, page, matched_count):
        logger.info("レアリティ %s ページ%d取得完了 (累計マッチ %d件)", rarity, page, matched_count)

    sync_prices(progress_callback=_print_progress)
