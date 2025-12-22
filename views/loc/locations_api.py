# views/inventory_locations/locations_api.py

from flask import request, jsonify


def init_location_api(app, get_db):
    @app.route("/inventory/api/shelves", methods=["GET"])
    def inventory_api_shelves():
        db = get_db()

        store_id = request.args.get("store_id")
        area_id = request.args.get("area_id")
        temp_zone = request.args.get("temp_zone")

        if not store_id:
            return jsonify({"ok": False, "error": "missing store_id"}), 400

        params = [store_id]
        where = ["sh.store_id = %s", "sh.is_active = TRUE"]

        if area_id:
            where.append("sh.area_id = %s")
            params.append(area_id)

        if temp_zone:
            where.append("sh.temp_zone = %s")
            params.append(temp_zone)

        rows = db.execute(
            f"""
            SELECT
              sh.id,
              sh.code,
              COALESCE(sh.name, '') AS name,
              sh.area_id,
              sh.temp_zone,
              sh.sort_order
            FROM store_shelves sh
            WHERE {' AND '.join(where)}
            ORDER BY sh.sort_order, sh.code
            """,
            tuple(params),
        ).fetchall()

        return jsonify({"ok": True, "shelves": [dict(r) for r in rows]})
