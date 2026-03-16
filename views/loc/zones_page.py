# views/inventory_locations/zones_page.py

from flask import render_template, request
from utils.access_scope import (
    get_accessible_stores,
    normalize_accessible_store_id,
)

def init_location_zones_page(app, get_db):
    """
    Temp zone master.
    Endpoint name must be 'zone_master' because templates call url_for('zone_master', ...)
    """

    @app.route("/inventory/zones", methods=["GET"], endpoint="zone_master")
    def zone_master():
        db = get_db()

        selected_store_id = normalize_accessible_store_id(
        request.args.get("store_id")
        )
        store_id = str(selected_store_id) if selected_store_id else ""

        mst_stores = get_accessible_stores()

        # IMPORTANT:
        # Your current schema uses store_shelves.temp_zone TEXT with CHECK (AMB/CHILL/FREEZE).
        # So "Zone Master" is effectively a fixed list for now.
        # We'll just show what exists in shelves for the store.
        zones = []
        if selected_store_id:
            zones = db.execute(
                """
                SELECT DISTINCT temp_zone
                FROM store_shelves
                WHERE store_id = %s AND is_active = TRUE
                ORDER BY temp_zone
                """,
                (selected_store_id,),
            ).fetchall()

        return render_template(
            "inventory/zone_master.html",
            mst_stores=mst_stores,
            selected_store_id=selected_store_id,
            zones=zones,
        )
