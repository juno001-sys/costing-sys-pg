from __future__ import annotations

from datetime import datetime

from flask import render_template, request

from . import reports_bp, get_db


@reports_bp.route("/cost/report", methods=["GET"])
def cost_report():
    db = get_db()

    # mst_stores list
    mst_stores = db.execute(
        "SELECT id, name FROM mst_stores ORDER BY code"
    ).fetchall()

    # store filter (optional)
    store_id = request.args.get("store_id") or ""
    selected_store_id = int(store_id) if store_id else None

    # None means "all mst_stores"
    store_id_param = None if store_id == "" else int(store_id)

    # last 13 months
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

    # date range [start_date, end_date)
    start_date = month_keys[0] + "-01"
    end_last = month_keys[-1]
    ey, em = map(int, end_last.split("-"))
    if em == 12:
        ey += 1
        em = 1
    else:
        em += 1
    end_date = f"{ey:04d}-{em:02d}-01"

    # 1) Purchases amount per month
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

    # 2) Ending inventory (FIFO valuation)
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
        ym = r["ym"]
        if ym in end_inv_by_month:
            end_inv_by_month[ym] = float(r["inv_amount"] or 0)

    # 3) Beginning inventory = previous month ending inventory
    beg_inv_by_month = {}
    prev_end = 0.0
    for ym in month_keys:
        beg_inv_by_month[ym] = prev_end
        prev_end = end_inv_by_month.get(ym, 0.0)

    # 4) COGS = Begin + Purchases - End
    cogs_by_month = {}
    for ym in month_keys:
        cogs_by_month[ym] = beg_inv_by_month[ym] + purchases_by_month[ym] - end_inv_by_month[ym]

    purchases_total = sum(purchases_by_month.values())
    beg_inv_total = sum(beg_inv_by_month.values())
    end_inv_total = sum(end_inv_by_month.values())
    cogs_total = sum(cogs_by_month.values())

    return render_template(
        "cost_report.html",
        mst_stores=mst_stores,
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
