"""遊戯王OCG公式カードデータベース (db.yugioh-card.com/yugiohdb/) のスクレイパー。

公式サイトはステートレスな検索結果URLに直接アクセスしても中身が空のページ
(コンテンツが空のシェルHTML)を返す。これは検索フォームページ
(card_search.action?ope=1&request_locale=ja) に一度アクセスしてセッション
Cookie (JSESSIONID 等) を発行させ、その後同じセッションで検索結果URLに
アクセスし、Referer に検索フォームURLを設定する必要があるため
(conan/onepiece-card-game と違い、このサイトは requests.Session() での
Cookie維持が必須)。

収集は2段階:
1. 検索結果一覧ページ (rp=100件ずつ, page=N でページング) を巡回し、
   カード基本情報 (cid, 名前, 属性, レベル/ランク, 種族/効果種別, ATK/DEF,
   効果テキスト, 画像URL) を取得する。
2. 各カードの詳細ページ (ope=2&cid=<cid>) から「収録」テーブルを取得し、
   型番(card_num) ごとの収録パック・レアリティ・発売日を取得する
   (1つの cid = 1つの効果を持つカードが、複数の型番・パック・レアリティで
   再録されるため、型番単位が db.py のレコード単位になる)。

このサイトは Imperva/Incapsula の bot 対策下にあるが、robots.txt は
404 (制限記載なし)。礼儀正しいクローラとして、実ブラウザ相当の User-Agent、
リクエスト間隔、指数バックオフ付きリトライ (504 等の一時的エラーを含む)
を実装している。
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import random
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import db

BASE = "https://www.db.yugioh-card.com/yugiohdb"
SEARCH_FORM_URL = f"{BASE}/card_search.action?ope=1&request_locale=ja"
RESULTS_URL = f"{BASE}/card_search.action"
DETAIL_URL = f"{BASE}/card_search.action"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

REQUEST_TIMEOUT = 20
REQUEST_DELAY_SEC = 1.5  # 検索結果/詳細ページ取得ごとのウェイト(礼儀)
RP = 100  # 1ページあたりの件数 (10/50/100 が選択可能。100が最速)
MAX_RETRIES = 3
BACKOFF_BASE = 3.0  # 秒。リトライ毎に2倍(指数バックオフ)

LOG_DIR = Path(__file__).parent / "data"
LOG_FILE = LOG_DIR / "scrape_log.txt"

logger = logging.getLogger("scraper_cards")


def setup_logging() -> None:
    """日本語を含むログはUTF-8ファイルにのみ書き、コンソールにはASCIIの
    進捗メッセージのみ出す(Windows端末のcp932でも文字化けしないように)。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))

    class AsciiOnlyFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            try:
                msg = record.getMessage()
            except Exception:
                return True
            return msg.isascii()

    console_handler.addFilter(AsciiOnlyFilter())
    logger.addHandler(console_handler)


# ---------------------------------------------------------------------------
# HTTPユーティリティ
# ---------------------------------------------------------------------------

def new_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(HEADERS)
    _prime_session(sess)
    return sess


def _prime_session(sess: requests.Session) -> None:
    """検索フォームページにアクセスしてセッションCookieを確立する。
    これをしないと検索結果URLへの直アクセスは空のシェルHTMLを返す。"""
    r = get_with_retry(sess, SEARCH_FORM_URL, referer=None)
    if r is None or "JSESSIONID" not in sess.cookies.get_dict():
        logger.warning("session priming did not yield JSESSIONID cookie (continuing anyway)")


def get_with_retry(sess: requests.Session, url: str, referer: str | None,
                    max_retries: int = MAX_RETRIES) -> requests.Response | None:
    """指数バックオフ付きGET。504等の一時的エラーでも最大 max_retries 回まで再試行する。"""
    headers = {"Referer": referer} if referer else {}
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = sess.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp
            logger.warning("HTTP %d on attempt %d/%d for %s", resp.status_code, attempt, max_retries, url)
            if resp.status_code in (429, 500, 502, 503, 504):
                sleep_s = BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 1)
                time.sleep(sleep_s)
                continue
            resp.raise_for_status()
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("request error attempt %d/%d for %s: %s", attempt, max_retries, url, exc)
            sleep_s = BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 1)
            time.sleep(sleep_s)
    if last_exc:
        logger.error("giving up on %s after %d attempts: %s", url, max_retries, last_exc)
    else:
        logger.error("giving up on %s after %d attempts (bad status)", url, max_retries)
    return None


