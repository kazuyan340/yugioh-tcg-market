/* デッキ作成ツール(遊戯王OCGルールに準拠した簡易デッキビルダー)。
   ルール:
   - メインデッキ: 40〜60枚
   - エクストラデッキ: 融合・シンクロ・エクシーズ・リンクモンスターのみ、0〜15枚
     (2020年の新マスタールール以降、ペンデュラムモンスターは特殊召喚時以外は
     メインデッキに入れる運用のため、種族に「融合/シンクロ/エクシーズ/リンク」を
     含まないペンデュラムモンスターはメインデッキ側として扱う)
   - サイドデッキ: 0〜15枚
   - 禁止/制限/準制限カード: data/banlist.jsonにエントリがある場合のみ、名前ベースで
     0/1/2枚に制限する(メイン+エクストラ+サイドの合計で数える、公式ルール通り)。
     banlist.jsonが空(スクレイパーがまだ対応していない)場合は、3枚までの通常上限のみ
     チェックする簡易モードにフォールバックする。
   conan-tcg-marketのデッキビルダー(複数デッキ管理・URL共有・レアリティ一括変更)の
   構成を踏襲しつつ、保存は単一デッキ(1つだけ)に簡略化している。 */

const MAIN_MIN = 40;
const MAIN_MAX = 60;
const EXTRA_MAX = 15;
const SIDE_MAX = 15;
const DEFAULT_MAX_COPIES = 3;
const DECK_KEY = "yugiohTcgDeckBuilder";

const EXTRA_DECK_TYPE_PATTERN = /融合|シンクロ|エクシーズ|リンク/;

function deckZoneFor(card) {
  if (card.card_type !== "モンスター") return "main";
  if (EXTRA_DECK_TYPE_PATTERN.test(card.monster_type || "")) return "extra";
  return "main";
}

const FILTER_FIELDS = {
  species: { listId: "filter-species-list", groupId: "filter-species-group" },
  attribute: { listId: "filter-attribute-list", groupId: "filter-attribute-group" },
  rarity: { listId: "filter-rarity-list", groupId: "filter-rarity-group" },
  pack: { listId: "filter-pack-list", groupId: "filter-pack-group" },
};

let allCards = [];
let cardById = new Map();
let cardsByName = new Map(); // name -> [card, ...] (同名カード=レアリティ・再録違いをまとめる)
let banlistByName = new Map(); // name -> {status, limit}
let hasBanlistData = false;
let filteredCards = [];
// 検索パネルの絞り込み対象ゾーン。null=メイン/エクストラ/サイド全部(初期状態)、
// "main"/"extra"/"side" のいずれかならそのゾーンに置けるカードだけに絞る。
let focusedZone = null;

// deck = { main: { name: count }, extra: { name: count }, side: { name: count } }
// カードの実体はnameで管理し、実際にデッキへ追加する際は候補の中から選ばれた
// 具体的なcard.id(レアリティ)を別途 variantByName に保持する(価格計算・表示用)。
let deck = { main: {}, extra: {}, side: {} };
let variantByName = {}; // name -> card.id (どのレアリティ/印刷を採用しているか)
let pricesLatest = {};

const grid = document.getElementById("card-grid");
const resultCount = document.getElementById("result-count");
const keywordInput = document.getElementById("keyword");
const sortSelect = document.getElementById("sort-select");

async function init() {
  const [cards, banlist, prices] = await Promise.all([
    loadCardData(),
    loadBanlist(),
    loadPricesLatest(),
  ]);
  allCards = cards;
  pricesLatest = prices;
  cardById = new Map(allCards.map((c) => [c.id, c]));
  cardsByName = new Map();
  for (const c of allCards) {
    if (!cardsByName.has(c.name)) cardsByName.set(c.name, []);
    cardsByName.get(c.name).push(c);
  }
  hasBanlistData = (banlist.entries || []).length > 0;
  banlistByName = new Map((banlist.entries || []).map((e) => [e.name, e]));
  document.getElementById("banlist-mode-note").textContent = hasBanlistData
    ? "禁止・制限カードリストを反映しています(禁止0枚/制限1枚/準制限2枚)。"
    : "禁止・制限カードリストのデータがまだ無いため、同名カード3枚までの簡易モードで動作しています。";

  populateFilterOptions();
  bindEvents();
  bindModalEvents();
  bindNavMenuToggle();
  loadDeckFromStorage();
  applyFilters();
  renderDeckPanel();
  await loadSiteMeta();
  renderLastUpdated();
}

