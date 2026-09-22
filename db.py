"""SQLite データベースアクセス層。

カードの主キーには「型番(card_num, 例: LOB-JP001)」をそのまま使う。同じカード
(公式サイトの cid で識別される効果・ステータス集合)でも、収録パックやレアリティが
違えば型番が変わり、見た目やスキャン対象商品ページが別になるため、型番単位を
1レコードとして扱う(onepiece-card-game の card_num 単位パターンを踏襲)。
同じ型番で複数レアリティが存在するごく一部のケース(例: 20th シークレットなど
型番が枝分かれしない再録)は、型番に "_" + レアリティ略号を付与して一意化する。
"""
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).parent / "data" / "yugioh_tcg.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY,
    cid INTEGER,
    card_num TEXT NOT NULL,
    name TEXT NOT NULL,
    ruby TEXT,
    card_type TEXT,
    monster_type TEXT,
    species TEXT,
    attribute TEXT,
    level INTEGER,
    rank INTEGER,
    link_rating INTEGER,
    link_markers TEXT,
    pendulum_scale INTEGER,
    atk TEXT,
    def TEXT,
    rarity TEXT,
    pack TEXT,
    pack_code TEXT,
    effect_text TEXT,
    pendulum_text TEXT,
    image_url TEXT,
    release_date TEXT,
    content_hash TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL,
    site TEXT NOT NULL,
    price INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    sample_count INTEGER,
    FOREIGN KEY (card_id) REFERENCES cards(id)
);

CREATE TABLE IF NOT EXISTS banlist (
    card_num TEXT,
    name TEXT NOT NULL PRIMARY KEY,
    status TEXT NOT NULL,
    fetched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name);
CREATE INDEX IF NOT EXISTS idx_cards_card_num ON cards(card_num);
CREATE INDEX IF NOT EXISTS idx_cards_cid ON cards(cid);
CREATE INDEX IF NOT EXISTS idx_price_card ON price_history(card_id);
"""

CARD_COLUMNS = [
    "id", "cid", "card_num", "name", "ruby", "card_type", "monster_type",
    "species", "attribute", "level", "rank", "link_rating", "link_markers",
    "pendulum_scale", "atk", "def", "rarity", "pack", "pack_code",
    "effect_text", "pendulum_text", "image_url", "release_date",
    "content_hash", "fetched_at",
]


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def upsert_cards(conn: sqlite3.Connection, cards: list[dict]) -> dict:
    """カードを一括 upsert する。新規/更新件数を返す。"""
    new_count = 0
    updated_count = 0
    now = datetime.now(timezone.utc).isoformat()

    placeholders = ", ".join(f":{c}" for c in CARD_COLUMNS)
    assignments = ", ".join(f"{c}=excluded.{c}" for c in CARD_COLUMNS if c not in ("id", "fetched_at"))

    sql = f"""
        INSERT INTO cards ({", ".join(CARD_COLUMNS)})
        VALUES ({placeholders})
        ON CONFLICT(id) DO UPDATE SET {assignments}
        WHERE excluded.content_hash IS NOT cards.content_hash
    """

    for card in cards:
        existing = conn.execute("SELECT content_hash FROM cards WHERE id = ?", (card["id"],)).fetchone()
        row = {**card, "fetched_at": now}
        conn.execute(sql, row)
        if existing is None:
            new_count += 1
        elif existing["content_hash"] != card["content_hash"]:
            updated_count += 1

    conn.commit()
    return {"new": new_count, "updated": updated_count, "total": len(cards)}


def search_cards(conn: sqlite3.Connection, keyword: str = "", card_types=None,
                  attributes=None, rarities=None, species=None) -> list[sqlite3.Row]:
    query = "SELECT * FROM cards WHERE 1=1"
    params: list = []

    if keyword:
        query += " AND (name LIKE ? OR effect_text LIKE ? OR species LIKE ?)"
        like = f"%{keyword}%"
        params += [like, like, like]

    def add_in_filter(column: str, values):
        if values:
            placeholders = ", ".join("?" for _ in values)
            return f" AND {column} IN ({placeholders})", list(values)
        return "", []

    for column, values in (("card_type", card_types), ("attribute", attributes),
                            ("rarity", rarities), ("species", species)):
        part, vals = add_in_filter(column, values)
        query += part
        params += vals

    query += " ORDER BY card_num, id"
    return conn.execute(query, params).fetchall()


def get_card(conn: sqlite3.Connection, card_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()


def get_distinct_values(conn: sqlite3.Connection, column: str) -> list[str]:
    rows = conn.execute(f"SELECT DISTINCT {column} FROM cards WHERE {column} IS NOT NULL ORDER BY {column}").fetchall()
    return [r[0] for r in rows]


def count_cards(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]


def insert_price(
    conn: sqlite3.Connection,
    card_id: str,
    site: str,
    price: int,
    recorded_at: str | None = None,
    sample_count: int | None = None,
) -> None:
    recorded_at = recorded_at or datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO price_history (card_id, site, price, recorded_at, sample_count) VALUES (?, ?, ?, ?, ?)",
        (card_id, site, price, recorded_at, sample_count),
    )
    conn.commit()


def get_price_history(conn: sqlite3.Connection, card_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM price_history WHERE card_id = ? ORDER BY recorded_at", (card_id,)
    ).fetchall()


def delete_prices(conn: sqlite3.Connection, card_ids: list[str], site: str) -> int:
    if not card_ids:
        return 0
    placeholders = ",".join("?" for _ in card_ids)
    cur = conn.execute(
        f"DELETE FROM price_history WHERE site = ? AND card_id IN ({placeholders})",
        (site, *card_ids),
    )
    conn.commit()
    return cur.rowcount


def upsert_banlist(conn: sqlite3.Connection, entries: list[dict]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM banlist")
    for e in entries:
        conn.execute(
            "INSERT INTO banlist (card_num, name, status, fetched_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET card_num=excluded.card_num, status=excluded.status, fetched_at=excluded.fetched_at",
            (e.get("card_num"), e["name"], e["status"], now),
        )
    conn.commit()
    return len(entries)


def get_banlist(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT name, status FROM banlist").fetchall()
    return {r["name"]: r["status"] for r in rows}
