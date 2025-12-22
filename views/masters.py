# views/masters.py

import sqlite3
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
)


def init_master_views(app, get_db):
    """
    マスタ系（suppliers/mst_items など）のルートを登録する初期化関数。

        from views.masters import init_master_views
        init_master_views(app, get_db)

    という形で app.py 側から呼び出します。
    """

    # ----------------------------------------
    # 仕入先マスタ
    # /suppliers
    # ----------------------------------------
    @app.route("/suppliers", methods=["GET", "POST"])
    def suppliers_master():
        db = get_db()

        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            code = (request.form.get("code") or "").strip()
            phone = (request.form.get("phone") or "").strip()
            email = (request.form.get("email") or "").strip()
            address = (request.form.get("address") or "").strip()

            if not name:
                flash("仕入先名は必須です。")
            else:
                try:
                    db.execute(
                        """
                        INSERT INTO suppliers (code, name, phone, email, address)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (code if code else None, name, phone, email, address),
                    )
                    db.commit()
                    flash("仕入先を登録しました。")
                except sqlite3.OperationalError as e:
                    flash(f"suppliers テーブルへの登録でエラーが発生しました: {e}")

            return redirect(url_for("suppliers_master"))

        # GET：有効な仕入先のみ表示
        suppliers = db.execute(
            """
            SELECT id, code, name, phone, email, address, is_active
            FROM suppliers
            WHERE is_active = 1
            ORDER BY code, id
            """
        ).fetchall()

        return render_template(
            "suppliers_master.html",
            suppliers=suppliers,
        )

    # ----------------------------------------
    # 仕入先編集・削除（実態は「無効化」）
    # /suppliers/<id>/edit
    # ----------------------------------------
    @app.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
    def edit_supplier(supplier_id):
        db = get_db()

        # 対象仕入先を取得（有効/無効問わず）
        supplier = db.execute(
            """
            SELECT id, code, name, phone, email, address, is_active
            FROM suppliers
            WHERE id = ?
            """,
            (supplier_id,),
        ).fetchone()

        if supplier is None:
            flash("指定された仕入先が見つかりません。")
            return redirect(url_for("suppliers_master"))

        if request.method == "POST":

            # ----------------------
            # 削除ボタン押下時（＝無効化）
            # ----------------------
            if "delete" in request.form:

                # 1) 利用中チェック
                # purchases / mst_items のどちらかで使われていたら無効化禁止
                in_pur = db.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM purchases
                    WHERE supplier_id = ?
                      AND is_deleted = 0
                    """,
                    (supplier_id,),
                ).fetchone()

                in_items = db.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM mst_items
                    WHERE supplier_id = ?
                    """,
                    (supplier_id,),
                ).fetchone()

                pur_cnt = in_pur["cnt"] or 0
                item_cnt = in_items["cnt"] or 0

                if pur_cnt > 0 or item_cnt > 0:
                    flash(
                        "この仕入先は取引または品目マスタで利用されているため無効化できません。"
                        "使用をやめる場合は、品目や取引を他の仕入先に切り替えてから無効化してください。"
                    )
                    return redirect(url_for("suppliers_master"))

                # 2) 利用されていなければ無効化
                try:
                    db.execute(
                        "UPDATE suppliers SET is_active = 0 WHERE id = ?",
                        (supplier_id,),
                    )
                    db.commit()
                    flash(f"仕入先（{supplier['name']}）を無効化しました。")
                except sqlite3.Error as e:
                    db.rollback()
                    flash(f"仕入先更新でエラーが発生しました: {e}")

                return redirect(url_for("suppliers_master"))

            # ----------------------
            # 通常の更新処理
            # ----------------------
            code = (request.form.get("code") or "").strip()
            name = (request.form.get("name") or "").strip()
            phone = (request.form.get("phone") or "").strip()
            email = (request.form.get("email") or "").strip()
            address = (request.form.get("address") or "").strip()

            if not name:
                flash("仕入先名は必須です。")
                return render_template(
                    "suppliers_edit.html",
                    supplier=supplier,
                )

            try:
                db.execute(
                    """
                    UPDATE suppliers
                    SET
                      code    = ?,
                      name    = ?,
                      phone   = ?,
                      email   = ?,
                      address = ?
                    WHERE id = ?
                    """,
                    (code if code else None, name, phone, email, address, supplier_id),
                )
                db.commit()
                flash("仕入先を更新しました。")
            except sqlite3.Error as e:
                db.rollback()
                flash(f"仕入先更新でエラーが発生しました: {e}")

            return redirect(url_for("suppliers_master"))

        # GET のとき：編集画面表示
        return render_template(
            "suppliers_edit.html",
            supplier=supplier,
        )

    # ----------------------------------------
    # 品目マスタ
    # /mst_items
    # 仕入先ごとに SSIII（5桁）コード自動採番
    # ----------------------------------------
    @app.route("/mst_items", methods=["GET", "POST"], endpoint="items_master")
    def mst_items():
        db = get_db()

        # 仕入先一覧（プルダウン用：有効なもののみ）
        suppliers = db.execute(
            """
            SELECT id, name, code
            FROM suppliers
            WHERE is_active = 1
            ORDER BY code
            """
        ).fetchall()

        # 登録済み品目一覧（有効なもののみ）
        mst_items = db.execute(
            """
            SELECT
              i.id,
              i.code,
              i.name,
              i.unit,
              i.temp_zone,
              i.is_internal,
              s.name AS supplier_name
            FROM mst_items i
            LEFT JOIN suppliers s ON i.supplier_id = s.id
            WHERE i.is_active = 1
            ORDER BY i.code, i.name
            """
        ).fetchall()

        # --------- 新規登録（POST） ----------
        if request.method == "POST":
            supplier_id = request.form.get("supplier_id")
            name = (request.form.get("name") or "").strip()
            unit = (request.form.get("unit") or "").strip()

            # ★ 追加：温度帯と内製フラグ
            temp_zone = (request.form.get("temp_zone") or "").strip() or None
            is_internal = 1 if request.form.get("is_internal") == "1" else 0

            # 必須チェック
            if not supplier_id or not name:
                flash("仕入先と品名は必須です。")
                return render_template(
                    "items_master.html",
                    suppliers=suppliers,
                    mst_items=mst_items,
                )

            # 仕入先コード2桁を取得（SS部分）
            supplier = db.execute(
                "SELECT code FROM suppliers WHERE id = ?",
                (supplier_id,),
            ).fetchone()

            if supplier is None or supplier["code"] is None:
                flash("仕入先コードが未設定です（仕入先マスタを確認してください）。")
                return render_template(
                    "items_master.html",
                    suppliers=suppliers,
                    mst_items=mst_items,
                )

            code2 = str(supplier["code"]).zfill(2)[:2]

            # 既存コードの最大値（SSIII の III 部分）を取得
            row = db.execute(
                "SELECT MAX(code) AS max_code FROM mst_items WHERE code LIKE ?",
                (f"{code2}%",),
            ).fetchone()

            if row["max_code"]:
                try:
                    current_seq = int(str(row["max_code"])[2:])
                except Exception:
                    current_seq = 0
            else:
                current_seq = 0

            next_seq = current_seq + 1
            new_code = f"{code2}{next_seq:03d}"  # 5桁 SSIII

            # unit（ケース入数）は整数 or NULL
            try:
                unit_val = int(unit) if unit else None
            except ValueError:
                unit_val = None

            # INSERT（code / name / unit / supplier_id / temp_zone / is_internal）
            try:
                db.execute(
                    """
                    INSERT INTO mst_items
                        (code, name, unit, supplier_id, temp_zone, is_internal)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (new_code, name, unit_val, supplier_id, temp_zone, is_internal),
                )
                db.commit()
                flash(f"品目を登録しました。（コード: {new_code}）")
            except sqlite3.Error as e:
                db.rollback()
                flash(f"mst_items テーブルへの登録でエラーが発生しました: {e}")

            return redirect(url_for("items_master.html"))

        # --------- GET：表示 ----------
        return render_template(
            "items_master.html",
            suppliers=suppliers,
            mst_items=mst_items,
        )

    # ----------------------------------------
    # 品目編集・削除（実態は「無効化」）
    # /mst_items/<id>/edit
    # ----------------------------------------
    @app.route("/mst_items/<int:item_id>/edit", methods=["GET", "POST"], endpoint="edit_item")
    def edit_item(item_id):
        db = get_db()

        # 仕入先一覧（プルダウン用：有効なもののみ）
        suppliers = db.execute(
            """
            SELECT id, name, code
            FROM suppliers
            WHERE is_active = 1
            ORDER BY code
            """
        ).fetchall()

        # 対象品目を取得（有効/無効問わず）
        item = db.execute(
            """
            SELECT
              i.id,
              i.code,
              i.name,
              i.unit,
              i.supplier_id,
              i.temp_zone,
              i.purchase_unit,
              i.inventory_unit,
              i.min_purchase_unit,
              i.is_internal,
              i.storage_cost,
              i.is_active
            FROM mst_items i
            WHERE i.id = ?
            """,
            (item_id,),
        ).fetchone()

        if item is None:
            flash("指定された品目が見つかりません。")
            return redirect(url_for("items_master"))

        if request.method == "POST":

            # ==============================
            # 削除ボタンが押された場合（＝無効化）
            # ==============================
            if "delete" in request.form:

                # 1) 利用中チェック
                # purchases / stock_counts で使われていたら無効化禁止
                in_pur = db.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM purchases
                    WHERE item_id = ?
                      AND is_deleted = 0
                    """,
                    (item_id,),
                ).fetchone()

                in_stock = db.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM stock_counts
                    WHERE item_id = ?
                    """,
                    (item_id,),
                ).fetchone()

                pur_cnt = in_pur["cnt"] or 0
                stock_cnt = in_stock["cnt"] or 0

                if pur_cnt > 0 or stock_cnt > 0:
                    flash(
                        "この品目は取引または棚卸で利用されているため無効化できません。"
                        "使用をやめる場合は、別の品目に切り替えたうえで無効化してください。"
                    )
                    return redirect(url_for("items_master"))

                # 2) 利用されていなければ無効化
                try:
                    db.execute(
                        "UPDATE mst_items SET is_active = 0 WHERE id = ?",
                        (item_id,),
                    )
                    db.commit()
                    flash(f"品目（コード: {item['code']}）を無効化しました。")
                except sqlite3.Error as e:
                    db.rollback()
                    flash(f"品目更新でエラーが発生しました: {e}")

                return redirect(url_for("items_master"))

            # ==============================
            # 通常の更新処理
            # ==============================
            name = (request.form.get("name") or "").strip()
            unit = (request.form.get("unit") or "").strip()
            supplier_id = request.form.get("supplier_id") or None
            temp_zone = (request.form.get("temp_zone") or "").strip() or None
            storage_cost = (request.form.get("storage_cost") or "").strip()
            # チェックボックス → 内製フラグ
            is_internal = 1 if request.form.get("is_internal") == "1" else 0

            # 整数系を安全に変換
            def to_int_or_none(v):
                v = (v or "").strip()
                if not v:
                    return None
                try:
                    return int(v)
                except ValueError:
                    return None

            unit_val = to_int_or_none(unit)
            purchase_unit = to_int_or_none(request.form.get("purchase_unit"))
            inventory_unit = to_int_or_none(request.form.get("inventory_unit"))
            min_purchase_unit = to_int_or_none(request.form.get("min_purchase_unit"))

            # storage_cost は小数もあり得るので float
            def to_float_or_none(v):
                v = (v or "").strip()
                if not v:
                    return None
                try:
                    return float(v)
                except ValueError:
                    return None

            storage_cost_val = to_float_or_none(storage_cost)

            if not name:
                flash("品目名は必須です。")
                return render_template(
                    "items_edit.html",
                    item=item,
                    suppliers=suppliers,
                )

            try:
                db.execute(
                    """
                    UPDATE mst_items
                    SET
                      name              = ?,
                      unit              = ?,
                      supplier_id       = ?,
                      temp_zone         = ?,
                      purchase_unit     = ?,
                      inventory_unit    = ?,
                      min_purchase_unit = ?,
                      is_internal       = ?,
                      storage_cost      = ?
                    WHERE id = ?
                    """,
                    (
                        name,
                        unit_val,
                        supplier_id,
                        temp_zone,
                        purchase_unit,
                        inventory_unit,
                        min_purchase_unit,
                        is_internal,
                        storage_cost_val,
                        item_id,
                    ),
                )
                db.commit()
                flash("品目を更新しました。")
            except sqlite3.Error as e:
                db.rollback()
                flash(f"品目更新でエラーが発生しました: {e}")

            return redirect(url_for("items_master"))

        # GET のとき：編集画面表示
        return render_template(
            "items_edit.html",
            item=item,
            suppliers=suppliers,
        )


    # ----------------------------------------
    # 店舗マスタ
    # /mst_stores
    # ----------------------------------------
    @app.route("/mst_stores", methods=["GET", "POST"], endpoint="stores_master")
    def mst_stores():
        db = get_db()
    
        if request.method == "POST":
            code = (request.form.get("code") or "").strip()
            name = (request.form.get("name") or "").strip()
            seats = (request.form.get("seats") or "").strip()
            opened_on = (request.form.get("opened_on") or "").strip()
    
            if not name:
                flash("店舗名は必須です。")
            else:
                db.execute(
                    """
                    INSERT INTO mst_stores (code, name, seats, opened_on)
                    VALUES (?, ?, ?, ?)
                    """,
                    (code or None, name, seats or None, opened_on or None)
                )
                db.commit()
                flash("店舗を登録しました。")
    
            return redirect(url_for("stores_master"))
    
        # GET
        mst_stores = db.execute(
            """
            SELECT id, code, name, seats, opened_on, closed_on, is_active
            FROM mst_stores
            ORDER BY code, id
            """
        ).fetchall()
    
        return render_template("stores_master.html", stores=mst_stores)


        # ----------------------------------------
    # 店舗編集・無効化 ＋ 仕入れ先紐付け
    # /mst_stores/<id>/edit
    # ----------------------------------------
    @app.route("/mst_stores/<int:store_id>/edit", methods=["GET", "POST"], endpoint="edit_store")
    def edit_store(store_id):
        db = get_db()

        # 店舗情報
        store = db.execute(
            """
            SELECT id, code, name, seats, opened_on, closed_on, is_active
            FROM mst_stores
            WHERE id = ?
            """,
            (store_id,),
        ).fetchone()

        if store is None:
            flash("指定された店舗が見つかりません。")
            return redirect(url_for("stores_master"))

        # 仕入れ先（全体）※有効なものだけ
        suppliers = db.execute(
            """
            SELECT id, code, name
            FROM suppliers
            WHERE is_active = 1
            ORDER BY code
            """
        ).fetchall()

        # この店舗に紐付いている仕入れ先ID一覧
        linked_rows = db.execute(
            """
            SELECT supplier_id
            FROM store_suppliers
            WHERE store_id = ?
              AND is_active = 1
            """,
            (store_id,),
        ).fetchall()
        linked_supplier_ids = {row["supplier_id"] for row in linked_rows}

        if request.method == "POST":

            # --------------------------
            # 無効化ボタン（delete）
            # --------------------------
            if "delete" in request.form:
                in_use = db.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM purchases
                    WHERE store_id = ?
                      AND is_deleted = 0
                    """,
                    (store_id,),
                ).fetchone()["cnt"] or 0

                if in_use > 0:
                    flash("この店舗は取引実績があるため無効化できません。")
                    return redirect(url_for("stores_master"))

                db.execute(
                    "UPDATE mst_stores SET is_active = 0 WHERE id = ?",
                    (store_id,),
                )
                db.commit()
                flash("店舗を無効化しました。")
                return redirect(url_for("stores_master"))

            # --------------------------
            # 通常の更新処理
            # --------------------------
            code = (request.form.get("code") or "").strip()
            name = (request.form.get("name") or "").strip()
            seats_raw = (request.form.get("seats") or "").strip()
            opened_on = (request.form.get("opened_on") or "").strip()
            closed_on = (request.form.get("closed_on") or "").strip()

            def to_int_or_none(v):
                if not v:
                    return None
                try:
                    return int(v)
                except ValueError:
                    return None

            seats_val = to_int_or_none(seats_raw)

            if not name:
                flash("店舗名は必須です。")
                return render_template(
                    "stores_edit.html",
                    store=store,
                    suppliers=suppliers,
                    linked_supplier_ids=linked_supplier_ids,
                )

            # ---- 店舗情報の更新 ----
            db.execute(
                """
                UPDATE mst_stores
                SET code = ?, name = ?, seats = ?, opened_on = ?, closed_on = ?
                WHERE id = ?
                """,
                (code or None, name, seats_val, opened_on or None, closed_on or None, store_id),
            )

            # ---- 仕入れ先紐付けの更新 ----
            # フォームから選択された supplier_ids（複数）
            form_supplier_ids = request.form.getlist("supplier_ids")
            form_supplier_ids = {int(sid) for sid in form_supplier_ids}  # set化

            current_ids = linked_supplier_ids  # 既存の有効な紐付け

            # 追加すべきもの = 新しくチェックが入ったもの
            to_add = form_supplier_ids - current_ids
            # 削除すべきもの = もともと紐付いてたけどチェックが外されたもの
            to_remove = current_ids - form_supplier_ids

            # 追加（is_active を 1 に）
            for sid in to_add:
                db.execute(
                    """
                    INSERT INTO store_suppliers (store_id, supplier_id, is_active)
                    VALUES (?, ?, 1)
                    ON CONFLICT (store_id, supplier_id)
                    DO UPDATE SET is_active = 1
                    """,
                    (store_id, sid),
                )

            # 削除（is_active を 0 に）
            for sid in to_remove:
                db.execute(
                    """
                    UPDATE store_suppliers
                    SET is_active = 0
                    WHERE store_id = ? AND supplier_id = ?
                    """,
                    (store_id, sid),
                )

            db.commit()
            flash("店舗を更新しました。")

            return redirect(url_for("stores_master"))

        # GET のとき：編集画面表示
        return render_template(
            "stores_edit.html",
            store=store,
            suppliers=suppliers,
            linked_supplier_ids=linked_supplier_ids,
        )
