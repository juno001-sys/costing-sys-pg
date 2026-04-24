from __future__ import annotations

from datetime import datetime

from flask import render_template, request,g
from utils.access_scope import (
    get_accessible_stores,
    normalize_accessible_store_id,
)

from . import reports_bp, get_db, shift_ym, parse_to_ym, month_keys_ending_at


@reports_bp.route("/purchases/report", methods=["GET"])
def purchase_report():
    db = get_db()

    mst_stores = get_accessible_stores()

    selected_store_id = normalize_accessible_store_id(
        request.args.get("store_id")
    )

    today = datetime.now().date()
    current_ym = f"{today.year:04d}-{today.month:02d}"
    to_ym = parse_to_ym(request.args.get("to_ym"), current_ym)

    month_keys = month_keys_ending_at(to_ym, 12)
    prev_to_ym = shift_ym(to_ym, -12)
    next_to_ym = shift_ym(to_ym, 12)
    is_current = (to_ym == current_ym)

    # Order-support pattern: empty state until a store is picked.
    if not selected_store_id:
        return render_template(
            "pur/purchase_report.html",
            mst_stores=mst_stores,
            selected_store_id=None,
            rows=[],
            month_keys=month_keys,
            month_totals=[0] * len(month_keys),
            to_ym=to_ym,
            prev_to_ym=prev_to_ym,
            next_to_ym=next_to_ym,
            is_current=is_current,
            no_store_selected=True,
        )

    start_date = month_keys[0] + "-01"
    ey, em = map(int, month_keys[-1].split("-"))
    end_date = f"{ey + (em == 12):04d}-{1 if em == 12 else em + 1:02d}-01"

    company_id = getattr(g, "current_company_id", None)

    rows_raw = db.execute(
        """
        SELECT
            s.id AS supplier_id,
            s.name AS supplier_name,
            TO_CHAR(p.delivery_date, 'YYYY-MM') AS ym,
            SUM(p.amount) AS total_amount
        FROM purchases p
        LEFT JOIN mst_items i ON p.item_id = i.id
        LEFT JOIN pur_suppliers s ON i.supplier_id = s.id
        LEFT JOIN mst_stores st ON p.store_id = st.id
        WHERE p.is_deleted = 0
          AND p.delivery_date >= %s
          AND p.delivery_date < %s
          AND st.company_id = %s
          AND p.store_id = %s
        GROUP BY s.id, s.name, ym
        ORDER BY s.id, ym
        """,
        [start_date, end_date, company_id, selected_store_id],
    ).fetchall()

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
        "pur/purchase_report.html",
        mst_stores=mst_stores,
        selected_store_id=selected_store_id,
        rows=rows,
        month_keys=month_keys,
        month_totals=month_totals,
        to_ym=to_ym,
        prev_to_ym=prev_to_ym,
        next_to_ym=next_to_ym,
        is_current=is_current,
        no_store_selected=False,
    )
