/* 値上がり/値下がり一覧。movers-up.html/movers-down.htmlの両方から
   window.MOVERS_DIRECTION("up"|"down")で共用する。ショップタブで表示対象を切り替えられる。 */
(function () {
  "use strict";

  let items = null;
  let cardsById = null;
  let pricesLatest = null;

  function render(site) {
    const filtered = items.filter((i) => i.site === site);
    const grid = document.getElementById("card-grid");
    const emptyMsg = document.getElementById("empty-message");
    document.getElementById("result-count").textContent = `${filtered.length}件`;

    if (filtered.length === 0) {
      grid.replaceChildren();
      emptyMsg.classList.remove("hidden");
      return;
    }
    emptyMsg.classList.add("hidden");

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

  async function init() {
    const direction = window.MOVERS_DIRECTION;
    const [cards, meta, prices, moversRes] = await Promise.all([
      loadCardData(),
      loadSiteMeta(),
      loadPricesLatest(),
      fetchFresh("data/movers.json"),
    ]);
    const movers = await moversRes.json();
    items = movers[direction] || [];
    cardsById = new Map(cards.map((c) => [c.id, c]));
    pricesLatest = prices;
    renderLastUpdated();

    createSiteTabController("site-tabs", (site) => render(site));
    render("全体");

    bindModalEvents();
    bindNavMenuToggle();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
