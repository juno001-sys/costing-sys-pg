# views/loc/shelves_page.py

from flask import render_template, request, redirect, url_for, flash


def init_location_shelves_page(app, get_db):
    @app.route("/inventory/shelves", methods=["GET", "POST"], endpoint="shelf_master")
    def shelf_master():
        db = get_db()

        # store selector
        store_id = request.values.get("store_id")  # works for GET+POST
        selected_store_id = int(store_id) if store_id else None

        mst_stores = db.execute(
            "SELECT id, name FROM mst_stores ORDER BY code"
        ).fetchall()

        # For area dropdown / labels (store-scoped enabled areas)
        store_areas = []
        if selected_store_id:
            store_areas = db.execute(
                """
                SELECT
                  sam.id AS store_area_map_id,
                  COALESCE(sam.display_name, am.name) AS area_name,
                  sam.sort_order
                FROM store_area_map sam
                JOIN area_master am ON am.id = sam.area_id
                WHERE sam.store_id = %s
                  AND COALESCE(sam.is_active, TRUE) = TRUE
                ORDER BY sam.sort_order, area_name
                """,
                (selected_store_id,),
            ).fetchall()

        # Temp zone list (store-scoped naming + order)
        temp_zones = []
        if selected_store_id:
            temp_zones = db.execute(
                """
                SELECT code, COALESCE(display_name, code) AS name, sort_order
                FROM store_temp_zones
                WHERE store_id = %s
                  AND COALESCE(is_active, TRUE) = TRUE
                ORDER BY sort_order, code
                """,
                (selected_store_id,),
            ).fetchall()

        # POST: update shelves
        if request.method == "POST":
            if not selected_store_id:
                flash("missing store_id")
                return redirect(url_for("shelf_master"))

            shelf_ids = request.form.getlist("shelf_ids")

            for sid in shelf_ids:
                use_flag = request.form.get(f"use_shelf_{sid}") == "on"
                name = request.form.get(f"name_{sid}") or ""
                sort_order = request.form.get(f"sort_{sid}") or "100"

                temp_zone = request.form.get(f"temp_zone_{sid}") or None

                try:
                    sort_order = int(sort_order)
                except ValueError:
                    sort_order = 100

                cur = db.execute(
                    """
                    UPDATE inv_store_shelves
                       SET name = %s,
                           sort_order = %s,
                           is_active = %s,
                           temp_zone = %s,
                           updated_at = NOW()
                       WHERE id = %s
                        AND store_id = %s
                    """,
                    (name, sort_order, use_flag,temp_zone, sid, selected_store_id),
                )

                print("UPDATE shelf", sid, "rowcount=", cur.rowcount, "temp_zone=", temp_zone)

            db.commit()
            flash("Updated shelves.")
            return redirect(url_for("shelf_master", store_id=selected_store_id))

        # GET: list shelves
        shelves = []
        if selected_store_id:
            shelves = db.execute(
                """
                SELECT
                  sh.id,
                  sh.store_id,
                  sh.store_area_map_id,
                  COALESCE(sam.display_name, am.name) AS area_name,
                  sh.temp_zone,
                  sh.code,
                  COALESCE(sh.name, '') AS name,
                  sh.sort_order,
                  COALESCE(sh.is_active, TRUE) AS is_active
                FROM store_shelves sh
                LEFT JOIN store_area_map sam ON sam.id = sh.store_area_map_id
                LEFT JOIN area_master am ON am.id = sam.area_id
                WHERE sh.store_id = %s
                ORDER BY sh.temp_zone, sam.sort_order, sh.sort_order, sh.code
                """,
                (selected_store_id,),
            ).fetchall()

        return render_template(
            "loc/shelf_master.html",
            mst_stores=mst_stores,
            selected_store_id=selected_store_id,
            store_areas=store_areas,
            temp_zones=temp_zones,
            shelves=shelves,
        )
