# views/loc/locations_page.py

from flask import render_template, request
from db import get_db  # only if your pattern allows; otherwise remove and use injected get_db


def init_location_page(app, get_db):
    @app.route("/inventory/locations", methods=["GET"])
    def inventory_locations():
        db = get_db()

        mst_stores = db.execute(
            "SELECT id, name FROM mst_stores ORDER BY code"
        ).fetchall()

        store_id = request.args.get("store_id") or ""
        selected_store_id = int(store_id) if store_id else None

        ZONE_MAP = {
            "常温": "AMB",
            "冷蔵": "CHILL",
            "冷凍": "FREEZE",
            "その他": "AMB",
        }

        mst_items = []
        if selected_store_id:
            # basic item list for the store (same logic style as your inventory_count)
            mst_items = db.execute(
                """
                SELECT DISTINCT
                  i.id,
                  i.code,
                  i.name
                FROM mst_items i
                LEFT JOIN purchases p
                  ON p.item_id = i.id
                 AND p.store_id = %s
                 AND p.is_deleted = 0
                WHERE i.is_internal = 1
                   OR p.id IS NOT NULL
                ORDER BY i.code
                """,
                (selected_store_id,),
            ).fetchall()

            # normalize temp_zone for UI
            for it in mst_items:
                raw = it.get("temp_zone") if hasattr(it, "get") else it["temp_zone"]
                norm = ZONE_MAP.get(raw, raw)
                try:
                    it["temp_zone_norm"] = norm
                except TypeError:
                    # if row is immutable, ignore (then we must normalize in SQL instead)
                    pass

        return render_template(
            "loc/locations.html",
            mst_stores=mst_stores,
            selected_store_id=selected_store_id,
            mst_items=mst_items,
            items=mst_items, 
            # for dropdown options (temp zones)
            temp_zones=["AMB", "CHILL", "FREEZE"],
        )
