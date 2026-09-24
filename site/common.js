/* 遊戯王OCG カード図鑑 — 各ページ共通のヘルパー
   (データ読み込み、カードタイル、モーダル、お気に入り、ナビ開閉、絞り込み)。 */

// ---- 収録パックのグループ分け・並び順。遊戯王OCGは型番の先頭2〜4文字(パックコード)で
// 大まかなシリーズを判別できる。実データ(pack_code)から実際に頻出することを確認済みの
// プレフィックスのみ分類し、それ以外は「その他」に入れる。 ----
const PACK_GROUP_ORDER = ["booster", "structure", "special", "other"];
const PACK_GROUP_LABELS = {
  booster: "ブースターパック", structure: "ストラクチャーデッキ",
  special: "スペシャルパック・デュエリストパック", other: "その他",
};
const STRUCTURE_PREFIX_PATTERN = /^(SD|SR|SS)/;
const SPECIAL_PREFIX_PATTERN = /^(DP|VP|CP|EP|DL|SP|CT)/;

function packGroupFor(packValue, packCode) {
  const code = (packCode || "").toUpperCase();
  if (STRUCTURE_PREFIX_PATTERN.test(code)) return "structure";
  if (SPECIAL_PREFIX_PATTERN.test(code)) return "special";
  if (code) return "booster";
  return "other";
}

function sortPackValues(values, packCodeByValue) {
  values.sort((a, b) => {
    const ga = PACK_GROUP_ORDER.indexOf(packGroupFor(a, packCodeByValue.get(a)));
    const gb = PACK_GROUP_ORDER.indexOf(packGroupFor(b, packCodeByValue.get(b)));
    if (ga !== gb) return ga - gb;
    return a.localeCompare(b, "ja");
  });
}

// ---- 3値(含む/除外/指定なし)の絞り込みチェックボックス。クリックのたびに
// 指定なし→含む→除外→指定なしと状態が変わる。valuesがgroupFnで分類できる
// 場合は見出しを挟んでグループ表示する(収録パック用)。 ----
function updateFilterCountBadge(containerId, includeSet, excludeSet) {
  const groupId = containerId.replace(/-list$/, "-group");
  const badge = document.querySelector(`#${groupId} .count-badge`);
  if (!badge) return;
  const n = includeSet.size + excludeSet.size;
  badge.textContent = n || "";
  badge.classList.toggle("hidden", n === 0);
}

function buildTriStateList(containerId, values, includeSet, excludeSet, onChange, groupFn) {
  const container = document.getElementById(containerId);
  const frag = document.createDocumentFragment();
  let lastGroup = null;

  function stateOf(value) {
    if (includeSet.has(value)) return "include";
    if (excludeSet.has(value)) return "exclude";
    return "none";
  }

  function applyVisual(btn, value) {
    const state = stateOf(value);
    btn.dataset.state = state;
    btn.textContent = (state === "include" ? "✅ " : state === "exclude" ? "🚫 " : "☐ ") + value;
  }

  updateFilterCountBadge(containerId, includeSet, excludeSet);

  for (const value of values) {
    if (!value) continue;
    if (groupFn) {
      const g = groupFn(value);
      if (g !== lastGroup) {
        const heading = document.createElement("div");
        heading.className = "checkbox-list-heading";
        heading.textContent = PACK_GROUP_LABELS[g] || g;
        frag.appendChild(heading);
        lastGroup = g;
      }
    }
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tri-filter-item";
    applyVisual(btn, value);
    btn.addEventListener("click", () => {
      const state = stateOf(value);
      if (state === "none") {
        includeSet.add(value);
      } else if (state === "include") {
        includeSet.delete(value);
        excludeSet.add(value);
      } else {
        excludeSet.delete(value);
      }
      applyVisual(btn, value);
      updateFilterCountBadge(containerId, includeSet, excludeSet);
      onChange();
    });
    frag.appendChild(btn);
  }
  container.replaceChildren(frag);
}

