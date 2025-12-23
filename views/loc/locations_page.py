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
                 AND p.store_id = %%s
                 AND p.is_deleted = 0
                WHERE i.is_internal = 1
                   OR p.id IS NOT NULL
                ORDER BY i.code
                """,
                (selected_store_id,),
            ).fetchall()

        return render_template(
            "inventory/locations.html",
            mst_stores=mst_stores,
            selected_store_id=selected_store_id,
            mst_items=mst_items,
            # for dropdown options (temp zones)
            temp_zones=["AMB", "CHILL", "FREEZE"],
        )
