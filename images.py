"""公式カードデータベースのカード画像をダウンロードし、site/images/cards/ に保存するモジュール。

db.yugioh-card.com の画像 (get_image.action) はレスポンスヘッダに
Cross-Origin-Resource-Policy: same-site が付いており、他ドメインからの
直リンクはブラウザ側でブロックされる(conan/onepiece-card-gameの過去サイトと
同じ問題)。そのため画像を一度ダウンロードして自サイトから配信する。

画像URLは `get_image.action?type=1&osplang=1&cid=<cid>&ciid=1&enc=<token>` の形で、
`enc` トークンはカードごと・取得タイミングごとに異なる値がカード検索結果ページの
インラインscriptに埋め込まれている。トークンを自前で再構築することはできないため、
scraper_cards.py が検索結果ページから読み取った実際のURLを db.cards.image_url に
保存しておき、本モジュールはそのURLをそのまま使ってダウンロードする
(scraper_cards.py 実行直後、URLが有効なうちに images.py を実行する必要がある)。
"""
import logging
import time
from pathlib import Path

import requests

import db

IMAGE_DIR = Path(__file__).parent / "site" / "images" / "cards"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://www.db.yugioh-card.com/yugiohdb/card_search.action?ope=1&request_locale=ja",
}
REQUEST_TIMEOUT = 20
REQUEST_DELAY_SEC = 0.5
MAX_RETRIES = 3
BACKOFF_BASE = 2.0

logger = logging.getLogger(__name__)


def _filename_for(card_id: str, image_url: str) -> str:
    # image_url はクエリ文字列(cid/enc等)しか持たない動的エンドポイントなので、
    # 拡張子は付かない。カードごとに一意な id (card_num or card_num_rarity) を
    # ファイル名にし、共通で .jpg として保存する(サイトが返す実体はJPEG)。
    safe_id = card_id.replace("/", "_")
    return f"{safe_id}.jpg"


def _get_with_retry(url: str, max_retries: int = MAX_RETRIES) -> requests.Response | None:
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
                continue
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("image fetch error attempt %d/%d for %s: %s", attempt, max_retries, url, exc)
            time.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
    return None


def download_missing_images(conn, delay: float = REQUEST_DELAY_SEC) -> dict:
    """DB内の全カードについて、まだ保存していない画像だけをダウンロードし、
    image_url をローカルの相対パスに書き換える。"""
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        "SELECT id, image_url FROM cards WHERE image_url IS NOT NULL"
    ).fetchall()

    downloaded = 0
    skipped = 0
    failed = 0

    # 何時間もかかる可能性があるため、1行ごとに書き込みトランザクションを
    # 開始→即コミットする(conn.commit()を最後にまとめて呼ぶと、実行中ずっと
    # 書き込みロックを保持してしまい、並行して動く他のスクリプト(banlist等)が
    # database is locked で失敗する。実際にこれで一度ロック競合を起こしたため、
    # 1件ごとのコミットに変更した)。
    for row in rows:
        card_id = row["id"]
        image_url = row["image_url"]
        if image_url is None or image_url.startswith("images/cards/"):
            # 既にローカルパスに書き換え済み(再実行時)
            skipped += 1
            continue

        filename = _filename_for(card_id, image_url)
        dest = IMAGE_DIR / filename
        local_rel = f"images/cards/{filename}"

        if dest.exists():
            skipped += 1
            if image_url != local_rel:
                conn.execute("UPDATE cards SET image_url = ? WHERE id = ?", (local_rel, card_id))
                conn.commit()
            continue

        resp = _get_with_retry(image_url)
        if resp is None or not resp.content:
            logger.warning("failed to download image for %s: %s", card_id, image_url)
            failed += 1
            continue

        dest.write_bytes(resp.content)
        conn.execute("UPDATE cards SET image_url = ? WHERE id = ?", (local_rel, card_id))
        conn.commit()
        downloaded += 1
        time.sleep(delay)

    return {"downloaded": downloaded, "skipped": skipped, "failed": failed, "total": len(rows)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    conn = db.get_connection()
    try:
        result = download_missing_images(conn)
    finally:
        conn.close()
    logger.info(
        "card images done: downloaded=%d skipped=%d failed=%d total=%d",
        result["downloaded"], result["skipped"], result["failed"], result["total"],
    )
