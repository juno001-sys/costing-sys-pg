from __future__ import annotations

import json
from datetime import datetime

from flask import render_template, request, g
from utils.access_scope import (
    get_accessible_stores,
    normalize_accessible_store_id,
)

from . import reports_bp, get_db


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
    )
