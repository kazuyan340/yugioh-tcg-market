/* お気に入り = 価格監視ダッシュボード。カードごとに値上がり/値下がり通知ライン
   (円)を設定でき、閾値を超えたらカードを強調表示する。グラフ・ショップ別価格表・
   購入ボタンもカードごとに埋め込む(モーダルを開かなくても一目で分かるように)。 */
(function () {
  "use strict";

  let allCards = [];
  let pricesLatest = {};
  let checkedCards = [];

  const THRESHOLDS_KEY = "yugiohTcgPriceThresholds";

  function loadThresholds() {
    try {
      return JSON.parse(localStorage.getItem(THRESHOLDS_KEY) || "{}");
    } catch {
      return {};
    }
  }

  function saveThreshold(cardId, key, value) {
    const thresholds = loadThresholds();
    const entry = { ...thresholds[cardId] };
    if (value === null) delete entry[key];
    else entry[key] = value;
    if (Object.keys(entry).length === 0) delete thresholds[cardId];
    else thresholds[cardId] = entry;
    localStorage.setItem(THRESHOLDS_KEY, JSON.stringify(thresholds));
  }

  function applyThresholdHighlight(box, price, over, under) {
    box.classList.remove("over-threshold", "under-threshold");
    if (over !== null && price !== null && price > over) box.classList.add("over-threshold");
    if (under !== null && price !== null && price < under) box.classList.add("under-threshold");
  }

  function debounce(fn, ms) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
  }

  function removeCard(cardId) {
    toggleFavorite(cardId);
    checkedCards = checkedCards.filter((c) => c.id !== cardId);
    render();
  }

  async function render() {
    const grid = document.getElementById("price-check-grid");
    const emptyMessage = document.getElementById("empty-message");
    document.getElementById("result-count").textContent = `${checkedCards.length}件`;
    grid.innerHTML = "";

    if (checkedCards.length === 0) {
      emptyMessage.classList.remove("hidden");
      return;
    }
    emptyMessage.classList.add("hidden");

    const thresholds = loadThresholds();
    const historyByCardId = new Map(
      await Promise.all(checkedCards.map(async (card) => [card.id, await fetchPriceHistory(card.id)]))
    );

    for (const card of checkedCards) {
      const box = document.createElement("div");
      box.className = "price-check-card";

      const history = historyByCardId.get(card.id) || [];
      const priceInfo = pricesLatest[card.id];
      const price = priceInfo ? priceInfo.pooled_avg : null;
      const entry = thresholds[card.id] || {};
      const live = { over: entry.over ?? null, under: entry.under ?? null };

      const header = document.createElement("div");
      header.className = "price-check-header";

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "compare-remove";
      removeBtn.textContent = "✕";
      removeBtn.title = "外す";
      removeBtn.addEventListener("click", () => removeCard(card.id));

      const link = document.createElement("a");
      link.className = "price-check-link";
      link.href = `card/${encodeURIComponent(card.id)}.html`;
      link.innerHTML = `
        <img src="${card.image_url || ""}" alt="${escapeHtml(card.name)}">
        <div class="price-check-info">
          <div class="name">${escapeHtml(card.name)}</div>
          <div class="sub">${escapeHtml(card.rarity || "")} / ${escapeHtml(card.card_type || "")} / ${escapeHtml(card.card_num || card.id)}</div>
        </div>
      `;
      link.addEventListener("click", (e) => {
        e.preventDefault();
        openModal(card, pricesLatest);
      });

      header.append(removeBtn, link);

      const stats = document.createElement("div");
      stats.className = "price-stats";
      stats.innerHTML = priceInfo ? priceStatsHtml(priceInfo) : "<p class=\"price-empty\">価格データがありません。</p>";

      function makeThresholdInput(key) {
        const input = document.createElement("input");
        input.type = "number";
        input.min = "0";
        input.step = "100";
        input.placeholder = "例: 1000";
        if (entry[key] != null) input.value = entry[key];
        return input;
      }

      const overInput = makeThresholdInput("over");
      const underInput = makeThresholdInput("under");

      function updateConstraints() {
        overInput.min = live.under !== null ? live.under + 1 : 0;
        if (live.over !== null) underInput.max = Math.max(0, live.over - 1);
        else underInput.removeAttribute("max");
      }
      updateConstraints();

      function bindThresholdInput(key, input, otherKey, clamp) {
        input.addEventListener("input", debounce(() => {
          let v = input.value === "" ? null : Number(input.value);
          const other = live[otherKey];
          if (v !== null && other !== null && !clamp.valid(v, other)) {
            v = clamp.fix(other);
            input.value = v;
          }
          saveThreshold(card.id, key, v);
          live[key] = v;
          updateConstraints();
          applyThresholdHighlight(box, price, live.over, live.under);
        }, 300));
      }

      bindThresholdInput("over", overInput, "under", {
        valid: (v, under) => v > under,
        fix: (under) => under + 1,
      });
      bindThresholdInput("under", underInput, "over", {
        valid: (v, over) => v < over,
        fix: (over) => Math.max(0, over - 1),
      });

      const overRow = document.createElement("label");
      overRow.className = "card-threshold-row";
      overRow.append("値上がり通知ライン(円): ", overInput);

      const underRow = document.createElement("label");
      underRow.className = "card-threshold-row";
      underRow.append("値下がり通知ライン(円): ", underInput);

      applyThresholdHighlight(box, price, live.over, live.under);

      const detailRow = document.createElement("div");
      detailRow.className = "price-detail-row";

      const tableCol = document.createElement("div");
      tableCol.className = "price-table-col";
      const tableWrap = document.createElement("div");
      tableWrap.innerHTML = priceInfo
        ? `${shopTableHtml(card, priceInfo)}${purchaseButtonsHtml(card.name, card.card_num, card.rarity)}`
        : purchaseButtonsHtml(card.name, card.card_num, card.rarity);
      tableCol.append(overRow, underRow, tableWrap);

      const chartCol = document.createElement("div");
      chartCol.className = "price-chart-col";
      const canvas = document.createElement("canvas");
      canvas.width = 320;
      canvas.height = 160;
      canvas.className = "price-check-canvas";
      chartCol.appendChild(canvas);

      detailRow.append(tableCol, chartCol);

      box.append(header, stats, detailRow);
      grid.appendChild(box);

      const series = pooledSeriesFromHistory(history);
      if (series.length >= 2) drawSimpleChart(canvas, series);
    }
  }

  async function init() {
    const [cards, meta, prices] = await Promise.all([
      loadCardData(),
      loadSiteMeta(),
      loadPricesLatest(),
    ]);
    allCards = cards;
    pricesLatest = prices;
    const favIds = loadFavorites();
    checkedCards = allCards.filter((c) => favIds.has(c.id));

    renderLastUpdated();
    await render();

    window.onModalFavoriteToggle = () => {
      const currentIds = loadFavorites();
      checkedCards = allCards.filter((c) => currentIds.has(c.id));
      render();
    };

    bindModalEvents();
    bindNavMenuToggle();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
