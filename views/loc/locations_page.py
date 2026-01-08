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

        areas = []
        if selected_store_id:
            areas = db.execute(
                """
                SELECT
                  sam.id  AS store_area_map_id,
                  am.name AS area_name
                FROM store_area_map sam
                JOIN area_master am ON am.id = sam.area_id
                WHERE sam.store_id = %s
                  AND COALESCE(sam.is_active, TRUE) = TRUE
                ORDER BY sam.sort_order, am.name
                """,
                (selected_store_id,),
            ).fetchall()

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
              i.name,
              i.temp_zone AS temp_zone,

              pref.temp_zone         AS pref_temp_zone,
              pref.store_area_map_id AS pref_store_area_map_id,

              m.shelf_id AS shelf_id,
              sh.name    AS shelf_name,

              am.name AS shelf_area_name,
              sam.id  AS shelf_store_area_map_id

            FROM mst_items i
            LEFT JOIN purchases p
              ON p.item_id = i.id
             AND p.store_id = %s
             AND p.is_deleted = 0

            LEFT JOIN item_location_prefs pref
              ON pref.store_id = %s
             AND pref.item_id  = i.id

            LEFT JOIN item_shelf_map m
              ON m.store_id = %s
             AND m.item_id  = i.id
             AND m.is_active = TRUE

            LEFT JOIN store_shelves sh
              ON sh.id = m.shelf_id

            LEFT JOIN store_area_map sam
              ON sam.id = sh.store_area_map_id
            LEFT JOIN area_master am
              ON am.id = sam.area_id

            WHERE i.is_internal = 1
               OR p.id IS NOT NULL
            ORDER BY i.code
            """,
            (selected_store_id, selected_store_id, selected_store_id),
            ).fetchall()

            # ------------------------------------------------------------
            # Normalize + defaulting rules for Operator UI
            #
            # Goal:
            # 1) Temp zone default:
            #      preference (item_location_prefs.temp_zone)
            #        -> item master (mst_items.temp_zone)
            #        -> "AMB"
            #
            # 2) Area default:
            #      preference (item_location_prefs.store_area_map_id)
            #        -> derived from current shelf (store_shelves.store_area_map_id)
            #        -> blank
            #
            # Output fields used by the template:
            #   it["temp_zone_norm"]
            #   it["area_store_area_map_id"]
            #   it["area_name"] (optional display label)
            # ------------------------------------------------------------

            ZONE_MAP = {
                "常温": "AMB",
                "冷蔵": "CHILL",
                "冷凍": "FREEZE",
                "その他": "AMB",
                None: None,
                "": None,
            }

            def _get(it, key, default=None):
                # Works for both dict rows and sqlite Row-like objects
                try:
                    return it.get(key, default)
                except AttributeError:
                    try:
                        return it[key]
                    except Exception:
                        return default

            def _set(it, key, value):
                # Some row types are immutable; this returns True/False
                try:
                    it[key] = value
                    return True
                except Exception:
                    return False

            for it in mst_items:
                # ----- A) read raw master temp_zone (could be JP or already code) -----
                raw_master_zone = _get(it, "temp_zone")

                # normalize master zone into AMB/CHILL/FREEZE
                master_norm = ZONE_MAP.get(raw_master_zone, raw_master_zone)

                # ----- B) read saved preferences (may be NULL if never configured) -----
                pref_tz = _get(it, "pref_temp_zone")                 # ex: "AMB"
                pref_area_map_id = _get(it, "pref_store_area_map_id") # ex: 12

                # ----- C) derive area from shelf (if shelf already selected) -----
                # This comes from the joined store_area_map sam.id AS shelf_store_area_map_id
                derived_area_map_id = _get(it, "shelf_store_area_map_id")

                # ----- D) decide final defaults (your required rules) -----
                # 1) temp zone: preference -> master -> AMB
                final_tz = pref_tz or master_norm or "AMB"

                # 2) area: preference -> derived -> blank
                # keep as "" (empty string) for template comparison with <option value="">
                final_area_map_id = pref_area_map_id or derived_area_map_id or ""

                # Optional display name (for read-only display or debugging)
                area_name = _get(it, "shelf_area_name") or ""

                # ----- E) write back for template use -----
                ok1 = _set(it, "temp_zone_norm", final_tz)
                ok2 = _set(it, "area_store_area_map_id", final_area_map_id)
                ok3 = _set(it, "area_name", area_name)

                # If rows are immutable, you must normalize in SQL instead.
                if not (ok1 and ok2 and ok3):
                    # keep a visible hint in logs if you want
                    # print("Row is immutable; move normalization into SQL.")
                    pass

            

        return render_template(
            "loc/locations.html",
            mst_stores=mst_stores,
            selected_store_id=selected_store_id,
            areas=areas,
            mst_items=mst_items,
            items=mst_items, 
            # for dropdown options (temp zones)
            temp_zones=["AMB", "CHILL", "FREEZE"],
        )
