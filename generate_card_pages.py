"""カード1枚ごとに固有のURL(site/card/{id}.html)を持つ静的ページを生成する。

検索エンジンから見て「このカード名の相場情報を載せている固有ページ」が存在するように、
カードごとに専用のtitle/meta description/OGP/構造化データ(JSON-LD, Product)を持つ静的HTMLを
生成する。価格情報はビルド時点の値(export_static.build_prices_latest_jsonと同じ計算)を
HTMLに直接埋め込み、JavaScript実行に依存せず検索エンジンが読み取れるようにする。
ページ読み込み後はcard-detail.jsがこのカード1枚分の価格履歴(data/prices/{id}.json、遅延取得)を
使って、期間切り替えタブ付きの簡易グラフに差し替える(値そのものは一致する)。
"""
import html
import json
import urllib.parse
from pathlib import Path

import db
from export_static import (
    CARD_FIELDS, _resolve_image_url, build_prices_latest_json,
    MERCARI_AFFILIATE_ID, AMAZON_ASSOCIATE_TAG, RAKUTEN_AFFILIATE_ID,
)

SITE_BASE_URL = "https://kazuyan340.github.io/yugioh-tcg-market"
SITE_DIR = Path(__file__).parent / "site"
CARD_PAGE_DIR = SITE_DIR / "card"

ASSET_VERSION = "1"

NAV_LINKS = [
    ("index.html", "📋 カード図鑑"),
    ("trends.html", "📊 価格の動きを見る"),
    ("movers-up.html", "🔺 値上がりを見る"),
    ("movers-down.html", "🔻 値下がりを見る"),
    ("ranking.html", "💰 相場ランキング"),
    ("compare.html", "★ お気に入り"),
    ("deck.html", "🃏 デッキ作成"),
]

STATIC_PAGES = [
    "index.html", "trends.html", "movers-up.html", "movers-down.html",
    "ranking.html", "compare.html", "deck.html", "about.html", "privacy.html",
]


def _e(value) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _json_script(data) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _nav_links_html() -> str:
    return "\n      ".join(f'<a href="{href}" class="nav-btn">{label}</a>' for href, label in NAV_LINKS)


def _purchase_buttons_html(card_name: str, card_num: str, rarity: str | None) -> str:
    query = " ".join(p for p in (card_name, card_num, rarity or "") if p).strip()
    q = urllib.parse.quote(query)
    mercari_url = f"https://jp.mercari.com/search?afid={MERCARI_AFFILIATE_ID}&keyword={q}"
    amazon_url = f"https://www.amazon.co.jp/s?k={q}&tag={AMAZON_ASSOCIATE_TAG}"
    rakuten_target = urllib.parse.quote(f"https://search.rakuten.co.jp/search/mall/{q}/", safe="")
    rakuten_url = f"https://hb.afl.rakuten.co.jp/hgc/{RAKUTEN_AFFILIATE_ID}/?pc={rakuten_target}"
    buttons = [
        (mercari_url, "🔍 メルカリで価格を確認する"),
        (amazon_url, "🔍 Amazonで価格を確認する"),
        (rakuten_url, "🔍 楽天市場で価格を確認する"),
    ]
    links = "".join(
        f'<a class="marketplace-check-btn" href="{url}" target="_blank" rel="nofollow noopener sponsored">{label} <span class="pr-label">PR</span></a>'
        for url, label in buttons
    )
    return f'<div class="purchase-buttons">{links}</div>'


def _static_price_html(price_info: dict | None) -> tuple[str, str]:
    if not price_info:
        return "", ""
    stats = f'<div class="price-best">¥{price_info["best"]["price"]:,}〜 ({_e(price_info["best"]["site"])}) / 全ショップ平均 ¥{price_info["pooled_avg"]:,}</div>'
    rows = []
    for shop in sorted(price_info["shops"], key=lambda s: s["price"]):
        rows.append(
            f'<div class="price-shop-row"><span>{_e(shop["site"])}</span>'
            f'<span>¥{shop["price"]:,} ({shop["sample_count"]}件の出品中最安値)</span></div>'
        )
    return stats, "".join(rows)


