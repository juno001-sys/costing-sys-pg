from __future__ import annotations

import json
from datetime import datetime

from flask import render_template, request, g
from utils.access_scope import (
    get_accessible_stores,
    normalize_accessible_store_id,
)

from . import reports_bp, get_db


# Per-category deadstock threshold (days since last delivery before flagged).
# Uncategorized items (ELSE branch) get 0 — they always surface as a
# data-quality nudge to assign a proper category.
CATEGORY_DEADSTOCK_DAYS: dict[str, int] = {
    "青果（野菜）": 7,
    "きのこ類": 7,
    "水産・魚": 7,
    "仕込み品": 7,
    "精肉・卵": 14,
    "乳製品・飲料": 14,
    "パン・製菓": 14,
    "冷凍食品（加工品）": 60,
    "米・穀物・乾物": 60,
    "缶詰・レトルト": 90,
    "調味料・油脂": 90,
    "消耗品・資材": 120,
}
DEADSTOCK_DEFAULT_DAYS = 0


def _build_threshold_case_sql() -> str:
    whens = "\n".join(
        f"            WHEN '{cat}' THEN {days}"
        for cat, days in CATEGORY_DEADSTOCK_DAYS.items()
    )
    return (
        "CASE i.category\n"
        f"{whens}\n"
        f"            ELSE {DEADSTOCK_DEFAULT_DAYS}\n"
        "          END"
    )


def _grouped_threshold_tiers() -> list[dict]:
    """Group categories by their threshold days, sorted ascending."""
    tiers: dict[int, list[str]] = {}
    for cat, days in CATEGORY_DEADSTOCK_DAYS.items():
        tiers.setdefault(days, []).append(cat)
    tiers.setdefault(DEADSTOCK_DEFAULT_DAYS, []).append("未分類")
    return [
        {"days": days, "categories": tiers[days]}
        for days in sorted(tiers.keys())
    ]


