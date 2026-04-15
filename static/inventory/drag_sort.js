/**
 * drag_sort.js  –  HTML5 drag-and-drop row reordering for admin tables.
 *
 * Usage:
 *   <script src="/static/inventory/drag_sort.js"></script>
 *   <script>initDragSort('#my-table');</script>
 *
 * Rows with a hidden input whose name contains "sort" (e.g. name="tz_sort_ABC")
 * are treated as draggable data rows.  After a drop the hidden inputs are
 * renumbered 1, 2, 3 … matching the new visual order.
 *
 * Rows without such an input (e.g. area group headers) are left in place.
 * Works with tables that have multiple <tbody> sections.
 *
 * A drag-handle cell (☰) is prepended to every data row automatically.
 */

function initDragSort(tableSelector) {
  var table = document.querySelector(tableSelector);
  if (!table) return;

  var SORT_SEL = 'input[type="hidden"][name*="sort"]';

  /* ── helper: is this a draggable data row? ── */
  function isDataRow(tr) {
    return tr && tr.querySelector(SORT_SEL);
  }

  /* ── inject grip-handle column ── */
  var headerRow = table.querySelector('thead tr');
  if (headerRow) {
    var th = document.createElement('th');
    th.style.width = '36px';
    headerRow.insertBefore(th, headerRow.firstChild);
  }

  /* Add handle to data rows; widen colspan on group-header rows */
  table.querySelectorAll('tbody tr').forEach(function (tr) {
    if (isDataRow(tr)) {
      var td = document.createElement('td');
      td.className = 'drag-handle';
      td.textContent = '\u2630';
      td.style.cursor = 'grab';
      td.style.textAlign = 'center';
      td.style.fontSize = '16px';
      td.style.userSelect = 'none';
      tr.insertBefore(td, tr.firstChild);
      tr.setAttribute('draggable', 'true');
    } else {
      /* group header — bump its colspan */
      var cell = tr.querySelector('td[colspan]');
      if (cell) {
        cell.setAttribute('colspan', parseInt(cell.getAttribute('colspan'), 10) + 1);
      }
    }
  });

  /* ── state ── */
  var dragRow = null;

  table.addEventListener('dragstart', function (e) {
    var tr = e.target.closest('tr');
    if (!isDataRow(tr)) return;
    dragRow = tr;
    tr.classList.add('ds-dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', '');
  });

  table.addEventListener('dragover', function (e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    var tr = e.target.closest('tr');
    if (!tr || tr === dragRow || !isDataRow(tr)) return;

    table.querySelectorAll('.ds-over').forEach(function (el) {
      el.classList.remove('ds-over');
    });
    tr.classList.add('ds-over');
  });

  table.addEventListener('dragleave', function (e) {
    var tr = e.target.closest('tr');
    if (tr) tr.classList.remove('ds-over');
  });

  table.addEventListener('drop', function (e) {
    e.preventDefault();
    var target = e.target.closest('tr');
    if (!target || !dragRow || target === dragRow || !isDataRow(target)) return;

    /* Move the dragged row's <tbody> next to the target's <tbody>.
       If they share the same tbody, simple insertBefore.
       If different tbodies, move dragged row into target's tbody. */
    var targetTbody = target.parentNode;
    var rect = target.getBoundingClientRect();
    var midY = rect.top + rect.height / 2;

    if (e.clientY < midY) {
      targetTbody.insertBefore(dragRow, target);
    } else {
      targetTbody.insertBefore(dragRow, target.nextSibling);
    }

    renumber();
  });

  table.addEventListener('dragend', function () {
    dragRow = null;
    table.querySelectorAll('.ds-dragging, .ds-over').forEach(function (el) {
      el.classList.remove('ds-dragging', 'ds-over');
    });
  });

  /* ── renumber hidden sort inputs across the whole table ── */
  function renumber() {
    var idx = 0;
    table.querySelectorAll('tbody tr').forEach(function (tr) {
      var inp = tr.querySelector(SORT_SEL);
      if (inp) {
        idx++;
        inp.value = idx;
      }
    });
  }

  renumber();

  /* ── inject minimal CSS ── */
  if (!document.getElementById('ds-style')) {
    var style = document.createElement('style');
    style.id = 'ds-style';
    style.textContent =
      '.ds-dragging { opacity: 0.4; }' +
      '.ds-over    { box-shadow: 0 -2px 0 0 #0d6efd inset; }';
    document.head.appendChild(style);
  }
}