def _meta_description(card: dict, price_info: dict | None) -> str:
    parts = [card["name"]]
    if card.get("card_num"):
        parts.append(f"({card['card_num']})")
    if card.get("rarity"):
        parts.append(f" {card['rarity']}")
    parts.append(" の相場・価格情報、効果テキスト、カードステータス。")
    if price_info:
        parts.append(f"現在の相場は¥{price_info['pooled_avg']:,}です。")
    parts.append("複数の販売サイトの価格をまとめて比較できます。")
    return "".join(parts)


def _field_rows_html(card: dict) -> str:
    fields = [
        ("カード番号", card.get("card_num")),
        ("種類", card.get("card_type")),
        ("モンスター種別", card.get("monster_type")),
        ("種族", card.get("species")),
        ("属性", card.get("attribute")),
        ("レベル", card.get("level")),
        ("ランク", card.get("rank")),
        ("リンクレイティング", card.get("link_rating")),
        ("リンクマーカー", card.get("link_markers")),
        ("ペンデュラムスケール", card.get("pendulum_scale")),
        ("ATK", card.get("atk")),
        ("DEF", card.get("def")),
        ("レアリティ", card.get("rarity")),
        ("収録パック", card.get("pack")),
        ("発売日", card.get("release_date")),
    ]
    return "\n".join(
        f'<div class="info-row"><span class="info-label">{_e(label)}</span><span class="info-value">{_e(value)}</span></div>'
        for label, value in fields
        if value not in (None, "", 0)
    )


def _card_page_html(card: dict, price_info: dict | None) -> str:
    title = f"{card['name']}({card.get('card_num') or '?'}) 相場・価格情報 | 遊戯王OCG カード図鑑"
    description = _meta_description(card, price_info)
    page_url = f"{SITE_BASE_URL}/card/{urllib.parse.quote(card['id'], safe='')}.html"
    image_url = card.get("image_url") or ""

    price_stats_html, price_table_html = _static_price_html(price_info)
    purchase_buttons_html = _purchase_buttons_html(card["name"], card.get("card_num") or "", card.get("rarity"))
    price_empty_hidden = "hidden" if price_info else ""

    effect_html = ""
    if card.get("effect_text"):
        effect_html = f'<div class="info-block"><h3>効果テキスト</h3><p>{_e(card["effect_text"])}</p></div>'
    pendulum_html = ""
    if card.get("pendulum_text"):
        pendulum_html = f'<div class="info-block"><h3>ペンデュラム効果</h3><p>{_e(card["pendulum_text"])}</p></div>'

    card_detail_json = _json_script({"id": card["id"], "card_num": card.get("card_num"), "name": card["name"]})

    ld_json = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": card["name"],
        "image": image_url,
        "description": description,
        "url": page_url,
        "sku": card.get("card_num"),
    }
    if price_info:
        prices = [s["price"] for s in price_info["shops"]]
        ld_json["offers"] = {
            "@type": "AggregateOffer",
            "priceCurrency": "JPY",
            "lowPrice": min(prices),
            "highPrice": max(prices),
            "offerCount": len(prices),
        }

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<!-- card/配下のページはサイトルート基準の相対パス(data/cards.json等)をそのまま
     使えるよう<base>でルートを指定する。 -->
<base href="../">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="{_e(description)}">
<link rel="canonical" href="{page_url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="遊戯王OCG カード図鑑">
<meta property="og:locale" content="ja_JP">
<meta property="og:url" content="{page_url}">
<meta property="og:title" content="{_e(title)}">
<meta property="og:description" content="{_e(description)}">
<meta property="og:image" content="{_e(image_url)}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{_e(title)}">
<meta name="twitter:description" content="{_e(description)}">
<meta name="twitter:image" content="{_e(image_url)}">
<title>{_e(title)}</title>
<link rel="stylesheet" href="style.css?v={ASSET_VERSION}">
<script type="application/ld+json">{_json_script(ld_json)}</script>
</head>
<body>
<header class="toolbar">
  <div class="title-row">
    <div class="title-block">
      <h1>{_e(card['name'])}</h1>
      <p id="last-updated" class="last-updated"></p>
    </div>
    <div class="nav-links">
      {_nav_links_html()}
    </div>
    <button type="button" class="nav-menu-toggle" aria-label="メニュー" aria-expanded="false">☰</button>
  </div>
