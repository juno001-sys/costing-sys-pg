from __future__ import annotations

from datetime import datetime

from flask import redirect, render_template, request, url_for

from . import reports_bp, get_db


# ----------------------------------------
# 仕入れ照会（月次・仕入先別 → 品目別）
# ----------------------------------------
@reports_bp.route("/purchases/report/supplier/<int:supplier_id>", methods=["GET"])
def purchase_report_supplier(supplier_id: int):
    db = get_db()

    # 店舗一覧
    mst_stores = db.execute(
        "SELECT id, name FROM mst_stores ORDER BY code"
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
            return redirect(url_for("reports.purchase_report"))
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
        params: list[object] = [start_date, end_date, supplier_id]

        if store_id:
            where_clauses.append("p.store_id = %s")
            params.append(int(store_id))

        where_sql = " AND ".join(where_clauses)

        sql = f"""
            SELECT
                i.id   AS item_id,
                i.code AS item_code,
                i.name AS item_name,
                TO_CHAR(p.delivery_date, 'YYYY-MM') AS ym,
                SUM(p.quantity) AS total_qty,
                SUM(p.amount)   AS total_amount
            FROM purchases p
            LEFT JOIN mst_items i ON p.item_id = i.id
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
        "pur/purchase_report_supplier.html",
        mst_stores=mst_stores,
        selected_store_id=selected_store_id,
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        month_keys=month_keys,
        item_rows=item_rows,
        month_totals_amount=month_totals_amount,
        month_totals_qty=month_totals_qty,
        suppliers=suppliers,
    )
