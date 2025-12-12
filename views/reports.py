# views/reports.py

import os
from datetime import datetime

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
)


def init_report_views(app, get_db):
    """
    app.py 側から呼び出してレポート系ルートを登録する初期化関数。

        from views.reports import init_report_views
        init_report_views(app, get_db)

    という形で使います。
    """

    # ----------------------------------------
    # 仕入れ照会（月次・直近13ヶ月）
    # ----------------------------------------
    @app.route("/purchases/report", methods=["GET"])
    def purchase_report():
        db = get_db()

        # 店舗一覧
        stores = db.execute(
            "SELECT id, name FROM stores ORDER BY code"
        ).fetchall()

        # 店舗（クエリパラメータ）
        store_id = request.args.get("store_id") or ""

        # 今日を基準に直近13ヶ月
        today = datetime.now().date()
        year = today.year
        month = today.month

        month_keys = []
        for _ in range(13):
            month_keys.append(f"{year:04d}-{month:02d}")
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        month_keys = list(reversed(month_keys))

        # 日付範囲の開始・終了
        start_ym = month_keys[0]
        end_ym = month_keys[-1]

        start_date = f"{start_ym}-01"

        end_year = int(end_ym[:4])
        end_month = int(end_ym[5:7])
        if end_month == 12:
            next_year = end_year + 1
            next_month = 1
        else:
            next_year = end_year
            next_month = end_month + 1
        end_date = f"{next_year:04d}-{next_month:02d}-01"

        # where句の作成
        where_clauses = [
            "p.is_deleted = 0",
            "p.delivery_date >= %s",
            "p.delivery_date < %s",
        ]
        params = [start_date, end_date]

        if store_id:
            where_clauses.append("p.store_id = %s")
            params.append(store_id)

        where_sql = " AND ".join(where_clauses)

        # Postgres版SQL
        sql = f"""
            SELECT
                s.id   AS supplier_id,
                s.name AS supplier_name,
                TO_CHAR(p.delivery_date, 'YYYY-MM') AS ym,
                SUM(p.amount) AS total_amount
            FROM purchases p
            LEFT JOIN items i     ON p.item_id = i.id
            LEFT JOIN suppliers s ON i.supplier_id = s.id
            WHERE {where_sql}
            GROUP BY s.id, s.name, ym
            ORDER BY s.id, ym
        """

        rows_raw = db.execute(sql, params).fetchall()

        # ピボット整形
        supplier_map = {}
        for r in rows_raw:
            sid = r["supplier_id"] or 0
            sname = r["supplier_name"] or "(仕入先不明)"
            ym = r["ym"]
            amt = r["total_amount"] or 0

            if sid not in supplier_map:
                supplier_map[sid] = {
                    "supplier_id": sid,
                    "supplier_name": sname,
                    "values": {},
                    "total": 0,
                }

            supplier_map[sid]["values"][ym] = amt
            supplier_map[sid]["total"] += amt

        rows = list(supplier_map.values())

        # 月ごとの合計
        month_totals = []
        for ym in month_keys:
            col_sum = 0
            for r in rows:
                col_sum += r["values"].get(ym, 0)
            month_totals.append(col_sum)

        selected_store_id = int(store_id) if store_id else None

        return render_template(
            "purchase_report.html",
            stores=stores,
            selected_store_id=selected_store_id,
            rows=rows,
            month_keys=month_keys,
            month_totals=month_totals,
        )

    # ----------------------------------------
    # 仕入れ照会（月次・仕入先別 → 品目別）
    # ----------------------------------------
    @app.route("/purchases/report/supplier/<int:supplier_id>", methods=["GET"])
    def purchase_report_supplier(supplier_id):
        db = get_db()

        # 店舗一覧
        stores = db.execute(
            "SELECT id, name FROM stores ORDER BY code"
        ).fetchall()

        # 仕入先一覧
        suppliers = db.execute(
            "SELECT id, name FROM suppliers ORDER BY code"
        ).fetchall()

        # 店舗（クエリパラメータ）
        store_id = request.args.get("store_id") or ""

        # 仕入先名
        if supplier_id == 0:
            supplier_name = "（仕入先を選択してください）"
        else:
            supplier_row = db.execute(
                "SELECT id, name FROM suppliers WHERE id = %s",
                [supplier_id],
            ).fetchone()
            if supplier_row is None:
                return redirect(url_for("purchase_report"))
            supplier_name = supplier_row["name"]

        # 直近13ヶ月
        today = datetime.now().date()
        year = today.year
        month = today.month

        month_keys = []
        for _ in range(13):
            month_keys.append(f"{year:04d}-{month:02d}")
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        month_keys = list(reversed(month_keys))

        # 日付範囲
        start_ym = month_keys[0]
        end_ym = month_keys[-1]

        start_date = f"{start_ym}-01"

        end_year = int(end_ym[:4])
        end_month = int(end_ym[5:7])
        if end_month == 12:
            next_year = end_year + 1
            next_month = 1
        else:
            next_year = end_year
            next_month = end_month + 1
        end_date = f"{next_year:04d}-{next_month:02d}-01"

        rows_raw = []

        if supplier_id != 0:
            where_clauses = [
                "p.is_deleted = 0",
                "p.delivery_date >= %s",
                "p.delivery_date < %s",
                "i.supplier_id = %s",
            ]
            params = [start_date, end_date, supplier_id]

            if store_id:
                where_clauses.append("p.store_id = %s")
                params.append(store_id)

            where_sql = " AND ".join(where_clauses)

            # Postgres用のSQL（strftime → TO_CHAR）
            sql = f"""
                SELECT
                    i.id   AS item_id,
                    i.code AS item_code,
                    i.name AS item_name,
                    TO_CHAR(p.delivery_date, 'YYYY-MM') AS ym,
                    SUM(p.quantity) AS total_qty,
                    SUM(p.amount)   AS total_amount
                FROM purchases p
                LEFT JOIN items i ON p.item_id = i.id
                WHERE {where_sql}
                GROUP BY i.id, i.code, i.name, ym
                ORDER BY i.code, ym
            """
            rows_raw = db.execute(sql, params).fetchall()

        # ピボット整形
        item_map = {}
        for r in rows_raw:
            iid = r["item_id"] or 0
            icode = r["item_code"] or ""
            iname = r["item_name"] or "(品目不明)"
            ym = r["ym"]
            amt = r["total_amount"] or 0
            qty = r["total_qty"] or 0

            if iid not in item_map:
                item_map[iid] = {
                    "item_id": iid,
                    "item_code": icode,
                    "item_name": iname,
                    "amount": {k: 0 for k in month_keys},
                    "qty": {k: 0 for k in month_keys},
                    "unit_price": {k: 0 for k in month_keys},
                    "total_amount": 0,
                    "total_qty": 0,
                }

            item_map[iid]["amount"][ym] += amt
            item_map[iid]["qty"][ym] += qty
            item_map[iid]["total_amount"] += amt
            item_map[iid]["total_qty"] += qty

        # 単価計算
        for item in item_map.values():
            for ym in month_keys:
                a = item["amount"][ym]
                q = item["qty"][ym]
                item["unit_price"][ym] = (a / q) if q else 0

        item_rows = list(item_map.values())

        # 月ごとの金額合計・数量合計
        month_totals_amount = []
        month_totals_qty = []
        for ym in month_keys:
            col_amt = 0
            col_qty = 0
            for r in item_rows:
                col_amt += r["amount"].get(ym, 0)
                col_qty += r["qty"].get(ym, 0)
            month_totals_amount.append(col_amt)
            month_totals_qty.append(col_qty)

        selected_store_id = int(store_id) if store_id else None

        return render_template(
            "purchase_report_supplier.html",
            stores=stores,
            selected_store_id=selected_store_id,
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            month_keys=month_keys,
            item_rows=item_rows,
            month_totals_amount=month_totals_amount,
            month_totals_qty=month_totals_qty,
            suppliers=suppliers,
        )

    # ----------------------------------------
    # 月次利用量レポート（Postgres/SQLite 両対応版）
    # /usage/report
    # ----------------------------------------
    @app.route("/usage/report", methods=["GET"])
    def usage_report():
        db = get_db()
    
        # 店舗一覧
        stores = db.execute(
            "SELECT id, name FROM stores ORDER BY code"
        ).fetchall()
    
        # 仕入先一覧（プルダウン用）
        suppliers = db.execute(
            "SELECT id, name FROM suppliers ORDER BY code"
        ).fetchall()
    
        # クエリパラメータ
        store_id = request.args.get("store_id") or ""
        selected_store_id = int(store_id) if store_id else None
    
        supplier_id = request.args.get("supplier_id") or ""
        selected_supplier_id = int(supplier_id) if supplier_id else None
    
        # 直近13ヶ月
        today = datetime.now().date()
        year = today.year
        month = today.month
    
        month_keys = []
        for _ in range(13):
            month_keys.append(f"{year:04d}-{month:02d}")
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        month_keys = list(reversed(month_keys))
    
        # 日付範囲（[start_date, end_date)）
        start_ym = month_keys[0]
        end_ym = month_keys[-1]
    
        start_date = f"{start_ym}-01"
        end_year = int(end_ym[:4])
        end_month = int(end_ym[5:7])
        if end_month == 12:
            next_year = end_year + 1
            next_month = 1
        else:
            next_year = end_year
            next_month = end_month + 1
        end_date = f"{next_year:04d}-{next_month:02d}-01"
    
        # Postgres / SQLite 切り替え
        is_postgres = (os.environ.get("DB_MODE") == "postgres")
    
        # ----------------------------------------
        # ① 月内仕入数量（仕入先フィルタ対応）
        # ----------------------------------------
        where_pur = [
            "p.delivery_date >= %s" if is_postgres else "p.delivery_date >= ?",
            "p.delivery_date < %s"  if is_postgres else "p.delivery_date < ?",
            "p.is_deleted = 0",
        ]
        params_pur = [start_date, end_date]
    
        if store_id:
            where_pur.append("p.store_id = %s" if is_postgres else "p.store_id = ?")
            params_pur.append(store_id)
    
        if supplier_id:
            where_pur.append("p.supplier_id = %s" if is_postgres else "p.supplier_id = ?")
            params_pur.append(supplier_id)
    
        where_pur_sql = " AND ".join(where_pur)
    
        date_expr_p = "TO_CHAR(p.delivery_date, 'YYYY-MM')" if is_postgres else "strftime('%Y-%m', p.delivery_date)"
    
        sql_pur = f"""
            SELECT
                p.item_id,
                {date_expr_p} AS ym,
                SUM(p.quantity) AS pur_qty
            FROM purchases p
            WHERE {where_pur_sql}
            GROUP BY p.item_id, ym
        """
    
        rows_pur = db.execute(sql_pur, params_pur).fetchall()
    
        pur_map = {}
        for r in rows_pur:
            iid = r["item_id"]
            ym = r["ym"]
            qty = int(r["pur_qty"] or 0)
            pur_map.setdefault(iid, {})[ym] = qty
    
        # ----------------------------------------
        # ② 各月の最新棚卸数量（店舗のみフィルタ）
        # ----------------------------------------
        where_inv = [
            "sc.count_date >= %s" if is_postgres else "sc.count_date >= ?",
            "sc.count_date < %s"  if is_postgres else "sc.count_date < ?",
        ]
        params_inv = [start_date, end_date]
    
        if store_id:
            where_inv.append("sc.store_id = %s" if is_postgres else "sc.store_id = ?")
            params_inv.append(store_id)
    
        where_inv_sql = " AND ".join(where_inv)
    
        date_expr_sc = "TO_CHAR(sc.count_date, 'YYYY-MM')" if is_postgres else "strftime('%Y-%m', sc.count_date)"
    
        sql_inv = f"""
            WITH last_counts AS (
              SELECT
                sc.store_id,
                sc.item_id,
                {date_expr_sc} AS ym,
                MAX(sc.count_date) AS max_date
              FROM stock_counts sc
              WHERE {where_inv_sql}
              GROUP BY sc.store_id, sc.item_id, ym
            ),
            month_end_inventory AS (
              SELECT
                lc.store_id,
                lc.item_id,
                lc.ym,
                sc.counted_qty
              FROM last_counts lc
              JOIN stock_counts sc
                ON sc.store_id  = lc.store_id
               AND sc.item_id   = lc.item_id
               AND sc.count_date = lc.max_date
            )
            SELECT item_id, ym, counted_qty
            FROM month_end_inventory
            ORDER BY item_id, ym
        """
    
        rows_inv = db.execute(sql_inv, params_inv).fetchall()
    
        end_inv_map = {}
        for r in rows_inv:
            iid = r["item_id"]
            ym = r["ym"]
            qty = int(r["counted_qty"] or 0)
            end_inv_map.setdefault(iid, {})[ym] = qty
    
        # ----------------------------------------
        # ③ アイテム情報（ここでも仕入先フィルタ）
        # ----------------------------------------
        item_ids = set(pur_map.keys()) | set(end_inv_map.keys())
        if item_ids:
            placeholders = ",".join(
                ["%s" if is_postgres else "?"] * len(item_ids)
            )
            sql_items = f"""
                SELECT id, code, name, supplier_id
                FROM items
                WHERE id IN ({placeholders})
            """
            params_items = list(item_ids)
    
            if supplier_id:
                sql_items += " AND supplier_id = %s" if is_postgres else " AND supplier_id = ?"
                params_items.append(supplier_id)
    
            items = db.execute(sql_items, params_items).fetchall()
        else:
            items = []
    
        item_meta = {row["id"]: row for row in items}
    
        # ----------------------------------------
        # ④ 期首・仕入・期末・利用量を計算
        # ----------------------------------------
        item_rows = []
    
        for iid in sorted(item_ids):
            meta = item_meta.get(iid)
            if not meta:
                # 仕入先フィルタで落ちたアイテムはスキップ
                continue
    
            code = meta["code"]
            name = meta["name"]
    
            per_month = {}
            total_pur = 0
            total_used = 0
            total_end = 0
    
            prev_end_qty = 0  # 前月の期末＝当月の期首
    
            for ym in month_keys:
                pur = pur_map.get(iid, {}).get(ym, 0)
                end_qty = end_inv_map.get(iid, {}).get(ym, 0)
    
                begin_qty = prev_end_qty
                used = begin_qty + pur - end_qty
    
                per_month[ym] = {
                    "begin_qty": begin_qty,
                    "pur_qty": pur,
                    "end_qty": end_qty,
                    "used_qty": used,
                }
    
                total_pur += pur
                total_used += used
                total_end = end_qty
    
                prev_end_qty = end_qty
    
            item_rows.append(
                {
                    "item_id": iid,
                    "item_code": code,
                    "item_name": name,
                    "per_month": per_month,
                    "total_pur": total_pur,
                    "total_used": total_used,
                    "total_end": total_end,
                }
            )
    
        # 使用量順に並べたい場合はこれを有効化
        item_rows.sort(key=lambda x: x["total_used"], reverse=True)
    
        return render_template(
            "usage_report.html",
            stores=stores,
            selected_store_id=selected_store_id,
            suppliers=suppliers,
            selected_supplier_id=selected_supplier_id,
            month_keys=month_keys,
            item_rows=item_rows,
        )
    
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