</header>

<main class="card-detail-page">
  <div class="modal-card card-detail-card">
    <div class="modal-body">
      <div class="modal-image">
        <img src="{_e(image_url)}" alt="{_e(card['name'])}">
      </div>
      <div class="modal-info">
        <div class="modal-card-id">{_e(card['id'])}</div>
        {_field_rows_html(card)}
        {effect_html}
        {pendulum_html}
      </div>
    </div>

    <div class="info-block price-block">
      <h3>相場</h3>
      {price_stats_html}
      {price_table_html}
      {purchase_buttons_html}
      <div class="price-chart-col">
        <div class="period-tabs hidden" id="period-tabs">
          <button type="button" class="period-tab" data-days="0">全期間</button>
          <button type="button" class="period-tab active" data-days="30">30日</button>
          <button type="button" class="period-tab" data-days="7">7日</button>
        </div>
        <canvas id="price-chart" width="420" height="200" class="hidden"></canvas>
        <p id="chart-empty" class="price-empty {price_empty_hidden}">{"" if price_info else "価格データがありません。"}</p>
      </div>
    </div>
  </div>

  <p class="card-detail-back"><a href="index.html">← カード一覧へ戻る</a></p>
</main>

<footer class="site-disclaimer">
  <p>価格情報は当サイト調べです。実際の価格・在庫状況と差異が生じる場合があります。掲載しているカード画像・カード情報は遊戯王OCG公式サイトの情報を参照しています。</p>
  <p><a href="about.html">運営者情報</a>　<a href="privacy.html">プライバシーポリシー・お問い合わせ</a></p>
</footer>

<script>window.CARD_DETAIL = {card_detail_json};</script>
<script src="common.js?v={ASSET_VERSION}"></script>
<script src="card-detail.js?v={ASSET_VERSION}"></script>
</body>
</html>
"""


def generate_card_pages(conn) -> int:
    CARD_PAGE_DIR.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(f"SELECT {', '.join(CARD_FIELDS)} FROM cards").fetchall()
    cards = []
    for row in rows:
        card = dict(row)
        card["image_url"] = _resolve_image_url(card["id"], card["image_url"])
        cards.append(card)

    prices_latest = build_prices_latest_json(conn)

    existing = {p.stem for p in CARD_PAGE_DIR.glob("*.html")}
    written = set()
    for card in cards:
        html_text = _card_page_html(card, prices_latest.get(card["id"]))
        safe_name = urllib.parse.quote(card["id"], safe="")
        (CARD_PAGE_DIR / f"{safe_name}.html").write_text(html_text, encoding="utf-8")
        written.add(safe_name)
    for stale in existing - written:
        (CARD_PAGE_DIR / f"{stale}.html").unlink(missing_ok=True)

    generate_sitemap([urllib.parse.quote(c["id"], safe="") for c in cards])
    return len(cards)


def generate_sitemap(card_ids: list[str]) -> None:
    urls = [f"{SITE_BASE_URL}/{page}" for page in STATIC_PAGES]
    urls += [f"{SITE_BASE_URL}/card/{card_id}.html" for card_id in card_ids]
    body = "\n".join(f"  <url><loc>{u}</loc></url>" for u in urls)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n"
        "</urlset>\n"
    )
    (SITE_DIR / "sitemap.xml").write_text(xml, encoding="utf-8")


def main():
    conn = db.get_connection()
    db.init_db(conn)
    try:
        count = generate_card_pages(conn)
    finally:
        conn.close()
    print(f"card pages: {count}件 -> {CARD_PAGE_DIR}")
    print(f"sitemap.xml: {len(STATIC_PAGES) + count}件のURL")


if __name__ == "__main__":
    main()
