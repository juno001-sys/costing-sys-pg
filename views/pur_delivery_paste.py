# views/pur_delivery_paste.py
# Paste-and-parse delivery note (納品書) from タノム-style email → purchase records

import json
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, g


def init_delivery_paste_views(app, get_db):

    # ── GET: show paste screen ────────────────────────────────────────────────
    @app.route("/pur/delivery_paste", methods=["GET"], endpoint="delivery_paste")
    def delivery_paste():
        db         = get_db()
        company_id = getattr(g, "current_company_id", None)

        # All active items (for client-side matching dropdown)
        items = db.execute(
            """
            SELECT i.id, i.code, i.name,
                   s.name AS supplier_name, s.id AS supplier_id
            FROM mst_items i
            JOIN pur_suppliers s ON i.supplier_id = s.id
            WHERE i.is_active = 1
              AND i.company_id = %s
            ORDER BY i.name
            """,
            (company_id,),
        ).fetchall()

        from utils.access_scope import get_accessible_stores
        stores = get_accessible_stores()

        suppliers = db.execute(
            "SELECT id, name, code FROM pur_suppliers "
            "WHERE is_active = 1 AND company_id = %s ORDER BY code",
            (company_id,),
        ).fetchall()

        items_list = [
            {
                "id":            r["id"],
                "code":          r["code"],
                "name":          r["name"],
                "supplier_name": r["supplier_name"],
                "supplier_id":   r["supplier_id"],
            }
            for r in items
        ]

        return render_template(
            "pur/delivery_paste.html",
            items_json=json.dumps(items_list, ensure_ascii=False),
            stores=stores,
            suppliers=suppliers,
        )

    # ── POST: save parsed rows as purchase records ───────────────────────────
    @app.route("/pur/delivery_paste/save", methods=["POST"], endpoint="delivery_paste_save")
    def delivery_paste_save():
        db         = get_db()
        company_id = getattr(g, "current_company_id", None)

        store_id      = request.form.get("store_id") or None
        supplier_id   = request.form.get("supplier_id")
        delivery_date = request.form.get("delivery_date")
        rows_json     = request.form.get("rows_json", "[]")

        if not supplier_id or not delivery_date:
            flash("仕入先と納品日は必須です。")
            return redirect(url_for("delivery_paste"))

        try:
            rows = json.loads(rows_json)
        except Exception:
            flash("データの読み込みに失敗しました。")
            return redirect(url_for("delivery_paste"))

        if not rows:
            flash("保存するデータがありません。品目マスタを選択してください。")
            return redirect(url_for("delivery_paste"))

        inserted = 0
        skipped  = 0

        try:
            for row in rows:
                item_id    = row.get("item_id")
                quantity   = row.get("quantity", 0)
                unit_price = row.get("unit_price", 0)

                if not item_id or not quantity:
                    skipped += 1
                    continue

                amount = int(quantity) * int(unit_price)

                db.execute(
                    """
                    INSERT INTO purchases
                        (store_id, supplier_id, item_id,
                         delivery_date, quantity, unit_price, amount, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        store_id,
                        int(supplier_id),
                        int(item_id),
                        delivery_date,
                        int(quantity),
                        int(unit_price),
                        amount,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                inserted += 1

            db.commit()

        except Exception as e:
            db.rollback()
            flash(f"保存中にエラーが発生しました: {e}")
            return redirect(url_for("delivery_paste"))

        if inserted == 0:
            flash("⚠️ 保存できる行がありませんでした。品目マスタの選択を確認してください。")
        else:
            flash(f"✅ {inserted}件の仕入れ記録を登録しました。"
                  + (f"（{skipped}件スキップ）" if skipped else ""))

        return redirect(url_for("new_purchase"))
