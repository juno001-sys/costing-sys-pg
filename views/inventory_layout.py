# views/inventory_layout.py

from datetime import datetime
import json
from flask import (
    render_template,
    request,
    jsonify,
)

# ----------------------------------------
# Inventory Count (Layout / Physical)
# ----------------------------------------

def init_inventory_layout_views(app, get_db):
    """
    Physical-layout-based inventory count screen.
    This does NOT replace inventory.py.
    """

    # ----------------------------------------
    # GET: Inventory Count (Layout)
    # /inventory/count/layout
    # ----------------------------------------
    @app.route("/inventory/count/layout", methods=["GET"])
    def inventory_count_layout():
        db = get_db()

        # Store list
        stores = db.execute(
            "SELECT id, name FROM stores ORDER BY code"
        ).fetchall()

        today = datetime.today().strftime("%Y-%m-%d")
        store_id = request.args.get("store_id") or ""
        count_date = request.args.get("count_date") or today
        selected_store_id = int(store_id) if store_id else None

        groups = []

        if selected_store_id:
            # -----------------------------
            # Reuse existing logic concept:
            # group by temp_zone (temporary)
            # -----------------------------
            zones = ["冷凍", "冷蔵", "常温", "その他"]
            grouped = {z: [] for z in zones}

            rows = db.execute(
                """
                SELECT
                    i.id   AS item_id,
                    i.code AS item_code,
                    i.name AS item_name,
                    COALESCE(i.temp_zone, 'その他') AS storage_type,
                    i.is_internal
                FROM items i
                WHERE i.is_internal = 1

                UNION

                SELECT DISTINCT
                    i.id   AS item_id,
                    i.code AS item_code,
                    i.name AS item_name,
                    COALESCE(i.temp_zone, 'その他') AS storage_type,
                    i.is_internal
                FROM items i
                JOIN purchases p
                  ON p.item_id = i.id
                 AND p.store_id = ?
                 AND p.delivery_date <= ?
                 AND p.is_deleted = 0
                WHERE i.is_internal = 0

                ORDER BY storage_type, item_code
                """,
                (selected_store_id, count_date),
            ).fetchall()

            for r in rows:
                item_id = r["item_id"]

                # -------- system stock --------
                last_cnt = db.execute(
                    """
                    SELECT counted_qty, count_date
                    FROM stock_counts
                    WHERE store_id = ?
                      AND item_id  = ?
                      AND count_date <= ?
                    ORDER BY count_date DESC, id DESC
                    LIMIT 1
                    """,
                    (selected_store_id, item_id, count_date),
                ).fetchone()

                if last_cnt:
                    opening_qty = last_cnt["counted_qty"]
                    start_date = last_cnt["count_date"]
                    pur = db.execute(
                        """
                        SELECT COALESCE(SUM(quantity), 0) AS qty
                        FROM purchases
                        WHERE store_id = ?
                          AND item_id  = ?
                          AND delivery_date > ?
                          AND delivery_date <= ?
                          AND is_deleted = 0
                        """,
                        (selected_store_id, item_id, start_date, count_date),
                    ).fetchone()
                else:
                    opening_qty = 0
                    pur = db.execute(
                        """
                        SELECT COALESCE(SUM(quantity), 0) AS qty
                        FROM purchases
                        WHERE store_id = ?
                          AND item_id  = ?
                          AND delivery_date <= ?
                          AND is_deleted = 0
                        """,
                        (selected_store_id, item_id, count_date),
                    ).fetchone()

                end_qty = opening_qty + (pur["qty"] if pur else 0)

                # -------- unit price --------
                price = db.execute(
                    """
                    SELECT
                      CASE
                        WHEN SUM(quantity) > 0 THEN
                          CAST(SUM(quantity * unit_price) AS REAL) / SUM(quantity)
                        ELSE 0
                      END AS unit_price
                    FROM purchases
                    WHERE store_id = ?
                      AND item_id  = ?
                      AND delivery_date <= ?
                      AND is_deleted = 0
                    """,
                    (selected_store_id, item_id, count_date),
                ).fetchone()

                unit_price = price["unit_price"] or 0
                stock_amount = end_qty * unit_price

                # -------- counted qty --------
                counted = db.execute(
                    """
                    SELECT counted_qty
                    FROM stock_counts
                    WHERE store_id = ?
                      AND item_id  = ?
                      AND count_date = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (selected_store_id, item_id, count_date),
                ).fetchone()

                counted_qty = counted["counted_qty"] if counted else None

                if end_qty > 0 or r["is_internal"] == 1:
                    z = r["storage_type"] or "その他"
                    if z not in grouped:
                        z = "その他"
                    grouped[z].append({
                        "item_id": item_id,
                        "code": r["item_code"],
                        "name": r["item_name"],
                        "system_qty": end_qty,
                        "unit_price": unit_price,
                        "stock_amount": stock_amount,
                        "count_qty": counted_qty,
                    })

            # Convert temp-zone groups → shelf-like groups
            for z in zones:
                rows = grouped.get(z) or []
                if not rows:
                    continue

                fake_shelf_id = abs(hash((selected_store_id, z))) % 1000000

                groups.append({
                    "shelf_id": fake_shelf_id,
                    "area_name": "TEMP",
                    "temp_zone": z,
                    "shelf_code": z,
                    "shelf_name": None,
                    "rows": rows,
                })

        return render_template(
            "inventory/inventory_count_layout.html",
            stores=stores,
            selected_store_id=selected_store_id,
            count_date=count_date,
            groups=groups,
        )

    # ----------------------------------------
    # POST: Reorder items (per shelf)
    # /inventory/reorder-items
    # ----------------------------------------
    @app.route("/inventory/reorder-items", methods=["POST"])
    def inventory_reorder_items():
        db = get_db()
        payload = request.get_json(force=True)

        store_id = payload.get("store_id")
        shelf_id = payload.get("shelf_id")
        item_ids = payload.get("item_ids") or []

        if not store_id or not shelf_id or not item_ids:
            return jsonify({"ok": False, "error": "missing params"}), 400

        # NOTE:
        # This endpoint becomes fully active once
        # item_shelf_map is introduced.
        # For now it safely no-ops.
        #
        # db.execute(...) will be added later.

        return jsonify({"ok": True})
