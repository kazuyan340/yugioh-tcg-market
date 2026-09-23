"""トレカショップ竜のしっぽ(ryuunoshippo.com)から遊戯王OCGの価格を取得し
price_history に保存するモジュール。

カードラボと同じ系列のECカートシステム(Ocnk系)を使っており、HTML構造は
ほぼ同一(`li.list_item_cell`, `.goods_name`, `.price .figure`, `.global_photo`,
`a.item_data_link`)。検索は `/product-list/?keyword=遊戯王` で行う
(2026-09時点で実測約10,000件・num=120で84ページ、cardlabo.py と同じ挙動)。

【他店との決定的な違い】カードラボ・まんぞく屋等と異なり、竜のしっぽの商品名には
DBのcard_num形式(例: LOB-JP001)が一切含まれない。商品名は
「{カード名}({店舗独自レアリティ略号})」という形式(例: "灰流うらら(高価N)"、
"聖なる心のバリア -マインドフォース-(ウルトラ)")、または略号すら無い
「{カード名}」のみのことも多い。レアリティ略号が全角【】で囲まれることもある
(例: "ビッグウェルカム・ラビュリンス【高価N】")。値札の横に商品ID
(`.model_number` 内の `[ 129342 ]` のような角括弧表記)があるが、これは
竜のしっぽ独自の内部SKUでありDBのcard_num/cidとは対応しない(対応表を
持っていないため、ログ・unresolved記録用の参考情報としてのみ扱う)。

card_numが取れないため、price_matching.build_lookup()(card_num単位の対応表)
ではなく price_matching.build_lookup_by_name()(カード名単位の対応表)を使い、
resolve()/apply_resolution()には「card_num」の代わりにカード名を渡す
(resolve()はキーの意味を問わない実装なのでそのまま使い回せる)。

遊戯王は同名カードの再録・レアリティ違いが非常に多く、カード名だけでは
1枚に絞り込めないことが他店より大幅に多い。「高価N」「ウルトラ」「スーパー」
「シークレット」等の略号でDB側のrarity列(公式サイトの正式なレアリティ名、
例: "ウルトラレア")をある程度補って絞り込みを試みるが(RARITY_MAP)、
一致しない場合は自動で決め打ちせず、通常通りambiguous/missingとして
管理ページ(site/admin-unofficial-cards.html)に回す。

竜のしっぽのrobots.txtには一般クローラー向けのCrawl-delay指定が無いが、
駿河屋・カードラボと同様に安全側でリクエスト間隔30秒を採用する。
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

SEARCH_URL = "https://www.ryuunoshippo.com/product-list/"
BASE_URL = "https://www.ryuunoshippo.com"
KEYWORD = "遊戯王"
PAGE_SIZE = 120

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_TIMEOUT = 15
REQUEST_DELAY_SEC = 30
MAX_PAGES = 120  # 安全のための上限(実際の最終ページはparse_last_pageから算出)

# 例: "灰流うらら(高価N)"、"聖なる心のバリア -マインドフォース-(ウルトラ)"、
#     "ビッグウェルカム・ラビュリンス【高価N】"、"★特価★聖王の粉砕(ウルトラ)"
NAME_PATTERN = re.compile(r"^(?:★特価★)?(.+?)[（(【]([^（）()【】]+)[）)】]\s*$")
PAGE_LINK_PATTERN = re.compile(r"[?&](?:amp;)?page=(\d+)")

# 竜のしっぽの略号 -> DB(公式サイト由来)のrarity列で使われがちな正式名称。
# あくまでヒントであり、一致しなければ自動narrowingは行わずambiguousに回る
# (price_matching.resolve()のrarity不一致時のフォールバック挙動)。
RARITY_MAP = {
    "ウルトラ": "ウルトラレア",
    "スーパー": "スーパーレア",
    "シークレット": "シークレットレア",
    "アルティメット": "アルティメットレア",
    "パラレル": "レアパラレルレア",
    "高価N": "ノーマル",
    "高価": None,  # 略号だけではレアリティ不明(絞り込みに使わない)
}

logger = logging.getLogger(__name__)


def fetch_search_page(page: int) -> str:
    params = {"keyword": KEYWORD, "num": PAGE_SIZE, "page": page}
    resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _parse_name(name_text: str) -> tuple[str, str | None]:
    """商品名から(カード名, レアリティ略号(あれば正式名称に変換、無ければNone))を返す。"""
    m = NAME_PATTERN.match(name_text.strip())
    if not m:
        return name_text.strip(), None
    card_name, raw_rarity = m.group(1).strip(), m.group(2).strip()
    rarity = RARITY_MAP.get(raw_rarity, raw_rarity)
    return card_name, rarity


def parse_items(html: str) -> list[tuple[str, str | None, int | None, str | None, str | None, str | None]]:
    """(カード名, レアリティ(あれば), price, 商品名(略号込み・表示用), 商品画像URL,
    商品ページURL) のリストを返す。在庫切れの場合はpriceがNoneになる。
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for li in soup.select("li.list_item_cell"):
        name_el = li.select_one(".goods_name")
        if not name_el:
            continue

        name_text = name_el.get_text()
        card_name, rarity = _parse_name(name_text)
        if not card_name:
            continue

        photo_el = li.select_one(".global_photo")
        image_url = photo_el.get("data-src") if photo_el else None
        link_el = li.select_one("a.item_data_link")
        product_url = urljoin(BASE_URL, link_el.get("href")) if link_el and link_el.get("href") else None

        if "list_item_soldout" in (li.get("class") or []):
            results.append((card_name, rarity, None, name_text.strip(), image_url, product_url))
            continue

        price_el = li.select_one(".price .figure")
        if not price_el:
            continue

        price_text = price_el.get_text().split("円")[0].replace(",", "").strip()
        try:
            price = int(price_text)
        except ValueError:
            continue

        results.append((card_name, rarity, price, name_text.strip(), image_url, product_url))
    return results


