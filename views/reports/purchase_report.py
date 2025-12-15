from __future__ import annotations
from . import reports_bp

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


