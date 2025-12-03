from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from datetime import datetime

purchase_bp = Blueprint("purchase", __name__)

# --------------------------------------------------
# 仕入れ入力フォーム（/purchase/new）
# --------------------------------------------------
@purchase_bp.route("/new", methods=["GET", "POST"])
def new_purchase():
    # ★ 循環 import 回避のため、関数内で import する
    from app import get_db
    db = get_db()

    # 店舗一覧
    stores = db.execute(
        "SELECT id, name FROM stores ORDER BY code"
    ).fetchall()

    # 仕入先一覧
    suppliers = db.execute(
        "SELECT id, name FROM suppliers ORDER BY code"
    ).fetchall()

    # NameError対策
    purchases = []
    results_count = 0

    if request.method == "POST":
        store_id = request.form.get("store_id") or None

        def to_int(val: str) -> int:
            if not val:
                return 0
            s = str(val)
            s = "".join(
                chr(ord(c) - 0xFEE0) if "０" <= c <= "９" else c
                for c in s
            )
            s = s.replace(",", "")
            try:
                return int(s)
            except ValueError:
                return 0

        any_inserted = False

        # 明細5行ループ
        for i in range(1, 6):
            delivery_date = request.form.get(f"detail_date_{i}") or ""
            supplier_id = request.form.get(f"supplier_id_{i}") or ""
            item_id = request.form.get(f"item_id_{i}") or ""
            qty_raw = request.form.get(f"quantity_{i}") or ""
            unit_price_raw = request.form.get(f"unit_price_{i}") or ""

            qty_val = to_int(qty_raw)
            unit_price_val = to_int(unit_price_raw)
            amount_val = qty_val * unit_price_val

            if (
                not delivery_date
                and not supplier_id
                and not item_id
                and qty_val == 0
                and unit_price_val == 0
            ):
                continue

            if not delivery_date or not item_id:
                continue

            cur = db.execute(
                """
                INSERT INTO purchases
                  (store_id, supplier_id, item_id,
                   delivery_date, quantity, unit_price, amount, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    store_id,
                    supplier_id or None,
                    item_id or None,
                    delivery_date,
                    qty_val,
                    unit_price_val,
                    amount_val,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            any_inserted = True

        if any_inserted:
            db.commit()
            flash("取引を登録しました。")
        else:
            flash("登録対象の行がありません。")

        if store_id:
            return redirect(url_for("purchase.new_purchase", store_id=store_id))
        else:
            return redirect(url_for("purchase.new_purchase"))

    # GET
    store_id = request.args.get("store_id") or ""
    selected_store_id = int(store_id) if store_id else None

    if request.args.get("clear") == "1":
        if store_id:
            return redirect(url_for("purchase.new_purchase", store_id=store_id))
        else:
            return redirect(url_for("purchase.new_purchase"))

    from_date = request.args.get("from_date") or ""
    to_date = request.args.get("to_date") or ""
    search_q = (request.args.get("q") or "").strip()

    where_clauses = ["p.is_deleted = 0"]
    params = []

    if store_id:
        where_clauses.append("p.store_id = ?")
        params.append(store_id)

    if from_date:
        where_clauses.append("p.delivery_date >= ?")
        params.append(from_date)

    if to_date:
        where_clauses.append("p.delivery_date <= ?")
        params.append(to_date)

    if search_q:
        where_clauses.append(
            "(i.name LIKE ? OR s.name LIKE ? OR i.code LIKE ?)"
        )
        like = f"%{search_q}%"
        params.extend([like, like, like])

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    sql = f"""
        SELECT
          p.id,
          p.delivery_date,
          s.name AS supplier_name,
          i.name AS item_name,
          p.quantity,
          p.unit_price,
          p.amount
        FROM purchases p
        LEFT JOIN suppliers s ON p.supplier_id = s.id
        LEFT JOIN items     i ON p.item_id     = i.id
        {where_sql}
        ORDER BY p.delivery_date DESC, p.id DESC
        LIMIT 50
    """

    purchases = db.execute(sql, params).fetchall()

    return render_template(
        "purchase_form.html",
        stores=stores,
        suppliers=suppliers,
        purchases=purchases,
        selected_store_id=selected_store_id,
        from_date=from_date,
        to_date=to_date,
        search_q=search_q,
    )


# --------------------------------------------------
# API: supplier に紐づく items 一覧
# --------------------------------------------------
@purchase_bp.route("/api/items/by_supplier/<int:supplier_id>")
def api_items_by_supplier(supplier_id):
    from app import get_db
    db = get_db()

    rows = db.execute(
        """
        SELECT id, code, name, unit
        FROM items
        WHERE supplier_id = ?
        ORDER BY name
        """,
        (supplier_id,),
    ).fetchall()

    return jsonify([
        {"id": r["id"], "code": r["code"], "name": r["name"], "unit": r["unit"]}
        for r in rows
    ])