def results_url(page: int, rp: int = RP) -> str:
    params = {
        "ope": "1", "sess": "2", "rp": str(rp), "page": str(page),
        "mode": "1", "sort": "1", "keyword": "", "stype": "1", "ctype": "",
        "othercon": "2", "starfr": "", "starto": "", "pscalefr": "", "pscaleto": "",
        "linkmarkerfr": "", "linkmarkerto": "", "atkfr": "", "atkto": "",
        "deffr": "", "defto": "",
        "releaseDStart": "1", "releaseMStart": "1", "releaseYStart": "1999",
        "releaseDEnd": "", "releaseMEnd": "", "releaseYEnd": "",
        "request_locale": "ja",
    }
    return RESULTS_URL + "?" + urllib.parse.urlencode(params)


def detail_url(cid: int) -> str:
    return DETAIL_URL + "?" + urllib.parse.urlencode({"ope": "2", "cid": cid, "request_locale": "ja"})


# ---------------------------------------------------------------------------
# 検索結果一覧ページのパース
# ---------------------------------------------------------------------------

IMG_RE = re.compile(
    r"get_image\.action\?type=1&osplang=1&cid=(\d+)&ciid=(\d+)&enc=([^'\"]+)"
)


def _clean_species_text(span) -> tuple[str | None, str | None]:
    """card_info_species_and_other_item の中身をパースする。
    例: 【悪魔族／効果】 -> species='悪魔族', monster_type='効果'
        【魔法／永続】 (罠/魔法の場合) -> species=None, monster_type='永続'
    """
    if span is None:
        return None, None
    text = span.get_text(strip=True)
    text = text.strip("【】").strip()
    if not text:
        return None, None
    parts = [p.strip() for p in text.split("／") if p.strip()]
    if not parts:
        return None, None
    return parts, None  # caller splits further


def parse_total_and_pages(soup: BeautifulSoup, rp: int) -> tuple[int, int]:
    text_div = soup.find("div", class_="text")
    total = 0
    if text_div:
        m = re.search(r"検索結果\s*([\d,]+)件中", text_div.get_text())
        if m:
            total = int(m.group(1).replace(",", ""))
    if total == 0:
        return 0, 0
    pages = (total + rp - 1) // rp
    return total, pages


