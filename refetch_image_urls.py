"""既存のcards.image_urlを、正しい日本語ロケール(&request_locale=ja付き)の
get_image.action URLで再取得し直すための一回限りの修復スクリプト。

背景: 検索結果一覧ページのサムネイルURLには &request_locale=ja が付いておらず、
これが無いとget_image.actionは北米TCG版(英語)の画像を返すことが分かった
(scraper_cards.py側は修正済み。本スクリプトは、既にimages.pyでローカルパスに
書き換えられ、かつ既に間違った英語版画像がダウンロードされてしまった
38,207件ぶんを、詳細ページの再クロール(14,279回、数時間)をせずに直すための
ショートカット)。

カード基本情報(名前・効果テキスト等)やcard_num/rarity/packは既に正しいため、
詳細ページ(ope=2&cid=<cid>)の再クロールは不要。検索結果一覧ページ
(143ページぶん)だけを再取得してcid -> 正しいimage_urlの対応表を作り、
同じcidを持つ全カード行(1つのcidに対し複数のcard_num/レアリティが対応する)の
image_urlを一括更新する。
"""
import logging
import sys
import time

from bs4 import BeautifulSoup

import db
from scraper_cards import (
    IMG_RE, SEARCH_FORM_URL, get_with_retry, new_session, parse_total_and_pages,
    results_url, setup_logging,
)

logger = logging.getLogger("refetch_image_urls")

REQUEST_DELAY_SEC = 1.5
RP = 100


def build_cid_image_map(delay: float = REQUEST_DELAY_SEC, rp: int = RP) -> dict[str, str]:
    """全結果ページを巡回し、cid -> 正しい(request_locale=ja付き)image_urlの
    対応表を作る(詳細ページの再クロールは不要)。"""
    sess = new_session()
    r1 = get_with_retry(sess, results_url(1, rp), referer=SEARCH_FORM_URL)
    if r1 is None:
        raise RuntimeError("failed to fetch first results page")
    soup1 = BeautifulSoup(r1.text, "lxml")
    total, total_pages = parse_total_and_pages(soup1, rp)
    logger.info("total cards reported by site: %d (rp=%d -> %d pages)", total, rp, total_pages)

    cid_image_map: dict[str, str] = {}
    html = r1.text
    page = 1
    while True:
        if page != 1:
            resp = get_with_retry(sess, results_url(page, rp), referer=SEARCH_FORM_URL)
            if resp is None:
                logger.error("skipping page %d after repeated failures", page)
                page += 1
                if page > total_pages:
                    break
                time.sleep(delay)
                continue
            html = resp.text
            time.sleep(delay)

        page_new = 0
        for cid, ciid, enc in IMG_RE.findall(html):
            if ciid != "1":
                continue
            if cid not in cid_image_map:
                page_new += 1
            cid_image_map[cid] = (
                "https://www.db.yugioh-card.com/yugiohdb/get_image.action"
                f"?type=1&osplang=1&cid={cid}&ciid={ciid}&enc={enc}&request_locale=ja"
            )
        logger.info("page %d/%d: %d new cids (running total=%d)", page, total_pages, page_new, len(cid_image_map))

        page += 1
        if page > total_pages:
            break

    return cid_image_map


def apply_to_db(conn, cid_image_map: dict[str, str]) -> int:
    updated = 0
    for cid, image_url in cid_image_map.items():
        cur = conn.execute(
            "UPDATE cards SET image_url = ? WHERE cid = ?", (image_url, int(cid))
        )
        updated += cur.rowcount
        conn.commit()
    return updated


def main() -> None:
    setup_logging()
    # setup_logging()(scraper_cards由来)はscraper_cards側のloggerにしか
    # ハンドラを付けないため、このモジュール自身のlogger呼び出しがコンソールに
    # 何も出ない不具合があった。ここに明示的にハンドラを付けて進捗を見えるようにする。
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(console_handler)
    logger.info("starting image URL refetch (results pages only, no detail-page recrawl)")
    cid_image_map = build_cid_image_map()
    logger.info("collected %d distinct cid -> image_url mappings", len(cid_image_map))

    conn = db.get_connection()
    try:
        updated = apply_to_db(conn, cid_image_map)
    finally:
        conn.close()
    logger.info("updated image_url on %d card rows", updated)


if __name__ == "__main__":
    main()
