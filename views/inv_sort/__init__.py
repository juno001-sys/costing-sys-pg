from flask import abort, redirect, request, url_for

from .sort_config import save_item_sort_config


def init_inventory_sort_views(app, get_db, SORTABLE_KEYS: set[str]):
    """
    Register sorting-config routes for inventory_count.
    Designed to be called from views/inventory.py (so app.py stays unchanged).
    """

    @app.route("/inventory/item-sort-config", methods=["POST"], endpoint="inventory_item_sort_config_save")
    def inventory_item_sort_config_save():
        store_id = int(request.form["store_id"])
        count_date = request.form.get("count_date")  # keep UX consistent if you pass it

        sort_key = request.form["sort_key"]
        sort_dir = request.form["sort_dir"]
        sort_key2 = request.form.get("sort_key2") or None
        sort_dir2 = request.form.get("sort_dir2") or None

        if sort_key not in SORTABLE_KEYS:
            abort(400)
        if sort_dir not in ("asc", "desc"):
            abort(400)
        if sort_key2 and sort_key2 not in SORTABLE_KEYS:
            abort(400)
        if sort_dir2 and sort_dir2 not in ("asc", "desc"):
            abort(400)

        cfg = {
            "sort_key": sort_key,
            "sort_dir": sort_dir,
            "sort_key2": sort_key2,
            "sort_dir2": sort_dir2,
        }

        conn = get_db()
        save_item_sort_config(conn, store_id, cfg)
        conn.commit()

        return redirect(url_for("inventory_count", store_id=store_id, count_date=count_date))
