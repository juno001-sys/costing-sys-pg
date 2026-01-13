from flask import abort, redirect, request, url_for
from .sort_config import save_sort_config

SORT_KEYS = {"item_code", "item_name"}


def init_location_sort_routes(app, get_db):
    @app.route("/inventory/locations/sort", methods=["POST"], endpoint="inventory_locations_sort_save")
    def inventory_locations_sort_save():
        store_id = int(request.form["store_id"])

        sort_key = request.form["sort_key"]
        sort_dir = request.form["sort_dir"]
        sort_key2 = request.form.get("sort_key2") or None
        sort_dir2 = request.form.get("sort_dir2") or None

        if sort_key not in SORT_KEYS:
            abort(400)
        if sort_dir not in ("asc", "desc"):
            abort(400)
        if sort_key2 and sort_key2 not in SORT_KEYS:
            abort(400)
        if sort_dir2 and sort_dir2 not in ("asc", "desc"):
            abort(400)

        db = get_db()
        save_sort_config(db, store_id, {
            "sort_key": sort_key,
            "sort_dir": sort_dir,
            "sort_key2": sort_key2,
            "sort_dir2": sort_dir2,
        })
        db.commit()

        return redirect(url_for("inventory_locations", store_id=store_id))