function resetTriState(includeSet, excludeSet) {
  includeSet.clear();
  excludeSet.clear();
}

function matchesTriState(value, includeSet, excludeSet) {
  if (excludeSet.has(value)) return false;
  if (includeSet.size > 0) return includeSet.has(value);
  return true;
}

function matchesTriStateArray(cardValues, includeSet, excludeSet) {
  if ([...excludeSet].some((v) => cardValues.includes(v))) return false;
  if (includeSet.size > 0) return [...includeSet].some((v) => cardValues.includes(v));
  return true;
}

// ---- 数値範囲(ATK/DEF等)の絞り込み。min/maxどちらも空なら絞り込みなし。 ----
function matchesRange(value, min, max) {
  if (value === null || value === undefined || value === "") return min === null && max === null;
  const n = Number(value);
  if (Number.isNaN(n)) return min === null && max === null;
  if (min !== null && n < min) return false;
  if (max !== null && n > max) return false;
  return true;
}

// ---- 値動き系ページ(trends.html/movers-up.html/movers-down.html)で共有する、
// ショップ別タブ切り替え。「全体」は各ショップの単純平均価格の推移が基準。 ----
const TREND_SITES = ["全体", "駿河屋", "カードラボ", "まんぞく屋", "わいTV", "カードラッシュ"];

function createSiteTabController(containerId, onChange) {
  let selectedSite = TREND_SITES[0];

  function render() {
    const container = document.getElementById(containerId);
    container.innerHTML = "";
    for (const site of TREND_SITES) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "site-tab" + (site === selectedSite ? " active" : "");
      btn.textContent = site;
      btn.addEventListener("click", () => {
        selectedSite = site;
        render();
        onChange(selectedSite);
      });
      container.appendChild(btn);
    }
  }

  render();
  return { getSite: () => selectedSite };
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function fetchFresh(url) {
  return fetch(url, { cache: "no-store" });
}

// カード一覧はチャンク分割して配信している(export_static.pyのwrite_card_chunks参照。
// 38,000件超の効果テキスト込み全文を1ファイルにまとめると37MB超になり、モバイル回線・
// 非力な端末ではダウンロード+JSON.parseに時間がかかりすぎてトップページが実質開けない
// 不具合が起きていたため)。data/cards/manifest.jsonでチャンク数を把握し、
// 軽量版チャンク(data/cards/chunk-N.json、効果テキストを含まない)を並列取得する。
// 効果テキスト(data/cards/text-N.json)は初期表示をブロックしないよう、この関数が
// 返した後にバックグラウンドで取得して同じカードオブジェクトへ書き足す
// (呼び出し側はstate.cards等で同じオブジェクト参照を保持しているため、
// 取得完了後は再フェッチ無しでeffect_text/pendulum_textが自動的に見えるようになる)。
const cardTextStatus = { done: false, promise: null };

