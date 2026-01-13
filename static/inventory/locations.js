(function () {
  const CFG = (window.KURAJIKA && window.KURAJIKA.LOCATIONS) || {};
  const storeId = CFG.storeId;
  const shelvesApi = CFG.shelvesApi;

  if (!storeId || !shelvesApi) return;

  let shelves = [];

  function opt(label, value, selected, disabled) {
    const o = document.createElement("option");
    o.value = value == null ? "" : String(value);
    o.textContent = label;
    if (selected) o.selected = true;
    if (disabled) o.disabled = true;
    return o;
  }

  function shelfLabel(sh) {
    const nm = (sh.name || "").trim();
    return nm ? `${sh.code} ${nm}` : `${sh.code}`;
  }

  function filterShelves(zone, areaMapId) {
    const z = zone ? String(zone) : "";
    const a = areaMapId ? String(areaMapId) : "";
    return shelves.filter(sh =>
      String(sh.temp_zone || "") === z &&
      String(sh.store_area_map_id || "") === a
    );
  }

  function setShelfPlaceholder(shelfSel, msg) {
    shelfSel.innerHTML = "";
    shelfSel.appendChild(opt(msg, "", true, true));
  }

  function refreshRow(tr, clearSelection) {
    const zoneSel = tr.querySelector("select.temp-zone");
    const areaSel = tr.querySelector("select.area");
    const shelfSel = tr.querySelector("select.shelf");

    if (!zoneSel || !areaSel || !shelfSel) return;

    const zone = zoneSel.value;
    const areaMapId = areaSel.value;

    if (!zone) {
      setShelfPlaceholder(shelfSel, "— 温度帯を選択 —");
      return;
    }
    if (!areaMapId) {
      setShelfPlaceholder(shelfSel, "— エリアを選択 —");
      return;
    }

    const current = clearSelection ? "" : shelfSel.value;
    const list = filterShelves(zone, areaMapId);

    shelfSel.innerHTML = "";
    shelfSel.appendChild(opt("", "", true, false));

    let kept = false;
    for (const sh of list) {
      const v = String(sh.id);
      const selected = current && v === String(current);
      if (selected) kept = true;
      shelfSel.appendChild(opt(shelfLabel(sh), v, selected, false));
    }

    // If old selection no longer valid, clear it
    if (current && !kept) shelfSel.value = "";
  }

  function wire() {
    const rows = document.querySelectorAll("tr[data-item-id]");
    rows.forEach(tr => {
      const zoneSel = tr.querySelector("select.temp-zone");
      const areaSel = tr.querySelector("select.area");

      if (zoneSel) zoneSel.addEventListener("change", () => refreshRow(tr, true));
      if (areaSel) areaSel.addEventListener("change", () => refreshRow(tr, true));

      // initial fill
      refreshRow(tr, false);
    });
  }

  fetch(`${shelvesApi}?store_id=${encodeURIComponent(storeId)}`)
    .then(r => r.json())
    .then(data => {
      shelves = (data && data.shelves) || [];
      wire();
    })
    .catch(() => {});
})();