function valuesForField(card, field) {
  const raw = card[field];
  if (raw === null || raw === undefined || raw === "") return [];
  return [String(raw)];
}

function packCodeMap() {
  const map = new Map();
  for (const c of allCards) {
    if (c.pack && !map.has(c.pack)) map.set(c.pack, c.pack_code);
  }
  return map;
}

function populateFilterOptions() {
  const codeMap = packCodeMap();
  for (const [field, { listId }] of Object.entries(FILTER_FIELDS)) {
    const listEl = document.getElementById(listId);
    const values = [...new Set(allCards.flatMap((c) => valuesForField(c, field)))];
    if (field === "pack") {
      sortPackValues(values, codeMap);
    } else {
      values.sort((a, b) => a.localeCompare(b, "ja"));
    }

    let lastPackGroup = null;
    for (const v of values) {
      if (field === "pack") {
        const g = packGroupFor(v, codeMap.get(v));
        if (g !== lastPackGroup) {
          const heading = document.createElement("div");
          heading.className = "checkbox-list-heading";
          heading.textContent = PACK_GROUP_LABELS[g];
          listEl.appendChild(heading);
          lastPackGroup = g;
        }
      }
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = v;
      checkbox.dataset.field = field;
      checkbox.addEventListener("change", () => {
        updateCountBadge(field);
        applyFilters();
      });
      label.appendChild(checkbox);
      label.append(` ${v}`);
      listEl.appendChild(label);
    }
  }
}

function getFilterSelection(listId) {
  const el = document.getElementById(listId);
  return [...el.querySelectorAll("input:checked")].map((cb) => cb.value);
}

function updateCountBadge(field) {
  const { listId, groupId } = FILTER_FIELDS[field];
  const badge = document.querySelector(`#${groupId} .count-badge`);
  const n = getFilterSelection(listId).length;
  badge.textContent = n || "";
  badge.classList.toggle("hidden", n === 0);
}

function bindEvents() {
  keywordInput.addEventListener("input", debounce(applyFilters, 200));
  sortSelect.addEventListener("change", applyFilters);

  document.getElementById("clear-deck").addEventListener("click", () => {
    if (!confirm("デッキの内容をすべてクリアします。よろしいですか?")) return;
    deck = { main: {}, extra: {}, side: {} };
    variantByName = {};
    saveDeck();
    renderDeckPanel();
  });

  document.getElementById("share-deck").addEventListener("click", shareDeckUrl);

  document.querySelectorAll(".deck-zone-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const zone = btn.dataset.zone === "all" ? null : btn.dataset.zone;
      focusedZone = zone;
      document.querySelectorAll(".deck-zone-tab").forEach((b) => b.classList.toggle("active", b === btn));
      applyFilters();
    });
  });

  document.addEventListener("click", (e) => {
    for (const details of document.querySelectorAll(".filter-group[open]")) {
      if (!details.contains(e.target)) details.removeAttribute("open");
    }
  });
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function saveDeck() {
  try {
    localStorage.setItem(DECK_KEY, JSON.stringify({ deck, variantByName }));
  } catch {
    // localStorageが使えない環境では保存をあきらめる(致命的ではない)
  }
}

function loadDeckFromStorage() {
  try {
    const raw = localStorage.getItem(DECK_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && parsed.deck) {
        deck = {
          main: parsed.deck.main || {},
          extra: parsed.deck.extra || {},
          side: parsed.deck.side || {},
        };
        variantByName = parsed.variantByName || {};
      }
    }
  } catch {
    // 壊れたデータは無視して初期状態のまま
  }

  const fromUrl = new URLSearchParams(location.search).get("deck");
  if (fromUrl) {
    try {
      const decoded = JSON.parse(decodeURIComponent(escape(atob(fromUrl))));
      if (decoded && decoded.main && confirm("共有されたデッキを読み込みますか?(今のデッキは上書きされます)")) {
        deck = { main: decoded.main || {}, extra: decoded.extra || {}, side: decoded.side || {} };
        variantByName = decoded.variantByName || {};
        saveDeck();
      }
      history.replaceState(null, "", location.pathname);
    } catch {
      // URLのデッキデータが壊れている場合は無視する
    }
  }
}

function shareDeckUrl() {
  const encoded = btoa(unescape(encodeURIComponent(JSON.stringify({ ...deck, variantByName }))));
  const url = `${location.origin}${location.pathname}?deck=${encoded}`;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(
      () => alert("デッキのURLをコピーしました。"),
      () => prompt("このURLをコピーしてください:", url)
    );
  } else {
    prompt("このURLをコピーしてください:", url);
  }
}

