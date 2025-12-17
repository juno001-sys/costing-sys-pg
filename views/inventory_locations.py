# views/inventory_locations.py
from flask import render_template, request, jsonify, redirect, url_for, flash

TEMP_ZONES = ["AMB", "CHILL", "FREEZE"]

def init_inventory_location_views(app, get_db):

    # ----------------------------------------
    # GET: Item -> location assignment page
    # /inventory/locations?store_id=...
    # ----------------------------------------
    @app.route("/inventory/locations", methods=["GET"])
    def inventory_locations():
        db = get_db()

        stores = db.execute(
            "SELECT id, name FROM stores ORDER BY code"
        ).fetchall()

        store_id = request.args.get("store_id") or ""
        selected_store_id = int(store_id) if store_id else None

        # areas for dropdown (store-scoped)
        areas = []
        if selected_store_id:
            areas = db.execute(
                """
                SELECT id, name
                FROM store_areas
                WHERE store_id = %s AND is_active = TRUE
                ORDER BY sort_order, name
                """,
                (selected_store_id,),
            ).fetchall()

        if not selected_store_id:
            return render_template(
                "inventory/inventory_locations.html",
                stores=stores,
                selected_store_id=None,
                items=[],
                areas=areas,
                temp_zones=TEMP_ZONES,
            )

        # Item universe = internal + purchased items for this store
        rows = db.execute(
            """
            WITH relevant_items AS (
              SELECT i.id
              FROM items i
              WHERE i.is_internal = 1

              UNION

              SELECT DISTINCT p.item_id AS id
              FROM purchases p
              JOIN items i ON i.id = p.item_id
              WHERE p.store_id = %s
                AND p.is_deleted = 0
                AND i.is_internal = 0
            )
            SELECT
              i.id   AS item_id,
              i.code AS item_code,
              i.name AS item_name,

              -- existing mapping (may be multiple)
              a.id   AS area_id,
              a.name AS area_name,
              sh.temp_zone AS temp_zone,
              sh.id  AS shelf_id,
              sh.code AS shelf_code,
              sh.name AS shelf_name,

              a.sort_order  AS area_sort,
              sh.sort_order AS shelf_sort,
              m.sort_order  AS map_sort
            FROM relevant_items ri
            JOIN items i ON i.id = ri.id
            LEFT JOIN item_shelf_map m
              ON m.item_id = i.id
             AND m.store_id = %s
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
            (selected_store_id, selected_store_id),
        ).fetchall()

        # Build one row per item, plus:
        # - locations[] (display)
        # - current selection (use first mapping as default)
        items_by_id = {}
        for r in rows:
            item_id = r["item_id"]

            if item_id not in items_by_id:
                items_by_id[item_id] = {
                    "id": item_id,
                    "code": r["item_code"],
                    "name": r["item_name"],
                    "locations": [],
                    "sel_area_id": None,
                    "sel_temp_zone": "",
                    "sel_shelf_id": None,
                }

            if r["shelf_id"]:
                items_by_id[item_id]["locations"].append(
                    {
                        "area_name": r["area_name"] or "",
                        "temp_zone": r["temp_zone"] or "",
                        "shelf_code": r["shelf_code"] or "",
                        "shelf_name": r["shelf_name"] or "",
                    }
                )
                # first mapping becomes default selection
                if items_by_id[item_id]["sel_shelf_id"] is None:
                    items_by_id[item_id]["sel_area_id"] = r["area_id"]
                    items_by_id[item_id]["sel_temp_zone"] = r["temp_zone"] or ""
                    items_by_id[item_id]["sel_shelf_id"] = r["shelf_id"]

        items = list(items_by_id.values())

        return render_template(
            "inventory/inventory_locations.html",
            stores=stores,
            selected_store_id=selected_store_id,
            items=items,
            areas=areas,
            temp_zones=TEMP_ZONES,
        )

    # ----------------------------------------
    # GET: shelves for cascading dropdown
    # /inventory/api/shelves?store_id=..&area_id=..&temp_zone=..
    # ----------------------------------------
    @app.route("/inventory/api/shelves", methods=["GET"])
    def inventory_api_shelves():
        db = get_db()
        store_id = request.args.get("store_id")
        area_id = request.args.get("area_id")
        temp_zone = request.args.get("temp_zone")

        if not store_id or not area_id or not temp_zone:
            return jsonify({"ok": False, "shelves": []})

        shelves = db.execute(
            """
            SELECT id, code, name
            FROM store_shelves
            WHERE store_id = %s
              AND area_id = %s
              AND temp_zone = %s
              AND is_active = TRUE
            ORDER BY sort_order, code
            """,
            (store_id, area_id, temp_zone),
        ).fetchall()

        return jsonify(
            {
                "ok": True,
                "shelves": [
                    {"id": s["id"], "code": s["code"], "name": s["name"] or ""}
                    for s in shelves
                ],
            }
        )

    # ----------------------------------------
    # POST: save mapping (replace mode)
    # /inventory/locations/save
    # ----------------------------------------
    @app.route("/inventory/locations/save", methods=["POST"])
    def inventory_locations_save():
        db = get_db()

        store_id = request.form.get("store_id")
        item_id = request.form.get("item_id")
        shelf_id = request.form.get("shelf_id")

        if not store_id or not item_id or not shelf_id:
            flash("__inventory.locations.save_missing__")
            return redirect(url_for("inventory_locations", store_id=store_id))

        # Replace mode: deactivate all current mappings for that item in that store
        db.execute(
            """
            UPDATE item_shelf_map
               SET is_active = FALSE
             WHERE store_id = %s
               AND item_id = %s
            """,
            (store_id, item_id),
        )

        # Insert the selected shelf mapping
        db.execute(
            """
            INSERT INTO item_shelf_map (store_id, shelf_id, item_id, sort_order, is_active)
            VALUES (%s, %s, %s, 100, TRUE)
            """,
            (store_id, shelf_id, item_id),
        )

        db.commit()
        flash("__inventory.locations.saved__")
        return redirect(url_for("inventory_locations", store_id=store_id))
