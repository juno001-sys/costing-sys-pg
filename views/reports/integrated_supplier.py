from __future__ import annotations

from datetime import datetime

from flask import render_template, request, g
from utils.access_scope import (
    get_accessible_stores,
    normalize_accessible_store_id,
)
from . import reports_bp, get_db


DEFAULT_WINDOW_MONTHS = 12


def _parse_ym(raw: str | None, fallback: str) -> str:
    if not raw:
        return fallback
    try:
        y, m = raw.split("-")
        yi, mi = int(y), int(m)
        if 1 <= mi <= 12 and 2000 <= yi <= 2100:
            return f"{yi:04d}-{mi:02d}"
    except Exception:
        pass
    return fallback


def _ym_range(from_ym: str, to_ym: str) -> list[str]:
    y1, m1 = map(int, from_ym.split("-"))
    y2, m2 = map(int, to_ym.split("-"))
    out: list[str] = []
    y, m = y1, m1
    while (y, m) <= (y2, m2):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
        if len(out) >= 60:
            break
    return out


def _ym_first_day(ym: str) -> str:
    return ym + "-01"


def _ym_next_first(ym: str) -> str:
    y, m = map(int, ym.split("-"))
    m += 1
    if m > 12:
        m = 1
        y += 1
    return f"{y:04d}-{m:02d}-01"


