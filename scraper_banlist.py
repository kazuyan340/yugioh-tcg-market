"""遊戯王OCG公式サイトの「リミットレギュレーション」(禁止・制限・準制限カード)
ページをスクレイピングし、db.banlist テーブルへ格納するモジュール。

対象ページ: https://www.db.yugioh-card.com/yugiohdb/forbidden_limited.action
(scraper_cards.py と同様に、検索フォームページでセッションCookieを確立してから
アクセスする必要がある)

ページは以下の4セクションに分かれている:
  #list_forbidden      禁止カード
  #list_limited        制限カード
  #list_semi_limited   準制限カード
  #list_release_of_restricted  制限解除されたカード (=いずれの制限も無し。banlist
                                 テーブルには入れない: 「解除された」という情報自体は
                                 現在の制限状態ではないため)
各セクション内の `.list .t_body .t_row.c_simple .card_name .name` がカード名、
`input.link_value` の cid=<n> からカードを特定できる。型番(card_num)はこのページ
単体では取得できない(カード名とcidのみ)ため、既にDBに存在するカード(cidが一致する
行)から card_num を逆引きする。一致しない場合は card_num=None のまま保存する
(db.py の banlist.card_num は NULL許容なので問題ない)。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import db

BASE = "https://www.db.yugioh-card.com/yugiohdb"
SEARCH_FORM_URL = f"{BASE}/card_search.action?ope=1&request_locale=ja"
BANLIST_URL = f"{BASE}/forbidden_limited.action?request_locale=ja"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
BACKOFF_BASE = 3.0

logger = logging.getLogger(__name__)

SECTION_STATUS = {
    "list_forbidden": "forbidden",
    "list_limited": "limited",
    "list_semi_limited": "semi-limited",
    # list_release_of_restricted (制限解除) はあえて含めない: 現在は無制限のカードであり
    # banlist には「現在の制限状態があるカード」だけを記録する方針のため。
}


def _get_with_retry(sess: requests.Session, url: str, referer: str | None) -> requests.Response | None:
    headers = {"Referer": referer} if referer else {}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = sess.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
                continue
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("banlist fetch error attempt %d/%d: %s", attempt, MAX_RETRIES, exc)
            time.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
    return None


def fetch_banlist_html() -> str | None:
    sess = requests.Session()
    sess.headers.update(HEADERS)
    r0 = _get_with_retry(sess, SEARCH_FORM_URL, referer=None)
    if r0 is None:
        logger.error("failed to prime session for banlist fetch")
        return None
    r1 = _get_with_retry(sess, BANLIST_URL, referer=SEARCH_FORM_URL)
    if r1 is None:
        logger.error("failed to fetch banlist page")
        return None
    return r1.text


def parse_banlist(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    entries = []
    for section_id, status in SECTION_STATUS.items():
        section = soup.find("div", id=section_id)
        if section is None:
            logger.warning("banlist section #%s not found on page", section_id)
            continue
        for row in section.select(".list .t_body .t_row.c_simple"):
            name_span = row.select_one(".card_name .name")
            if name_span is None:
                continue
            name = name_span.get_text(strip=True)
            link = row.select_one("input.link_value")
            cid = None
            if link is not None and link.get("value"):
                val = link["value"]
                if "cid=" in val:
                    cid = val.split("cid=")[-1].split("&")[0]
            entries.append({"name": name, "status": status, "cid": cid})
    return entries


def resolve_card_nums(conn, entries: list[dict]) -> list[dict]:
    """既存のcards テーブル(cid列)から、同じcidを持つ最初のcard_numを逆引きする。"""
    for e in entries:
        card_num = None
        if e.get("cid"):
            row = conn.execute(
                "SELECT card_num FROM cards WHERE cid = ? ORDER BY card_num LIMIT 1", (e["cid"],)
            ).fetchone()
            if row:
                card_num = row["card_num"]
        e["card_num"] = card_num
    return entries


def sync_banlist(conn) -> dict:
    html = fetch_banlist_html()
    if html is None:
        return {"ok": False, "count": 0}
    entries = parse_banlist(html)
    entries = resolve_card_nums(conn, entries)
    count = db.upsert_banlist(conn, [
        {"card_num": e["card_num"], "name": e["name"], "status": e["status"]} for e in entries
    ])
    by_status = {}
    for e in entries:
        by_status[e["status"]] = by_status.get(e["status"], 0) + 1
    logger.info("banlist synced: %d entries (%s)", count, by_status)
    return {"ok": True, "count": count, "by_status": by_status}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    conn = db.get_connection()
    db.init_db(conn)
    try:
        result = sync_banlist(conn)
    finally:
        conn.close()
    print(result)