function cardPrice(card) {
  const info = pricesLatest[card.id];
  return info ? info.pooled_avg : null;
}

function zoneCount(zone) {
  return Object.values(deck[zone]).reduce((sum, n) => sum + n, 0);
}

// 同名カード(レアリティ違い含む)がメイン+エクストラ+サイド合計で何枚入っているか。
function totalCopiesOfName(name) {
  return ["main", "extra", "side"].reduce((sum, z) => sum + (deck[z][name] || 0), 0);
}

function maxCopiesFor(name) {
  if (hasBanlistData) {
    const entry = banlistByName.get(name);
    if (entry) return entry.limit;
  }
  return DEFAULT_MAX_COPIES;
}

// カードをクリックしたときの挙動。ゾーンはカード自身の種類(モンスター種族)で
// 自動的に決まる(融合/シンクロ/エクシーズ/リンクはエクストラ、それ以外はメイン)。
// フォーカス中がサイドデッキの場合のみ、メイン/エクストラどちらのカードもサイドへ入る。
function addCard(card) {
  const zone = focusedZone === "side" ? "side" : deckZoneFor(card);

  const zoneMax = zone === "main" ? MAIN_MAX : zone === "extra" ? EXTRA_MAX : SIDE_MAX;
  if (zoneCount(zone) >= zoneMax) {
    const label = zone === "main" ? "メインデッキ" : zone === "extra" ? "エクストラデッキ" : "サイドデッキ";
    alert(`${label}は${zoneMax}枚までです。`);
    return;
  }

  const limit = maxCopiesFor(card.name);
  if (limit <= 0) {
    alert(`「${card.name}」は禁止カードのためデッキに入れられません。`);
    return;
  }
  if (totalCopiesOfName(card.name) >= limit) {
    alert(`「${card.name}」は最大${limit}枚までです。`);
    return;
  }

  deck[zone][card.name] = (deck[zone][card.name] || 0) + 1;
  variantByName[card.name] = card.id;
  saveDeck();
  renderDeckPanel();
}

function removeOneFrom(zone, name) {
  const current = deck[zone][name] || 0;
  if (current <= 1) delete deck[zone][name];
  else deck[zone][name] = current - 1;
  saveDeck();
  renderDeckPanel();
}

function representativeCard(name) {
  const variantId = variantByName[name];
  if (variantId && cardById.has(variantId)) return cardById.get(variantId);
  const candidates = cardsByName.get(name) || [];
  return candidates[0] || null;
}

function computeDeckTotal() {
  let total = 0;
  for (const zone of ["main", "extra", "side"]) {
    for (const [name, count] of Object.entries(deck[zone])) {
      const card = representativeCard(name);
      if (!card) continue;
      const p = cardPrice(card);
      if (p !== null) total += p * count;
    }
  }
  return total;
}

function createSearchTile(card) {
  const tile = document.createElement("div");
  tile.className = "card-tile";
  tile.title = card.name;
  const price = cardPrice(card);
  const priceText = price !== null ? `${price.toLocaleString()}円` : "-";
  const zone = deckZoneFor(card);
  const zoneLabel = zone === "extra" ? "EX" : "";

  tile.innerHTML = `
    <div class="trend-badge">${escapeHtml(priceText)}${zoneLabel ? ` ・ ${zoneLabel}` : ""}</div>
    <div class="deck-tile-img-wrap">
      <img src="${card.image_url || ""}" alt="${escapeHtml(card.name)}" loading="lazy">
      <button type="button" class="deck-info-btn" title="詳細を見る">🔍</button>
    </div>
  `;

  tile.addEventListener("click", () => addCard(card));
  tile.querySelector(".deck-info-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    openModal(card, pricesLatest);
  });

  return tile;
}

function cardAllowedInFocusedZone(card) {
  if (!focusedZone) return true;
  if (focusedZone === "side") return true; // サイドデッキにはメイン/エクストラどちらのカードも入る
  return deckZoneFor(card) === focusedZone;
}

