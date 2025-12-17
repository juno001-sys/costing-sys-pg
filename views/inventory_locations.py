# views/inventory_locations.py

from datetime import datetime
from flask import (
    render_template,
    request,
    jsonify,
)


def init_inventory_locations_views(app, get_db):
    """
    Physical-layout-based inventory count screen (shelf layout).
    Uses:
      - stores.company_id
      - store_areas
      - temp_groups / temp_zones
      - store_shelves
      - item_shelf_map
      - (existing) purchases + stock_counts for system_qty and pricing
    """

    # ----------------------------------------
    # GET: Inventory Count (Layout)
    # /inventory/count/layout
    # ----------------------------------------
    @app.route("/inventory/count/layout", methods=["GET"])
    def inventory_count_layout():
        db = get_db()

        stores = db.execute(
            "SELECT id, name FROM stores ORDER BY code"
        ).fetchall()

        today = datetime.today().strftime("%Y-%m-%d")
        store_id = request.args.get("store_id") or ""
        count_date = request.args.get("count_date") or today
        selected_store_id = int(store_id) if store_id else None

        groups = []

        if not selected_store_id:
            return render_template(
                "inventory/inventory_count_layout.html",
                stores=stores,
                selected_store_id=None,
                count_date=count_date,
                groups=[],
            )

        # ---- find company_id for this store (tenant scope) ----
        store_row = db.execute(
            "SELECT id, company_id, name FROM stores WHERE id = ?",
            (selected_store_id,),
        ).fetchone()

        if not store_row or not store_row["company_id"]:
            # If company_id is missing, treat as configuration error
            return render_template(
                "inventory/inventory_count_layout.html",
                stores=stores,
                selected_store_id=selected_store_id,
                count_date=count_date,
                groups=[],
            )

        company_id = store_row["company_id"]

        # ---- load shelves for this store in configured order ----
        shelf_rows = db.execute(
            """
            SELECT
              sh.id AS shelf_id,
              sh.code AS shelf_code,
              sh.name AS shelf_name,
              sh.sort_order AS shelf_sort,

              a.id AS area_id,
              a.name AS area_name,
              a.sort_order AS area_sort,

              tg.code AS temp_group_code,
              tg.name AS temp_group_name,
              tg.sort_order AS temp_group_sort,

              tz.id AS temp_zone_id,
              tz.name AS temp_zone_name,
              tz.sort_order AS temp_zone_sort
            FROM store_shelves sh
            JOIN store_areas a
              ON a.id = sh.area_id
            JOIN temp_zones tz
              ON tz.id = sh.temp_zone_id
             AND tz.company_id = ?
            JOIN temp_groups tg
              ON tg.id = tz.group_id
            WHERE sh.store_id = ?
              AND sh.is_active = TRUE
              AND a.is_active = TRUE
              AND tz.is_active = TRUE
            ORDER BY
              a.sort_order, a.name,
              tg.sort_order, tg.code,
              tz.sort_order, tz.name,
              sh.sort_order, sh.code
            """,
            (company_id, selected_store_id),
        ).fetchall()

        # ---- for each shelf, load mapped items in order ----
        for sh in shelf_rows:
            shelf_id = sh["shelf_id"]

            mapped_items = db.execute(
                """
                SELECT
                  m.item_id,
                  m.sort_order,
                  i.code AS item_code,
                  i.name AS item_name,
                  i.is_internal
                FROM item_shelf_map m
                JOIN items i
                  ON i.id = m.item_id
                WHERE m.store_id = ?
                  AND m.shelf_id = ?
                  AND m.is_active = TRUE
                ORDER BY m.sort_order, i.code
                """,
                (selected_store_id, shelf_id),
            ).fetchall()

            rows_for_ui = []

            for r in mapped_items:
                item_id = r["item_id"]

                # ---------- system stock calculation ----------
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
                    pur_row = db.execute(
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
                    pur_row = db.execute(
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

                pur_qty = pur_row["qty"] if pur_row else 0
                system_qty = opening_qty + pur_qty

                # ---------- unit price (weighted avg) ----------
                price_row = db.execute(
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

                unit_price = price_row["unit_price"] or 0.0
                stock_amount = system_qty * unit_price

                # ---------- counted qty for this count_date ----------
                counted_row = db.execute(
                    """
                    SELECT counted_qty
                    FROM stock_counts
                    WHERE store_id   = ?
                      AND item_id    = ?
                      AND count_date = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (selected_store_id, item_id, count_date),
                ).fetchone()

                count_qty = counted_row["counted_qty"] if counted_row else None

                # policy: show if system_qty>0 OR internal
                is_internal = (r["is_internal"] == 1)
                if system_qty > 0 or is_internal:
                    rows_for_ui.append(
                        {
                            "item_id": item_id,
                            "code": r["item_code"],
                            "name": r["item_name"],
                            "system_qty": system_qty,
                            "unit_price": unit_price,
                            "stock_amount": stock_amount,
                            "count_qty": count_qty,
                        }
                    )

            # You may choose to hide empty shelves; for rollout, it can be useful to show them.
            # Here: hide shelves that have no visible rows.
            if not rows_for_ui:
                continue

            groups.append(
                {
                    "shelf_id": shelf_id,
                    "area_name": sh["area_name"],
                    "temp_zone": sh["temp_zone_name"],        # displayed string
                    "temp_group": sh["temp_group_code"],      # optional for UI
                    "shelf_code": sh["shelf_code"],
                    "shelf_name": sh["shelf_name"],
                    "rows": rows_for_ui,
                }
            )

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

        if not store_id or not shelf_id or not isinstance(item_ids, list) or not item_ids:
            return jsonify({"ok": False, "error": "missing params"}), 400

        # Update sort_order for items that already exist in item_shelf_map
        # (Assumes mapping rows exist. If you want auto-create, tell me and I’ll add UPSERT.)
        for idx, item_id in enumerate(item_ids, start=1):
            db.execute(
                """
                UPDATE item_shelf_map
                   SET sort_order = ?
                 WHERE store_id = ?
                   AND shelf_id = ?
                   AND item_id = ?
                """,
                (idx, store_id, shelf_id, item_id),
            )

        db.commit()
        return jsonify({"ok": True})
