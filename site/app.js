/* 遊戯王OCG カード図鑑 — 一覧・検索(common.jsの上に乗る一覧ページ専用処理) */
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
    atkMin: null, atkMax: null, defMin: null, defMax: null,
  };

  const grid = document.getElementById("card-grid");
  const resultCount = document.getElementById("result-count");
  const keywordInput = document.getElementById("keyword");

  function cardMatches(card) {
    if (state.keyword) {
      const kw = state.keyword;
      const haystack = [card.name, card.ruby, card.species, card.effect_text, card.pendulum_text]
        .filter(Boolean).join(" ");
      if (!haystack.includes(kw)) return false;
    }
    if (!matchesTriState(card.card_type, state.cardTypes.include, state.cardTypes.exclude)) return false;
    if (!matchesTriState(card.species, state.species.include, state.species.exclude)) return false;
    if (!matchesTriState(card.attribute, state.attributes.include, state.attributes.exclude)) return false;
    if (!matchesTriState(card.rarity, state.rarities.include, state.rarities.exclude)) return false;
    if (!matchesTriState(card.pack, state.packs.include, state.packs.exclude)) return false;
    if ((state.atkMin !== null || state.atkMax !== null) && !matchesRange(card.atk, state.atkMin, state.atkMax)) return false;
    if ((state.defMin !== null || state.defMax !== null) && !matchesRange(card.def, state.defMin, state.defMax)) return false;
    return true;
  }

  function renderGrid() {
    const filtered = allCards.filter(cardMatches);
    resultCount.textContent = `${filtered.length}件 / 全${allCards.length}件`;

    const frag = document.createDocumentFragment();
    for (const card of filtered) {
      frag.appendChild(createCardTile(card, pricesLatest));
    }
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

  function bindRangeInputs() {
    const ids = [
      ["atk-min", "atkMin"], ["atk-max", "atkMax"],
      ["def-min", "defMin"], ["def-max", "defMax"],
    ];
    for (const [id, key] of ids) {
      const el = document.getElementById(id);
      if (!el) continue;
      el.addEventListener("input", () => {
        const v = el.value.trim();
        state[key] = v === "" ? null : Number(v);
        renderGrid();
      });
    }
  }

  function resetFilters(meta) {
    state.keyword = "";
    keywordInput.value = "";
    state.atkMin = state.atkMax = state.defMin = state.defMax = null;
    for (const id of ["atk-min", "atk-max", "def-min", "def-max"]) {
      const el = document.getElementById(id);
      if (el) el.value = "";
    }
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
    bindRangeInputs();
    renderGrid();

    keywordInput.addEventListener("input", () => {
      state.keyword = keywordInput.value.trim();
      renderGrid();
    });

    document.getElementById("reset-filters").addEventListener("click", () => resetFilters(meta));
    bindModalEvents();
    bindFiltersToggle();
    bindNavMenuToggle();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
