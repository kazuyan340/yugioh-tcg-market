"""DBの内容を静的サイト用のJSONファイル(site/data/配下)へ書き出すスクリプト。

GitHub Pages等の静的ホスティングで動かすため、サイト側はこのJSONをfetchするだけで
完結する(サーバーサイド処理は一切不要)。db.py(カードスクレイパー担当)の
スキーマ・price_matching.py/unresolved_report.py(価格スクレイパー担当)が書き出す
生成物にのみ依存し、それらのファイル自体は変更しない。

出力ファイル:
  site/data/cards.json               … カード一覧全件
  site/data/meta.json                … 総件数・絞り込み用の候補値一覧・生成時刻
  site/data/prices_latest.json       … カードごとの直近最安値・全ショップ平均
  site/data/prices/{id}.json         … カード1枚分の価格履歴全件(データがあるカードのみ)
  site/data/trends.json              … 値動き(直近上昇/上昇傾向/直近下降/下降傾向)
  site/data/movers.json              … 値上がり/値下がりカード一覧
  site/data/banlist.json             … 禁止/制限/準制限カード一覧(空でも可)
  site/data/unofficial-cards.json    … (admin向け予備出力。cards.jsonと同一だが将来の
                                          データソース分離に備えて別出力にしている)
"""
import json
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import db

try:
    import images
    HAS_IMAGES = True
except ImportError:
    HAS_IMAGES = False

SITE_DATA_DIR = Path(__file__).parent / "site" / "data"

# メルカリ・Amazon・楽天のアフィリエイトID(site/common.jsと共用。conan/onepieceと
# 同一のIDを流用する、ユーザー承認済み)。
MERCARI_AFFILIATE_ID = "8969530097"
AMAZON_ASSOCIATE_TAG = "conantcgmarke-22"
RAKUTEN_AFFILIATE_ID = "567cd45a.2625f6eb.567cd45b.7e49c506"

TREND_LIMIT = 50
MOVERS_LIMIT = 100
POOLED_SITE_LABEL = "全体"

CARD_FIELDS = [
    "id", "cid", "card_num", "name", "ruby", "card_type", "monster_type",
    "species", "attribute", "level", "rank", "link_rating", "link_markers",
    "pendulum_scale", "atk", "def", "rarity", "pack", "pack_code",
    "effect_text", "pendulum_text", "image_url", "release_date",
]


def _resolve_image_url(card_id: str, image_url: str | None) -> str | None:
    """画像URLをサイト内配信パスに変換する。

    images.py は download_missing_images() の中でダウンロード成功の都度、
    DBの cards.image_url 自体を "images/cards/{ファイル名}" のローカル相対パスに
    書き換えている。そのためこの関数に渡ってくる image_url は、
    ダウンロード済みのカードなら既にローカルパス、未ダウンロードのカードなら
    公式サイトの一時URLのどちらかであり、ここでは値をそのまま返すだけでよい
    (images.py未実行の開発初期段階でも公式URLへのフォールバックとして自然に動く)。
    """
    if not image_url:
        return None
    return image_url


def build_cards_json(conn) -> list[dict]:
    rows = conn.execute(
        f"SELECT {', '.join(CARD_FIELDS)} FROM cards ORDER BY card_num, id"
    ).fetchall()
    cards = []
    for row in rows:
        card = dict(row)
        card["image_url"] = _resolve_image_url(card["id"], card["image_url"])
        cards.append(card)
    return cards


def build_prices_latest_json(conn) -> dict:
    """カードごとの直近の最安値を site/data/prices_latest.json 用に組み立てる。

    price_historyは日々の実行のたびに行を積み増していく(店舗ごとの推移を残すため)。
    ここでは各カード・各店舗の最新1件だけを抜き出し、店舗間の最安値をそのカードの
    代表価格とする。
    """
    rows = conn.execute(
        """
        SELECT ph.card_id, ph.site, ph.price, ph.recorded_at, ph.sample_count
        FROM price_history ph
        INNER JOIN (
            SELECT card_id, site, MAX(recorded_at) AS max_recorded_at
            FROM price_history
            GROUP BY card_id, site
        ) latest
        ON ph.card_id = latest.card_id
        AND ph.site = latest.site
        AND ph.recorded_at = latest.max_recorded_at
        """
    ).fetchall()

    by_card: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_card[row["card_id"]].append({
            "site": row["site"],
            "price": row["price"],
            "recorded_at": row["recorded_at"],
            "sample_count": row["sample_count"],
        })

    result = {}
    for card_id, entries in by_card.items():
        best = min(entries, key=lambda e: e["price"])
        pooled_avg = round(sum(e["price"] for e in entries) / len(entries))
        result[card_id] = {"best": best, "shops": entries, "pooled_avg": pooled_avg}
    return result