@reports_bp.route("/integrated", methods=["GET"])
def integrated_report():
    """Integrated purchase + usage per item per month.

    Each item shows 4 sub-rows (期首・仕入・使用・繰越); each month
    column contains 3 sub-cells (単価・数量・金額). Default window
    is the last 12 months; operator can widen via 開始月/終了月.
    Store filter is required; supplier filter is optional.
    """
    db = get_db()

    mst_stores = get_accessible_stores()
    selected_store_id = normalize_accessible_store_id(
        request.args.get("store_id")
    )

    # --- Default window: 12 months ending in the current month ---
    today = datetime.now().date()
    to_ym_default = f"{today.year:04d}-{today.month:02d}"
    y, m = today.year, today.month
    for _ in range(DEFAULT_WINDOW_MONTHS - 1):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    from_ym_default = f"{y:04d}-{m:02d}"

    from_ym = _parse_ym(request.args.get("from_ym"), from_ym_default)
    to_ym = _parse_ym(request.args.get("to_ym"), to_ym_default)
    if from_ym > to_ym:
        from_ym, to_ym = to_ym, from_ym
    month_keys = _ym_range(from_ym, to_ym)

    company_id = getattr(g, "current_company_id", None)

    # --- Supplier filter (optional) ---
    supplier_id_raw = (request.args.get("supplier_id") or "").strip()
    try:
        selected_supplier_id: int | None = int(supplier_id_raw) if supplier_id_raw else None
    except ValueError:
        selected_supplier_id = None

    # --- Suppliers list for the filter dropdown (scoped to selected store) ---
    suppliers: list = []
    if selected_store_id:
        suppliers = db.execute(
            """
            SELECT s.id, s.code, s.name
            FROM pur_suppliers s
            JOIN pur_store_suppliers ss
              ON s.id = ss.supplier_id
             AND ss.store_id = %s
             AND ss.is_active = 1
            WHERE s.is_active = 1
              AND s.company_id = %s
            ORDER BY s.code
            """,
            (selected_store_id, company_id),
        ).fetchall()

    # --- Empty state: no store selected ---
    if not selected_store_id:
        return render_template(
            "pur/integrated_report.html",
            mst_stores=mst_stores,
            selected_store_id=None,
            suppliers=suppliers,
            selected_supplier_id=selected_supplier_id,
            month_keys=month_keys,
            from_ym=from_ym,
            to_ym=to_ym,
            item_rows=[],
        )

    start_date = _ym_first_day(from_ym)
    end_date = _ym_next_first(to_ym)

    # --- 1) Purchases per item per month (optionally supplier-filtered) ---
    pur_sql = """
        SELECT
            p.item_id,
            TO_CHAR(p.delivery_date, 'YYYY-MM') AS ym,
            SUM(p.quantity) AS qty,
            SUM(p.amount)   AS amount
        FROM purchases p
        JOIN mst_items i ON p.item_id = i.id
        LEFT JOIN mst_stores st ON p.store_id = st.id
        WHERE p.is_deleted = 0
          AND p.delivery_date >= %s
          AND p.delivery_date < %s
          AND p.store_id = %s
          AND st.company_id = %s
    """
    pur_params: list = [start_date, end_date, selected_store_id, company_id]
    if selected_supplier_id:
        pur_sql += " AND i.supplier_id = %s"
        pur_params.append(selected_supplier_id)
    pur_sql += " GROUP BY p.item_id, ym"
    pur_rows = db.execute(pur_sql, pur_params).fetchall()

    # --- 2) Month-end stock counts per item ---
    inv_sql = """
        WITH latest_in_month AS (
          SELECT
            sc.item_id,
            TO_CHAR(sc.count_date, 'YYYY-MM') AS ym,
            sc.count_date,
            sc.counted_qty,
            ROW_NUMBER() OVER (
              PARTITION BY sc.item_id, TO_CHAR(sc.count_date, 'YYYY-MM')
              ORDER BY sc.count_date DESC, sc.id DESC
            ) AS rn
          FROM stock_counts sc
          JOIN mst_items i ON sc.item_id = i.id
          WHERE sc.count_date >= %s
            AND sc.count_date < %s
            AND sc.store_id = %s
            AND i.company_id = %s
    """
    inv_params: list = [start_date, end_date, selected_store_id, company_id]
    if selected_supplier_id:
        inv_sql += " AND i.supplier_id = %s"
        inv_params.append(selected_supplier_id)
    inv_sql += """
        )
        SELECT item_id, ym, count_date, counted_qty
        FROM latest_in_month
        WHERE rn = 1
    """
    inv_rows = db.execute(inv_sql, inv_params).fetchall()

    # --- 3) Per-count end valuation (latest purchase unit price at/before count_date) ---
    end_valuation: dict[tuple[int, str], float] = {}
    for r in inv_rows:
        price_row = db.execute(
            """
            SELECT unit_price
            FROM purchases
            WHERE item_id = %s
              AND store_id = %s
              AND is_deleted = 0
              AND delivery_date <= %s
            ORDER BY delivery_date DESC, id DESC
            LIMIT 1
            """,
            [r["item_id"], selected_store_id, r["count_date"]],
        ).fetchone()
        unit_price = (
            float(price_row["unit_price"])
            if price_row and price_row["unit_price"] is not None
            else 0.0
        )
        end_valuation[(r["item_id"], r["ym"])] = unit_price

    # --- Item meta (+ supplier name) ---
    items_sql = """
        SELECT i.id, i.code, i.name, i.supplier_id, s.name AS supplier_name
        FROM mst_items i
        LEFT JOIN pur_suppliers s ON i.supplier_id = s.id
        WHERE i.company_id = %s
          AND i.is_active = 1
    """
    items_params: list = [company_id]
    if selected_supplier_id:
        items_sql += " AND i.supplier_id = %s"
        items_params.append(selected_supplier_id)
    items_meta_rows = db.execute(items_sql, items_params).fetchall()
    item_meta = {r["id"]: r for r in items_meta_rows}

    # --- Latest stock-count date per item (freshness hint) ---
    last_count_sql = """
        SELECT sc.item_id, MAX(sc.count_date) AS last_date
        FROM stock_counts sc
        JOIN mst_items i ON sc.item_id = i.id
        WHERE sc.store_id = %s
          AND i.company_id = %s
    """
    last_params: list = [selected_store_id, company_id]
    if selected_supplier_id:
        last_count_sql += " AND i.supplier_id = %s"
        last_params.append(selected_supplier_id)
    last_count_sql += " GROUP BY sc.item_id"
    last_count_rows = db.execute(last_count_sql, last_params).fetchall()
    last_count_by_item = {r["item_id"]: r["last_date"] for r in last_count_rows}

    # --- Active items (anything with either purchases or counts in window) ---
    active_ids: set[int] = set()
    for r in pur_rows:
        active_ids.add(r["item_id"])
    for r in inv_rows:
        active_ids.add(r["item_id"])

    def _blank_month_cell() -> dict:
        return {
            "begin_qty": None, "begin_amount": None, "begin_price": None,
            "pur_qty": 0.0,    "pur_amount": 0.0,    "pur_price": None,
            "used_qty": None,  "used_amount": None,  "used_price": None,
            "end_qty": None,   "end_amount": None,   "end_price": None,
        }

    per_item: dict[int, dict] = {}
    for iid in active_ids:
        meta = item_meta.get(iid)
        if not meta:
            continue
        per_item[iid] = {
            "item_id": iid,
            "item_code": meta["code"] or "",
            "item_name": meta["name"] or "",
            "supplier_name": meta.get("supplier_name") if hasattr(meta, "get") else meta["supplier_name"],
            "last_count_date": last_count_by_item.get(iid),
            "months": {ym: _blank_month_cell() for ym in month_keys},
        }

    for r in pur_rows:
        iid, ym = r["item_id"], r["ym"]
        if iid in per_item and ym in per_item[iid]["months"]:
            mo = per_item[iid]["months"][ym]
            mo["pur_qty"] = float(r["qty"] or 0)
            mo["pur_amount"] = float(r["amount"] or 0)
            mo["pur_price"] = (mo["pur_amount"] / mo["pur_qty"]) if mo["pur_qty"] else None

    for r in inv_rows:
        iid, ym = r["item_id"], r["ym"]
        if iid in per_item and ym in per_item[iid]["months"]:
            mo = per_item[iid]["months"][ym]
            eq = float(r["counted_qty"] or 0)
            up = end_valuation.get((iid, ym), 0.0)
            mo["end_qty"] = eq
            mo["end_amount"] = eq * up
            mo["end_price"] = up if eq else None

    # Derive 期首 (from prev month 繰越) and 使用 (= begin + pur − end)
    for item in per_item.values():
        prev_end_qty: float | None = None
        prev_end_amount: float | None = None
        prev_end_price: float | None = None
        for ym in month_keys:
            mo = item["months"][ym]
            mo["begin_qty"] = prev_end_qty
            mo["begin_amount"] = prev_end_amount
            mo["begin_price"] = prev_end_price

            if mo["end_qty"] is not None and mo["begin_qty"] is not None:
                mo["used_qty"] = mo["begin_qty"] + mo["pur_qty"] - mo["end_qty"]
                mo["used_amount"] = (mo["begin_amount"] or 0) + mo["pur_amount"] - (mo["end_amount"] or 0)
                mo["used_price"] = (mo["used_amount"] / mo["used_qty"]) if mo["used_qty"] else None

            if mo["end_qty"] is not None:
                prev_end_qty = mo["end_qty"]
                prev_end_amount = mo["end_amount"]
                prev_end_price = mo["end_price"]

    item_rows = sorted(per_item.values(), key=lambda x: (x["item_code"] or "", x["item_id"]))

    return render_template(
        "pur/integrated_report.html",
        mst_stores=mst_stores,
        selected_store_id=selected_store_id,
        suppliers=suppliers,
        selected_supplier_id=selected_supplier_id,
        month_keys=month_keys,
        from_ym=from_ym,
        to_ym=to_ym,
        item_rows=item_rows,
    )