def parse_results_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")

    # cid -> live image URL (from the inline <script> that sets each thumbnail's src)
    image_map: dict[str, str] = {}
    for cid, ciid, enc in IMG_RE.findall(html):
        if ciid == "1" and cid not in image_map:
            image_map[cid] = (
                f"{BASE}/get_image.action?type=1&osplang=1&cid={cid}&ciid={ciid}&enc={enc}"
            )

    cards = []
    for row in soup.select("div.t_row.c_normal.open"):
        cid_input = row.select_one("input.cid")
        if cid_input is None or not cid_input.get("value"):
            continue
        cid = cid_input["value"].strip()

        ruby_span = row.select_one("span.card_ruby")
        ruby = ruby_span.get_text(strip=True) if ruby_span else None

        name_span = row.select_one("span.card_name")
        name = re.sub(r"\s+", " ", name_span.get_text(strip=True)) if name_span else None
        if not name:
            continue

        attr_span = row.select_one("span.box_card_attribute")
        attr_text = None
        if attr_span:
            inner = attr_span.find("span")
            attr_text = inner.get_text(strip=True) if inner else attr_span.get_text(strip=True)

        level_span = row.select_one("span.box_card_level_rank")
        level = rank = None
        if level_span is not None:
            inner = level_span.find("span")
            lvl_text = inner.get_text(strip=True) if inner else level_span.get_text(strip=True)
            m = re.search(r"(\d+)", lvl_text)
            num = int(m.group(1)) if m else None
            classes = level_span.get("class", [])
            if "rank" in classes:
                rank = num
            else:
                level = num

        link_span = row.select_one("span.box_card_linkmarker")
        link_rating = None
        if link_span is not None:
            inner = link_span.find_all("span")
            link_text = inner[-1].get_text(strip=True) if inner else link_span.get_text(strip=True)
            m = re.search(r"(\d+)", link_text)
            link_rating = int(m.group(1)) if m else None

        species_span = row.select_one("span.card_info_species_and_other_item")
        species = None
        monster_type = None
        card_type = None
        if attr_text in ("魔法", "罠"):
            card_type = attr_text
            attribute = None
            if species_span is not None:
                parts, _ = _clean_species_text(species_span)
                monster_type = "／".join(parts) if parts else "通常"
            else:
                monster_type = "通常"
        else:
            card_type = "モンスター" if attr_text else None
            attribute = attr_text.replace("属性", "") if attr_text else None
            if species_span is not None:
                parts, _ = _clean_species_text(species_span)
                if parts:
                    species = parts[0]
                    if len(parts) > 1:
                        monster_type = "／".join(parts[1:])

        atk_span = row.select_one("span.atk_power")
        def_span = row.select_one("span.def_power")
        atk = def_ = None
        if atk_span is not None:
            inner = atk_span.find("span")
            t = (inner.get_text(strip=True) if inner else atk_span.get_text(strip=True))
            m = re.search(r"攻撃力\s*(\S+)", t)
            atk = m.group(1) if m else None
        if def_span is not None:
            inner = def_span.find("span")
            t = (inner.get_text(strip=True) if inner else def_span.get_text(strip=True))
            m = re.search(r"守備力\s*(\S+)", t)
            def_ = m.group(1) if m else None

        text_dds = row.select("dd.box_card_text.c_text")
        effect_text = None
        if text_dds:
            # 最初の c_text が本文(2つ目以降はペンデュラム効果テキストや備考)
            effect_text = text_dds[0].get_text(strip=True)
        pendulum_text = None
        if len(text_dds) > 1:
            # 備考 (biko) は非公式カードの注記なので除外しつつ、pendulumらしき
            # 2つ目のテキストのみpendulum_textとして扱う
            second = text_dds[1]
            if "biko" not in (second.get("class") or []):
                pendulum_text = second.get_text(strip=True)

        pscale_span = row.select_one("span.box_card_pen_scale, span.pen_scale")
        pendulum_scale = None
        if pscale_span is not None:
            m = re.search(r"(\d+)", pscale_span.get_text())
            pendulum_scale = int(m.group(1)) if m else None

        image_url = image_map.get(cid)

        cards.append({
            "cid": int(cid),
            "name": name,
            "ruby": ruby,
            "card_type": card_type,
            "monster_type": monster_type,
            "species": species,
            "attribute": attribute if card_type == "モンスター" else None,
            "level": level,
            "rank": rank,
            "link_rating": link_rating,
            "link_markers": None,  # 検索結果ページからは矢印の向きが読めないため未取得(既知の制約)
            "pendulum_scale": pendulum_scale,
            "atk": atk,
            "def": def_,
            "effect_text": effect_text,
            "pendulum_text": pendulum_text,
            "image_url": image_url,
        })

    return cards


# ---------------------------------------------------------------------------
# 詳細ページ(収録パック一覧)のパース
# ---------------------------------------------------------------------------

