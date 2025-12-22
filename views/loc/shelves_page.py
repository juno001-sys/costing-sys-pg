# views/inventory_locations/shelves_page.py

from flask import render_template, request


def init_location_shelves_page(app, get_db):
    """
    Shelf master screen (per store).
    Endpoint name must be 'shelf_master' because templates call url_for('shelf_master', ...)
    """

    @app.route("/inventory/shelves", methods=["GET"], endpoint="shelf_master")
    def shelf_master():
        db = get_db()

        store_id = request.args.get("store_id")
        selected_store_id = int(store_id) if store_id else None

        mst_stores = db.execute(
            "SELECT id, name FROM mst_stores ORDER BY code"
        ).fetchall()

        shelves = []
        if selected_store_id:
            shelves = db.execute(
                """
                SELECT
                  sh.id,
                  sh.store_id,
                  sh.area_id,
                  sh.temp_zone,
                  sh.code,
                  COALESCE(sh.name, '') AS name,
                  sh.sort_order,
                  sh.is_active
                FROM store_shelves sh
                WHERE sh.store_id = %s
                ORDER BY sh.area_id, sh.temp_zone, sh.sort_order, sh.code
                """,
                (selected_store_id,),
            ).fetchall()

        return render_template(
            "inventory/shelf_master.html",
            mst_stores=mst_stores,
            selected_store_id=selected_store_id,
            shelves=shelves,
        )
