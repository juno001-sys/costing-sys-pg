# views/purchases.py

from datetime import datetime

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
)


def init_purchase_views(app, get_db, log_purchase_change):
    """
    app.py 側から呼び出してルートを登録する初期化関数。

        from views.purchases import init_purchase_views
        init_purchase_views(app, get_db, log_purchase_change)

    という形で使います。
    """

    # ----------------------------------------
    # 取引入力（納品書）
    # /purchases/new
    # ----------------------------------------
    @app.route("/purchases/new", methods=["GET", "POST"])
    def new_purchase():
        db = get_db()

        # 店舗一覧
        stores = db.execute(
            "SELECT id, name FROM stores ORDER BY code"
        ).fetchall()

        # 仕入先一覧
        suppliers = db.execute(
            "SELECT id, name FROM suppliers ORDER BY code"
        ).fetchall()

        # ----------------------------------------------------
        # POST: 登録（新規 INSERT）処理
        #   ヘッダー：store_id, supplier_id, delivery_date
        #   明細   ：item_id_i, quantity_i, unit_price_i (i=1..row_count)
        # ----------------------------------------------------
        if request.method == "POST":
            store_id = request.form.get("store_id") or None
            header_supplier_id = request.form.get("supplier_id") or None
            delivery_date = request.form.get("delivery_date") or ""

            # 必須チェック（ブラウザ側でも required だが念のため）
            if not store_id or not header_supplier_id or not delivery_date:
                flash("店舗・仕入先・納品日は必須です。")
                return redirect(url_for("new_purchase", store_id=store_id or ""))

            # 行数（＋行追加ボタンで増える）
            try:
                row_count = int(request.form.get("row_count") or 0)
            except ValueError:
                row_count = 0

            def to_int(val: str) -> int:
                """カンマ入り・全角混じりでも、とにかく int にする保険関数"""
                if not val:
                    return 0
                s = str(val)
                # 全角数字 → 半角
                s = "".join(
                    chr(ord(c) - 0xFEE0) if "０" <= c <= "９" else c
                    for c in s
                )
                # カンマ除去
                s = s.replace(",", "")
                try:
                    return int(s)
                except ValueError:
                    return 0

            any_inserted = False

            for i in range(1, row_count + 1):
                item_id = request.form.get(f"item_id_{i}") or ""
                qty_raw = request.form.get(f"quantity_{i}") or ""
                unit_price_raw = request.form.get(f"unit_price_{i}") or ""

                qty_val = to_int(qty_raw)
                unit_price_val = to_int(unit_price_raw)
                amount_val = qty_val * unit_price_val

                # 完全に空行ならスキップ
                if not item_id and qty_val == 0 and unit_price_val == 0:
                    continue

                # 最低限の必須：品目
                if not item_id:
                    # その行だけスキップ
                    continue

                # INSERT（★RETURNING id）
                cur = db.execute(
                    """
                    INSERT INTO purchases
                      (store_id, supplier_id, item_id,
                       delivery_date, quantity, unit_price, amount, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (
                        store_id,
                        header_supplier_id,
                        item_id,
                        delivery_date,
                        qty_val,
                        unit_price_val,
                        amount_val,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )

                row = cur.fetchone()
                new_id = row["id"]

                # ログ用に新レコードを読み直し
                new_row = db.execute(
                    "SELECT * FROM purchases WHERE id = ?",
                    (new_id,),
                ).fetchone()

                # CREATE ログを記録
                log_purchase_change(
                    db,
                    purchase_id=new_id,
                    action="CREATE",
                    old_row=None,
                    new_row=new_row,
                    changed_by=None,
                )

                any_inserted = True

            if any_inserted:
                db.commit()
                flash("取引を登録しました。")
            else:
                flash("登録対象の行がありません。")

            # ヘッダーで選んだ店舗を維持して再表示
            return redirect(url_for("new_purchase", store_id=store_id or ""))

        # ----------------------------------------------------
        # GET: 画面表示（フォーム + 検索付き直近50件）
        # ----------------------------------------------------
        store_id = request.args.get("store_id") or ""
        selected_store_id = int(store_id) if store_id else None

        # 条件クリアボタン
        if request.args.get("clear") == "1":
            if store_id:
                return redirect(url_for("new_purchase", store_id=store_id))
            else:
                return redirect(url_for("new_purchase"))

        # 検索条件
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

    
    # ----------------------------------------
    # API: 仕入先に紐づく品目一覧を返す
    # /api/items/by_supplier/<supplier_id>
    # ----------------------------------------
    @app.route("/api/items/by_supplier/<int:supplier_id>")
    def api_items_by_supplier(supplier_id):
        db = get_db()
        rows = db.execute(
            """
            SELECT 
                i.id,
                i.code,
                i.name,
                i.unit,
                COALESCE(SUM(p.amount), 0) AS total_amount
            FROM items i
            LEFT JOIN purchases p 
                ON p.item_id = i.id
                AND p.supplier_id = i.supplier_id
                AND p.delivery_date >= CURRENT_DATE - INTERVAL '3 months'
                AND p.is_deleted = 0
            WHERE i.supplier_id = %s
            GROUP BY i.id, i.code, i.name, i.unit
            ORDER BY total_amount DESC, i.name ASC;
            """,
            (supplier_id,),
        ).fetchall()

        return jsonify(
            [
                {
                    "id": r["id"],
                    "code": r["code"],
                    "name": r["name"],
                    "unit": r["unit"],
                    "total_amount": r["total_amount"],
                }
                for r in rows
            ]
        )

    # ----------------------------------------
    # 取引編集・削除（ソフトデリート対応）
    # /purchases/<id>/edit
    # ----------------------------------------
    @app.route(
        "/purchases/<int:purchase_id>/edit",
        methods=["GET", "POST"],
        endpoint="edit_purchase",        # ★ url_for('edit_purchase') を壊さない
    )
    def edit_purchase(purchase_id):
        db = get_db()

        # 店舗・仕入先一覧（プルダウン用）
        stores = db.execute(
            "SELECT id, name FROM stores ORDER BY code"
        ).fetchall()

        suppliers = db.execute(
            "SELECT id, name FROM suppliers ORDER BY code"
        ).fetchall()

        # 対象の取引を取得
        purchase = db.execute(
            """
            SELECT
              p.id,
              p.store_id,
              p.supplier_id,
              p.item_id,
              p.delivery_date,
              p.quantity,
              p.unit_price,
              p.amount,
              i.code AS item_code,
              i.name AS item_name
            FROM purchases p
            LEFT JOIN items i ON p.item_id = i.id
            WHERE p.id = ?
              AND p.is_deleted = 0
            """,
            (purchase_id,),
        ).fetchone()

        if purchase is None:
            flash("指定された取引が見つかりません。")
            return redirect(url_for("new_purchase"))

        if request.method == "POST":
            # -------------------------
            # 削除（ソフトデリート）
            # -------------------------
            if "delete" in request.form:
                old_row = db.execute(
                    "SELECT * FROM purchases WHERE id = ?",
                    (purchase_id,),
                ).fetchone()

                db.execute(
                    """
                    UPDATE purchases
                    SET is_deleted = 1
                    WHERE id = ?
                    """,
                    (purchase_id,),
                )

                new_row = db.execute(
                    "SELECT * FROM purchases WHERE id = ?",
                    (purchase_id,),
                ).fetchone()

                log_purchase_change(
                    db,
                    purchase_id=purchase_id,
                    action="DELETE",
                    old_row=old_row,
                    new_row=new_row,
                    changed_by=None,
                )

                db.commit()
                flash("取引を削除しました。")

                return redirect(
                    url_for("new_purchase", store_id=purchase["store_id"])
                )

            # -------------------------
            # 更新処理
            # -------------------------
            store_id = request.form.get("store_id") or None
            delivery_date = request.form.get("delivery_date") or ""
            supplier_id = request.form.get("supplier_id") or None
            item_id = request.form.get("item_id") or None
            quantity = (request.form.get("quantity") or "").replace(",", "")
            unit_price = (request.form.get("unit_price") or "").replace(",", "")

            if not delivery_date or not item_id:
                flash("納品日と品目は必須です。")
                return render_template(
                    "purchase_edit.html",
                    purchase=purchase,
                    stores=stores,
                    suppliers=suppliers,
                )

            try:
                qty_val = int(quantity) if quantity else 0
            except ValueError:
                qty_val = 0

            try:
                unit_price_val = int(unit_price) if unit_price else 0
            except ValueError:
                unit_price_val = 0

            amount_val = qty_val * unit_price_val

            old_row = db.execute(
                "SELECT * FROM purchases WHERE id = ?",
                (purchase_id,),
            ).fetchone()

            db.execute(
                """
                UPDATE purchases
                SET
                  store_id     = ?,
                  supplier_id  = ?,
                  item_id      = ?,
                  delivery_date = ?,
                  quantity     = ?,
                  unit_price   = ?,
                  amount       = ?
                WHERE id = ?
                """,
                (
                    store_id,
                    supplier_id,
                    item_id,
                    delivery_date,
                    qty_val,
                    unit_price_val,
                    amount_val,
                    purchase_id,
                ),
            )

            new_row = db.execute(
                "SELECT * FROM purchases WHERE id = ?",
                (purchase_id,),
            ).fetchone()

            log_purchase_change(
                db,
                purchase_id=purchase_id,
                action="UPDATE",
                old_row=old_row,
                new_row=new_row,
                changed_by=None,
            )

            db.commit()
            flash("取引を更新しました。")

            return redirect(url_for("new_purchase", store_id=store_id))

        # GET のとき：編集画面表示
        return render_template(
            "purchase_edit.html",
            purchase=purchase,
            stores=stores,
            suppliers=suppliers,
        )