def export_prices(conn) -> dict[str, list[dict]]:
    rows = conn.execute(
        "SELECT card_id, site, price, recorded_at, sample_count FROM price_history ORDER BY recorded_at"
    ).fetchall()
    prices: dict[str, list[dict]] = {}
    for row in rows:
        prices.setdefault(row["card_id"], []).append({
            "site": row["site"],
            "price": row["price"],
            "recorded_at": row["recorded_at"],
            "sample_count": row["sample_count"],
        })
    return prices


def write_prices_per_card(prices: dict[str, list[dict]], out_dir: Path) -> int:
    """カードごとの価格履歴全件を site/data/prices/{id}.json に1枚1ファイルで書き出す。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = {p.stem for p in out_dir.glob("*.json")}
    written = set()
    for card_id, points in prices.items():
        safe_name = urllib.parse.quote(card_id, safe="")
        (out_dir / f"{safe_name}.json").write_text(
            json.dumps(points, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        written.add(safe_name)
    for stale in existing - written:
        (out_dir / f"{stale}.json").unlink(missing_ok=True)
    return len(prices)


def _price_points_by_card_site(conn) -> dict[tuple[str, str], list[tuple[str, int]]]:
    """(card_id, site) -> [(recorded_at, price), ...] (日時順)。

    サイトごとに独立した時系列として扱う。仕入れ元が違えば価格帯そのものが異なるため、
    サイトをまたいで1本の時系列にすると「サイトが入れ替わっただけ」を値上がり/値下がりと
    誤検出してしまう。同じ日に複数回記録されていた場合はその日の最後の値だけを残す。
    """
    rows = conn.execute(
        "SELECT card_id, site, price, recorded_at FROM price_history ORDER BY card_id, site, recorded_at"
    ).fetchall()

    latest_by_day: dict[tuple[str, str], dict[str, tuple[str, int]]] = defaultdict(dict)
    for row in rows:
        key = (row["card_id"], row["site"])
        day = row["recorded_at"][:10]
        latest_by_day[key][day] = (row["recorded_at"], row["price"])

    return {
        key: [days[day] for day in sorted(days)] for key, days in latest_by_day.items()
    }


def _pooled_points_by_card(
    by_card_site: dict[tuple[str, str], list[tuple[str, int]]],
) -> dict[str, list[tuple[str, int]]]:
    """(card_id) -> [(日付, 全ショップ単純平均価格), ...]。最新日のショップ構成と一致する
    連続区間(末尾から遡って同じ顔ぶれが続く範囲)だけを対象にする(新規ショップ参入/撤退で
    平均だけが動いて見える誤検出を避けるため)。
    """
    by_card_day: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    site_set_by_card_day: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for (card_id, site), points in by_card_site.items():
        for recorded_at, price in points:
            day = recorded_at[:10]
            by_card_day[card_id][day].append(price)
            site_set_by_card_day[card_id][day].add(site)

    result: dict[str, list[tuple[str, int]]] = {}
    for card_id, days in by_card_day.items():
        sorted_days = sorted(days)
        latest_set = frozenset(site_set_by_card_day[card_id][sorted_days[-1]])
        cutoff = len(sorted_days) - 1
        for i in range(len(sorted_days) - 2, -1, -1):
            if frozenset(site_set_by_card_day[card_id][sorted_days[i]]) != latest_set:
                break
            cutoff = i
        stable_days = sorted_days[cutoff:]
        result[card_id] = [(day, round(sum(days[day]) / len(days[day]))) for day in stable_days]
    return result


def _all_price_series(conn) -> dict[tuple[str, str], list[tuple[str, int]]]:
    by_card_site = _price_points_by_card_site(conn)
    pooled = _pooled_points_by_card(by_card_site)
    combined = dict(by_card_site)
    for card_id, points in pooled.items():
        combined[(card_id, POOLED_SITE_LABEL)] = points
    return combined


def _previous_day_moves(
    by_card_site: dict[tuple[str, str], list[tuple[str, int]]],
) -> tuple[list[dict], list[dict]]:
    up, down = [], []
    for (card_id, site), points in by_card_site.items():
        if len(points) < 2:
            continue
        prev_date, prev_price = points[-2]
        last_date, last_price = points[-1]
        if prev_price <= 0 or prev_price == last_price:
            continue

        pct = (last_price - prev_price) / prev_price * 100
        item = {
            "card_id": card_id, "site": site, "change_pct": round(pct, 1),
            "previous_price": prev_price, "previous_date": prev_date,
            "latest_price": last_price, "latest_date": last_date,
        }
        (up if pct > 0 else down).append(item)
    return up, down


def _sort_limit_per_site(items: list[dict], limit: int, reverse: bool) -> list[dict]:
    by_site: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        by_site[item["site"]].append(item)

    result = []
    for site_items in by_site.values():
        site_items.sort(key=lambda x: x["change_pct"], reverse=reverse)
        result.extend(site_items[:limit])
    return result


def compute_trends(by_card_site: dict[tuple[str, str], list[tuple[str, int]]]) -> dict[str, list[dict]]:
    recent_up, recent_down = _previous_day_moves(by_card_site)

    trend_up, trend_down = [], []
    for (card_id, site), points in by_card_site.items():
        if len(points) < 3:
            continue
        mid_date, mid_price = points[-2]
        last_date, last_price = points[-1]
        prev_price = points[-3][1]
        if prev_price <= 0 or mid_price <= 0:
            continue
        item = {
            "card_id": card_id, "site": site,
            "change_pct": round((last_price - mid_price) / mid_price * 100, 1),
            "previous_price": mid_price, "previous_date": mid_date,
            "latest_price": last_price, "latest_date": last_date,
        }
        if mid_price > prev_price and last_price > mid_price:
            trend_up.append(item)
        elif mid_price < prev_price and last_price < mid_price:
            trend_down.append(item)

    return {
        "recent_up": _sort_limit_per_site(recent_up, TREND_LIMIT, reverse=True),
        "trend_up": _sort_limit_per_site(trend_up, TREND_LIMIT, reverse=True),
        "recent_down": _sort_limit_per_site(recent_down, TREND_LIMIT, reverse=False),
        "trend_down": _sort_limit_per_site(trend_down, TREND_LIMIT, reverse=False),
    }


def compute_movers(by_card_site: dict[tuple[str, str], list[tuple[str, int]]]) -> dict[str, list[dict]]:
    up, down = _previous_day_moves(by_card_site)
    return {
        "up": _sort_limit_per_site(up, MOVERS_LIMIT, reverse=True),
        "down": _sort_limit_per_site(down, MOVERS_LIMIT, reverse=False),
    }


# 禁止/制限/準制限カードの上限枚数。banlistテーブルが空(スクレイパーがまだソースを
# 見つけられていない場合含む)なら空のまま出力し、フロントエンド側は制限なしの
# 簡易デッキ構築にフォールバックする。
BANLIST_LIMITS = {"forbidden": 0, "limited": 1, "semi-limited": 2}


def build_banlist_json(conn) -> dict:
    banlist = db.get_banlist(conn)  # name -> status
    return {
        "entries": [{"name": name, "status": status, "limit": BANLIST_LIMITS.get(status, 3)}
                    for name, status in sorted(banlist.items())],
        "limits": BANLIST_LIMITS,
    }


def build_meta_json(conn) -> dict:
    return {
        "total_cards": db.count_cards(conn),
        "card_types": db.get_distinct_values(conn, "card_type"),
        "species": db.get_distinct_values(conn, "species"),
        "attributes": db.get_distinct_values(conn, "attribute"),
        "rarities": db.get_distinct_values(conn, "rarity"),
        "packs": db.get_distinct_values(conn, "pack"),
        "monster_types": db.get_distinct_values(conn, "monster_type"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def export_unofficial_cards(conn) -> list[dict]:
    """admin向け予備出力(現時点ではcards.jsonと同一集合)。将来、非公式/型番不明カードを
    別ソースとして扱うようになった際にここだけ差し替えられるよう分離しておく。"""
    return build_cards_json(conn)


def export_static() -> None:
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = db.get_connection()
    db.init_db(conn)
    try:
        cards = build_cards_json(conn)
        meta = build_meta_json(conn)
        prices_latest = build_prices_latest_json(conn)
        prices = export_prices(conn)
        all_series = _all_price_series(conn)
        trends = compute_trends(all_series)
        movers = compute_movers(all_series)
        banlist = build_banlist_json(conn)
        unofficial_cards = export_unofficial_cards(conn)
    finally:
        conn.close()

    (SITE_DATA_DIR / "cards.json").write_text(
        json.dumps(cards, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (SITE_DATA_DIR / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (SITE_DATA_DIR / "prices_latest.json").write_text(
        json.dumps(prices_latest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    per_card_count = write_prices_per_card(prices, SITE_DATA_DIR / "prices")
    (SITE_DATA_DIR / "trends.json").write_text(
        json.dumps(trends, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (SITE_DATA_DIR / "movers.json").write_text(
        json.dumps(movers, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (SITE_DATA_DIR / "banlist.json").write_text(
        json.dumps(banlist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (SITE_DATA_DIR / "unofficial-cards.json").write_text(
        json.dumps(unofficial_cards, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(
        f"cards.json: {len(cards)}件 / meta.json / prices_latest.json({len(prices_latest)}件) / "
        f"prices/({per_card_count}件) / trends.json / movers.json / "
        f"banlist.json({len(banlist['entries'])}件) / unofficial-cards.json を書き出しました。"
    )
    if not banlist["entries"]:
        print("banlist.json は空です(banlistテーブルが未収集)。デッキ作成ページは制限なしの簡易モードで動作します。")


if __name__ == "__main__":
    export_static()
