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

  // renderedCount/matchedSoFar は「カード読み込み中に、直前の描画から新しく
  // 増えた分だけをグリッドに追記する」ための進捗管理(appendNewCards参照)。
  // フィルタ変更等でグリッドを作り直す(renderGrid)際は必ず0にリセットする。
  let renderedCount = 0;
  let matchedSoFar = 0;

  function renderGrid(fullyLoaded = true) {
    // フィルタ変更・検索・価格反映など「今表示している内容を作り直す」場合は
    // 全件フルリビルドする。読み込み中の進捗表示にはappendNewCardsを使う
    // (allCardsがチャンクごとに増えていく最中に毎回これを呼ぶと、増えた件数分
    // 丸ごと作り直すことになり件数が増えるほど1回のコストも増えてO(件数²)の
    // 無駄なDOM再構築になってしまう不具合があったため)。
    renderedCount = 0;
    matchedSoFar = 0;
    grid.replaceChildren();
    appendNewCards(fullyLoaded);
  }

  // allCardsのうち、まだグリッドに反映していない末尾分(直近のチャンクで増えた分)
  // だけをフィルタ条件と照合し、マッチした分だけ追記する。既存のタイルには触れない
  // ため、読み込み中に何度呼んでもコストは「今回増えた件数」だけで済む。
  function appendNewCards(fullyLoaded = true) {
    const newCards = allCards.slice(renderedCount);
    const frag = document.createDocumentFragment();
    for (const card of newCards) {
      if (cardMatches(card)) {
        frag.appendChild(createCardTile(card, pricesLatest));
        matchedSoFar++;
      }
    }
    grid.appendChild(frag);
    renderedCount = allCards.length;
    resultCount.textContent = fullyLoaded
      ? `${matchedSoFar}件 / 全${allCards.length}件`
      : `${matchedSoFar}件 / 全${allCards.length}件以上(読み込み中…)`;
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
    // カード一覧(data/cards/chunk-*.json、計13ファイル・約19MB)も価格一覧
    // (data/prices_latest.json、約5.5MB)も、全部揃うのを待ってから初めて何か表示する
    // 形だと、遅い回線ではトップページがいつまでも開かないように見えてしまう
    // (実際にこれが原因でユーザーから「重すぎて開けない」と報告があった)。
    // そのためmeta.json(約90KB、フィルタ選択肢の一覧)だけ先に読み込んでUIの枠を
    // 用意し、カード本体・価格はどちらも取得を並行して開始しつつ、待たずに
    // 「最初のチャンクが届き次第グリッドを表示→残りはバックグラウンドで追記」する。
    const meta = await loadSiteMeta();
    renderLastUpdated();

    buildAllFilterLists(meta);
    bindRangeInputs();

    keywordInput.addEventListener("input", () => {
      state.keyword = keywordInput.value.trim();
      renderGrid();
    });

    document.getElementById("reset-filters").addEventListener("click", () => resetFilters(meta));
    bindModalEvents();
    bindFiltersToggle();
    bindNavMenuToggle();

    // 価格一覧(約5.5MB)は結果に出るまで待たず、届いた時点で価格バッジだけ後から
    // 反映する。ただし最初のカードチャンクと同時に投げると、遅い回線では帯域を
    // 奪い合って肝心の1枚目の表示までさらに遅くなってしまう(実測で確認済み)ため、
    // 最初のチャンクが表示できてから(=グリッドが一度でも表示された後)に投げる。
    let isLoadingCards = true;
    let pricesRequested = false;
    // チャンクが届くたびにappendNewCards(今回増えた分だけ追記)を呼ぶ。
    // renderGrid(グリッド全体を毎回作り直す)をチャンクの度に呼ぶと、読み込み済み
    // 件数が増えるほど1回あたりのコストも増え、全体ではO(チャンク数²)の無駄な
    // DOM再構築になってしまう不具合が実際にあった(ローカルの回線制限無し環境
    // でも全件表示まで60秒以上かかっていた)ため、追記方式に変更している。
    await loadCardData((cardsSoFar, isFullyLoaded) => {
      allCards = cardsSoFar;
      isLoadingCards = !isFullyLoaded;
      appendNewCards(isFullyLoaded);
      if (!pricesRequested) {
        pricesRequested = true;
        loadPricesLatest().then((prices) => {
          pricesLatest = prices;
          renderGrid(!isLoadingCards);
        });
      }
      if (isFullyLoaded) {
        // パック絞り込みの並び順(packCodeMap)は全件揃って初めて正確になるため、
        // 選択状態(state.packs)を保ったままフィルタ一覧だけ作り直す。
        buildAllFilterLists(meta);
      }
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
