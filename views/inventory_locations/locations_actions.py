# views/inventory_locations/locations_actions.py

from flask import request, redirect, url_for, flash, jsonify


def init_inventory_locations_actions(app, get_db):
    @app.route("/inventory/locations/save", methods=["POST"])
    def inventory_locations_save():
        db = get_db()

        store_id = request.form.get("store_id")
        if not store_id:
            flash("missing store_id")
            return redirect(url_for("inventory_locations"))

        # Expect fields like:
        # shelf_id_<item_id> = <shelf_id>
        # Example: shelf_id_123 = 55
        item_ids = request.form.getlist("item_ids")

        for item_id in item_ids:
            shelf_id = request.form.get(f"shelf_id_{item_id}") or None

            # deactivate current mappings for this item in this store
            db.execute(
                """
                UPDATE item_shelf_map
                   SET is_active = FALSE
                 WHERE store_id = %s
                   AND item_id  = %s
                """,
                (store_id, item_id),
            )

            if shelf_id:
                # upsert-like: insert new active mapping
                db.execute(
                    """
                    INSERT INTO item_shelf_map (store_id, shelf_id, item_id, sort_order, is_active)
                    VALUES (%s, %s, %s, 100, TRUE)
                    """,
                    (store_id, shelf_id, item_id),
                )

        db.commit()
        flash("Saved inventory locations.")
        return redirect(url_for("inventory_locations", store_id=store_id))

    @app.route("/inventory/reorder-items", methods=["POST"])
    def inventory_reorder_items():
        db = get_db()
        payload = request.get_json(force=True)

        store_id = payload.get("store_id")
        shelf_id = payload.get("shelf_id")
        item_ids = payload.get("item_ids") or []

        if not store_id or not shelf_id or not isinstance(item_ids, list) or not item_ids:
            return jsonify({"ok": False, "error": "missing params"}), 400

        for idx, item_id in enumerate(item_ids, start=1):
            db.execute(
                """
                UPDATE item_shelf_map
                   SET sort_order = %s
                 WHERE store_id = %s
                   AND shelf_id = %s
                   AND item_id = %s
                   AND is_active = TRUE
                """,
                (idx, store_id, shelf_id, item_id),
            )

        db.commit()
        return jsonify({"ok": True})
