# views/inventory_locations.py

from flask import (
    render_template,
    request,
    jsonify,
)


def init_inventory_location_views(app, get_db):
    """
    Inventory item locations (store -> area -> temp zone -> shelf).

    Uses:
      - stores.company_id
      - store_areas
      - temp_groups / temp_zones
      - store_shelves
      - item_shelf_map
    """

    # ----------------------------------------
    # GET: Inventory Locations
    # /inventory/locations
    # ----------------------------------------
    @app.route("/inventory/locations", methods=["GET"])
    def inventory_locations():
        db = get_db()

        stores = db.execute(
            "SELECT id, name FROM stores ORDER BY code"
        ).fetchall()

        store_id = request.args.get("store_id") or ""
        selected_store_id = int(store_id) if store_id else None

        items = []

        if selected_store_id:
            # One row per (item x shelf). We'll group into item -> locations.
            rows = db.execute(
                """
                SELECT
                  i.id   AS item_id,
                  i.code AS item_code,
                  i.name AS item_name,
                  i.is_internal,

                  a.name  AS area_name,
                  tg.code AS temp_group_code,
                  tz.name AS temp_zone_name,
                  sh.code AS shelf_code,
                  sh.name AS shelf_name,

                  a.sort_order  AS area_sort,
                  tg.sort_order AS group_sort,
                  tz.sort_order AS zone_sort,
                  sh.sort_order AS shelf_sort,
                  m.sort_order  AS item_sort
                FROM item_shelf_map m
                JOIN items i            ON i.id = m.item_id
                JOIN store_shelves sh   ON sh.id = m.shelf_id AND sh.store_id = m.store_id
                JOIN store_areas a      ON a.id = sh.area_id
                JOIN temp_zones tz      ON tz.id = sh.temp_zone_id
                JOIN temp_groups tg     ON tg.id = tz.group_id
                WHERE m.store_id = ?
                  AND m.is_active = TRUE
                  AND sh.is_active = TRUE
                  AND a.is_active = TRUE
                  AND tz.is_active = TRUE
                ORDER BY
                  i.code,
                  a.sort_order, tg.sort_order, tz.sort_order, sh.sort_order,
                  m.sort_order
                """,
                (selected_store_id,),
            ).fetchall()

            by_item = {}

            for r in rows:
                item_id = r["item_id"]

                if item_id not in by_item:
                    by_item[item_id] = {
                        "item_id": item_id,
                        "code": r["item_code"],
                        "name": r["item_name"],
                        "is_internal": r["is_internal"],
                        "locations": [],
                    }

                by_item[item_id]["locations"].append(
                    {
                        "area": r["area_name"],
                        "temp_group": r["temp_group_code"],
                        "temp_zone": r["temp_zone_name"],
                        "shelf_code": r["shelf_code"],
                        "shelf_name": r["shelf_name"],
                    }
                )

            items = list(by_item.values())

        return render_template(
            "inventory/inventory_locations.html",
            stores=stores,
            selected_store_id=selected_store_id,
            items=items,
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
