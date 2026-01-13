from __future__ import annotations

from datetime import datetime

from flask import render_template, request

from . import reports_bp, get_db


@reports_bp.route("/usage/report", methods=["GET"])
def usage_report():
    db = get_db()

    # Stores
    mst_stores = db.execute(
        "SELECT id, name FROM mst_stores ORDER BY code"
    ).fetchall()

    # Suppliers (dropdown)
    suppliers = db.execute(
        "SELECT id, name FROM suppliers ORDER BY code"
    ).fetchall()

    # Query params
    store_id = request.args.get("store_id") or ""
    supplier_id = request.args.get("supplier_id") or ""

    selected_store_id = int(store_id) if store_id else None
    selected_supplier_id = int(supplier_id) if supplier_id else None

    # Last 13 months
    today = datetime.now().date()
    y, m = today.year, today.month

    month_keys = []
    for _ in range(13):
        month_keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    month_keys = list(reversed(month_keys))

    start_date = month_keys[0] + "-01"
    ey, em = map(int, month_keys[-1].split("-"))
    if em == 12:
        end_date = f"{ey + 1}-01-01"
    else:
        end_date = f"{ey}-{em + 1:02d}-01"

    # ----------------------------------------
    # ① Purchases per month (qty)
    # ----------------------------------------
    where_pur = [
        "p.delivery_date >= %s",
        "p.delivery_date < %s",
        "p.is_deleted = 0",
    ]
    params_pur: list[object] = [start_date, end_date]

    if store_id:
        where_pur.append("p.store_id = %s")
        params_pur.append(int(store_id))

    if supplier_id:
        where_pur.append("p.supplier_id = %s")
        params_pur.append(int(supplier_id))

    sql_pur = f"""
        SELECT
            p.item_id,
            TO_CHAR(p.delivery_date, 'YYYY-MM') AS ym,
            SUM(p.quantity) AS pur_qty
        FROM purchases p
        WHERE {' AND '.join(where_pur)}
        GROUP BY p.item_id, ym
    """
    rows_pur = db.execute(sql_pur, params_pur).fetchall()

    pur_map: dict[int, dict[str, int]] = {}
    for r in rows_pur:
        iid = int(r["item_id"])
        ym = r["ym"]
        pur_map.setdefault(iid, {})[ym] = int(r["pur_qty"] or 0)

    # ----------------------------------------
    # ② Month-end inventory (latest count in month)
    # ----------------------------------------
    where_inv = [
        "sc.count_date >= %s",
        "sc.count_date < %s",
    ]
    params_inv: list[object] = [start_date, end_date]

    if store_id:
        where_inv.append("sc.store_id = %s")
        params_inv.append(int(store_id))

    sql_inv = f"""
        WITH last_counts AS (
          SELECT
            sc.store_id,
            sc.item_id,
            TO_CHAR(sc.count_date, 'YYYY-MM') AS ym,
            MAX(sc.count_date) AS max_date
          FROM stock_counts sc
          WHERE {' AND '.join(where_inv)}
          GROUP BY sc.store_id, sc.item_id, ym
        )
        SELECT
            lc.item_id,
            lc.ym,
            sc.counted_qty
        FROM last_counts lc
        JOIN stock_counts sc
          ON sc.store_id = lc.store_id
         AND sc.item_id = lc.item_id
         AND sc.count_date = lc.max_date
        ORDER BY lc.item_id, lc.ym
    """
    rows_inv = db.execute(sql_inv, params_inv).fetchall()

    end_inv_map: dict[int, dict[str, int]] = {}
    for r in rows_inv:
        iid = int(r["item_id"])
        ym = r["ym"]
        end_inv_map.setdefault(iid, {})[ym] = int(r["counted_qty"] or 0)

    # ----------------------------------------
    # ③ Item meta
    # ----------------------------------------
    item_ids = set(pur_map.keys()) | set(end_inv_map.keys())

    if item_ids:
        placeholders = ",".join(["%s"] * len(item_ids))
        sql_items = f"""
            SELECT id, code, name, supplier_id
            FROM mst_items
            WHERE id IN ({placeholders})
        """
        params_items: list[object] = list(item_ids)

        if supplier_id:
            sql_items += " AND supplier_id = %s"
            params_items.append(int(supplier_id))

        mst_items = db.execute(sql_items, params_items).fetchall()
    else:
        mst_items = []

    item_meta = {int(row["id"]): row for row in mst_items}

    # ----------------------------------------
    # ④ Calculate begin/purchase/end/used
    # ----------------------------------------
    item_rows = []

    for iid in sorted(item_ids):
        meta = item_meta.get(iid)
        if not meta:
            continue

        per_month = {}
        total_pur = 0
        total_used = 0
        total_end = 0

        prev_end = 0
        for ym in month_keys:
            pur = pur_map.get(iid, {}).get(ym, 0)
            end_qty = end_inv_map.get(iid, {}).get(ym, 0)

            begin_qty = prev_end
            used = begin_qty + pur - end_qty

            per_month[ym] = {
                "begin_qty": begin_qty,
                "pur_qty": pur,
                "end_qty": end_qty,
                "used_qty": used,
            }

            total_pur += pur
            total_used += used
            total_end = end_qty
            prev_end = end_qty

        item_rows.append(
            {
                "item_id": iid,
                "item_code": meta["code"],
                "item_name": meta["name"],
                "per_month": per_month,
                "total_pur": total_pur,
                "total_used": total_used,
                "total_end": total_end,
            }
        )

    item_rows.sort(key=lambda x: x["total_used"], reverse=True)

    return render_template(
        "inv/usage_report.html",
        mst_stores=mst_stores,
        selected_store_id=selected_store_id,
        suppliers=suppliers,
        selected_supplier_id=selected_supplier_id,
        month_keys=month_keys,
        item_rows=item_rows,
    )
