# views/inventory_v2.py
import time


from datetime import datetime
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
)

def _t():
    return time.perf_counter()

def get_latest_stock_count_dates(db, store_id, limit=3):
    """
    棚卸し履歴ヘルパー（最新日＋過去2回）
    """
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
    """
    棚卸し系ルートを登録する初期化関数。

        from views.inventory import init_inventory_views
        init_inventory_views(app, get_db)

    という形で app.py から呼び出します。
    """

    # ----------------------------------------
    # 棚卸し入力
    # /inventory/count
    # ----------------------------------------
    @app.route("/inventory/count_v2", methods=["GET", "POST"] )
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

        # 今日の日付をデフォルトに
        today = datetime.today().strftime("%Y-%m-%d")

        # -----------------------------
        # POST：棚卸し登録
        # -----------------------------
        if request.method == "POST":
            store_id = request.form.get("store_id") or None
            count_date = request.form.get("count_date") or today

            if not store_id:
                flash("店舗を選択してください。")
                return redirect(url_for("inventory_count"))

            row_count = int(request.form.get("row_count", 0))

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

            db.commit()
            flash("棚卸し結果を登録しました。")
            return redirect(
                url_for("inventory_count", store_id=store_id, count_date=count_date)
            )

        # -----------------------------
        # GET：表示
        # -----------------------------
        store_id = request.args.get("store_id") or ""
        count_date = request.args.get("count_date") or today

        mst_items = []
        selected_store_id = int(store_id) if store_id else None

        # ★ 最新棚卸日（＋過去2回）を取得
        latest_dates = []
        latest_date = None
        if selected_store_id:
            latest_dates = get_latest_stock_count_dates(db, selected_store_id, limit=3)
            latest_date = latest_dates[0] if latest_dates else None

        # ★ 温度帯ラベルと入れ物を先に用意しておく（store_id が空でも定義されるように）
        #zones = ["冷凍", "冷蔵", "常温", "その他"]
        #grouped_items = {z: [] for z in zones}


        t0 = time.perf_counter()
        


        if store_id:
            # まず、内製品（is_internal=1）は仕入がなくても拾う
            # 通常品（is_internal=0）は、指定店舗・指定日までに一度でも仕入がある品目だけ拾う
            base_rows = db.execute(
                 """
                SELECT
                i.id   AS item_id,
                i.code AS item_code,
                i.name AS item_name,
                sh.code AS shelf_code,
                sh.name AS shelf_name,

                -- temp zone preference: prefs -> item master -> default
                COALESCE(NULLIF(pref.temp_zone, ''), NULLIF(i.temp_zone, ''), 'その他') AS tz_raw,

                -- shelf / area (from locations)
                m.shelf_id,
                COALESCE(m.sort_order, 9999) AS item_sort_order,

                sh.code AS shelf_code,
                COALESCE(sh.sort_order, 9999) AS shelf_sort_order,
                sam.sort_order AS area_sort_order,
                am.name AS area_name,

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
                -- show items that are placed/configured OR internal OR have purchase history
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
                -- temp zone order: FREEZE -> CHILL -> AMB -> その他
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

            print(f"[V2-1] 1 elapsed: {time.perf_counter() - t0:.3f}s")

            item_ids = [r["item_id"] for r in base_rows]
            if not item_ids:
                item_ids = []



            for row in base_rows:
                item_id = row["item_id"]
                item_code = row["item_code"]
                item_name = row["item_name"]

                # ---------- システム在庫の計算 ----------
                # 最新の棚卸し（count_date 以前）を取得
                last_cnt = db.execute(
                    """
                    SELECT counted_qty, count_date
                    FROM stock_counts
                    WHERE store_id = %s
                      AND item_id  = %s
                      AND count_date <= %s
                    ORDER BY count_date DESC, id DESC
                    LIMIT 1
                    """,
                    (store_id, item_id, count_date),
                ).fetchone()


                print(f"[V2-1] 2 elapsed: {time.perf_counter() - t0:.3f}s")



                if last_cnt:
                    opening_qty = last_cnt["counted_qty"]
                    start_date = last_cnt["count_date"]

                    pur_row = db.execute(
                        """
                        SELECT COALESCE(SUM(quantity), 0) AS qty
                        FROM purchases
                        WHERE store_id = %s
                          AND item_id  = %s
                          AND delivery_date > %s
                          AND delivery_date <= %s
                          AND is_deleted = 0
                        """,
                        (store_id, item_id, start_date, count_date),
                    ).fetchone()
                else:
                    opening_qty = 0
                    pur_row = db.execute(
                        """
                        SELECT COALESCE(SUM(quantity), 0) AS qty
                        FROM purchases
                        WHERE store_id = %s
                          AND item_id  = %s
                          AND delivery_date <= %s
                          AND is_deleted = 0
                        """,
                        (store_id, item_id, count_date),
                    ).fetchone()

         
                print(f"[V2-1] 3 elapsed: {time.perf_counter() - t0:.3f}s")


                pur_qty = pur_row["qty"] if pur_row else 0
                end_qty = opening_qty + pur_qty   # システム在庫

                # ---------- 単価（加重平均） ----------
                price_row = db.execute(
                    """
                    SELECT
                      CASE
                        WHEN SUM(quantity) > 0 THEN
                          CAST(SUM(quantity * unit_price) AS REAL) / SUM(quantity)
                        ELSE 0
                      END AS unit_price
                    FROM purchases
                    WHERE store_id = %s
                      AND item_id  = %s
                      AND delivery_date <= %s
                      AND is_deleted = 0
                    """,
                    (store_id, item_id, count_date),
                ).fetchone()

                unit_price = price_row["unit_price"] or 0.0
                stock_amount = end_qty * unit_price


                print(f"[V2-1] 4 elapsed: {time.perf_counter() - t0:.3f}s")


                # ---------- この棚卸し日の棚卸数量を取得 ----------
                counted_row = db.execute(
                    """
                    SELECT counted_qty
                    FROM stock_counts
                    WHERE store_id   = %s
                      AND item_id    = %s
                      AND count_date = %s
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (store_id, item_id, count_date),
                ).fetchone()


                print(f"[V2-1] 5 elapsed: {time.perf_counter() - t0:.3f}s")


                counted_qty = counted_row["counted_qty"] if counted_row else None

                # 在庫ゼロは通常は表示しないが、
                # 内製品（is_internal=1）は在庫ゼロでも表示する
                is_internal = row["is_internal"] == 1

                # --- normalize temp zone (location-aware) ---
                tz_raw = row["tz_raw"] or "その他"

                if tz_raw in ("冷凍", "FREEZE"):
                    storage_type = "冷凍"
                elif tz_raw in ("冷蔵", "CHILL"):
                    storage_type = "冷蔵"
                elif tz_raw in ("常温", "AMB"):
                    storage_type = "常温"
                else:
                    storage_type = "その他"

                print(f"[V2-1] 6 elapsed: {time.perf_counter() - t0:.3f}s")

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
                            "area_name": area_name,
                            "shelf_label": shelf_label,

                            "storage_type": storage_type,
                            
                            "system_qty": end_qty,
                            "unit_price": unit_price,
                            "stock_amount": stock_amount,
                            "counted_qty": counted_qty,
                            
                            "is_internal": row["is_internal"],
                        }
                    )
            

                print(f"[V2-1] 7 elapsed: {time.perf_counter() - t0:.3f}s")


            # ★ mst_items を温度帯ごとにグルーピング
            #for it in mst_items:
            #    z = it.get("storage_type") or "その他"
            #    if z not in grouped_items:
            #        grouped_items[z] = []
            #    grouped_items[z].append(it)

        return render_template(
            "inv/inventory_count_v2.html",
            stores=stores,
            selected_store_id=selected_store_id,
            count_date=count_date,
            items=mst_items,   
            mst_items=mst_items,
            latest_date=latest_date,
            latest_dates=latest_dates,
            #zones=zones,
            #grouped_items=grouped_items,
        )