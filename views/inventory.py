# views/inventory.py

from datetime import datetime
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
)


def get_latest_stock_count_dates(db, store_id, limit=3):
    """
    棚卸し履歴ヘルパー（最新日＋過去2回）
    """
    rows = db.execute(
        """
        SELECT DISTINCT count_date
        FROM stock_counts
        WHERE store_id = ?
        ORDER BY count_date DESC
        LIMIT ?
        """,
        (store_id, limit),
    ).fetchall()
    return [r["count_date"] for r in rows]


def init_inventory_views(app, get_db):
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
    @app.route("/inventory/count", methods=["GET", "POST"])
    def inventory_count():
        db = get_db()

        # 店舗一覧
        mst_stores = db.execute(
            "SELECT id, name FROM mst_stores ORDER BY code"
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
                    VALUES (?, ?, ?, ?, ?, ?, ?)
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
        zones = ["冷凍", "冷蔵", "常温", "その他"]
        grouped_items = {z: [] for z in zones}

        if store_id:
            # まず、内製品（is_internal=1）は仕入がなくても拾う
            # 通常品（is_internal=0）は、指定店舗・指定日までに一度でも仕入がある品目だけ拾う
            base_rows = db.execute(
                """
                SELECT
                    i.id   AS item_id,
                    i.code AS item_code,
                    i.name AS item_name,
                    COALESCE(i.temp_zone, 'その他') AS storage_type,
                    i.is_internal
                FROM mst_items i
                WHERE i.is_internal = 1

                UNION

                SELECT DISTINCT
                    i.id   AS item_id,
                    i.code AS item_code,
                    i.name AS item_name,
                    COALESCE(i.temp_zone, 'その他') AS storage_type,
                    i.is_internal
                FROM mst_items i
                JOIN purchases p
                  ON p.item_id = i.id
                 AND p.store_id = ?
                 AND p.delivery_date <= ?
                 AND p.is_deleted = 0
                WHERE i.is_internal = 0

                ORDER BY storage_type, item_code
                """,
                (store_id, count_date),
            ).fetchall()

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
                    WHERE store_id = ?
                      AND item_id  = ?
                      AND count_date <= ?
                    ORDER BY count_date DESC, id DESC
                    LIMIT 1
                    """,
                    (store_id, item_id, count_date),
                ).fetchone()

                if last_cnt:
                    opening_qty = last_cnt["counted_qty"]
                    start_date = last_cnt["count_date"]

                    pur_row = db.execute(
                        """
                        SELECT COALESCE(SUM(quantity), 0) AS qty
                        FROM purchases
                        WHERE store_id = ?
                          AND item_id  = ?
                          AND delivery_date > ?
                          AND delivery_date <= ?
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
                        WHERE store_id = ?
                          AND item_id  = ?
                          AND delivery_date <= ?
                          AND is_deleted = 0
                        """,
                        (store_id, item_id, count_date),
                    ).fetchone()

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
                    WHERE store_id = ?
                      AND item_id  = ?
                      AND delivery_date <= ?
                      AND is_deleted = 0
                    """,
                    (store_id, item_id, count_date),
                ).fetchone()

                unit_price = price_row["unit_price"] or 0.0
                stock_amount = end_qty * unit_price

                # ---------- この棚卸し日の棚卸数量を取得 ----------
                counted_row = db.execute(
                    """
                    SELECT counted_qty
                    FROM stock_counts
                    WHERE store_id   = ?
                      AND item_id    = ?
                      AND count_date = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (store_id, item_id, count_date),
                ).fetchone()

                counted_qty = counted_row["counted_qty"] if counted_row else None

                # 在庫ゼロは通常は表示しないが、
                # 内製品（is_internal=1）は在庫ゼロでも表示する
                is_internal = row["is_internal"] == 1

                if end_qty > 0 or is_internal:
                    mst_items.append(
                        {
                            "item_id": item_id,
                            "item_code": item_code,
                            "item_name": item_name,
                            "system_qty": end_qty,
                            "unit_price": unit_price,
                            "stock_amount": stock_amount,
                            "counted_qty": counted_qty,
                            "storage_type": row["storage_type"],
                            "is_internal": row["is_internal"],
                        }
                    )

            # ★ mst_items を温度帯ごとにグルーピング
            for it in mst_items:
                z = it.get("storage_type") or "その他"
                if z not in grouped_items:
                    grouped_items[z] = []
                grouped_items[z].append(it)

        return render_template(
            "inventory_count.html",
            mst_stores=mst_stores,
            selected_store_id=selected_store_id,
            count_date=count_date,
            mst_items=mst_items,
            latest_date=latest_date,
            latest_dates=latest_dates,
            zones=zones,
            grouped_items=grouped_items,
        )
