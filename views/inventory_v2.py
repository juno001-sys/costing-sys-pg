# views/inventory_v2.py

import csv
import io
import time
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, g, Response
from views.reports.audit_log import log_event

from utils.access_scope import (
    get_accessible_stores,
    normalize_accessible_store_id,
)


def _is_recent_duplicate_count(db, store_id, item_id, count_date,
                               counted_qty, window_minutes=10):
    """Layer B: detect re-submits.

    Returns True if an identical count (same store/item/date/qty) was
    inserted within the last `window_minutes`. Used to suppress duplicate
    INSERTs when an operator presses Save multiple times in a row.

    Background: on 2026-04-20, item 15005 was saved 4 times (qty=41) within
    50 min from the SP UI; the dashboard correctly showed the latest value
    but the audit history was noisy. This check keeps the row count clean.
    """
    rows = db.execute(
        """
        SELECT 1 FROM stock_counts
        WHERE store_id = %s AND item_id = %s AND count_date = %s
          AND counted_qty = %s
          AND created_at >= now() - (interval '1 minute' * %s)
        LIMIT 1
        """,
        (store_id, item_id, count_date, counted_qty, window_minutes),
    ).fetchone()
    return rows is not None


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


def _fetch_count_data(db, store_id, count_date, company_id=None):
    """
    Shared data-fetching logic for both the desktop (v2) and
    smartphone (sp) inventory count screens.
    Returns a list of item dicts ready for rendering.
    """
    base_rows = db.execute(
        """
        SELECT
          i.id   AS item_id,
          i.code AS item_code,
          i.name AS item_name,
          COALESCE(NULLIF(pref.temp_zone,''),NULLIF(i.temp_zone,''),'その他') AS tz_raw,
          m.shelf_id,
          COALESCE(m.sort_order, 9999) AS item_sort_order,
          sh.code AS shelf_code,
          sh.name AS shelf_name,
          COALESCE(sh.sort_order, 9999) AS shelf_sort_order,
          COALESCE(sam.sort_order, 9999) AS area_sort_order,
          COALESCE(sam.display_name, am.name, '') AS area_name,
          i.is_internal
        FROM mst_items i
        LEFT JOIN item_location_prefs pref
          ON pref.store_id = %s AND pref.item_id = i.id
        LEFT JOIN item_shelf_map m
          ON m.store_id = %s AND m.item_id = i.id AND m.is_active = TRUE
        LEFT JOIN store_shelves sh ON sh.id = m.shelf_id
        LEFT JOIN store_area_map sam ON sam.id = sh.store_area_map_id
        LEFT JOIN area_master am ON am.id = sam.area_id
        WHERE i.company_id = %s
          AND (
            pref.item_id IS NOT NULL OR m.item_id IS NOT NULL
            OR i.is_internal = 1
            OR EXISTS (
              SELECT 1 FROM purchases p
              WHERE p.store_id = %s AND p.item_id = i.id
                AND p.is_deleted = 0 AND p.delivery_date <= %s
            )
          )
        ORDER BY
          CASE
            WHEN COALESCE(NULLIF(pref.temp_zone,''),NULLIF(i.temp_zone,''),'その他') IN ('冷凍','FREEZE') THEN 1
            WHEN COALESCE(NULLIF(pref.temp_zone,''),NULLIF(i.temp_zone,''),'その他') IN ('冷蔵','CHILL')  THEN 2
            WHEN COALESCE(NULLIF(pref.temp_zone,''),NULLIF(i.temp_zone,''),'その他') IN ('常温','AMB')    THEN 3
            ELSE 9
          END,
          COALESCE(sam.sort_order,9999), COALESCE(am.name,''),
          COALESCE(sh.sort_order,9999),  COALESCE(sh.code,''),
          COALESCE(m.sort_order,9999),   i.code
        """,
        (store_id, store_id, company_id, store_id, count_date),
    ).fetchall()

    item_ids = [r["item_id"] for r in base_rows] or []

    # Last counted qty per item
    last_rows = db.execute(
        """
        SELECT DISTINCT ON (item_id) item_id,
               count_date AS last_count_date, counted_qty AS opening_qty
        FROM stock_counts
        WHERE store_id = %s AND item_id = ANY(%s) AND count_date <= %s
        ORDER BY item_id, count_date DESC, id DESC
        """,
        (store_id, item_ids, count_date),
    ).fetchall()
    last_map = {r["item_id"]: (r["opening_qty"] or 0, r["last_count_date"]) for r in last_rows}

    # Purchases after last count
    after_rows = db.execute(
        """
        WITH last_cnt AS (
          SELECT DISTINCT ON (item_id) item_id, count_date AS last_count_date
          FROM stock_counts
          WHERE store_id = %s AND item_id = ANY(%s) AND count_date <= %s
          ORDER BY item_id, count_date DESC, id DESC
        )
        SELECT p.item_id, COALESCE(SUM(p.quantity),0) AS qty_after
        FROM purchases p
        LEFT JOIN last_cnt lc ON lc.item_id = p.item_id
        WHERE p.store_id = %s AND p.item_id = ANY(%s)
          AND p.is_deleted = 0 AND p.delivery_date <= %s
          AND (lc.last_count_date IS NULL OR p.delivery_date > lc.last_count_date)
        GROUP BY p.item_id
        """,
        (store_id, item_ids, count_date, store_id, item_ids, count_date),
    ).fetchall()
    after_map = {r["item_id"]: (r["qty_after"] or 0) for r in after_rows}

    # Weighted avg unit price
    price_rows = db.execute(
        """
        SELECT item_id,
               CASE WHEN SUM(quantity)>0
                    THEN SUM(quantity*unit_price)::numeric/SUM(quantity)
                    ELSE 0 END AS unit_price
        FROM purchases
        WHERE store_id = %s AND item_id = ANY(%s)
          AND is_deleted = 0 AND delivery_date <= %s
        GROUP BY item_id
        """,
        (store_id, item_ids, count_date),
    ).fetchall()
    price_map = {r["item_id"]: float(r["unit_price"] or 0) for r in price_rows}

    # Already counted today
    counted_rows = db.execute(
        "SELECT item_id, counted_qty FROM stock_counts "
        "WHERE store_id = %s AND item_id = ANY(%s) AND count_date = %s",
        (store_id, item_ids, count_date),
    ).fetchall()
    counted_map = {r["item_id"]: r["counted_qty"] for r in counted_rows}

    # Latest-ever save timestamp per item (used by the smart/v3 screen for
    # the per-item "最終 YYYY-MM-DD HH:MM" freshness display).
    last_ever_rows = db.execute(
        """
        SELECT DISTINCT ON (item_id)
          item_id,
          count_date   AS last_ever_date,
          created_at   AS last_ever_at
        FROM stock_counts
        WHERE store_id = %s AND item_id = ANY(%s)
        ORDER BY item_id, count_date DESC, id DESC
        """,
        (store_id, item_ids),
    ).fetchall()
    last_ever_map = {
        r["item_id"]: (r["last_ever_date"], r["last_ever_at"])
        for r in last_ever_rows
    }

    # Build item list
    TZ_MAP = {
        "冷凍": "冷凍", "FREEZE": "冷凍",
        "冷蔵": "冷蔵", "CHILL":  "冷蔵",
        "常温": "常温", "AMB":    "常温",
    }
    items = []
    for row in base_rows:
        item_id    = row["item_id"]
        opening, last_count_date = last_map.get(item_id, (0, None))
        system_qty = opening + after_map.get(item_id, 0)
        if system_qty <= 0 and not row["is_internal"]:
            continue
        shelf_name = row["shelf_name"] or row["shelf_code"] or "—"
        last_ever_date, last_ever_at = last_ever_map.get(item_id, (None, None))
        items.append({
            "item_id":     item_id,
            "item_code":   row["item_code"],
            "item_name":   row["item_name"],
            "temp_zone":   TZ_MAP.get(row["tz_raw"] or "", "その他"),
            "area_name":   row["area_name"] or "—",
            "shelf_label": shelf_name,
            "system_qty":  system_qty,
            "unit_price":  price_map.get(item_id, 0.0),
            "stock_amount": system_qty * price_map.get(item_id, 0.0),
            "counted_qty": counted_map.get(item_id),
            "last_count_date": last_count_date,
            "last_ever_date": last_ever_date,
            "last_ever_at": last_ever_at,
            "is_internal": row["is_internal"],
        })
    return items