@reports_bp.route("/dashboard", methods=["GET"])
def purchase_dashboard():
    db = get_db()
    company_id = getattr(g, "current_company_id", None)

    mst_stores = get_accessible_stores()

    selected_store_id = normalize_accessible_store_id(
        request.args.get("store_id")
    )
    store_id = str(selected_store_id) if selected_store_id else ""

    # Date range (default: current month)
    today = datetime.now().date()
    from_date = request.args.get("from_date") or f"{today.year}-{today.month:02d}-01"
    y, m = today.year, today.month
    if m == 12:
        to_date_default = f"{y + 1}-01-01"
    else:
        to_date_default = f"{y}-{m + 1:02d}-01"
    to_date = request.args.get("to_date") or to_date_default

    # ── Pie Chart 1: by supplier ─────────────────────────────────
    supplier_sql = """
        SELECT s.name AS label, SUM(p.amount) AS total
        FROM purchases p
        JOIN pur_suppliers s ON p.supplier_id = s.id
        LEFT JOIN mst_stores st ON p.store_id = st.id
        WHERE p.is_deleted = 0
          AND p.delivery_date >= %s
          AND p.delivery_date < %s
          AND st.company_id = %s
    """
    supplier_params = [from_date, to_date, company_id]

    if selected_store_id:
        supplier_sql += " AND p.store_id = %s"
        supplier_params.append(selected_store_id)

    supplier_sql += " GROUP BY s.id, s.name ORDER BY total DESC"

    supplier_data = db.execute(supplier_sql, supplier_params).fetchall()

    # ── Pie Chart 2: by category ─────────────────────────────────
    category_sql = """
        SELECT COALESCE(i.category, '未分類') AS label, SUM(p.amount) AS total
        FROM purchases p
        JOIN mst_items i ON p.item_id = i.id
        LEFT JOIN mst_stores st ON p.store_id = st.id
        WHERE p.is_deleted = 0
          AND p.delivery_date >= %s
          AND p.delivery_date < %s
          AND st.company_id = %s
    """
    category_params = [from_date, to_date, company_id]

    if selected_store_id:
        category_sql += " AND p.store_id = %s"
        category_params.append(selected_store_id)

    category_sql += " GROUP BY label ORDER BY total DESC"

    category_data = db.execute(category_sql, category_params).fetchall()

    # ── Pie Chart 3: by process level ───────────────────────────
    process_sql = """
        SELECT COALESCE(i.process_level, '未設定') AS label, SUM(p.amount) AS total
        FROM purchases p
        JOIN mst_items i ON p.item_id = i.id
        LEFT JOIN mst_stores st ON p.store_id = st.id
        WHERE p.is_deleted = 0
          AND p.delivery_date >= %s
          AND p.delivery_date < %s
          AND st.company_id = %s
    """
    process_params = [from_date, to_date, company_id]

    if selected_store_id:
        process_sql += " AND p.store_id = %s"
        process_params.append(selected_store_id)

    process_sql += " GROUP BY label ORDER BY total DESC"

    process_data = db.execute(process_sql, process_params).fetchall()

    # ── Summary totals ───────────────────────────────────────────
    grand_total = sum(r["total"] for r in supplier_data) if supplier_data else 0

    # ── Top items by amount ──────────────────────────────────────
    top_items_sql = """
        SELECT i.code, i.name, s.name AS supplier_name,
               i.category,
               SUM(p.quantity) AS total_qty,
               SUM(p.amount) AS total_amount
        FROM purchases p
        JOIN mst_items i ON p.item_id = i.id
        JOIN pur_suppliers s ON p.supplier_id = s.id
        LEFT JOIN mst_stores st ON p.store_id = st.id
        WHERE p.is_deleted = 0
          AND p.delivery_date >= %s
          AND p.delivery_date < %s
          AND st.company_id = %s
    """
    top_items_params = [from_date, to_date, company_id]

    if selected_store_id:
        top_items_sql += " AND p.store_id = %s"
        top_items_params.append(selected_store_id)

    top_items_sql += """
        GROUP BY i.id, i.code, i.name, s.name, i.category
        ORDER BY total_amount DESC
        LIMIT 20
    """

    top_items = db.execute(top_items_sql, top_items_params).fetchall()

    # ── Dead stock (per-category threshold) ─────────────────────────────
    # Each category has its own "ideal duration" before stock is considered
    # stale (see CATEGORY_DEADSTOCK_DAYS at module top). Uncategorized items
    # use threshold = 0 so they surface as a data-quality nudge.
    threshold_case = _build_threshold_case_sql()
    dead_stock_sql = f"""
        WITH latest_stock AS (
          SELECT DISTINCT ON (sc.store_id, sc.item_id)
            sc.store_id, sc.item_id, sc.counted_qty, sc.count_date
          FROM stock_counts sc
          ORDER BY sc.store_id, sc.item_id, sc.count_date DESC, sc.id DESC
        ),
        purchases_after_count AS (
          SELECT ls.store_id, ls.item_id,
                 COALESCE(SUM(p.quantity), 0) AS qty_after
          FROM latest_stock ls
          LEFT JOIN purchases p
            ON p.store_id = ls.store_id
           AND p.item_id  = ls.item_id
           AND p.is_deleted = 0
           AND p.delivery_date > ls.count_date
          GROUP BY ls.store_id, ls.item_id
        ),
        last_purchase AS (
          SELECT DISTINCT ON (p.store_id, p.item_id)
            p.store_id, p.item_id, p.delivery_date, p.unit_price
          FROM purchases p
          WHERE p.is_deleted = 0
          ORDER BY p.store_id, p.item_id, p.delivery_date DESC, p.id DESC
        ),
        scored AS (
          SELECT
            i.code,
            i.name,
            s.name AS supplier_name,
            i.category,
            (ls.counted_qty + pac.qty_after) AS current_stock,
            lp.delivery_date AS last_purchase_date,
            (CURRENT_DATE - lp.delivery_date) AS days_since_purchase,
            lp.unit_price,
            ((ls.counted_qty + pac.qty_after) * lp.unit_price) AS estimated_value,
            {threshold_case} AS threshold_days,
            ls.store_id AS store_id
          FROM latest_stock ls
          JOIN purchases_after_count pac
            ON pac.store_id = ls.store_id AND pac.item_id = ls.item_id
          JOIN mst_items i      ON i.id = ls.item_id AND i.is_active = 1
          JOIN pur_suppliers s  ON s.id = i.supplier_id
          LEFT JOIN mst_stores st ON st.id = ls.store_id
          LEFT JOIN last_purchase lp
            ON lp.store_id = ls.store_id AND lp.item_id = ls.item_id
          WHERE (ls.counted_qty + pac.qty_after) > 0
            AND lp.delivery_date IS NOT NULL
            AND st.company_id = %s
        )
        SELECT
          code, name, supplier_name, category,
          current_stock, last_purchase_date, days_since_purchase,
          unit_price, estimated_value, threshold_days,
          (days_since_purchase - threshold_days) AS days_over
        FROM scored
        WHERE days_since_purchase >= threshold_days
    """
    dead_stock_params = [company_id]
    if selected_store_id:
        dead_stock_sql += " AND store_id = %s"
        dead_stock_params.append(selected_store_id)
    dead_stock_sql += """
        ORDER BY days_over DESC, estimated_value DESC NULLS LAST
        LIMIT 50
    """
    dead_stock_items = db.execute(dead_stock_sql, dead_stock_params).fetchall()

    return render_template(
        "pur/purchase_dashboard.html",
        mst_stores=mst_stores,
        selected_store_id=selected_store_id or "",
        from_date=from_date,
        to_date=to_date,
        supplier_labels=json.dumps([r["label"] for r in supplier_data], ensure_ascii=False),
        supplier_values=json.dumps([int(r["total"]) for r in supplier_data]),
        category_labels=json.dumps([r["label"] for r in category_data], ensure_ascii=False),
        category_values=json.dumps([int(r["total"]) for r in category_data]),
        grand_total=grand_total,
        top_items=top_items,
        process_labels=json.dumps([r["label"] for r in process_data], ensure_ascii=False),
        process_values=json.dumps([int(r["total"]) for r in process_data]),
        dead_stock_items=dead_stock_items,
        deadstock_threshold_tiers=_grouped_threshold_tiers(),
    )