def parse_last_page(html: str) -> int:
    """ページネーションのリンクに現れる最大のpage番号を最終ページとする
    (count_numberの件数表示は10,000件でカンストするため使わない)。"""
    numbers = [int(n) for n in PAGE_LINK_PATTERN.findall(html)]
    return max(numbers) if numbers else 1


def sync_prices(conn=None, delay: float = REQUEST_DELAY_SEC, progress_callback=None) -> dict:
    """竜のしっぽから遊戯王OCGの価格を取得し price_history に保存する。

    card_numが取れないショップのため、他店とは異なりカード名単位のlookup
    (price_matching.build_lookup_by_name())で解決する。

    progress_callback(page, last_page, matched_count) が指定されていれば
    ページ取得のたびに呼び出す。
    """
    owns_conn = conn is None
    if owns_conn:
        conn = db.get_connection()
        db.init_db(conn)

    lookup = price_matching.build_lookup_by_name(conn)
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
                logger.warning("竜のしっぽの取得に失敗 (page=%d): %s", page, exc)
                break

            if page == 1:
                last_page = parse_last_page(html)

            for card_name, rarity, price, product_name, image_url, product_url in parse_items(html):
                if price is None:
                    continue
                price_matching.apply_resolution(
                    all_prices, unresolved_entries, card_name, rarity, price,
                    lookup, manual_resolutions, product_name, image_url, product_url,
                )

            if progress_callback:
                progress_callback(page, last_page, len(all_prices))

            page += 1

        run_recorded_at = datetime.now(timezone.utc).isoformat()
        for card_id, prices in all_prices.items():
            db.insert_price(
                conn, card_id, "竜のしっぽ", min(prices),
                recorded_at=run_recorded_at, sample_count=len(prices),
            )

        write_unresolved("竜のしっぽ", unresolved_entries)

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
