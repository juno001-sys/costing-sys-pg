from flask import request, jsonify


def init_item_location_api(app, get_db):
    @app.route("/inventory/api/item-location", methods=["GET"], endpoint="inventory_api_item_location")
    def inventory_api_item_location():
        db = get_db()

        store_id = request.args.get("store_id")
        item_id = request.args.get("item_id")

        if not store_id or not item_id:
            return jsonify({"ok": False, "error": "missing store_id/item_id"}), 400

        row = db.execute(
            """
            SELECT
              -- temp zone: pref -> item master -> default
              CASE
                WHEN pref.temp_zone IS NOT NULL AND pref.temp_zone <> '' THEN pref.temp_zone
                WHEN i.temp_zone IN ('常温','AMB') THEN 'AMB'
                WHEN i.temp_zone IN ('冷蔵','CHILL') THEN 'CHILL'
                WHEN i.temp_zone IN ('冷凍','FREEZE') THEN 'FREEZE'
                ELSE 'AMB'
              END AS temp_zone,

              COALESCE(sam.display_name, am.name) AS area_name,
              sh.code AS shelf_code,
              COALESCE(sh.name,'') AS shelf_name

            FROM mst_items i
            LEFT JOIN item_location_prefs pref
              ON pref.store_id = %s
             AND pref.item_id  = %s

            LEFT JOIN item_shelf_map m
              ON m.store_id = %s
             AND m.item_id  = %s
             AND m.is_active = TRUE

            LEFT JOIN store_shelves sh
              ON sh.id = m.shelf_id

            LEFT JOIN store_area_map sam
              ON sam.id = sh.store_area_map_id
            LEFT JOIN area_master am
              ON am.id = sam.area_id

            WHERE i.id = %s
            LIMIT 1
            """,
            (store_id, item_id, store_id, item_id, item_id),
        ).fetchone()

        if not row:
            return jsonify({"ok": True, "temp_zone": None, "area_name": None, "shelf_code": None, "shelf_name": None})

        return jsonify({
            "ok": True,
            "temp_zone": row["temp_zone"],
            "area_name": row["area_name"],
            "shelf_code": row["shelf_code"],
            "shelf_name": row["shelf_name"],
        })
