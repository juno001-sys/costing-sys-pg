# views/loc/locations_api.py

from flask import request, jsonify


def init_location_api(app, get_db):
    @app.route("/inventory/api/shelves", methods=["GET"], endpoint="inventory_api_shelves")
    def inventory_api_shelves():
        db = get_db()

        store_id = request.args.get("store_id")
        store_area_map_id = request.args.get("store_area_map_id")
        temp_zone = request.args.get("temp_zone")

        if not store_id:
            return jsonify({"ok": False, "error": "missing store_id"}), 400

        params = [store_id]
        where = ["sh.store_id = %s", "COALESCE(sh.is_active, TRUE) = TRUE"]

        if store_area_map_id:
            where.append("sh.store_area_map_id = %s")
            params.append(store_area_map_id)

        if temp_zone:
            where.append("sh.temp_zone = %s")
            params.append(temp_zone)

        rows = db.execute(
            f"""
            SELECT
              sh.id,
              sh.code,
              COALESCE(sh.name, '') AS name,
              sh.store_area_map_id,
              am.name AS area_name,
              sh.temp_zone,
              sh.sort_order
            FROM store_shelves sh
            LEFT JOIN store_area_map sam
              ON sam.id = sh.store_area_map_id
            LEFT JOIN area_master am
              ON am.id = sam.area_id
            WHERE {' AND '.join(where)}
            ORDER BY
              sh.temp_zone,
              sam.sort_order,
              sh.sort_order,
              sh.code
            """,
            tuple(params),
        ).fetchall()

        return jsonify({"ok": True, "shelves": [dict(r) for r in rows]})