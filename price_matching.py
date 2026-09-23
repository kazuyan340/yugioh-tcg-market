"""各ショップの価格スクレイパー共通: card_num(+わかる場合はレアリティ)からDBのカードIDを
引き当てるヘルパー。

遊戯王OCGでは同じカード番号(card_num, 例: LOB-JP001)であっても、収録パックや
レアリティが異なれば別のDB行(cards.id)として存在し、しかも20thシークレット等
ごく一部のケースではcard_numそのものが枝分かれせず、同じcard_numに対して
複数レアリティが並存することもある(db.py参照)。ショップの商品情報(商品名から
読み取れるカード番号・レアリティ表記)だけでは、この枝分かれを確実に一意化
できないことがある。

遊戯王は再録(構築済みデッキ収録、ストラクチャーデッキの度重なる再録等)や
レアリティ違いの絶対数が非常に多いジャンルのため、そのようなケースで
自動的に「どちらか」を推測することは行わない。候補が複数残った場合は常に
「特定できなかったもの」として扱い、管理ページ(site/admin-unofficial-cards.html)
でユーザー自身に選んでもらう。

ユーザーが管理ページで選んだ結果はmanual_resolutions.json(商品ページURL -> card_id)
に反映され、次回以降のスクレイパー実行ではそちらが最優先で使われる。
"""
import json
from collections import defaultdict
from pathlib import Path

MANUAL_RESOLUTIONS_PATH = Path(__file__).parent / "manual_resolutions.json"


def build_lookup(conn) -> dict[str, list[dict]]:
    """card_num -> そのcard_numを持つcards行(dict化)のリスト、を返す。"""
    rows = conn.execute("SELECT id, card_num, rarity, name, pack, image_url FROM cards").fetchall()
    lookup: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        lookup[row["card_num"]].append(dict(row))
    return lookup


def build_lookup_by_name(conn) -> dict[str, list[dict]]:
    """name -> 同名の全cards行(dict化)のリスト、を返す。

    竜のしっぽ(scraper_prices_ryuunoshippo.py)のように、商品名にDBのcard_num形式が
    一切含まれず、カード名(+わかれば店舗独自のレアリティ略号)しか手掛かりが無い
    ショップ向け。resolve()/apply_resolution()はキーの意味を問わないため、この
    lookupと(card_numの代わりに)カード名を渡せばそのまま使い回せる。

    遊戯王は同名カードの再録・レアリティ違いが非常に多いため、この方式では
    ambiguous(候補複数)になる頻度がcard_num方式より大幅に高くなる。これは
    「名前だけでは絵柄・収録パックまで確実に区別できない」という制約上の
    想定内の挙動であり、誤った1枚に決め打ちするよりは安全側に倒している。
    """
    rows = conn.execute("SELECT id, card_num, rarity, name, pack, image_url FROM cards").fetchall()
    lookup: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        lookup[row["name"]].append(dict(row))
    return lookup


def load_manual_resolutions() -> dict[str, str]:
    """商品ページURL -> cards.id の手動確定マップを読み込む。ファイルが無ければ
    空の辞書を返す(このリポジトリではオプトインの仕組みなので、無くても正常動作する)。
    """
    if not MANUAL_RESOLUTIONS_PATH.exists():
        return {}
    data = json.loads(MANUAL_RESOLUTIONS_PATH.read_text(encoding="utf-8"))
    return {entry["product_url"]: entry["card_id"] for entry in data if entry.get("product_url")}


def resolve(
    card_num: str,
    lookup: dict[str, list[dict]],
    rarity: str | None = None,
    product_url: str | None = None,
    manual_resolutions: dict[str, str] | None = None,
) -> dict:
    """商品(card_num, rarity, product_url)からカードを1枚に特定する。

    戻り値は次のいずれか:
    - {"status": "resolved", "card_id": ...}
        1枚に特定できた(手動確定リストに載っている、またはDB候補が1件だけ)。
    - {"status": "ambiguous", "candidates": [...]}
        DBには該当card_numの候補が複数あり、自動では1枚に絞れない
        (例: 同じcard_numで複数パック・複数レアリティに再録されている)。
    - {"status": "missing"}
        該当card_numがDBに1件も無い(未収録、もしくは表記揺れで一致しない)。

    レアリティで絞り込んでも1件に絞れない場合(rarityが取れない商品、または
    同じcard_num+rarityの組で複数レコードが残る場合)は、決して自動で1つを
    選ばず ambiguous として返す。
    """
    if manual_resolutions and product_url and product_url in manual_resolutions:
        return {"status": "resolved", "card_id": manual_resolutions[product_url]}

    candidates = lookup.get(card_num, [])
    if rarity:
        base_rarity = rarity.split("/")[0].strip()
        narrowed = [c for c in candidates if c["rarity"] == base_rarity]
        if narrowed:
            candidates = narrowed

    if not candidates:
        return {"status": "missing"}
    if len(candidates) == 1:
        return {"status": "resolved", "card_id": candidates[0]["id"]}
    return {"status": "ambiguous", "candidates": candidates}


def apply_resolution(
    all_prices: dict,
    unresolved_entries: list,
    card_num: str,
    rarity: str | None,
    price: int,
    lookup: dict[str, list[dict]],
    manual_resolutions: dict[str, str],
    product_name: str | None = None,
    image_url: str | None = None,
    product_url: str | None = None,
) -> None:
    """1商品分の価格を解決し、確定できればall_prices(card_id -> [price,...])へ、
    特定できなければunresolved_entriesへ追加する(各スクレイパー共通のロジック)。
    """
    result = resolve(card_num, lookup, rarity, product_url, manual_resolutions)
    if result["status"] == "resolved":
        all_prices[result["card_id"]].append(price)
        return

    entry = {
        "raw_key": card_num,
        "rarity": rarity,
        "price": price,
        "product_name": product_name,
        "image_url": image_url,
        "product_url": product_url,
        "candidates": result.get("candidates", []),
    }
    unresolved_entries.append(entry)