function applyFilters() {
  const keyword = keywordInput.value.trim().toLowerCase();
  const selected = {};
  for (const [field, { listId }] of Object.entries(FILTER_FIELDS)) {
    selected[field] = getFilterSelection(listId);
  }

  filteredCards = allCards.filter((c) => {
    if (!cardAllowedInFocusedZone(c)) return false;
    if (keyword && !String(c.name || "").toLowerCase().includes(keyword)) return false;
    for (const field of Object.keys(FILTER_FIELDS)) {
      const sel = selected[field];
      if (sel.length === 0) continue;
      const cardValues = valuesForField(c, field);
      if (!sel.some((v) => cardValues.includes(v))) return false;
    }
    return true;
  });

  filteredCards = sortCards(filteredCards, sortSelect.value);
  resultCount.textContent = `${filteredCards.length} 件`;
  renderResults();
}

function sortCards(cards, sortMode) {
  const cut = sortMode.lastIndexOf("_");
  const field = sortMode.slice(0, cut);
  const direction = sortMode.slice(cut + 1);
  const sign = direction === "desc" ? -1 : 1;

  return [...cards].sort((a, b) => {
    if (field === "id") return sign * String(a.card_num || a.id).localeCompare(String(b.card_num || b.id), "ja", { numeric: true });
    if (field === "level") {
      const av = a.level ?? a.rank ?? null;
      const bv = b.level ?? b.rank ?? null;
      if (av === null && bv === null) return a.name.localeCompare(b.name, "ja");
      if (av === null) return 1;
      if (bv === null) return -1;
      return sign * (av - bv);
    }
    if (field === "atk" || field === "def") {
      const av = a[field] === null || a[field] === undefined || a[field] === "" ? null : Number(a[field]);
      const bv = b[field] === null || b[field] === undefined || b[field] === "" ? null : Number(b[field]);
      const aMissing = av === null || Number.isNaN(av);
      const bMissing = bv === null || Number.isNaN(bv);
      if (aMissing && bMissing) return a.name.localeCompare(b.name, "ja");
      if (aMissing) return 1;
      if (bMissing) return -1;
      return sign * (av - bv);
    }
    return sign * a.name.localeCompare(b.name, "ja");
  });
}

function renderResults() {
  grid.innerHTML = "";
  filteredCards.forEach((card) => grid.appendChild(createSearchTile(card)));
}

function renderZoneGrid(elId, zone, maxSlots) {
  const zoneGrid = document.getElementById(elId);
  zoneGrid.innerHTML = "";

  const entries = Object.entries(deck[zone])
    .map(([name, count]) => ({ name, card: representativeCard(name), count }))
    .filter((e) => e.card)
    .sort((a, b) => a.card.name.localeCompare(b.card.name, "ja"));

  let slotsFilled = 0;
  for (const { name, card, count } of entries) {
    for (let i = 0; i < count; i++) {
      const slot = document.createElement("div");
      slot.className = "deck-slot-box";
      slot.title = `${card.name}(クリックで1枚減らす)`;
      slot.innerHTML = `<img src="${card.image_url || ""}" alt="${escapeHtml(card.name)}">`;
      slot.addEventListener("click", () => removeOneFrom(zone, name));
      zoneGrid.appendChild(slot);
      slotsFilled++;
    }
  }
  for (let i = slotsFilled; i < maxSlots; i++) {
    const slot = document.createElement("div");
    slot.className = "deck-slot-box empty";
    zoneGrid.appendChild(slot);
  }
}

function renderDeckPanel() {
  renderZoneGrid("deck-main-grid", "main", Math.max(MAIN_MIN, zoneCount("main")));
  renderZoneGrid("deck-extra-grid", "extra", EXTRA_MAX);
  renderZoneGrid("deck-side-grid", "side", SIDE_MAX);

  const mainCount = zoneCount("main");
  const extraCount = zoneCount("extra");
  const sideCount = zoneCount("side");

  const mainCountEl = document.getElementById("deck-main-count");
  mainCountEl.textContent = `メインデッキ ${mainCount}枚(規定 ${MAIN_MIN}〜${MAIN_MAX}枚)`;
  mainCountEl.classList.toggle("deck-count-ok", mainCount >= MAIN_MIN && mainCount <= MAIN_MAX);
  mainCountEl.classList.toggle("deck-count-warn", mainCount > 0 && (mainCount < MAIN_MIN || mainCount > MAIN_MAX));

  document.getElementById("deck-extra-count").textContent = `エクストラデッキ ${extraCount}/${EXTRA_MAX}枚`;
  document.getElementById("deck-side-count").textContent = `サイドデッキ ${sideCount}/${SIDE_MAX}枚`;

  document.getElementById("deck-total").textContent = `合計 ${computeDeckTotal().toLocaleString()}円`;
}

init();
