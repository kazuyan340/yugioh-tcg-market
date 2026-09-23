/* 相場ランキング(既定は全ショップ平均価格が高い順)。フィルタ・並び替えともにapp.jsと同じ考え方。 */
(function () {
  "use strict";

  let allCards = [];
  let pricesLatest = {};

  const state = {
    keyword: "",
    cardTypes: { include: new Set(), exclude: new Set() },
    species: { include: new Set(), exclude: new Set() },
    attributes: { include: new Set(), exclude: new Set() },
    rarities: { include: new Set(), exclude: new Set() },
    packs: { include: new Set(), exclude: new Set() },
  };

  const grid = document.getElementById("card-grid");
  const resultCount = document.getElementById("result-count");
  const keywordInput = document.getElementById("keyword");
  const sortSelect = document.getElementById("sort-select");

  function cardMatches(card) {
    if (state.keyword) {
      const kw = state.keyword;
      const haystack = [card.name, card.ruby, card.species, card.effect_text].filter(Boolean).join(" ");
      if (!haystack.includes(kw)) return false;
    }
    if (!matchesTriState(card.card_type, state.cardTypes.include, state.cardTypes.exclude)) return false;
    if (!matchesTriState(card.species, state.species.include, state.species.exclude)) return false;
    if (!matchesTriState(card.attribute, state.attributes.include, state.attributes.exclude)) return false;
    if (!matchesTriState(card.rarity, state.rarities.include, state.rarities.exclude)) return false;
    if (!matchesTriState(card.pack, state.packs.include, state.packs.exclude)) return false;
    return true;
  }

  function sortEntries(entries) {
    const [field, dir] = sortSelect.value.split("_");
    const mul = dir === "asc" ? 1 : -1;
    entries.sort((a, b) => {
      let va, vb;
      if (field === "price") {
        va = a.price;
        vb = b.price;
      } else if (field === "id") {
        return a.card.id.localeCompare(b.card.id) * mul;
      } else {
        va = Number(a.card[field]) || -Infinity;
        vb = Number(b.card[field]) || -Infinity;
      }
      return (va - vb) * mul;
    });
  }

  function renderGrid() {
    const entries = allCards
      .filter(cardMatches)
      .filter((c) => pricesLatest[c.id])
      .map((card) => ({ card, price: pricesLatest[card.id].pooled_avg }));

    sortEntries(entries);
    resultCount.textContent = `${entries.length}件(価格データがあるカードのみ)`;

    const frag = document.createDocumentFragment();
    entries.forEach(({ card, price }, i) => {
      const badge = `<div class="trend-badge">#${i + 1}　¥${price.toLocaleString()}</div>`;
      frag.appendChild(createCardTile(card, pricesLatest, badge));
    });
    grid.replaceChildren(frag);
  }

  function packCodeMap(cards) {
    const map = new Map();
    for (const c of cards) {
      if (c.pack && !map.has(c.pack)) map.set(c.pack, c.pack_code);
    }
    return map;
  }

  function buildAllFilterLists(meta) {
    buildTriStateList("filter-cardtype-list", meta.card_types, state.cardTypes.include, state.cardTypes.exclude, renderGrid);
    buildTriStateList("filter-species-list", meta.species, state.species.include, state.species.exclude, renderGrid);
    buildTriStateList("filter-attribute-list", meta.attributes, state.attributes.include, state.attributes.exclude, renderGrid);
    buildTriStateList("filter-rarity-list", meta.rarities, state.rarities.include, state.rarities.exclude, renderGrid);
    const packValues = [...meta.packs];
    const codeMap = packCodeMap(allCards);
    sortPackValues(packValues, codeMap);
    buildTriStateList("filter-pack-list", packValues, state.packs.include, state.packs.exclude, renderGrid, (v) => packGroupFor(v, codeMap.get(v)));
  }

  function resetFilters(meta) {
    state.keyword = "";
    keywordInput.value = "";
    sortSelect.value = "price_desc";
    for (const key of ["cardTypes", "species", "attributes", "rarities", "packs"]) {
      state[key].include.clear();
      state[key].exclude.clear();
    }
    buildAllFilterLists(meta);
    renderGrid();
  }

  async function init() {
    const [cards, meta, prices] = await Promise.all([
      loadCardData(),
      loadSiteMeta(),
      loadPricesLatest(),
    ]);
    allCards = cards;
    pricesLatest = prices;
    renderLastUpdated();

    buildAllFilterLists(meta);
    renderGrid();

    keywordInput.addEventListener("input", () => {
      state.keyword = keywordInput.value.trim();
      renderGrid();
    });
    sortSelect.addEventListener("change", renderGrid);

    document.getElementById("reset-filters").addEventListener("click", () => resetFilters(meta));
    bindModalEvents();
    bindFiltersToggle();
    bindNavMenuToggle();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
