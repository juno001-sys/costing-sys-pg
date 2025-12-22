from __future__ import annotations

from datetime import datetime

from flask import render_template, request

from . import reports_bp, get_db


@reports_bp.route("/purchases/report", methods=["GET"])
def purchase_report():
    db = get_db()

    mst_stores = db.execute(
        "SELECT id, name FROM mst_stores ORDER BY code"
    ).fetchall()

    store_id = request.args.get("store_id") or ""

    # last 13 months
    today = datetime.now().date()
    year, month = today.year, today.month

    month_keys = []
    for _ in range(13):
        month_keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    month_keys.reverse()

    start_date = month_keys[0] + "-01"
    ey, em = map(int, month_keys[-1].split("-"))
    end_date = f"{ey + (em == 12):04d}-{1 if em == 12 else em + 1:02d}-01"

    where = [
        "p.is_deleted = 0",
        "p.delivery_date >= %s",
        "p.delivery_date < %s",
    ]
    params: list[object] = [start_date, end_date]

    if store_id:
        where.append("p.store_id = %s")
        params.append(int(store_id))

    sql = f"""
        SELECT
            s.id AS supplier_id,
            s.name AS supplier_name,
            TO_CHAR(p.delivery_date, 'YYYY-MM') AS ym,
            SUM(p.amount) AS total_amount
        FROM purchases p
        LEFT JOIN mst_items i ON p.item_id = i.id
        LEFT JOIN suppliers s ON i.supplier_id = s.id
        WHERE {' AND '.join(where)}
        GROUP BY s.id, s.name, ym
        ORDER BY s.id, ym
    """

    rows_raw = db.execute(sql, params).fetchall()

    supplier_map = {}
    for r in rows_raw:
        sid = r["supplier_id"] or 0
        if sid not in supplier_map:
            supplier_map[sid] = {
                "supplier_id": sid,
                "supplier_name": r["supplier_name"] or "(Unknown)",
                "values": {k: 0 for k in month_keys},  # ensure all months exist
                "total": 0,
            }

        ym = r["ym"]
        amt = r["total_amount"] or 0
        if ym in supplier_map[sid]["values"]:
            supplier_map[sid]["values"][ym] = amt
        supplier_map[sid]["total"] += amt

    rows = list(supplier_map.values())

    month_totals = [
        sum(r["values"].get(ym, 0) for r in rows)
        for ym in month_keys
    ]

    return render_template(
        "purchase_report.html",
        mst_stores=mst_stores,
        selected_store_id=int(store_id) if store_id else None,
        rows=rows,
        month_keys=month_keys,
        month_totals=month_totals,
    )