def parse_detail_variants(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    variants = []
    for row in soup.select("#update_list .t_body .t_row"):
        inside = row.select_one(".inside")
        if inside is None:
            continue
        time_div = inside.select_one(".time")
        release_date = time_div.get_text(strip=True) if time_div else None

        num_div = inside.select_one(".card_number")
        card_num = num_div.get_text(strip=True) if num_div else None
        if not card_num:
            continue  # 型番のない収録行(未発売/一部特殊枠)はスキップ

        pack_div = inside.select_one(".pack_name")
        pack = pack_div.get_text(strip=True) if pack_div else None

        rarity_p = inside.select_one(".icon.rarity .lr_icon p")
        rarity = rarity_p.get_text(strip=True) if rarity_p else None

        pack_code = card_num.split("-")[0] if "-" in card_num else None

        variants.append({
            "card_num": card_num,
            "pack": pack,
            "pack_code": pack_code,
            "rarity": rarity,
            "release_date": release_date,
        })
    return variants


# ---------------------------------------------------------------------------
# クロール本体
# ---------------------------------------------------------------------------

def content_hash_for(card: dict) -> str:
    key = "|".join(str(card.get(k, "")) for k in (
        "name", "ruby", "card_type", "monster_type", "species", "attribute",
        "level", "rank", "link_rating", "link_markers", "pendulum_scale",
        "atk", "def", "rarity", "pack", "pack_code", "effect_text",
        "pendulum_text", "image_url", "release_date",
    ))
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def build_rows(cid_card: dict, variants: list[dict]) -> list[dict]:
    """1枚のカード(cid)の基本情報 + 複数の収録バリアントから、db.cards に
    upsertするための行を組み立てる。card_num が同じでレアリティが違う
    (ごく稀な再録パターン)場合のみ、idにレアリティ接尾辞を付けて一意化する。"""
    rows = []
    seen_card_nums: dict[str, int] = {}
    for v in variants:
        seen_card_nums[v["card_num"]] = seen_card_nums.get(v["card_num"], 0) + 1

    for v in variants:
        card_num = v["card_num"]
        if seen_card_nums[card_num] > 1 and v.get("rarity"):
            row_id = f"{card_num}_{v['rarity']}"
        else:
            row_id = card_num

        merged = {**cid_card, **v}
        merged["id"] = row_id
        merged["cid"] = cid_card["cid"]
        merged["card_num"] = card_num
        merged["content_hash"] = content_hash_for(merged)
        row = {col: merged.get(col) for col in db.CARD_COLUMNS}
        rows.append(row)
    return rows


@dataclass
class CrawlStats:
    pages_fetched: int = 0
    cards_seen: int = 0
    details_fetched: int = 0
    details_failed: int = 0
    variants_built: int = 0
    unreleased_skipped: int = 0


def crawl(conn, limit: int | None = None, delay: float = REQUEST_DELAY_SEC,
          rp: int = RP, start_page: int = 1) -> CrawlStats:
    stats = CrawlStats()
    sess = new_session()

    # 1ページ目を取得して総件数/総ページ数を把握する
    r1 = get_with_retry(sess, results_url(start_page, rp), referer=SEARCH_FORM_URL)
    if r1 is None:
        raise RuntimeError("failed to fetch first results page")
    soup1 = BeautifulSoup(r1.text, "lxml")
    total, total_pages = parse_total_and_pages(soup1, rp)
    logger.info("total cards reported by site: %d (rp=%d -> %d pages)", total, rp, total_pages)

    page = start_page
    html = r1.text
    upsert_summary = {"new": 0, "updated": 0, "total": 0}

    while True:
        if page != start_page:
            resp = get_with_retry(sess, results_url(page, rp), referer=SEARCH_FORM_URL)
            if resp is None:
                logger.error("skipping page %d after repeated failures", page)
                page += 1
                if total_pages and page > total_pages:
                    break
                time.sleep(delay)
                continue
            html = resp.text
            time.sleep(delay)

        page_cards = parse_results_page(html)
        stats.pages_fetched += 1
        stats.cards_seen += len(page_cards)
        logger.info("page %d: %d cards parsed", page, len(page_cards))

        rows_batch = []
        for card in page_cards:
            if limit is not None and stats.details_fetched >= limit:
                break
            durl = detail_url(card["cid"])
            dresp = get_with_retry(sess, durl, referer=SEARCH_FORM_URL)
            time.sleep(delay)
            if dresp is None:
                stats.details_failed += 1
                continue
            stats.details_fetched += 1
            variants = parse_detail_variants(dresp.text)
            if not variants:
                stats.unreleased_skipped += 1
                continue
            rows = build_rows(card, variants)
            stats.variants_built += len(rows)
            rows_batch.extend(rows)

        if rows_batch:
            result = db.upsert_cards(conn, rows_batch)
            upsert_summary["new"] += result["new"]
            upsert_summary["updated"] += result["updated"]
            upsert_summary["total"] += result["total"]
            logger.info(
                "db upsert after page %d: +%d new / %d updated (running total upserted=%d)",
                page, result["new"], result["updated"], upsert_summary["total"],
            )

        if limit is not None and stats.details_fetched >= limit:
            logger.info("reached --limit %d, stopping crawl", limit)
            break

        page += 1
        if total_pages and page > total_pages:
            break

    logger.info(
        "crawl finished: pages=%d cards_seen=%d details_fetched=%d details_failed=%d "
        "variants_built=%d unreleased_skipped=%d db_new=%d db_updated=%d",
        stats.pages_fetched, stats.cards_seen, stats.details_fetched, stats.details_failed,
        stats.variants_built, stats.unreleased_skipped, upsert_summary["new"], upsert_summary["updated"],
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Yu-Gi-Oh! OCG official DB scraper")
    parser.add_argument("--limit", type=int, default=None,
                         help="fetch at most N cards' detail pages (small-sample/fast-iteration mode)")
    parser.add_argument("--rp", type=int, default=RP, choices=[10, 50, 100],
                         help="results per page from the site (default 100)")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY_SEC)
    args = parser.parse_args()

    setup_logging()
    logger.info("starting crawl: limit=%s rp=%d start_page=%d delay=%.1f",
                args.limit, args.rp, args.start_page, args.delay)

    conn = db.get_connection()
    db.init_db(conn)
    try:
        crawl(conn, limit=args.limit, delay=args.delay, rp=args.rp, start_page=args.start_page)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
