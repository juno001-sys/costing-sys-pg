# cost_report.py
from __future__ import annotations

from datetime import datetime

from flask import render_template, request

from . import reports_bp, get_db

from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP

def ym_to_month_start(ym: str) -> date:
    y, m = ym.split("-")
    return date(int(y), int(m), 1)

def yen(v: Decimal) -> int:
    return int(v.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


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

    # -----------------------------
    # Profit estimate (single month)
    # -----------------------------
    profit_ym = request.args.get("profit_ym") or (month_keys[-1] if month_keys else None)

    profit_setting_row = None
    profit_est = None

    # Only meaningful when a store is selected
    if selected_store_id and profit_ym:
      month_start = profit_ym + "-01"

      profit_setting_row = db.execute(
          """
          SELECT
            fl_ratio,
            food_ratio,
            utility_ratio,
            fixed_cost_yen,
            store_id
          FROM mst_profit_settings
          WHERE (store_id = %s OR store_id IS NULL)
            AND effective_from <= %s
            AND (effective_to IS NULL OR effective_to >= %s)
          ORDER BY
            (store_id IS NOT NULL) DESC,
            effective_from DESC
          LIMIT 1
          """,
          [selected_store_id, month_start, month_start],
      ).fetchone()

    profit_est = None
    if profit_setting_row:
        from decimal import Decimal, ROUND_HALF_UP

        def yen(v):
            return int(Decimal(v).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

        fl = Decimal(str(profit_setting_row["fl_ratio"]))
        f  = Decimal(str(profit_setting_row["food_ratio"]))
        u  = Decimal(str(profit_setting_row["utility_ratio"]))
        fixed = Decimal(str(profit_setting_row["fixed_cost_yen"]))
        l = fl - f

        cogs = Decimal(str(cogs_by_month.get(profit_ym) or 0))

        ideal_sales = cogs / f
        ideal_labor = ideal_sales * l
        utility = ideal_sales * u
        contrib = ideal_sales - cogs - ideal_labor - utility
        est_profit = contrib - fixed

        profit_est = {
            "fl_ratio": float(fl),
            "food_ratio": float(f),
            "l_ratio": float(l),
            "utility_ratio": float(u),
            "fixed_cost_yen": int(profit_setting_row["fixed_cost_yen"]),
            "ideal_sales_yen": yen(ideal_sales),
            "cogs_yen": yen(cogs),
            "ideal_labor_yen": yen(ideal_labor),
            "utility_yen": yen(utility),
            "contrib_yen": yen(contrib),
            "est_profit_yen": yen(est_profit),
        }


    def fetch_profit_setting(db, store_id: int, month_start: date):
      return db.execute(
        """
        SELECT
          effective_from,
          fl_ratio,
          food_ratio,
          utility_ratio,
          fixed_cost_yen,
          store_id
        FROM mst_profit_settings
        WHERE (store_id = %s OR store_id IS NULL)
          AND effective_from <= %s
          AND (effective_to IS NULL OR effective_to >= %s)
        ORDER BY
          (store_id IS NOT NULL) DESC,
          effective_from DESC
        LIMIT 1
        """,
        [store_id, month_start, month_start],
    ).fetchone()

    def calc_profit_estimate(cogs_yen: float, setting_row):
        if not setting_row:
            return None
        if not cogs_yen:
            return None

        fl = Decimal(str(setting_row["fl_ratio"]))
        f  = Decimal(str(setting_row["food_ratio"]))
        u = Decimal(str(setting_row["utility_ratio"]))
        fixed = Decimal(str(setting_row["fixed_cost_yen"]))

        l = fl - f

        cogs = Decimal(str(cogs_yen))

        ideal_sales = cogs / f
        ideal_labor = ideal_sales * l
        utility = ideal_sales * u
        contrib = ideal_sales - cogs - ideal_labor - utility
        est_profit = contrib - fixed

        return {
            "fl_ratio": float(fl),
            "food_ratio": float(f),
            "l_ratio": float(l),
            "utility_ratio": float(u),
            "fixed_cost_yen": int(setting_row["fixed_cost_yen"]),
            "setting_store_id": setting_row["store_id"],  # None => global

            "ideal_sales_yen": _yen(ideal_sales),
            "cogs_yen": _yen(cogs),
            "ideal_labor_yen": _yen(ideal_labor),
            "utility_yen": _yen(utility),
            "contrib_yen": _yen(contrib),
            "est_profit_yen": _yen(est_profit),
        }


    return render_template(
        "inv/cost_report.html",
        mst_stores=mst_stores,
        stores=mst_stores,
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
        profit_ym=profit_ym,
        profit_setting_row=profit_setting_row,
        profit_est=profit_est,
    )
