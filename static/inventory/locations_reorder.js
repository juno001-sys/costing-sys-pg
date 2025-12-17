const modal = document.getElementById("reorderModal");
const list = document.getElementById("reorderList");
const meta = document.getElementById("reorderMeta");
const saveBtn = document.getElementById("saveOrderBtn");

let currentShelfId = null;

function buildReorderListFromShelf(shelfId) {
  list.innerHTML = "";
  const section = document.querySelector(`.section[data-shelf-id="${shelfId}"]`);
  const headerText = section.querySelector(".meta")?.textContent?.trim() || "";
  meta.textContent = headerText;

  const rows = section.querySelectorAll("tbody tr");
  rows.forEach((tr) => {
    const code = tr.children[0].textContent.trim();
    const name = tr.children[1].textContent.trim();
    const input = tr.querySelector("input[name^='qty_']");
    const m = input?.name?.match(/^qty_(\d+)_(\d+)$/);
    const itemId = m ? m[2] : null;
    if (!itemId) return;

    const li = document.createElement("li");
    li.className = "reorder-item";
    li.draggable = true;
    li.dataset.itemId = itemId;

    li.innerHTML = `
      <span class="drag-handle" title="Drag">≡</span>
      <div style="flex:1;">
        <div style="font-weight:700;">${code}</div>
        <div class="muted">${name}</div>
      </div>
    `;
    list.appendChild(li);
  });
}

function enableDragSort(ul) {
  let dragEl = null;

  ul.addEventListener("dragstart", (e) => {
    const li = e.target.closest(".reorder-item");
    if (!li) return;
    dragEl = li;
    li.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
  });

  ul.addEventListener("dragend", (e) => {
    const li = e.target.closest(".reorder-item");
    if (!li) return;
    li.classList.remove("dragging");
    dragEl = null;
  });

  ul.addEventListener("dragover", (e) => {
    e.preventDefault();
    const over = e.target.closest(".reorder-item");
    if (!over || !dragEl || over === dragEl) return;

    const rect = over.getBoundingClientRect();
    const before = (e.clientY - rect.top) < rect.height / 2;
    ul.insertBefore(dragEl, before ? over : over.nextSibling);
  });
}

enableDragSort(list);

document.querySelectorAll("[data-open-reorder]").forEach((btn) => {
  btn.addEventListener("click", () => {
    currentShelfId = btn.dataset.shelfId;
    buildReorderListFromShelf(currentShelfId);
    modal.showModal();
  });
});

saveBtn.addEventListener("click", async () => {
  if (!currentShelfId) return;

  const itemIds = [...list.querySelectorAll(".reorder-item")].map((li) => Number(li.dataset.itemId));

  // Reorder the table rows immediately (nice UX)
  const section = document.querySelector(`.section[data-shelf-id="${currentShelfId}"]`);
  const tbody = section.querySelector("tbody");
  const trs = [...tbody.querySelectorAll("tr")];

  const byItemId = new Map();
  trs.forEach((tr) => {
    const input = tr.querySelector("input[name^='qty_']");
    const m = input?.name?.match(/^qty_(\d+)_(\d+)$/);
    if (m) byItemId.set(Number(m[2]), tr);
  });

  tbody.innerHTML = "";
  itemIds.forEach((id) => {
    const tr = byItemId.get(id);
    if (tr) tbody.appendChild(tr);
  });

  // Save to server
  const res = await fetch(window.KURAJIKA.INVENTORY.reorderUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      store_id: window.KURAJIKA.INVENTORY.storeId,
      shelf_id: Number(currentShelfId),
      item_ids: itemIds
    })
  });

  if (!res.ok) {
    alert("Failed to save order.");
    return;
  }

  modal.close();
});
