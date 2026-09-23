/* 特定できなかったカード(管理用) — 候補ピッカー + manual_resolutions.jsonエクスポート。
   data/unresolved-shop-items.json(build_unresolved_report.py生成)を読み込み、ショップの
   出品がDBのカード1枚に自動特定できなかったもの("候補は絞れたが1枚に決められない" /
   "DBに見当たらない")を一覧表示する。候補があるものは画像から選んでもらい、選択結果を
   manual_resolutions.json形式でエクスポートできる(price_matching.load_manual_resolutionsが
   読む形式と同一)。 */
(function () {
  "use strict";

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text ?? "";
    return div.innerHTML;
  }

  function formatPriceRange(prices) {
    if (!prices || prices.length === 0) return "価格不明";
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    return min === max ? `¥${min.toLocaleString()}` : `¥${min.toLocaleString()}〜¥${max.toLocaleString()}`;
  }

  function groupBySite(items) {
    const groups = new Map();
    for (const item of items) {
      if (!groups.has(item.site)) groups.set(item.site, []);
      groups.get(item.site).push(item);
    }
    return groups;
  }

  const MANUAL_RESOLUTION_KEY = "yugioh_manual_resolutions_v1";

  function loadManualResolutions() {
    try {
      return JSON.parse(localStorage.getItem(MANUAL_RESOLUTION_KEY) || "{}");
    } catch {
      return {};
    }
  }

  function saveManualResolutions(map) {
    localStorage.setItem(MANUAL_RESOLUTION_KEY, JSON.stringify(map));
  }

  // 出品ごとに一意なキー。product_urlがあればそれで(Python側のload_manual_resolutions
  // はproduct_urlでマッチする)。無い場合はこの端末内だけで一意なフォールバックキー。
  function resolutionKeyFor(item, listing, index) {
    if (listing && listing.product_url) return listing.product_url;
    return `${item.site}|${item.raw_key}|${item.rarity || ""}|${index}`;
  }

  function updateManualResolutionCount() {
    const el = document.getElementById("manual-resolution-count");
    if (!el) return;
    const count = Object.keys(loadManualResolutions()).length;
    el.textContent = `${count}件選択済み`;
  }

  function renderCandidatePicker(item, listing, index) {
    const key = resolutionKeyFor(item, listing, index);
    const picker = document.createElement("div");
    picker.className = "unresolved-picker";

    const grid = document.createElement("div");
    grid.className = "unresolved-picker-grid";

    const resolutions = loadManualResolutions();
    const chosenCardId = resolutions[key] ? resolutions[key].card_id : null;

    for (const c of item.candidates) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "unresolved-picker-item" + (chosenCardId === c.id ? " selected" : "");
      btn.title = `${c.id} ${c.name}${c.pack ? ` / ${c.pack}` : ""}`;
      btn.innerHTML = `<img src="${c.image_url || ""}" alt="" loading="lazy"><span>${escapeHtml(c.id)}</span>${c.pack ? `<span class="unresolved-picker-pack">${escapeHtml(c.pack)}</span>` : ""}`;
      btn.addEventListener("click", () => {
        const current = loadManualResolutions();
        current[key] = {
          product_url: listing.product_url || null,
          site: item.site, raw_key: item.raw_key, rarity: item.rarity,
          card_id: c.id, card_num: c.card_num, name: c.name,
        };
        saveManualResolutions(current);
        grid.querySelectorAll(".unresolved-picker-item").forEach((el) => el.classList.remove("selected"));
        btn.classList.add("selected");
        updateManualResolutionCount();
      });
      grid.appendChild(btn);
    }
    picker.appendChild(grid);
    return picker;
  }

  function renderThumb(url, imageUrl) {
    const thumbLink = document.createElement("a");
    thumbLink.className = "unresolved-thumb";
    if (url) {
      thumbLink.href = url;
      thumbLink.target = "_blank";
      thumbLink.rel = "noopener";
    }
    if (imageUrl) {
      const img = document.createElement("img");
      img.src = imageUrl;
      img.alt = "";
      img.loading = "lazy";
      thumbLink.appendChild(img);
    } else {
      thumbLink.classList.add("unresolved-thumb-empty");
      thumbLink.textContent = "🔍";
    }
    return thumbLink;
  }

  function renderMissingRow(item) {
    const url = item.product_url || null;
    const row = document.createElement("div");
    row.className = "unresolved-row";
    row.appendChild(renderThumb(url, item.image_url));

    const info = document.createElement("div");
    info.className = "unresolved-row-info";
    const listingNote = item.listing_count > 1 ? `(${item.listing_count}件の出品)` : "";
    const titleText = item.product_name || item.raw_key;
    const titleHtml = url
      ? `<a href="${url}" target="_blank" rel="noopener">${escapeHtml(titleText)}</a>`
      : escapeHtml(titleText);
    info.innerHTML = `<div class="unresolved-row-title">
        ${titleHtml}
        <code>${escapeHtml(item.raw_key)}</code>${item.rarity ? ` <span class="unresolved-rarity">${escapeHtml(item.rarity)}</span>` : ""}
        <span class="unresolved-price">${escapeHtml(formatPriceRange(item.prices))}${listingNote}</span>
      </div>`;
    row.appendChild(info);
    return row;
  }

  function renderAmbiguousListingRow(item, listing, index) {
    const url = listing.product_url || null;
    const row = document.createElement("div");
    row.className = "unresolved-row unresolved-listing-row";
    row.appendChild(renderThumb(url, listing.image_url));

    const info = document.createElement("div");
    info.className = "unresolved-row-info";
    const titleText = listing.product_name || item.raw_key;
    const titleHtml = url
      ? `<a href="${url}" target="_blank" rel="noopener">${escapeHtml(titleText)}</a>`
      : escapeHtml(titleText);
    info.innerHTML = `<div class="unresolved-row-title">
        ${titleHtml}
        <span class="unresolved-price">${escapeHtml(formatPriceRange(listing.price != null ? [listing.price] : []))}</span>
      </div>`;
    info.appendChild(renderCandidatePicker(item, listing, index));
    row.appendChild(info);
    return row;
  }

  function renderAmbiguousGroup(item) {
    const wrap = document.createElement("div");
    wrap.className = "unresolved-group";

    const heading = document.createElement("div");
    heading.className = "unresolved-candidates";
    heading.textContent = `${item.raw_key}${item.rarity ? ` ${item.rarity}` : ""} 区別できていない候補: ${item.candidates.map((c) => `${c.id}(${c.name})${c.pack ? `[${c.pack}]` : ""}`).join(" / ")}`;
    wrap.appendChild(heading);

    item.listings.forEach((listing, index) => {
      wrap.appendChild(renderAmbiguousListingRow(item, listing, index));
    });

    return wrap;
  }

  function renderUnresolvedGroup(containerId, countId, items, renderRow, countOf) {
    const container = document.getElementById(containerId);
    container.innerHTML = "";
    const totalCount = countOf ? items.reduce((sum, item) => sum + countOf(item), 0) : items.length;
    document.getElementById(countId).textContent = `${totalCount}件`;

    for (const [site, siteItems] of groupBySite(items)) {
      const siteCount = countOf ? siteItems.reduce((sum, item) => sum + countOf(item), 0) : siteItems.length;
      const siteHeading = document.createElement("h4");
      siteHeading.className = "unresolved-site-heading";
      siteHeading.textContent = `${site}(${siteCount}件)`;
      container.appendChild(siteHeading);

      for (const item of siteItems) {
        container.appendChild(renderRow(item));
      }
    }
  }

  function renderUnresolved(data) {
    window.__unresolvedData = data;
    renderUnresolvedGroup(
      "ambiguous-list", "ambiguous-count", data.ambiguous || [],
      renderAmbiguousGroup, (item) => item.listings.length,
    );
    renderUnresolvedGroup("missing-list", "missing-count", data.missing || [], renderMissingRow);
    updateManualResolutionCount();
  }

  function exportManualResolutions() {
    const resolutions = loadManualResolutions();
    const list = Object.values(resolutions);
    const blob = new Blob([JSON.stringify(list, null, 1)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "manual_resolutions.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function bindManualResolutionControls() {
    updateManualResolutionCount();
    document.getElementById("export-manual-resolutions").addEventListener("click", exportManualResolutions);
    document.getElementById("clear-manual-resolutions").addEventListener("click", () => {
      if (!confirm("この端末に保存した選択を全て消します。よろしいですか?")) return;
      localStorage.removeItem(MANUAL_RESOLUTION_KEY);
      renderUnresolved(window.__unresolvedData || { ambiguous: [], missing: [] });
    });
  }

  function init() {
    bindManualResolutionControls();
    fetch("data/unresolved-shop-items.json")
      .then((r) => (r.ok ? r.json() : { ambiguous: [], missing: [] }))
      .then((data) => renderUnresolved(data))
      .catch(() => renderUnresolved({ ambiguous: [], missing: [] }));

    fetch("data/meta.json")
      .then((r) => r.json())
      .then((meta) => {
        document.getElementById("last-updated").textContent =
          "最終更新: " + new Date(meta.generated_at).toLocaleString("ja-JP");
      })
      .catch(() => {});
  }

  document.addEventListener("DOMContentLoaded", init);
})();