// onProgress(cardsSoFar, isFullyLoaded) が渡されていれば、チャンクが届くたびに
// (全チャンク分の取得を待たずに)呼び出す。トップページ(app.js)はこれを使って
// 最初のチャンクだけでグリッドを描画し始め、残りはバックグラウンドで追記する
// (フルカタログ37MBを全部落とし切るまで何も表示できなかった問題の対策)。
// onProgressを渡さない呼び出し(ranking.js等)は従来通り、全チャンク到着後の
// 配列をawaitで受け取るだけでよい。
// 取得は全チャンクぶんまとめてfetch()を発行してから順番にawaitするため、
// ネットワーク的には並列に走る(1件ずつ待ってから次を投げるわけではない)。
async function loadCardData(onProgress) {
  const manifestRes = await fetchFresh("data/cards/manifest.json");
  const manifest = await manifestRes.json();
  const cards = [];
  // チャンクは1つずつ順番にfetchする(全チャンクを同時にfetch()すると、
  // 遅い回線環境でブラウザの同時接続数上限(HTTP/1.1で1オリジンあたり6本)を
  // 超えた分が詰まり、かえって1チャンク目の表示まで遅くなる/不安定になる
  // ことを実機相当の低速回線シミュレーションで確認したため)。
  for (let i = 0; i < manifest.chunk_count; i++) {
    const res = await fetchFresh(`data/cards/chunk-${i}.json`);
    const batch = await res.json();
    cards.push(...batch);
    if (onProgress) onProgress(cards, i === manifest.chunk_count - 1);
    // 描画の機会をブラウザに明け渡してから次のチャンクを取りに行く。これが無いと、
    // チャンク取得が速く終わる環境ではonProgressが呼ばれても画面が一度も
    // 再描画されないまま最終状態まで進んでしまうことがある。
    // requestAnimationFrameは背景タブ/ヘッドレス実行環境で大幅に間引かれる
    // (実測でチャンクの度に呼ぶと本来ミリ秒単位のはずが合計数十秒かかった)
    // ことがあるため、マクロタスクキューに乗るsetTimeoutを使う。
    if (i < manifest.chunk_count - 1) {
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  }
  loadCardTextInBackground(cards, manifest.chunk_count);
  return cards;
}

function loadCardTextInBackground(cards, chunkCount) {
  const byId = new Map(cards.map((c) => [c.id, c]));
  cardTextStatus.done = false;
  cardTextStatus.promise = (async () => {
    for (let i = 0; i < chunkCount; i++) {
      try {
        const res = await fetchFresh(`data/cards/text-${i}.json`);
        if (!res.ok) continue;
        const textMap = await res.json();
        for (const id of Object.keys(textMap)) {
          const card = byId.get(id);
          if (card) {
            card.effect_text = textMap[id].effect_text || null;
            card.pendulum_text = textMap[id].pendulum_text || null;
          }
        }
      } catch {
        // 1チャンクの取得失敗でも他のチャンク・検索/一覧表示自体は続行する
        // (効果テキスト検索がその分だけ不完全になるのみ)。
      }
    }
    cardTextStatus.done = true;
  })();
  return cardTextStatus.promise;
}

// 指定カードの効果テキストがまだ届いていなければ、バックグラウンド取得の完了を待つ。
// (モーダルを開いた時点でまだtext-*.jsonの取得が終わっていない場合のフォールバック)
async function ensureCardText(card) {
  if (card.effect_text !== undefined || !cardTextStatus.promise) return;
  await cardTextStatus.promise;
}

async function loadPricesLatest() {
  const res = await fetchFresh("data/prices_latest.json");
  return res.ok ? res.json() : {};
}

async function loadBanlist() {
  const res = await fetchFresh("data/banlist.json");
  return res.ok ? res.json() : { entries: [], limits: {} };
}

let siteMeta = null;

async function loadSiteMeta() {
  const res = await fetchFresh("data/meta.json");
  siteMeta = await res.json();
  return siteMeta;
}

function renderLastUpdated() {
  const el = document.getElementById("last-updated");
  if (el && siteMeta) {
    el.textContent = "最終更新: " + new Date(siteMeta.generated_at).toLocaleString("ja-JP");
  }
}

// ---- お気に入り(ブラウザのlocalStorageのみに保存。サーバーには送らない) ----
const FAVORITES_KEY = "yugiohTcgFavorites";

function loadFavorites() {
  try {
    return new Set(JSON.parse(localStorage.getItem(FAVORITES_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function saveFavorites(set) {
  localStorage.setItem(FAVORITES_KEY, JSON.stringify([...set]));
}

function isFavorite(cardId) {
  return loadFavorites().has(cardId);
}

function toggleFavorite(cardId) {
  const set = loadFavorites();
  const now = !set.has(cardId);
  if (now) set.add(cardId);
  else set.delete(cardId);
  saveFavorites(set);
  return now;
}

// ---- ショップへのリンク(検索リンク。アフィリエイトは駿河屋のみ未設定のためsearch URLのみ) ----
const SHOP_LINK_BUILDERS = {
  "駿河屋": (cardNum) => `https://www.suruga-ya.jp/search?category=&search_word=${encodeURIComponent(`遊戯王 ${cardNum}`)}`,
  "カードラボ": (cardNum) => `https://www.c-labo-online.jp/product-list/?keyword=${encodeURIComponent(cardNum)}`,
  "まんぞく屋": (cardNum) => `https://shopmanzokuya.com/products/list?name=${encodeURIComponent(cardNum)}`,
  "わいTV": (cardNum) => `https://www.cardshop-waitv.net/product-list/1?keyword=${encodeURIComponent(cardNum)}`,
  "カードラッシュ": (cardNum) => `https://www.cardrush-yugioh.jp/product-list?keyword=${encodeURIComponent(cardNum)}`,
};

function shopLinkUrl(site, cardNum) {
  const builder = SHOP_LINK_BUILDERS[site];
  return builder ? builder(cardNum) : null;
}

// ---- メルカリ・Amazon・楽天市場の「価格を確認する」ボタン(アフィリエイトIDは
// conan-tcg-market/onepiece-tcg-marketと共用、ユーザー承認済み) ----
const MERCARI_AFFILIATE_ID = "8969530097";

function mercariAffiliateUrl(query) {
  return `https://jp.mercari.com/search?afid=${MERCARI_AFFILIATE_ID}&keyword=${encodeURIComponent(query)}`;
}

function mercariButtonHtml(cardName, cardNum, cardRarity) {
  if (!cardName) return "";
  const query = `${cardName} ${cardNum || ""} ${cardRarity || ""}`.replace(/\s+/g, " ").trim();
  return `<a class="marketplace-check-btn" href="${mercariAffiliateUrl(query)}" target="_blank" rel="nofollow noopener sponsored">🔍 メルカリで価格を確認する <span class="pr-label">PR</span></a>`;
}

const AMAZON_ASSOCIATE_TAG = "conantcgmarke-22";

function amazonCardSearchUrl(query) {
  const url = `https://www.amazon.co.jp/s?k=${encodeURIComponent(query)}`;
  return AMAZON_ASSOCIATE_TAG ? `${url}&tag=${AMAZON_ASSOCIATE_TAG}` : url;
}

function amazonButtonHtml(cardName, cardNum, cardRarity) {
  if (!cardName) return "";
  const query = `${cardName} ${cardNum || ""} ${cardRarity || ""}`.replace(/\s+/g, " ").trim();
  return `<a class="marketplace-check-btn" href="${amazonCardSearchUrl(query)}" target="_blank" rel="nofollow noopener sponsored">🔍 Amazonで価格を確認する <span class="pr-label">PR</span></a>`;
}

const RAKUTEN_AFFILIATE_ID = "567cd45a.2625f6eb.567cd45b.7e49c506";

function rakutenCardSearchUrl(query) {
  const url = `https://search.rakuten.co.jp/search/mall/${encodeURIComponent(query)}/`;
  if (!RAKUTEN_AFFILIATE_ID) return url;
  return `https://hb.afl.rakuten.co.jp/hgc/${RAKUTEN_AFFILIATE_ID}/?pc=${encodeURIComponent(url)}`;
}

function rakutenButtonHtml(cardName, cardNum, cardRarity) {
  if (!cardName) return "";
  const query = `${cardName} ${cardNum || ""} ${cardRarity || ""}`.replace(/\s+/g, " ").trim();
  return `<a class="marketplace-check-btn" href="${rakutenCardSearchUrl(query)}" target="_blank" rel="nofollow noopener sponsored">🔍 楽天市場で価格を確認する <span class="pr-label">PR</span></a>`;
}

function purchaseButtonsHtml(cardName, cardNum, cardRarity) {
  return `<div class="purchase-buttons">${mercariButtonHtml(cardName, cardNum, cardRarity)}${amazonButtonHtml(cardName, cardNum, cardRarity)}${rakutenButtonHtml(cardName, cardNum, cardRarity)}</div>`;
}

// ---- カードタイル(一覧・ランキング・値動き等で共用) ----
function statRow(label, value) {
  if (value === null || value === undefined || value === "") return "";
  return `<div class="info-row"><span class="info-label">${label}</span><span class="info-value">${value}</span></div>`;
}

function cardSubLabel(card) {
  const bits = [card.card_num, card.rarity].filter(Boolean);
  return bits.join(" ・ ");
}

function createCardTile(card, pricesLatest, badgeHtml) {
  const priceInfo = pricesLatest[card.id];
  const priceBadge = priceInfo
    ? `<div class="card-tile-price">${priceInfo.pooled_avg.toLocaleString()}円</div>`
    : "";
  const tile = document.createElement("button");
  tile.type = "button";
  tile.className = "card-tile";
  tile.innerHTML = `
    ${badgeHtml || ""}
    <img class="card-thumb" src="${card.image_url || ""}" alt="${escapeHtml(card.name)}" loading="lazy">
    <div class="card-tile-name">${escapeHtml(card.name)}</div>
    <div class="card-tile-sub">${escapeHtml(cardSubLabel(card))}</div>
    ${priceBadge}
  `;

  const star = document.createElement("button");
  star.type = "button";
  star.className = "favorite-star" + (isFavorite(card.id) ? " active" : "");
  star.textContent = isFavorite(card.id) ? "★" : "☆";
  star.title = "お気に入りに登録/解除";
  star.addEventListener("click", (e) => {
    e.stopPropagation();
    const nowFav = toggleFavorite(card.id);
    star.textContent = nowFav ? "★" : "☆";
    star.classList.toggle("active", nowFav);
  });
  tile.prepend(star);

  tile.addEventListener("click", () => openModal(card, pricesLatest));
  return tile;
}

// ---- 詳細モーダル(相場・簡易グラフ付き) ----
const priceHistoryCache = new Map();

function fetchPriceHistory(cardId) {
  if (priceHistoryCache.has(cardId)) return priceHistoryCache.get(cardId);
  const promise = fetchFresh(`data/prices/${encodeURIComponent(cardId)}.json`)
    .then((r) => (r.ok ? r.json() : []))
    .catch(() => []);
  priceHistoryCache.set(cardId, promise);
  return promise;
}

function shopTableHtml(card, priceInfo) {
  if (!priceInfo) return "";
  return priceInfo.shops
    .slice()
    .sort((a, b) => a.price - b.price)
    .map((shop) => {
      const url = shopLinkUrl(shop.site, card.card_num);
      const shopLabel = url
        ? `<a href="${url}" target="_blank" rel="noopener">${shop.site}</a>`
        : shop.site;
      return `
        <div class="price-shop-row">
          <span>${shopLabel}</span>
          <span>¥${shop.price.toLocaleString()} (${shop.sample_count}件の出品中最安値)</span>
        </div>
      `;
    }).join("");
}

function priceStatsHtml(priceInfo) {
  if (!priceInfo) return "";
  const updatedAt = new Date(priceInfo.best.recorded_at).toLocaleDateString("ja-JP");
  return `
    <div class="price-best">¥${priceInfo.best.price.toLocaleString()}〜 (${priceInfo.best.site}) / 全ショップ平均 ¥${priceInfo.pooled_avg.toLocaleString()}</div>
    <div class="price-avg-date">最終取得: ${updatedAt}</div>
  `;
}

function priceSection(card, pricesLatest) {
  const priceInfo = pricesLatest[card.id];
  const purchaseButtons = purchaseButtonsHtml(card.name, card.card_num, card.rarity);
  if (!priceInfo) {
    return `<div class="info-block price-block"><h3>相場</h3><p class="price-empty">価格データがありません。</p>${purchaseButtons}</div>`;
  }
  return `
    <div class="info-block price-block">
      <h3>相場</h3>
      ${priceStatsHtml(priceInfo)}
      ${shopTableHtml(card, priceInfo)}
      ${purchaseButtons}
      <div class="price-chart-col">
        <div class="period-tabs" id="period-tabs">
          <button type="button" class="period-tab" data-days="0">全期間</button>
          <button type="button" class="period-tab active" data-days="30">30日</button>
          <button type="button" class="period-tab" data-days="7">7日</button>
        </div>
        <canvas id="price-chart" width="420" height="200"></canvas>
        <p id="chart-empty" class="price-empty hidden">推移データがまだありません。</p>
      </div>
    </div>
  `;
}

function pooledSeriesFromHistory(history) {
  const byDay = new Map();
  for (const p of history) {
    const day = p.recorded_at.slice(0, 10);
    if (!byDay.has(day)) byDay.set(day, []);
    byDay.get(day).push(p.price);
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([day, prices]) => ({
      day,
      price: Math.round(prices.reduce((a, b) => a + b, 0) / prices.length),
    }));
}

// マウスを合わせた点に近い位置にツールチップ(日付・価格)を出す。1つのdiv要素を使い回す。
let chartTooltipEl = null;

function getChartTooltip() {
  if (!chartTooltipEl) {
    chartTooltipEl = document.createElement("div");
    chartTooltipEl.className = "chart-tooltip";
    document.body.appendChild(chartTooltipEl);
  }
  return chartTooltipEl;
}

function setupChartHover(canvas, hitPoints) {
  canvas._chartHitPoints = hitPoints;
  if (canvas._chartHoverBound) return;
  canvas._chartHoverBound = true;

  const tooltip = getChartTooltip();
  const HIT_RADIUS = 12;

  canvas.addEventListener("mousemove", (e) => {
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    let nearest = null;
    let nearestDist = HIT_RADIUS;
    for (const p of canvas._chartHitPoints || []) {
      const dist = Math.hypot(p.x - mx, p.y - my);
      if (dist <= nearestDist) {
        nearest = p;
        nearestDist = dist;
      }
    }

    if (nearest) {
      tooltip.textContent = `${nearest.date}: ¥${nearest.price.toLocaleString()}`;
      tooltip.style.left = `${e.clientX + 12}px`;
      tooltip.style.top = `${e.clientY + 12}px`;
      tooltip.style.display = "block";
      canvas.style.cursor = "pointer";
    } else {
      tooltip.style.display = "none";
      canvas.style.cursor = "default";
    }
  });

  canvas.addEventListener("mouseleave", () => {
    tooltip.style.display = "none";
    canvas.style.cursor = "default";
  });
}

function drawSimpleChart(canvas, points) {
  // canvasの描画バッファ解像度をCSS表示サイズ(+devicePixelRatio)に合わせる。
  // これをしないと、CSS側で#price-chart{width:100%;height:260px}によって拡大
  // 表示された分だけ線がぼやけて見えてしまう(canvas要素はwidth/height属性=
  // 描画解像度と、CSSサイズ=表示サイズが別物のため。conan/site/common.jsの
  // drawPriceChartと同じ対処)。
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || canvas.width;
  const h = canvas.clientHeight || canvas.height;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (points.length < 2) return;

  const padding = { top: 12, right: 12, bottom: 22, left: 50 };
  const plotW = w - padding.left - padding.right;
  const plotH = h - padding.top - padding.bottom;
  const prices = points.map((p) => p.price);
  const minP = Math.min(...prices);
  const maxP = Math.max(...prices);
  const range = maxP - minP || 1;

  const x = (i) => padding.left + (i / (points.length - 1)) * plotW;
  const y = (v) => padding.top + plotH - ((v - minP) / range) * plotH;

  ctx.strokeStyle = "#3a4a5c";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padding.left, padding.top);
  ctx.lineTo(padding.left, padding.top + plotH);
  ctx.lineTo(padding.left + plotW, padding.top + plotH);
  ctx.stroke();

  ctx.fillStyle = "#a9b4bf";
  ctx.font = "10px sans-serif";
  ctx.textAlign = "right";
  ctx.fillText(`¥${maxP.toLocaleString()}`, padding.left - 4, padding.top + 8);
  ctx.fillText(`¥${minP.toLocaleString()}`, padding.left - 4, padding.top + plotH);

  ctx.strokeStyle = "#ffd166";
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p, i) => {
    const px = x(i);
    const py = y(p.price);
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.stroke();

  ctx.fillStyle = "#ffd166";
  const hitPoints = [];
  points.forEach((p, i) => {
    const px = x(i);
    const py = y(p.price);
    ctx.beginPath();
    ctx.arc(px, py, 2.5, 0, Math.PI * 2);
    ctx.fill();
    hitPoints.push({ x: px, y: py, price: p.price, date: p.day });
  });

  ctx.fillStyle = "#a9b4bf";
  ctx.textAlign = "center";
  ctx.fillText(points[0].day.slice(5), x(0), h - 6);
  ctx.fillText(points[points.length - 1].day.slice(5), x(points.length - 1), h - 6);

  setupChartHover(canvas, hitPoints);
}

function bindPeriodTabs(canvas, series) {
  const tabs = document.querySelectorAll("#period-tabs .period-tab");
  const emptyEl = document.getElementById("chart-empty");

  function render(days) {
    const filtered = days > 0
      ? series.filter((p) => {
          const diffDays = (Date.now() - new Date(p.day).getTime()) / 86400000;
          return diffDays <= days;
        })
      : series;
    canvas.hidden = filtered.length < 2;
    emptyEl.hidden = filtered.length >= 2;
    if (filtered.length >= 2) drawSimpleChart(canvas, filtered);
  }

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      render(Number(tab.dataset.days));
    });
  });

  render(30);
}

function monsterStatLine(card) {
  const bits = [];
  if (card.level) bits.push(`★${card.level}`);
  if (card.rank) bits.push(`ランク${card.rank}`);
  if (card.link_rating) bits.push(`LINK-${card.link_rating}`);
  if (card.pendulum_scale !== null && card.pendulum_scale !== undefined && card.pendulum_scale !== "") {
    bits.push(`Pスケール${card.pendulum_scale}`);
  }
  return bits.join(" ");
}

function modalEffectTextHtml(card) {
  // 効果テキストはバックグラウンドで遅延取得している(loadCardTextInBackground参照)。
  // card.effect_text === undefined はまだ届いていない状態、null/""は本当に無い
  // (罠・魔法の一部やテキスト取得失敗など)を表す。
  if (card.effect_text === undefined && card.pendulum_text === undefined) {
    return `<div class="info-block" id="modal-effect-text-block"><h3>効果テキスト</h3><p class="price-empty">読み込み中…</p></div>`;
  }
  return `<div id="modal-effect-text-block">
    ${card.effect_text ? `<div class="info-block"><h3>効果テキスト</h3><p>${escapeHtml(card.effect_text)}</p></div>` : ""}
    ${card.pendulum_text ? `<div class="info-block"><h3>ペンデュラム効果</h3><p>${escapeHtml(card.pendulum_text)}</p></div>` : ""}
  </div>`;
}

function openModal(card, pricesLatest) {
  const overlay = document.getElementById("modal-overlay");
  const img = document.getElementById("modal-image");
  const info = document.getElementById("modal-info");
  const favBtn = document.getElementById("modal-favorite");

  overlay.dataset.openCardId = card.id;
  img.src = card.image_url || "";
  img.alt = card.name;

  const statLine = monsterStatLine(card);

  info.innerHTML = `
    <h2 class="modal-card-name">${escapeHtml(card.name)}</h2>
    <div class="modal-card-id">${escapeHtml(card.card_num || card.id)} ・ ${escapeHtml(card.rarity || "")} ・ ${escapeHtml(card.card_type || "")}</div>
    <a class="modal-detail-link" href="card/${encodeURIComponent(card.id)}.html">🔗 このカードの詳細ページを見る</a>
    ${statLine ? `<div class="modal-card-id">${escapeHtml(statLine)}</div>` : ""}
    ${statRow("種族", card.species)}
    ${statRow("属性", card.attribute)}
    ${statRow("ATK", card.atk)}
    ${statRow("DEF", card.def)}
    ${modalEffectTextHtml(card)}
    ${statRow("収録パック", card.pack)}
    ${priceSection(card, pricesLatest)}
  `;

  if (card.effect_text === undefined) {
    ensureCardText(card).then(() => {
      // 取得完了までの間にモーダルを閉じる/別カードを開いた場合は上書きしない。
      if (overlay.dataset.openCardId !== card.id) return;
      const block = document.getElementById("modal-effect-text-block");
      if (block) block.outerHTML = modalEffectTextHtml(card);
    });
  }

  if (favBtn) {
    favBtn.textContent = isFavorite(card.id) ? "★" : "☆";
    favBtn.classList.toggle("active", isFavorite(card.id));
    favBtn.onclick = () => {
      const nowFav = toggleFavorite(card.id);
      favBtn.textContent = nowFav ? "★" : "☆";
      favBtn.classList.toggle("active", nowFav);
      if (typeof window.onModalFavoriteToggle === "function") window.onModalFavoriteToggle(card);
    };
  }

  overlay.classList.remove("hidden");

  const canvas = document.getElementById("price-chart");
  if (canvas && pricesLatest[card.id]) {
    fetchPriceHistory(card.id).then((history) => {
      const series = pooledSeriesFromHistory(history);
      bindPeriodTabs(canvas, series);
    });
  }
}

function closeModal() {
  document.getElementById("modal-overlay").classList.add("hidden");
}

function bindModalEvents() {
  document.getElementById("modal-close").addEventListener("click", closeModal);
  document.getElementById("modal-overlay").addEventListener("click", (e) => {
    if (e.target.id === "modal-overlay") closeModal();
  });
}

// ---- ナビ用の三本線メニュー ----
function bindNavMenuToggle() {
  const btn = document.querySelector(".nav-menu-toggle");
  const nav = document.querySelector(".nav-links");
  if (!btn || !nav) return;

  const backdrop = document.createElement("div");
  backdrop.className = "nav-backdrop";
  nav.parentNode.insertBefore(backdrop, nav);

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "nav-drawer-close";
  closeBtn.setAttribute("aria-label", "閉じる");
  closeBtn.textContent = "✕";
  nav.insertBefore(closeBtn, nav.firstChild);

  function setOpen(isOpen) {
    nav.classList.toggle("open", isOpen);
    backdrop.classList.toggle("open", isOpen);
    btn.setAttribute("aria-expanded", String(isOpen));
    document.body.style.overflow = isOpen ? "hidden" : "";
  }

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    setOpen(!nav.classList.contains("open"));
  });

  closeBtn.addEventListener("click", () => setOpen(false));
  backdrop.addEventListener("click", () => setOpen(false));

  document.addEventListener("click", (e) => {
    if (nav.classList.contains("open") && !nav.contains(e.target) && e.target !== btn) {
      setOpen(false);
    }
  });
}

// ---- 絞り込みパネルの開閉(一覧・ランキングで共用) ----
function bindFiltersToggle() {
  const btn = document.getElementById("toggle-filters");
  const panel = document.getElementById("filters-panel");
  if (!btn || !panel) return;
  btn.addEventListener("click", () => panel.classList.toggle("open"));
}
