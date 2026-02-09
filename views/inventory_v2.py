# views/inventory_v2.py

import time
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash
from views.reports.audit_log import log_event


def get_latest_stock_count_dates(db, store_id, limit=3):
    rows = db.execute(
        """
        SELECT DISTINCT count_date
        FROM stock_counts
        WHERE store_id = %s
        ORDER BY count_date DESC
        LIMIT %s
        """,
        (store_id, limit),
    ).fetchall()
    return [r["count_date"] for r in rows]


def init_inventory_views_v2(app, get_db):
    @app.route("/inventory/count_v2", methods=["GET", "POST"], endpoint="inventory_count_v2")
    def inventory_count_v2():
        db = get_db()

        # 店舗一覧
        stores = db.execute(
            """
            SELECT id, code, name
            FROM mst_stores
            WHERE COALESCE(is_active, 1) = 1
            ORDER BY code, id
            """
        ).fetchall()

        today = datetime.today().strftime("%Y-%m-%d")

        # -----------------------------
        # POST：棚卸し登録
        # -----------------------------
        if request.method == "POST":
            store_id = request.form.get("store_id") or None
            count_date = request.form.get("count_date") or today

            if not store_id:
                flash("店舗を選択してください。")
                return redirect(url_for("inventory_count_v2"))

            row_count = int(request.form.get("row_count", 0))
            
            inserted_rows = 0

            for i in range(1, row_count + 1):
                item_id = request.form.get(f"item_id_{i}")
                system_qty = request.form.get(f"system_qty_{i}")
                counted_qty = request.form.get(f"count_qty_{i}")

                if not item_id:
                    continue
                if counted_qty is None or counted_qty == "":
                    continue

                try:
                    sys_val = int(system_qty or 0)
                    cnt_val = int(counted_qty or 0)
                except ValueError:
                    continue

                diff = cnt_val - sys_val

                db.execute(
                    """
                    INSERT INTO stock_counts
                        (store_id, item_id, count_date,
                         system_qty, counted_qty, diff_qty, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        store_id,
                        item_id,
                        count_date,
                        sys_val,
                        cnt_val,
                        diff,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
            
                inserted_rows += 1  # NEW

            #  NEW: one audit log per submit (lightweight)
            try:
                batch_id = f"{store_id}:{count_date}"
                log_event(
                    db,
                    action="SUBMIT",
                    module="inv",
                    entity_table="stock_counts",
                    entity_id=batch_id,
                    message=f"Inventory count submitted (v2) {count_date}",
                    store_id=int(store_id),
                    status_code=200,
                    meta={
                        "count_date": str(count_date),
                        "row_count": int(row_count),
                        "inserted_rows": int(inserted_rows),
                        "version": "v2",
                    },
                )
            except Exception:
                pass

            db.commit()
            flash("棚卸し結果を登録しました。")
            return redirect(url_for("inventory_count_v2", store_id=store_id, count_date=count_date))

        # -----------------------------
        # GET：表示
        # -----------------------------
        store_id = request.args.get("store_id") or ""
        count_date = request.args.get("count_date") or today

        mst_items = []
        selected_store_id = int(store_id) if store_id else None

        latest_dates = []
        latest_date = None
        if selected_store_id:
            latest_dates = get_latest_stock_count_dates(db, selected_store_id, limit=3)
            latest_date = latest_dates[0] if latest_dates else None

        if not store_id:
            return render_template(
                "inv/inventory_count_v2.html",
                stores=stores,
                selected_store_id=selected_store_id,
                count_date=count_date,
                items=[],
                mst_items=[],
                latest_date=latest_date,
                latest_dates=latest_dates,
            )

        t0 = time.perf_counter()

        # =========================================================
        # 1) base_rows: locations-aware ordered item list (FAST)
        # =========================================================
        base_rows = db.execute(
            """
            SELECT
              i.id   AS item_id,
              i.code AS item_code,
              i.name AS item_name,

              -- temp zone preference: prefs -> item master -> default
              COALESCE(NULLIF(pref.temp_zone, ''), NULLIF(i.temp_zone, ''), 'その他') AS tz_raw,

              -- shelf / area (from locations)
              m.shelf_id,
              COALESCE(m.sort_order, 9999) AS item_sort_order,

              sh.code AS shelf_code,
              sh.name AS shelf_name,
              COALESCE(sh.sort_order, 9999) AS shelf_sort_order,

              COALESCE(sam.sort_order, 9999) AS area_sort_order,
              COALESCE(am.name, '') AS area_name,

              i.is_internal
            FROM mst_items i

            LEFT JOIN item_location_prefs pref
              ON pref.store_id = %s
             AND pref.item_id  = i.id

            LEFT JOIN item_shelf_map m
              ON m.store_id   = %s
             AND m.item_id    = i.id
             AND m.is_active  = TRUE

            LEFT JOIN store_shelves sh
              ON sh.id = m.shelf_id

            LEFT JOIN store_area_map sam
              ON sam.id = sh.store_area_map_id

            LEFT JOIN area_master am
              ON am.id = sam.area_id

            WHERE
              pref.item_id IS NOT NULL
              OR m.item_id IS NOT NULL
              OR i.is_internal = 1
              OR EXISTS (
                SELECT 1
                FROM purchases p
                WHERE p.store_id = %s
                  AND p.item_id = i.id
                  AND p.is_deleted = 0
                  AND p.delivery_date <= %s
              )

            ORDER BY
              CASE
                WHEN COALESCE(NULLIF(pref.temp_zone, ''), NULLIF(i.temp_zone, ''), 'その他') IN ('冷凍','FREEZE') THEN 1
                WHEN COALESCE(NULLIF(pref.temp_zone, ''), NULLIF(i.temp_zone, ''), 'その他') IN ('冷蔵','CHILL')  THEN 2
                WHEN COALESCE(NULLIF(pref.temp_zone, ''), NULLIF(i.temp_zone, ''), 'その他') IN ('常温','AMB')    THEN 3
                ELSE 9
              END,
              COALESCE(sam.sort_order, 9999),
              COALESCE(am.name, ''),
              COALESCE(sh.sort_order, 9999),
              COALESCE(sh.code, ''),
              COALESCE(m.sort_order, 9999),
              i.code;
            """,
            (store_id, store_id, store_id, count_date),
        ).fetchall()

        print(f"[V2-1] 1 base_rows elapsed: {time.perf_counter() - t0:.3f}s rows={len(base_rows)}")

        item_ids = [r["item_id"] for r in base_rows]
        if not item_ids:
            item_ids = []

        # =========================================================
        # 2) Batch: last count (opening_qty + last_count_date)
        # =========================================================
        t1 = time.perf_counter()
        last_rows = db.execute(
            """
            SELECT DISTINCT ON (item_id)
              item_id,
              count_date AS last_count_date,
              counted_qty AS opening_qty
            FROM stock_counts
            WHERE store_id = %s
              AND item_id = ANY(%s)
              AND count_date <= %s
            ORDER BY item_id, count_date DESC, id DESC
            """,
            (store_id, item_ids, count_date),
        ).fetchall()

        last_map = {}
        for r in last_rows:
            last_map[r["item_id"]] = (r["opening_qty"] or 0, r["last_count_date"])

        print(f"[V2-1] 2 last_cnt batch elapsed: {time.perf_counter() - t1:.3f}s rows={len(last_rows)}")

        # =========================================================
        # 3) Batch: purchases AFTER last_count_date (system_qty component)
        # =========================================================
        t2 = time.perf_counter()
        after_rows = db.execute(
            """
            WITH last_cnt AS (
              SELECT DISTINCT ON (item_id)
                item_id,
                count_date AS last_count_date
              FROM stock_counts
              WHERE store_id = %s
                AND item_id = ANY(%s)
                AND count_date <= %s
              ORDER BY item_id, count_date DESC, id DESC
            )
            SELECT
              p.item_id,
              COALESCE(SUM(p.quantity), 0) AS qty_after
            FROM purchases p
            LEFT JOIN last_cnt lc ON lc.item_id = p.item_id
            WHERE p.store_id = %s
              AND p.item_id = ANY(%s)
              AND p.is_deleted = 0
              AND p.delivery_date <= %s
              AND (lc.last_count_date IS NULL OR p.delivery_date > lc.last_count_date)
            GROUP BY p.item_id
            """,
            (store_id, item_ids, count_date, store_id, item_ids, count_date),
        ).fetchall()

        after_map = {r["item_id"]: (r["qty_after"] or 0) for r in after_rows}

        print(f"[V2-1] 3 purchases-after batch elapsed: {time.perf_counter() - t2:.3f}s rows={len(after_rows)}")

        # =========================================================
        # 4) Batch: weighted avg unit price up to count_date
        # =========================================================
        t3 = time.perf_counter()
        price_rows = db.execute(
            """
            SELECT
              item_id,
              CASE
                WHEN SUM(quantity) > 0 THEN
                  (SUM(quantity * unit_price)::numeric / SUM(quantity))
                ELSE 0
              END AS unit_price
            FROM purchases
            WHERE store_id = %s
              AND item_id = ANY(%s)
              AND is_deleted = 0
              AND delivery_date <= %s
            GROUP BY item_id
            """,
            (store_id, item_ids, count_date),
        ).fetchall()

        price_map = {r["item_id"]: float(r["unit_price"] or 0) for r in price_rows}

        print(f"[V2-1] 4 price batch elapsed: {time.perf_counter() - t3:.3f}s rows={len(price_rows)}")

        # =========================================================
        # 5) Batch: counted_qty on this count_date (for input default)
        # =========================================================
        t4 = time.perf_counter()
        counted_rows = db.execute(
            """
            SELECT item_id, counted_qty
            FROM stock_counts
            WHERE store_id = %s
              AND item_id = ANY(%s)
              AND count_date = %s
            """,
            (store_id, item_ids, count_date),
        ).fetchall()

        counted_map = {r["item_id"]: r["counted_qty"] for r in counted_rows}

        print(f"[V2-1] 5 counted_today batch elapsed: {time.perf_counter() - t4:.3f}s rows={len(counted_rows)}")

        # =========================================================
        # 6) Build items (NO DB calls here)
        # =========================================================
        t5 = time.perf_counter()

        for row in base_rows:
            item_id = row["item_id"]
            item_code = row["item_code"]
            item_name = row["item_name"]

            # temp zone normalize to Japanese labels
            tz_raw = row["tz_raw"] or "その他"
            if tz_raw in ("冷凍", "FREEZE"):
                storage_type = "冷凍"
            elif tz_raw in ("冷蔵", "CHILL"):
                storage_type = "冷蔵"
            elif tz_raw in ("常温", "AMB"):
                storage_type = "常温"
            else:
                storage_type = "その他"

            opening_qty, _last_date = last_map.get(item_id, (0, None))
            pur_after = after_map.get(item_id, 0)
            end_qty = opening_qty + pur_after

            unit_price = price_map.get(item_id, 0.0)
            stock_amount = end_qty * unit_price

            counted_qty = counted_map.get(item_id)

            is_internal = (row["is_internal"] == 1)

            area_name = row["area_name"] or "—"
            shelf_code = row["shelf_code"] or ""
            shelf_name = row["shelf_name"] or ""
            shelf_label = (f"{shelf_code} {shelf_name}").strip() or "—"

            if end_qty > 0 or is_internal:
                mst_items.append(
                    {
                        "item_id": item_id,
                        "item_code": item_code,
                        "item_name": item_name,
                        "temp_zone": storage_type,
                        "area_name": area_name if area_name else "—",
                        "shelf_label": shelf_label,
                        "system_qty": end_qty,
                        "unit_price": unit_price,
                        "stock_amount": stock_amount,
                        "counted_qty": counted_qty,
                        "is_internal": row["is_internal"],
                    }
                )

        print(f"[V2-1] 6 build-items elapsed: {time.perf_counter() - t5:.3f}s items={len(mst_items)}")

        # =========================================================
        # 7) Render
        # =========================================================
        t6 = time.perf_counter()
        html = render_template(
            "inv/inventory_count_v2.html",
            stores=stores,
            selected_store_id=selected_store_id,
            count_date=count_date,
            items=mst_items,
            mst_items=mst_items,
            latest_date=latest_date,
            latest_dates=latest_dates,
        )
        print(f"[V2-1] 7 render elapsed: {time.perf_counter() - t6:.3f}s")
        print(f"[V2-1] TOTAL elapsed: {time.perf_counter() - t0:.3f}s")

        return html