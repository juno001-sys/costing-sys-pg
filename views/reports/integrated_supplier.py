from __future__ import annotations

from datetime import datetime

from flask import redirect, render_template, request, url_for, g
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
    """Inclusive month list between from_ym and to_ym (hard-capped at 60)."""
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


@reports_bp.route("/supplier/<int:supplier_id>/integrated", methods=["GET"])
def integrated_supplier(supplier_id: int):
    """Integrated purchase + usage drill-down for one supplier.

    Per item, per month: shows 期首・仕入・使用・期末 (begin / purchase
    / used / end), each with 数量 / 金額 / 平均単価. Default window is
    the last 12 months; the user can widen it to look at past data.
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

    # --- Supplier name ---
    supplier_row = db.execute(
        "SELECT id, code, name FROM pur_suppliers WHERE id = %s",
        [supplier_id],
    ).fetchone()
    if supplier_row is None:
        return redirect(url_for("reports.purchase_report"))
    supplier_name = supplier_row["name"]

    # --- Empty state: no store selected ---
    if not selected_store_id:
        return render_template(
            "pur/purchase_report_supplier_integrated.html",
            mst_stores=mst_stores,
            selected_store_id=None,
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            month_keys=month_keys,
            from_ym=from_ym,
            to_ym=to_ym,
            item_rows=[],
        )

    company_id = getattr(g, "current_company_id", None)
    start_date = _ym_first_day(from_ym)
    end_date = _ym_next_first(to_ym)

    # --- 1) Purchases per item per month ---
    pur_rows = db.execute(
        """
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
          AND i.supplier_id = %s
        GROUP BY p.item_id, ym
        """,
        [start_date, end_date, selected_store_id, company_id, supplier_id],
    ).fetchall()

    # --- 2) Month-end stock count per item per month (latest count in month) ---
    inv_rows = db.execute(
        """
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
            AND i.supplier_id = %s
            AND i.company_id = %s
        )
        SELECT item_id, ym, count_date, counted_qty
        FROM latest_in_month
        WHERE rn = 1
        """,
        [start_date, end_date, selected_store_id, supplier_id, company_id],
    ).fetchall()

    # --- 3) Per-count latest purchase unit price at-or-before count_date.
    #        Used to value end-of-month stock. ---
    # Simple approach: for each (item, count_date) find the latest purchase
    # unit_price at or before that date for the same store.
    end_valuation: dict[tuple[int, str], float] = {}
    for r in inv_rows:
        iid = r["item_id"]
        cd = r["count_date"]
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
            [iid, selected_store_id, cd],
        ).fetchone()
        unit_price = float(price_row["unit_price"]) if price_row and price_row["unit_price"] is not None else 0.0
        end_valuation[(iid, r["ym"])] = unit_price

    # --- Item meta ---
    items_meta = db.execute(
        """
        SELECT id, code, name
        FROM mst_items
        WHERE supplier_id = %s
          AND company_id = %s
        """,
        [supplier_id, company_id],
    ).fetchall()
    item_meta = {r["id"]: r for r in items_meta}

    # --- Latest stock count date per item (any date, for freshness hint) ---
    last_count_rows = db.execute(
        """
        SELECT sc.item_id, MAX(sc.count_date) AS last_date
        FROM stock_counts sc
        JOIN mst_items i ON sc.item_id = i.id
        WHERE sc.store_id = %s
          AND i.supplier_id = %s
          AND i.company_id = %s
        GROUP BY sc.item_id
        """,
        [selected_store_id, supplier_id, company_id],
    ).fetchall()
    last_count_by_item = {r["item_id"]: r["last_date"] for r in last_count_rows}

    # --- Collect items with any activity in the window ---
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
            "last_count_date": last_count_by_item.get(iid),
            "months": {ym: _blank_month_cell() for ym in month_keys},
        }

    # Fill purchases
    for r in pur_rows:
        iid, ym = r["item_id"], r["ym"]
        if iid in per_item and ym in per_item[iid]["months"]:
            mo = per_item[iid]["months"][ym]
            mo["pur_qty"] = float(r["qty"] or 0)
            mo["pur_amount"] = float(r["amount"] or 0)
            mo["pur_price"] = (mo["pur_amount"] / mo["pur_qty"]) if mo["pur_qty"] else None

    # Fill end-of-month counts
    for r in inv_rows:
        iid, ym = r["item_id"], r["ym"]
        if iid in per_item and ym in per_item[iid]["months"]:
            mo = per_item[iid]["months"][ym]
            eq = float(r["counted_qty"] or 0)
            up = end_valuation.get((iid, ym), 0.0)
            mo["end_qty"] = eq
            mo["end_amount"] = eq * up
            mo["end_price"] = up if eq else None

    # Derive begin (from prev month end) and used (= begin + pur − end)
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

    item_rows = sorted(per_item.values(), key=lambda x: x["item_code"] or "")

    return render_template(
        "pur/purchase_report_supplier_integrated.html",
        mst_stores=mst_stores,
        selected_store_id=selected_store_id,
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        month_keys=month_keys,
        from_ym=from_ym,
        to_ym=to_ym,
        item_rows=item_rows,
    )