def init_inventory_views_v2(app, get_db):
    @app.route("/inventory/count_v2", methods=["GET", "POST"], endpoint="inventory_count_v2")
    def inventory_count_v2():
        db = get_db()

        # 店舗一覧
        mst_stores = get_accessible_stores()

        today = datetime.today().strftime("%Y-%m-%d")

        # -----------------------------
        # POST：棚卸し登録
        # -----------------------------
        if request.method == "POST":
            selected_post_store_id = normalize_accessible_store_id(
                request.form.get("store_id")
            )
            store_id = str(selected_post_store_id) if selected_post_store_id else None


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

                # Layer B — skip if an identical save just landed.
                if _is_recent_duplicate_count(
                    db, store_id, item_id, count_date, cnt_val
                ):
                    continue

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

                inserted_rows += 1

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
        selected_store_id = normalize_accessible_store_id(
            request.args.get("store_id")
        )
        store_id = str(selected_store_id) if selected_store_id else ""

        count_date = request.args.get("count_date") or today

        mst_items = []
        selected_store_id = normalize_accessible_store_id(
        request.args.get("store_id")
        )
        store_id = str(selected_store_id) if selected_store_id else ""

        latest_dates = []
        latest_date = None
        if selected_store_id:
            latest_dates = get_latest_stock_count_dates(db, selected_store_id, limit=3)
            latest_date = latest_dates[0] if latest_dates else None

        if not store_id:
            return render_template(
                "inv/inventory_count_v2.html",
                stores=mst_stores,
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
              COALESCE(sam.display_name, am.name, '') AS area_name,

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
            shelf_label = shelf_name or shelf_code or "—"

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
            stores=mst_stores,
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

    # ── Smartphone-optimised inventory count ──────────────────────────────────
    @app.route("/inventory/count_sp", methods=["GET", "POST"], endpoint="inventory_count_sp")
    def inventory_count_sp():
        db      = get_db()
        stores  = get_accessible_stores()
        today   = datetime.today().strftime("%Y-%m-%d")

        # ── POST: save counts (same logic as v2) ──────────────────────────────
        if request.method == "POST":
            store_id = str(
                normalize_accessible_store_id(request.form.get("store_id")) or ""
            )
            count_date = request.form.get("count_date") or today

            if not store_id:
                flash("店舗を選択してください。")
                return redirect(url_for("inventory_count_sp"))

            row_count    = int(request.form.get("row_count", 0))
            inserted_rows = 0

            for i in range(1, row_count + 1):
                item_id    = request.form.get(f"item_id_{i}")
                system_qty = request.form.get(f"system_qty_{i}")
                counted_qty = request.form.get(f"count_qty_{i}")

                if not item_id or counted_qty is None or counted_qty == "":
                    continue
                try:
                    sys_val = int(system_qty or 0)
                    cnt_val = int(counted_qty)
                except ValueError:
                    continue

                # Layer B — skip if an identical save just landed.
                if _is_recent_duplicate_count(
                    db, store_id, item_id, count_date, cnt_val
                ):
                    continue

                db.execute(
                    """
                    INSERT INTO stock_counts
                        (store_id, item_id, count_date,
                         system_qty, counted_qty, diff_qty, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (store_id, item_id, count_date,
                     sys_val, cnt_val, cnt_val - sys_val,
                     datetime.now().isoformat(timespec="seconds")),
                )
                inserted_rows += 1

            try:
                log_event(db, action="SUBMIT", module="inv",
                          entity_table="stock_counts",
                          entity_id=f"{store_id}:{count_date}",
                          message=f"Inventory count submitted (sp) {count_date}",
                          store_id=int(store_id), status_code=200,
                          meta={"count_date": str(count_date),
                                "row_count": row_count,
                                "inserted_rows": inserted_rows,
                                "version": "sp"})
            except Exception:
                pass

            db.commit()
            flash(f"✅ {inserted_rows}件の棚卸し結果を登録しました。")
            return redirect(url_for("inventory_count_sp",
                                    store_id=store_id, count_date=count_date))

        # ── GET: render ───────────────────────────────────────────────────────
        selected_store_id = normalize_accessible_store_id(
            request.args.get("store_id")
        )
        store_id   = str(selected_store_id) if selected_store_id else ""
        count_date = request.args.get("count_date") or today
        freq_filter = (request.args.get("freq") or "").strip()

        latest_dates = []
        items        = []
        freq_map     = {}

        if selected_store_id:
            latest_dates = get_latest_stock_count_dates(db, selected_store_id, limit=3)
            company_id = getattr(g, "current_company_id", None)
            items = _fetch_count_data(db, store_id, count_date, company_id)

            # Attach frequency bucket + filter
            from utils.item_frequency import fetch_item_frequency
            freq_map = fetch_item_frequency(db, [i["item_id"] for i in items])
            for it in items:
                it["frequency"] = freq_map.get(it["item_id"], {"bucket": "none"})

            if freq_filter:
                items = [i for i in items if i["frequency"]["bucket"] == freq_filter]

        # Group items by temp zone for the template
        from collections import OrderedDict
        ZONE_ORDER = ["冷凍", "冷蔵", "常温", "その他"]
        zones: dict = OrderedDict((z, []) for z in ZONE_ORDER)
        for item in items:
            tz = item["temp_zone"] if item["temp_zone"] in zones else "その他"
            zones[tz].append(item)
        # Remove empty zones
        zones = {z: v for z, v in zones.items() if v}

        return render_template(
            "inv/inventory_count_sp.html",
            stores=stores,
            selected_store_id=selected_store_id,
            count_date=count_date,
            items=items,
            zones=zones,
            latest_dates=latest_dates,
            freq_filter=freq_filter,
        )

        return html


def init_inventory_views_v3(app, get_db):
    """Brand-new inventory count screens (desktop + smartphone) that only
    save items whose counted_qty changed. The existing v2 + SP screens
    are untouched — users can still reach them via their original URLs.

    Key behaviour differences vs v2:
      - Each count input carries its original value as data-original.
      - On 保存, JS disables count inputs whose value == data-original,
        so the browser never submits them and the server skips them.
        Result: unchanged items are not re-inserted, and only the
        touched items get a fresh timestamp.
      - Per-item "最終 YYYY-MM-DD HH:MM" badge shows when that specific
        item was last counted (not when the whole sheet was submitted).
    """

    # ── Desktop v3 ────────────────────────────────────────────────────────
    @app.route("/inventory/count_v3", methods=["GET", "POST"], endpoint="inventory_count_v3")
    def inventory_count_v3():
        db = get_db()
        stores = get_accessible_stores()
        today = datetime.today().strftime("%Y-%m-%d")

        # POST: identical insert behaviour to v2. Unchanged rows are
        # never submitted (the JS disables them), so the existing
        # row-skipping logic ("if counted_qty is None or empty: continue")
        # naturally does the right thing.
        if request.method == "POST":
            store_id = str(
                normalize_accessible_store_id(request.form.get("store_id")) or ""
            )
            count_date = request.form.get("count_date") or today

            if not store_id:
                flash("店舗を選択してください。")
                return redirect(url_for("inventory_count_v3"))

            row_count = int(request.form.get("row_count", 0))
            inserted_rows = 0

            for i in range(1, row_count + 1):
                item_id = request.form.get(f"item_id_{i}")
                system_qty = request.form.get(f"system_qty_{i}")
                counted_qty = request.form.get(f"count_qty_{i}")

                if not item_id or counted_qty is None or counted_qty == "":
                    continue
                try:
                    sys_val = int(system_qty or 0)
                    cnt_val = int(counted_qty)
                except ValueError:
                    continue

                if _is_recent_duplicate_count(
                    db, store_id, item_id, count_date, cnt_val
                ):
                    continue

                db.execute(
                    """
                    INSERT INTO stock_counts
                        (store_id, item_id, count_date,
                         system_qty, counted_qty, diff_qty, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (store_id, item_id, count_date,
                     sys_val, cnt_val, cnt_val - sys_val,
                     datetime.now().isoformat(timespec="seconds")),
                )
                inserted_rows += 1

            try:
                log_event(
                    db, action="SUBMIT", module="inv",
                    entity_table="stock_counts",
                    entity_id=f"{store_id}:{count_date}",
                    message=f"Inventory count submitted (v3-smart) {count_date}",
                    store_id=int(store_id), status_code=200,
                    meta={"count_date": str(count_date),
                          "row_count": int(row_count),
                          "inserted_rows": int(inserted_rows),
                          "version": "v3-smart"},
                )
            except Exception:
                pass

            db.commit()
            flash(f"✅ {inserted_rows}件の変更を保存しました。")
            return redirect(url_for("inventory_count_v3",
                                    store_id=store_id, count_date=count_date))

        # GET: render
        selected_store_id = normalize_accessible_store_id(
            request.args.get("store_id")
        )
        store_id = str(selected_store_id) if selected_store_id else ""
        count_date = request.args.get("count_date") or today

        latest_dates = []
        items = []
        if selected_store_id:
            latest_dates = get_latest_stock_count_dates(db, selected_store_id, limit=3)
            company_id = getattr(g, "current_company_id", None)
            items = _fetch_count_data(db, store_id, count_date, company_id)

        return render_template(
            "inv/inventory_count_v3.html",
            stores=stores,
            selected_store_id=selected_store_id,
            count_date=count_date,
            items=items,
            latest_dates=latest_dates,
        )

    # ── CSV export (for accounting) ──────────────────────────────────────
    @app.route("/inventory/count/export-csv", methods=["GET"],
               endpoint="inventory_count_export_csv")
    def inventory_count_export_csv():
        """Export the latest count of every item at this store as a CSV
        snapshot for the accounting team. v3 is per-item independent, so
        different items may have different last-count dates — each row
        carries its own 最終棚卸日 column.

        Columns: 店舗 / コード / 品目名 / カテゴリ / 仕入先 / 単位 /
        数量 / 単価 / 金額 / 最終棚卸日.

        Output is UTF-8 with BOM so Excel on Japanese Windows opens it
        without mojibake.
        """
        db = get_db()
        company_id = getattr(g, "current_company_id", None)

        selected_store_id = normalize_accessible_store_id(
            request.args.get("store_id")
        )

        if not selected_store_id:
            flash("店舗を選択してください。")
            return redirect(url_for("inventory_count_v3"))

        store = db.execute(
            "SELECT id, code, name FROM mst_stores WHERE id = %s AND company_id = %s",
            (selected_store_id, company_id),
        ).fetchone()

        # Latest count per item at this store + weighted-avg unit price
        # computed over each item's own count_date (LATERAL subquery).
        rows = db.execute(
            """
            SELECT DISTINCT ON (sc.item_id)
              sc.item_id,
              sc.count_date,
              sc.counted_qty,
              i.code     AS item_code,
              i.name     AS item_name,
              i.category,
              i.unit,
              s.name     AS supplier_name,
              COALESCE(pr.weighted_price, 0) AS unit_price
            FROM stock_counts sc
            JOIN mst_items i ON i.id = sc.item_id
            LEFT JOIN pur_suppliers s ON s.id = i.supplier_id
            LEFT JOIN LATERAL (
              SELECT CASE WHEN SUM(p.quantity) > 0
                          THEN SUM(p.quantity * p.unit_price)::numeric / SUM(p.quantity)
                          ELSE 0 END AS weighted_price
              FROM purchases p
              WHERE p.store_id = sc.store_id
                AND p.item_id  = sc.item_id
                AND p.is_deleted = 0
                AND p.delivery_date <= sc.count_date
            ) pr ON TRUE
            WHERE sc.store_id = %s
              AND i.company_id = %s
            ORDER BY sc.item_id, sc.count_date DESC, sc.id DESC
            """,
            (selected_store_id, company_id),
        ).fetchall()

        # Drop zero-qty items — the accounting team only wants lines that
        # actually represent stock on hand. DISTINCT ON runs first (above)
        # so "latest count = 0" correctly skips items whose stock was
        # exhausted in the most recent count.
        rows = [r for r in rows if (r["counted_qty"] or 0) > 0]

        if not rows:
            flash("この店舗の棚卸しデータがありません（在庫数量ゼロの品目のみ、または未カウント）。")
            return redirect(url_for(
                "inventory_count_v3", store_id=selected_store_id,
            ))

        buf = io.StringIO()
        writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
        writer.writerow([
            "店舗", "コード", "品目名", "カテゴリ",
            "仕入先", "単位", "数量", "単価", "金額", "最終棚卸日",
        ])

        store_name = store["name"] if store else ""
        for r in rows:
            qty = int(r["counted_qty"] or 0)
            price = float(r["unit_price"] or 0)
            amount = round(qty * price)
            writer.writerow([
                store_name,
                r["item_code"] or "",
                r["item_name"] or "",
                r["category"] or "",
                r["supplier_name"] or "",
                r["unit"] or "",
                qty,
                round(price),
                amount,
                r["count_date"].isoformat() if r["count_date"] else "",
            ])

        # UTF-8 BOM so Excel on Japanese Windows auto-detects the encoding.
        body = "\ufeff" + buf.getvalue()

        today_str = datetime.today().strftime("%Y-%m-%d")
        store_code = (store["code"] if store else str(selected_store_id)) or str(selected_store_id)
        filename = f"inventory_snapshot_{store_code}_{today_str}.csv"

        try:
            log_event(
                db, action="EXPORT", module="inv",
                entity_table="stock_counts",
                entity_id=f"{selected_store_id}:snapshot",
                message=f"Inventory snapshot CSV exported ({store_name})",
                store_id=int(selected_store_id), status_code=200,
                meta={"rows": len(rows), "exported_on": today_str},
            )
            db.commit()
        except Exception:
            pass

        return Response(
            body.encode("utf-8"),
            mimetype="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    # ── Smartphone v3 ─────────────────────────────────────────────────────
    @app.route("/inventory/count_sp_v3", methods=["GET", "POST"], endpoint="inventory_count_sp_v3")
    def inventory_count_sp_v3():
        db = get_db()
        stores = get_accessible_stores()
        today = datetime.today().strftime("%Y-%m-%d")

        if request.method == "POST":
            store_id = str(
                normalize_accessible_store_id(request.form.get("store_id")) or ""
            )
            count_date = request.form.get("count_date") or today

            if not store_id:
                flash("店舗を選択してください。")
                return redirect(url_for("inventory_count_sp_v3"))

            row_count = int(request.form.get("row_count", 0))
            inserted_rows = 0

            for i in range(1, row_count + 1):
                item_id = request.form.get(f"item_id_{i}")
                system_qty = request.form.get(f"system_qty_{i}")
                counted_qty = request.form.get(f"count_qty_{i}")

                if not item_id or counted_qty is None or counted_qty == "":
                    continue
                try:
                    sys_val = int(system_qty or 0)
                    cnt_val = int(counted_qty)
                except ValueError:
                    continue

                if _is_recent_duplicate_count(
                    db, store_id, item_id, count_date, cnt_val
                ):
                    continue

                db.execute(
                    """
                    INSERT INTO stock_counts
                        (store_id, item_id, count_date,
                         system_qty, counted_qty, diff_qty, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (store_id, item_id, count_date,
                     sys_val, cnt_val, cnt_val - sys_val,
                     datetime.now().isoformat(timespec="seconds")),
                )
                inserted_rows += 1

            try:
                log_event(
                    db, action="SUBMIT", module="inv",
                    entity_table="stock_counts",
                    entity_id=f"{store_id}:{count_date}",
                    message=f"Inventory count submitted (sp-v3-smart) {count_date}",
                    store_id=int(store_id), status_code=200,
                    meta={"count_date": str(count_date),
                          "row_count": int(row_count),
                          "inserted_rows": int(inserted_rows),
                          "version": "sp-v3-smart"},
                )
            except Exception:
                pass

            db.commit()
            flash(f"✅ {inserted_rows}件の変更を保存しました。")
            return redirect(url_for("inventory_count_sp_v3",
                                    store_id=store_id, count_date=count_date))

        # GET
        selected_store_id = normalize_accessible_store_id(
            request.args.get("store_id")
        )
        store_id = str(selected_store_id) if selected_store_id else ""
        count_date = request.args.get("count_date") or today
        freq_filter = (request.args.get("freq") or "").strip()

        latest_dates = []
        items = []
        if selected_store_id:
            latest_dates = get_latest_stock_count_dates(db, selected_store_id, limit=3)
            company_id = getattr(g, "current_company_id", None)
            items = _fetch_count_data(db, store_id, count_date, company_id)

            from utils.item_frequency import fetch_item_frequency
            freq_map = fetch_item_frequency(db, [i["item_id"] for i in items])
            for it in items:
                it["frequency"] = freq_map.get(it["item_id"], {"bucket": "none"})
            if freq_filter:
                items = [i for i in items if i["frequency"]["bucket"] == freq_filter]

        from collections import OrderedDict
        ZONE_ORDER = ["冷凍", "冷蔵", "常温", "その他"]
        zones = OrderedDict((z, []) for z in ZONE_ORDER)
        for item in items:
            tz = item["temp_zone"] if item["temp_zone"] in zones else "その他"
            zones[tz].append(item)
        zones = {z: v for z, v in zones.items() if v}

        return render_template(
            "inv/inventory_count_sp_v3.html",
            stores=stores,
            selected_store_id=selected_store_id,
            count_date=count_date,
            items=items,
            zones=zones,
            latest_dates=latest_dates,
            freq_filter=freq_filter,
        )