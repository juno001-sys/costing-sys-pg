from flask import render_template, request, redirect, url_for, flash

def init_admin_store_config(app, get_db):

    @app.route("/inventory/store-temp-zones", methods=["GET"], endpoint="store_temp_zones_admin")
    def store_temp_zones_admin():
        db = get_db()
        store_id = request.args.get("store_id") or ""
        selected_store_id = int(store_id) if store_id else None

        stores = db.execute("SELECT id, code, name FROM mst_stores ORDER BY code").fetchall()

        tz_master = db.execute(
            """
            SELECT code, default_name, sort_order, is_active
            FROM temp_zone_master
            WHERE COALESCE(is_active, TRUE) = TRUE
            ORDER BY sort_order, code
            """
        ).fetchall()

        store_tz = {}
        if selected_store_id:
            rows = db.execute(
                """
                SELECT code,
                       COALESCE(display_name,'') AS display_name,
                       sort_order,
                       is_active
                FROM store_temp_zones
                WHERE store_id = %s
                ORDER BY sort_order, code
                """,
                (selected_store_id,),
            ).fetchall()
            store_tz = {r["code"]: r for r in rows}

        return render_template(
            "loc/store_temp_zones_admin.html",
            stores=stores,
            selected_store_id=selected_store_id,
            tz_master=tz_master,
            store_tz=store_tz,
        )

    @app.route("/inventory/store-temp-zones/save", methods=["POST"], endpoint="store_temp_zones_admin_save")
    def store_temp_zones_admin_save():
        db = get_db()
        store_id = request.form.get("store_id")
        if not store_id:
            flash("missing store_id")
            return redirect(url_for("store_temp_zones_admin"))

        codes = request.form.getlist("tz_codes")
        for code in codes:
            use_flag = request.form.get(f"use_tz_{code}") == "on"
            display_name = request.form.get(f"tz_name_{code}") or None
            sort_order = request.form.get(f"tz_sort_{code}") or "100"
            try:
                sort_order = int(sort_order)
            except ValueError:
                sort_order = 100

            db.execute(
                """
                INSERT INTO inv_store_temp_zones (store_id, code, display_name, sort_order, is_active, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (store_id, code)
                DO UPDATE SET
                  display_name = EXCLUDED.display_name,
                  sort_order = EXCLUDED.sort_order,
                  is_active = EXCLUDED.is_active,
                  updated_at = NOW()
                """,
                (store_id, code, display_name, sort_order, use_flag),
            )

        db.commit()
        flash("Updated store temp zones.")
        return redirect(url_for("store_temp_zones_admin", store_id=store_id))


    @app.route("/inventory/store-areas", methods=["GET"], endpoint="store_areas_admin")
    def store_areas_admin():
        db = get_db()
        store_id = request.args.get("store_id") or ""
        selected_store_id = int(store_id) if store_id else None

        stores = db.execute("SELECT id, code, name FROM mst_stores ORDER BY code").fetchall()

        areas_master = db.execute(
            """
            SELECT id, name, sort_order, COALESCE(is_active, TRUE) AS is_active
            FROM area_master
            ORDER BY sort_order, name
            """
        ).fetchall()

        map_by_area = {}
        if selected_store_id:
            mappings = db.execute(
                """
                SELECT area_id,
                       COALESCE(display_name,'') AS display_name,
                       COALESCE(sort_order, 100) AS sort_order,
                       COALESCE(is_active, TRUE) AS is_active
                FROM store_area_map
                WHERE store_id = %s
                """,
                (selected_store_id,),
            ).fetchall()
            map_by_area = {m["area_id"]: m for m in mappings}

        return render_template(
            "loc/store_areas_admin.html",
            stores=stores,
            selected_store_id=selected_store_id,
            areas_master=areas_master,
            map_by_area=map_by_area,
        )

    @app.route("/inventory/store-areas/save", methods=["POST"], endpoint="store_areas_admin_save")
    def store_areas_admin_save():
        db = get_db()
        store_id = request.form.get("store_id")
        if not store_id:
            flash("missing store_id")
            return redirect(url_for("store_areas_admin"))

        area_ids = request.form.getlist("area_ids")
        for area_id in area_ids:
            use_flag = request.form.get(f"use_area_{area_id}") == "on"
            display_name = request.form.get(f"display_name_{area_id}") or None
            sort_order = request.form.get(f"sort_order_{area_id}") or "100"
            try:
                sort_order = int(sort_order)
            except ValueError:
                sort_order = 100

            # Upsert into base table
            db.execute(
                """
                INSERT INTO inv_store_area_map (store_id, area_id, display_name, sort_order, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (store_id, area_id)
                DO UPDATE SET
                  display_name = EXCLUDED.display_name,
                  sort_order = EXCLUDED.sort_order,
                  is_active = EXCLUDED.is_active,
                  updated_at = NOW()
                """,
                (store_id, area_id, display_name, sort_order, use_flag),
            )

        db.commit()
        flash("Updated store areas.")
        return redirect(url_for("store_areas_admin", store_id=store_id))
