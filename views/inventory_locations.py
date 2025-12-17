# views/inventory_locations.py
from flask import render_template, request

TEMP_LABEL = {"AMB": "AMB", "CHILL": "CHILL", "FREEZE": "FREEZE"}  # keep codes visible

def init_inventory_location_views(app, get_db):

    @app.route("/inventory/locations", methods=["GET"])
    def inventory_locations():
        db = get_db()

        stores = db.execute(
            "SELECT id, name FROM stores ORDER BY code"
        ).fetchall()

        store_id = request.args.get("store_id") or ""
        selected_store_id = int(store_id) if store_id else None

        if not selected_store_id:
            return render_template(
                "inventory/inventory_locations.html",
                stores=stores,
                selected_store_id=None,
                items=[],
            )

        # Item-centered query (items -> (optional) mapping -> shelf -> area)
        rows = db.execute(
            """
            SELECT
              i.id   AS item_id,
              i.code AS item_code,
              i.name AS item_name,

              a.name AS area_name,
              sh.temp_zone AS temp_zone_code,
              sh.code AS shelf_code,
              sh.name AS shelf_name,

              a.sort_order  AS area_sort,
              sh.sort_order AS shelf_sort,
              m.sort_order  AS item_sort
            FROM items i
            LEFT JOIN item_shelf_map m
              ON m.item_id = i.id
             AND m.store_id = ?
             AND m.is_active = TRUE
            LEFT JOIN store_shelves sh
              ON sh.id = m.shelf_id
             AND sh.store_id = m.store_id
             AND sh.is_active = TRUE
            LEFT JOIN store_areas a
              ON a.id = sh.area_id
             AND a.is_active = TRUE
            ORDER BY
              i.code,
              a.sort_order NULLS LAST,
              sh.temp_zone NULLS LAST,
              sh.sort_order NULLS LAST,
              m.sort_order NULLS LAST
            """,
            (selected_store_id,),
        ).fetchall()

        # Build: one row per item, with locations list
        items_by_id = {}
        for r in rows:
            item_id = r["item_id"]

            if item_id not in items_by_id:
                items_by_id[item_id] = {
                    "id": item_id,
                    "code": r["item_code"],
                    "name": r["item_name"],
                    "locations": [],
                }

            # If not mapped, shelf_code will be None -> skip location
            if r["shelf_code"]:
                items_by_id[item_id]["locations"].append(
                    {
                        "area": r["area_name"] or "",
                        "temp_zone": TEMP_LABEL.get(r["temp_zone_code"], r["temp_zone_code"] or ""),
                        "shelf_code": r["shelf_code"],
                        "shelf_name": r["shelf_name"] or "",
                    }
                )

        items = list(items_by_id.values())

        return render_template(
            "inventory/inventory_locations.html",
            stores=stores,
            selected_store_id=selected_store_id,
            items=items,
        )
