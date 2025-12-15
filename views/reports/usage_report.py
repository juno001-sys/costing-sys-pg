from __future__ import annotations

from flask import render_template, request
from datetime import datetime

def register(app, get_db):
    
    @reports_bp.route("/usage/report", methods=["GET"])
    def usage_report():
        db = get_db()
    
        stores = db.execute("SELECT id, name FROM stores ORDER BY code").fetchall()
        suppliers = db.execute("SELECT id, name FROM suppliers ORDER BY code").fetchall()
    
        store_id = request.args.get("store_id") or ""
        supplier_id = request.args.get("supplier_id") or ""
    
        selected_store_id = int(store_id) if store_id else None
        selected_supplier_id = int(supplier_id) if supplier_id else None
    
        # last 13 months
        today = datetime.now().date()
        year, month = today.year, today.month
    
        month_keys = []
        for _ in range(13):
            month_keys.append(f"{year:04d}-{month:02d}")
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        month_keys.reverse()
    
        start_ym = month_keys[0]
        end_ym = month_keys[-1]
    
        start_date = f"{start_ym}-01"
        end_year = int(end_ym[:4])
        end_month = int(end_ym[5:7])
        if end_month == 12:
            end_date = f"{end_year + 1}-01-01"
        else:
            end_date = f"{end_year}-{end_month + 1:02d}-01"
    
        # ① purchases per month
        where_pur = [
            "p.delivery_date >= ?",
            "p.delivery_date < ?",
            "p.is_deleted = 0",
        ]
        params_pur = [start_date, end_date]
    
        if store_id:
            where_pur.append("p.store_id = ?")
            params_pur.append(store_id)
    
        if supplier_id:
            where_pur.append("p.supplier_id = ?")
            params_pur.append(supplier_id)
    
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
    
        pur_map = {}
        for r in rows_pur:
            iid = r["item_id"]
            ym = r["ym"]
            pur_map.setdefault(iid, {})[ym] = int(r["pur_qty"] or 0)
    
        # ② month-end inventory
        where_inv = [
            "sc.count_date >= ?",
            "sc.count_date < ?",
        ]
        params_inv = [start_date, end_date]
    
        if store_id:
            where_inv.append("sc.store_id = ?")
            params_inv.append(store_id)
    
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
    
        end_inv_map = {}
        for r in rows_inv:
            iid = r["item_id"]
            ym = r["ym"]
            end_inv_map.setdefault(iid, {})[ym] = int(r["counted_qty"] or 0)
    
        # ③ items meta
        item_ids = set(pur_map.keys()) | set(end_inv_map.keys())
    
        if item_ids:
            placeholders = ",".join(["?"] * len(item_ids))
            sql_items = f"""
                SELECT id, code, name, supplier_id
                FROM items
                WHERE id IN ({placeholders})
            """
            params_items = list(item_ids)
    
            if supplier_id:
                sql_items += " AND supplier_id = ?"
                params_items.append(supplier_id)
    
            items = db.execute(sql_items, params_items).fetchall()
        else:
            items = []
    
        item_meta = {row["id"]: row for row in items}
    
        # ④ calc used
        item_rows = []
    
        for iid in sorted(item_ids):
            meta = item_meta.get(iid)
            if not meta:
                continue
    
            per_month = {}
            total_pur = total_used = total_end = 0
            prev_end = 0
    
            for ym in month_keys:
                pur = pur_map.get(iid, {}).get(ym, 0)
                end_qty = end_inv_map.get(iid, {}).get(ym, 0)
    
                used = prev_end + pur - end_qty
    
                per_month[ym] = {
                    "begin_qty": prev_end,
                    "pur_qty": pur,
                    "end_qty": end_qty,
                    "used_qty": used,
                }
    
                total_pur += pur
                total_used += used
                total_end = end_qty
                prev_end = end_qty
    
            item_rows.append({
                "item_id": iid,
                "item_code": meta["code"],
                "item_name": meta["name"],
                "per_month": per_month,
                "total_pur": total_pur,
                "total_used": total_used,
                "total_end": total_end,
            })
    
        item_rows.sort(key=lambda x: x["total_used"], reverse=True)
    
        return render_template(
            "usage_report.html",
            stores=stores,
            selected_store_id=selected_store_id,
            suppliers=suppliers,
            selected_supplier_id=selected_supplier_id,
            month_keys=month_keys,
            item_rows=item_rows,
        )
