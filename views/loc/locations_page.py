# views/loc/locations_page.py
from flask import render_template, request, jsonify


def init_location_page(app, get_db):

    @app.get("/api/locations/areas")
    def api_locations_areas():
        db = get_db()
        store_id = request.args.get("store_id", type=int)
        temp_zone = (request.args.get("temp_zone") or "").strip()

        if not store_id or not temp_zone:
            return jsonify([])

        rows = db.execute(
            """
            SELECT DISTINCT
              sam.id AS store_area_map_id,
              COALESCE(sam.display_name, am.name) AS area_name,
              sam.sort_order
            FROM inv_store_shelves sh
            JOIN store_area_map sam ON sam.id = sh.store_area_map_id
            JOIN area_master am ON am.id = sam.area_id
            WHERE sh.store_id = %s
              AND COALESCE(sh.is_active, TRUE) = TRUE
              AND sh.temp_zone = %s
              AND COALESCE(sam.is_active, TRUE) = TRUE
            ORDER BY sam.sort_order, area_name
            """,
            (store_id, temp_zone),
        ).fetchall()

        return jsonify([
            {"id": r["store_area_map_id"], "name": r["area_name"]}
            for r in rows
        ])

    @app.get("/api/locations/shelves")
    def api_locations_shelves():
        db = get_db()
        store_id = request.args.get("store_id", type=int)
        area_id = request.args.get("area_id", type=int)
        temp_zone = (request.args.get("temp_zone") or "").strip()

        if not store_id or not area_id:
            return jsonify([])

        params = [store_id, area_id]
        tz_sql = ""
        if temp_zone:
            tz_sql = "AND sh.temp_zone = %s"
            params.append(temp_zone)

        rows = db.execute(
            f"""
            SELECT
              sh.id,
              sh.code,
              COALESCE(sh.name, sh.code) AS name
            FROM inv_store_shelves sh
            WHERE sh.store_id = %s
              AND sh.store_area_map_id = %s
              AND COALESCE(sh.is_active, TRUE) = TRUE
              {tz_sql}
            ORDER BY sh.sort_order, sh.code
            """,
            tuple(params),
        ).fetchall()

        return jsonify([
            {"id": r["id"], "name": r["name"]}
            for r in rows
        ])

    @app.get("/api/locations/shelves_all")
    def api_locations_shelves_all():
        db = get_db()
        store_id = request.args.get("store_id", type=int)
        if not store_id:
            return jsonify([])

        rows = db.execute(
            """
            SELECT
              sh.id,
              sh.store_area_map_id AS area_id,
              sh.temp_zone,
              COALESCE(sh.name, sh.code) AS name
            FROM inv_store_shelves sh
            WHERE sh.store_id = %s
              AND COALESCE(sh.is_active, TRUE) = TRUE
            ORDER BY sh.sort_order, sh.code
            """,
            (store_id,),
        ).fetchall()

        return jsonify([
            {
                "id": r["id"],
                "area_id": r["area_id"],
                "temp_zone": r["temp_zone"],
                "name": r["name"],
            }
            for r in rows
        ])

    @app.route("/inventory/locations", methods=["GET"])
    def inventory_locations():
        db = get_db()

        mst_stores = db.execute(
            "SELECT id, name FROM mst_stores ORDER BY code"
        ).fetchall()

        store_id = request.args.get("store_id") or ""
        selected_store_id = int(store_id) if store_id else None

        areas = []
        if selected_store_id:
            areas = db.execute(
                """
                SELECT
                  sam.id  AS store_area_map_id,
                  am.name AS area_name
                FROM store_area_map sam
                JOIN area_master am ON am.id = sam.area_id
                WHERE sam.store_id = %s
                  AND COALESCE(sam.is_active, TRUE) = TRUE
                ORDER BY sam.sort_order, am.name
                """,
                (selected_store_id,),
            ).fetchall()

        mst_items = []
        if selected_store_id:
            mst_items = db.execute(
                """
                SELECT DISTINCT
                  i.id,
                  i.code,
                  i.name,

                  i.temp_zone AS temp_zone,

                  CASE
                    WHEN pref.temp_zone IS NOT NULL AND pref.temp_zone <> '' THEN pref.temp_zone
                    WHEN i.temp_zone IN ('常温','AMB') THEN 'AMB'
                    WHEN i.temp_zone IN ('冷蔵','CHILL') THEN 'CHILL'
                    WHEN i.temp_zone IN ('冷凍','FREEZE') THEN 'FREEZE'
                    ELSE 'AMB'
                  END AS temp_zone_norm,

                  pref.temp_zone         AS pref_temp_zone,
                  pref.store_area_map_id AS pref_store_area_map_id,

                  m.shelf_id AS shelf_id,
                  sh.name    AS shelf_name,

                  am.name AS shelf_area_name,
                  sam.id  AS shelf_store_area_map_id,

                  COALESCE(pref.store_area_map_id, sam.id) AS area_store_area_map_id
                FROM mst_items i
                  LEFT JOIN purchases p
                    ON p.item_id = i.id
                   AND p.store_id = %s
                   AND p.is_deleted = 0

                  LEFT JOIN item_location_prefs pref
                    ON pref.store_id = %s
                   AND pref.item_id  = i.id

                  LEFT JOIN item_shelf_map m
                    ON m.store_id = %s
                   AND m.item_id  = i.id
                   AND m.is_active = TRUE

                  LEFT JOIN store_shelves sh
                    ON sh.id = m.shelf_id

                  LEFT JOIN store_area_map sam
                    ON sam.id = sh.store_area_map_id
                  LEFT JOIN area_master am
                    ON am.id = sam.area_id

                WHERE i.is_internal = 1
                   OR p.id IS NOT NULL
                ORDER BY 6,7
                """,
                (selected_store_id, selected_store_id, selected_store_id),
            ).fetchall()

        return render_template(
            "loc/locations.html",
            mst_stores=mst_stores,
            selected_store_id=selected_store_id,
            areas=areas,
            mst_items=mst_items,
            items=mst_items,
            temp_zones=["AMB", "CHILL", "FREEZE"],
        )