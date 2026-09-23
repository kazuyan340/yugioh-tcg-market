"""各価格スクレイパーが「出品はあり価格も付いているのに、DBのカード1枚に
特定できなかった商品」を site/data/unresolved_raw/{site}.json に書き出すための
共通ヘルパー。通常の価格取得(sync_prices)の副産物として書き出すので、
このためだけに追加でサイトへアクセスすることはない。

build_unresolved_report.py がこれら各サイトの生データをまとめて、管理ページ
(site/admin-unofficial-cards.html)が読み込む site/data/unresolved-shop-items.json を作る。
"""
import json
from pathlib import Path

UNRESOLVED_DIR = Path(__file__).parent / "site" / "data" / "unresolved_raw"


def write_unresolved(site: str, entries: list[dict]) -> None:
    """entriesの各要素は price_matching.resolve() の結果("ambiguous"/"missing"だった分)に
    {"raw_key": card_num, "rarity": ..., "price": ..., "product_name": ..., "image_url": ...,
    "product_url": ..., "candidates": [...]} (candidatesが空なら"missing"扱い)を足したもの。
    """
    UNRESOLVED_DIR.mkdir(parents=True, exist_ok=True)
    path = UNRESOLVED_DIR / f"{site}.json"
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
