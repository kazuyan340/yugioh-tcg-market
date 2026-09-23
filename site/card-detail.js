/* カード個別ページ(card/{id}.html)用。ページ本体の主要な情報・相場の数値は
   ビルド時(generate_card_pages.py)に静的HTMLへ直接埋め込み済みで、検索エンジンや
   JavaScript無効の環境でもそのまま読める。このスクリプトは読み込み後、
   このカード1枚分の価格履歴(data/prices/{id}.json)だけを取得し、期間切り替え
   タブ付きの簡易グラフに差し替える(値そのものは静的埋め込み分と一致する)。 */
(function () {
  "use strict";

  async function init() {
    bindNavMenuToggle();
    await loadSiteMeta();
    renderLastUpdated();

    const card = window.CARD_DETAIL;
    const history = await fetchPriceHistory(card.id);
    const series = pooledSeriesFromHistory(history);
    if (series.length >= 2) {
      const canvas = document.getElementById("price-chart");
      document.getElementById("period-tabs").classList.remove("hidden");
      canvas.classList.remove("hidden");
      document.getElementById("chart-empty").classList.add("hidden");
      bindPeriodTabs(canvas, series);
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
