from __future__ import annotations

from flask import render_template, request
from datetime import datetime

def register(app, get_db):
    # ----------------------------------------
    # 売上原価 月次推移（棚卸しは最新棚卸しを FIFO 単価で評価）
    # /cost/report
    # ----------------------------------------
    @app.route("/cost/report", methods=["GET"])
    def cost_report():
        db = get_db()

        # 店舗一覧
        stores = db.execute(
            "SELECT id, name FROM stores ORDER BY code"
        ).fetchall()

        # 店舗
        store_id = request.args.get("store_id") or ""
        selected_store_id = int(store_id) if store_id else None

        # 空文字のときは None（= 全店舗）、指定があれば int にキャスト
        store_id_param = None if store_id == "" else int(store_id)

        # 対象13ヶ月
        today = datetime.now().date()
        y, m = today.year, today.month

        month_keys = []
        for _ in range(13):
            month_keys.append(f"{y:04d}-{m:02d}")
            m -= 1
            if m == 0:
                m = 12
                y -= 1
        month_keys = list(reversed(month_keys))

        # 日付範囲
        start_date = month_keys[0] + "-01"
        end_last = month_keys[-1]
        ey, em = map(int, end_last.split("-"))
        if em == 12:
            ey += 1
            em = 1
        else:
            em += 1
        end_date = f"{ey:04d}-{em:02d}-01"

        #
        # 1. 当月仕入高（Postgres版）
        #
        sql_pur = """
            SELECT
              TO_CHAR(p.delivery_date, 'YYYY-MM') AS ym,
              SUM(p.amount) AS total_amount
            FROM purchases p
            WHERE p.delivery_date >= %s
              AND p.delivery_date < %s
              AND p.is_deleted = 0
              AND ( %s IS NULL OR p.store_id = %s )
            GROUP BY ym
        """
        pur_rows = db.execute(
            sql_pur,
            [start_date, end_date, store_id_param, store_id_param],
        ).fetchall()

        purchases_by_month = {ym: 0 for ym in month_keys}
        for r in pur_rows:
            ym = r["ym"]
            amt = r["total_amount"] or 0
            if ym in purchases_by_month:
                purchases_by_month[ym] = amt

        #
        # 2. 期末棚卸（FIFO 評価 / Postgres版）
        #
        sql_inv_fifo = """
            WITH latest AS (
                SELECT
                  sc.store_id,
                  sc.item_id,
                  sc.count_date,
                  TO_CHAR(sc.count_date, 'YYYY-MM') AS ym,
                  sc.counted_qty,
                  ROW_NUMBER() OVER (
                    PARTITION BY sc.store_id, sc.item_id, TO_CHAR(sc.count_date, 'YYYY-MM')
                    ORDER BY sc.count_date DESC, sc.id DESC
                  ) AS rn
                FROM stock_counts sc
                WHERE sc.count_date >= %s
                  AND sc.count_date < %s
                  AND ( %s IS NULL OR sc.store_id = %s )
            ),
            end_stock AS (
                SELECT
                  store_id,
                  item_id,
                  ym,
                  count_date,
                  counted_qty AS end_qty
                FROM latest
                WHERE rn = 1 AND counted_qty > 0
            ),
            fifo_base AS (
                SELECT
                  e.store_id,
                  e.item_id,
                  e.ym,
                  e.end_qty,
                  p.id AS purchase_id,
                  p.delivery_date,
                  p.quantity,
                  p.unit_price,
                  SUM(p.quantity) OVER (
                    PARTITION BY e.store_id, e.item_id, e.ym
                    ORDER BY p.delivery_date DESC, p.id DESC
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                  ) AS running_qty
                FROM end_stock e
                JOIN purchases p
                  ON p.store_id = e.store_id
                 AND p.item_id  = e.item_id
                 AND p.delivery_date <= e.count_date
            ),
            fifo_layers AS (
                SELECT
                  store_id,
                  item_id,
                  ym,
                  end_qty,
                  purchase_id,
                  delivery_date,
                  quantity,
                  unit_price,
                  running_qty,
                  LAG(running_qty, 1, 0) OVER (
                    PARTITION BY store_id, item_id, ym
                    ORDER BY delivery_date DESC, purchase_id DESC
                  ) AS prev_running
                FROM fifo_base
            )
            SELECT
              ym,
              SUM(
                CASE
                  WHEN prev_running >= end_qty THEN 0
                  WHEN running_qty <= end_qty THEN quantity * unit_price
                  ELSE (end_qty - prev_running) * unit_price
                END
              ) AS inv_amount
            FROM fifo_layers
            GROUP BY ym
        """
        inv_rows = db.execute(
            sql_inv_fifo,
            [start_date, end_date, store_id_param, store_id_param],
        ).fetchall()

        end_inv_by_month = {ym: 0.0 for ym in month_keys}
        for r in inv_rows:
            end_inv_by_month[r["ym"]] = float(r["inv_amount"] or 0)

        #
        # 3. 期首棚卸（前月の期末）
        #
        beg_inv_by_month = {}
        prev_end = 0.0
        for ym in month_keys:
            beg_inv_by_month[ym] = prev_end
            prev_end = end_inv_by_month.get(ym, 0.0)

        #
        # 4. 売上原価 = 期首 + 仕入 - 期末
        #
        cogs_by_month = {}
        for ym in month_keys:
            beg = beg_inv_by_month[ym]
            pur = purchases_by_month[ym]
            end = end_inv_by_month[ym]
            cogs_by_month[ym] = beg + pur - end

        purchases_total = sum(purchases_by_month.values())
        beg_inv_total = sum(beg_inv_by_month.values())
        end_inv_total = sum(end_inv_by_month.values())
        cogs_total = sum(cogs_by_month.values())

        return render_template(
            "cost_report.html",
            stores=stores,
            selected_store_id=selected_store_id,
            month_keys=month_keys,
            purchases_by_month=purchases_by_month,
            beg_inv_by_month=beg_inv_by_month,
            end_inv_by_month=end_inv_by_month,
            cogs_by_month=cogs_by_month,
            purchases_total=purchases_total,
            beg_inv_total=beg_inv_total,
            end_inv_total=end_inv_total,
            cogs_total=cogs_total,
        )
