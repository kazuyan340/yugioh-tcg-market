/* 価格の動き(直近上昇/上昇傾向/直近下降/下降傾向)。ショップタブで表示対象を切り替えられる。 */
(function () {
  "use strict";

  let trends = null;
  let cardsById = null;
  let pricesLatest = null;

  function renderSection(gridId, emptyId, items, site) {
    const grid = document.getElementById(gridId);
    const empty = document.getElementById(emptyId);
    const filtered = items.filter((i) => i.site === site);

    if (filtered.length === 0) {
      grid.replaceChildren();
      empty.classList.remove("hidden");
      return;
    }
    empty.classList.add("hidden");

    const frag = document.createDocumentFragment();
    for (const item of filtered) {
      const card = cardsById.get(item.card_id);
      if (!card) continue;
      const sign = item.change_pct > 0 ? "+" : "";
      const badge = `<div class="trend-badge">${sign}${item.change_pct}%　¥${item.previous_price.toLocaleString()}→¥${item.latest_price.toLocaleString()}</div>`;
      frag.appendChild(createCardTile(card, pricesLatest, badge));
    }
    grid.replaceChildren(frag);
  }

  function renderAll(site) {
    renderSection("recent-up-grid", "recent-up-empty", trends.recent_up, site);
    renderSection("trend-up-grid", "trend-up-empty", trends.trend_up, site);
    renderSection("recent-down-grid", "recent-down-empty", trends.recent_down, site);
    renderSection("trend-down-grid", "trend-down-empty", trends.trend_down, site);
  }

  async function init() {
    const [cards, meta, prices, trendsRes] = await Promise.all([
      loadCardData(),
      loadSiteMeta(),
      loadPricesLatest(),
      fetchFresh("data/trends.json"),
    ]);
    trends = await trendsRes.json();
    cardsById = new Map(cards.map((c) => [c.id, c]));
    pricesLatest = prices;
    renderLastUpdated();

    createSiteTabController("site-tabs", (site) => renderAll(site));
    renderAll("全体");

    bindModalEvents();
    bindNavMenuToggle();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
