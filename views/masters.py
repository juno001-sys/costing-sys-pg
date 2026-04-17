# views/masters.py

import sqlite3
import json
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,g,
)
from views.reports.audit_log import log_event


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
        
        company_id = getattr(g, "current_company_id", None)

        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            code = (request.form.get("code") or "").strip()
            phone = (request.form.get("phone") or "").strip()
            email = (request.form.get("email") or "").strip()
            address = (request.form.get("address") or "").strip()
            contact_person = (request.form.get("contact_person") or "").strip()
            contact_phone = (request.form.get("contact_phone") or "").strip()
            company_phone = (request.form.get("company_phone") or "").strip()
            fax = (request.form.get("fax") or "").strip()
            order_method = (request.form.get("order_method") or "").strip()
            order_url = (request.form.get("order_url") or "").strip()
            order_notes = (request.form.get("order_notes") or "").strip()
            holidays_off = 1 if request.form.get("holidays_off") else 0

            # Build delivery_schedule JSON
            DAYS = ['mon','tue','wed','thu','fri','sat','sun']
            schedule = {}
            for day in DAYS:
                if request.form.get(f"delivery_{day}"):
                    deadline_days = request.form.get(f"deadline_days_{day}")
                    deadline_time = request.form.get(f"deadline_time_{day}") or None
                    schedule[day] = {
                        "deadline_days": int(deadline_days) if deadline_days else 1,
                        "deadline_time": deadline_time,
                    }
            delivery_schedule = json.dumps(schedule) if schedule else None

            if not name:
                flash("仕入先名は必須です。")
            else:
                try:
                    company_id = getattr(g, "current_company_id", None)

                    db.execute(
                        """
                        INSERT INTO pur_suppliers
                            (company_id, code, name, phone, email, address,
                             contact_person, contact_phone, company_phone, fax,
                             order_method, order_url, delivery_schedule, order_notes, holidays_off)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (company_id, code if code else None, name, phone, email, address,
                         contact_person or None, contact_phone or None, company_phone or None, fax or None,
                         order_method or None, order_url or None, delivery_schedule, order_notes or None, holidays_off),
                    )
                     # NEW: audit log (CREATE supplier)
                    try:
                        log_event(
                            db,
                            action="CREATE",
                            module="mst",
                            entity_table="suppliers",
                            entity_id="(new)",
                            message="Supplier created",
                            status_code=200,
                            meta={
                                "code": code or None,
                                "name": name,
                                "phone": phone,
                                "email": email,
                                "address": address,
                            },
                        )
                    except Exception:
                        pass

                    db.commit()
                    flash("仕入先を登録しました。")
                except sqlite3.OperationalError as e:
                    flash(f"suppliers テーブルへの登録でエラーが発生しました: {e}")

            return redirect(url_for("suppliers_master"))

        # GET：有効な仕入先のみ表示
        company_id = getattr(g, "current_company_id", None)

        suppliers = db.execute(
            """
            SELECT id, code, name, phone, email, address, is_active,
                   contact_person, contact_phone, company_phone, fax,
                   order_method, order_url, delivery_schedule, order_notes, holidays_off
            FROM pur_suppliers
            WHERE is_active = 1
              AND company_id = %s
            ORDER BY code, id
            """,
            (company_id,),
        ).fetchall()

        return render_template(
            "mst/suppliers_master.html",
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
        company_id = getattr(g, "current_company_id", None)

        supplier = db.execute(
            """
            SELECT id, code, name, phone, email, address, is_active,
                   contact_person, contact_phone, company_phone, fax,
                   order_method, order_url, delivery_schedule, order_notes, holidays_off
            FROM pur_suppliers
            WHERE id = %s
              AND company_id = %s
            """,
            (supplier_id, company_id,)
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
                    FROM purchases p
                    LEFT JOIN mst_stores st ON p.store_id = st.id
                    WHERE p.supplier_id = %s
                      AND p.is_deleted = 0
                      AND st.company_id = %s
                    """,
                    (supplier_id, company_id,)
                ).fetchone()

                in_items = db.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM mst_items
                    WHERE supplier_id = %s
                    AND company_id = %s
                    """,
                    (supplier_id,company_id,)
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
                        """
                        UPDATE pur_suppliers
                        SET is_active = 0
                        WHERE id = %s
                          AND company_id = %s
                        """,
                        (supplier_id, company_id,)
                    )
                     # NEW: audit log (DISABLE supplier)
                    try:
                        log_event(
                            db,
                            action="DISABLE",
                            module="mst",
                            entity_table="suppliers",
                            entity_id=str(supplier_id),
                            message="Supplier disabled",
                            status_code=200,
                            meta={
                                "name": supplier["name"],
                                "code": supplier["code"],
                            },
                        )
                    except Exception:
                        pass

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
            contact_person = (request.form.get("contact_person") or "").strip()
            contact_phone = (request.form.get("contact_phone") or "").strip()
            company_phone = (request.form.get("company_phone") or "").strip()
            fax = (request.form.get("fax") or "").strip()
            order_method = (request.form.get("order_method") or "").strip()
            order_url = (request.form.get("order_url") or "").strip()
            order_notes = (request.form.get("order_notes") or "").strip()
            holidays_off = 1 if request.form.get("holidays_off") else 0

            # Build delivery_schedule JSON
            DAYS = ['mon','tue','wed','thu','fri','sat','sun']
            schedule = {}
            for day in DAYS:
                if request.form.get(f"delivery_{day}"):
                    deadline_days = request.form.get(f"deadline_days_{day}")
                    deadline_time = request.form.get(f"deadline_time_{day}") or None
                    schedule[day] = {
                        "deadline_days": int(deadline_days) if deadline_days else 1,
                        "deadline_time": deadline_time,
                    }
            delivery_schedule = json.dumps(schedule) if schedule else None

            if not name:
                flash("仕入先名は必須です。")
                return render_template(
                    "mst/suppliers_edit.html",
                    supplier=supplier,
                )

            try:
                db.execute(
                    """
                    UPDATE pur_suppliers
                    SET
                      code              = %s,
                      name              = %s,
                      phone             = %s,
                      email             = %s,
                      address           = %s,
                      contact_person    = %s,
                      contact_phone     = %s,
                      company_phone     = %s,
                      fax               = %s,
                      order_method      = %s,
                      order_url         = %s,
                      delivery_schedule = %s,
                      order_notes       = %s,
                      holidays_off      = %s
                    WHERE id = %s
                     AND company_id = %s
                    """,
                     (code if code else None, name, phone, email, address,
                      contact_person or None, contact_phone or None, company_phone or None, fax or None,
                      order_method or None, order_url or None, delivery_schedule, order_notes or None,
                      holidays_off, supplier_id, company_id,)
                )
                # NEW: audit log (UPDATE supplier)
                try:
                    log_event(
                        db,
                        action="UPDATE",
                        module="mst",
                        entity_table="suppliers",
                        entity_id=str(supplier_id),
                        message="Supplier updated",
                        status_code=200,
                        meta={
                            "code": code or None,
                            "name": name,
                            "phone": phone,
                            "email": email,
                            "address": address,
                        },
                    )
                except Exception:
                    pass
                db.commit()
                flash("仕入先を更新しました。")
            except sqlite3.Error as e:
                db.rollback()
                flash(f"仕入先更新でエラーが発生しました: {e}")

            return redirect(url_for("suppliers_master"))

        # GET のとき：編集画面表示
        import calendar as cal_mod
        from datetime import date as date_cls
        cal_year = int(request.args.get("cal_year", date_cls.today().year))

        # Supplier holidays
        sup_holidays = db.execute(
            "SELECT id, holiday_date, name FROM supplier_holidays WHERE supplier_id = %s AND company_id = %s AND EXTRACT(YEAR FROM holiday_date) = %s ORDER BY holiday_date",
            (supplier_id, company_id, cal_year),
        ).fetchall()
        sup_holiday_dates = {str(h["holiday_date"]) for h in sup_holidays}
        sup_holiday_names = {str(h["holiday_date"]): h["name"] for h in sup_holidays}

        # Store holidays (all stores, for overlay reference)
        store_holiday_rows = db.execute(
            "SELECT DISTINCT holiday_date, name FROM store_holidays WHERE company_id = %s AND EXTRACT(YEAR FROM holiday_date) = %s ORDER BY holiday_date",
            (company_id, cal_year),
        ).fetchall()
        store_holiday_dates = {str(h["holiday_date"]) for h in store_holiday_rows}
        store_holiday_names = {str(h["holiday_date"]): h["name"] for h in store_holiday_rows}

        cal = cal_mod.Calendar(firstweekday=0)
        yearly_calendar = [{"month": m, "weeks": cal.monthdayscalendar(cal_year, m)} for m in range(1, 13)]

        return render_template(
            "mst/suppliers_edit.html",
            supplier=supplier,
            sup_holidays=sup_holidays,
            sup_holiday_dates=sup_holiday_dates,
            sup_holiday_names=sup_holiday_names,
            store_holiday_dates=store_holiday_dates,
            store_holiday_names=store_holiday_names,
            yearly_calendar=yearly_calendar,
            cal_year=cal_year,
        )

    # ----------------------------------------
    # 品目マスタ
    # /mst_items
    # 仕入先ごとに SSIII（5桁）コード自動採番
    # ----------------------------------------
    @app.route("/mst_items", methods=["GET", "POST"], endpoint="items_master")
    def mst_items():
        db = get_db()

        company_id = getattr(g, "current_company_id", None)

        # 仕入先一覧（プルダウン用：有効なもののみ）
        suppliers = db.execute(
            """
            SELECT id, name, code
            FROM pur_suppliers
            WHERE is_active = 1
            AND company_id = %s
            ORDER BY code
            """,
             (company_id,),
        ).fetchall()

        # 温度帯マスタ（プルダウン用）
        temp_zones = db.execute(
            "SELECT code, default_name FROM inv_temp_zone_master WHERE COALESCE(is_active,TRUE)=TRUE ORDER BY sort_order, code"
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
              i.category,
              i.process_level,
              i.est_order_qty,
              s.name AS supplier_name
            FROM mst_items i
            LEFT JOIN pur_suppliers s ON i.supplier_id = s.id
            WHERE i.is_active = 1
             AND i.company_id = %s
            ORDER BY i.code, i.name
            """,
            (company_id,)
        ).fetchall()

        # --------- 新規登録（POST） ----------
        if request.method == "POST":
            supplier_id = request.form.get("supplier_id")
            name = (request.form.get("name") or "").strip()
            unit = (request.form.get("unit") or "").strip()

            # ★ 追加：温度帯と内製フラグ
            temp_zone = (request.form.get("temp_zone") or "").strip() or None
            is_internal = 1 if request.form.get("is_internal") == "1" else 0

            # ★ 追加：カテゴリ・加工レベル・発注目安数量
            category = (request.form.get("category") or "").strip() or None
            process_level = (request.form.get("process_level") or "").strip() or None
            est_order_qty_raw = request.form.get("est_order_qty")
            try:
                est_order_qty = int(est_order_qty_raw) if est_order_qty_raw else None
            except ValueError:
                est_order_qty = None

            # 必須チェック
            if not supplier_id or not name:
                flash("仕入先と品名は必須です。")
                return render_template(
                    "mst/items_master.html",
                    suppliers=suppliers,
                    mst_items=mst_items,
                    temp_zones=temp_zones,
                )

            # 仕入先コード2桁を取得（SS部分）
            supplier = db.execute(
                """
                SELECT code
                FROM pur_suppliers
                WHERE id = %s
                  AND company_id = %s
                """,
                (supplier_id, company_id,)
            ).fetchone()

            if supplier is None or supplier["code"] is None:
                flash("仕入先コードが未設定です（仕入先マスタを確認してください）。")
                return render_template(
                    "mst/items_master.html",
                    suppliers=suppliers,
                    mst_items=mst_items,
                    temp_zones=temp_zones,
                )

            code2 = str(supplier["code"]).zfill(2)[:2]

            # 既存コードの最大値（SSIII の III 部分）を取得
            row = db.execute(
                """
                SELECT MAX(code) AS max_code
                FROM mst_items
                WHERE code LIKE %s
                  AND company_id = %s
                """,
                (f"{code2}%", company_id,)
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
                        (company_id, code, name, unit, supplier_id, temp_zone, is_internal, category, process_level, est_order_qty)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (company_id, new_code, name, unit_val, supplier_id, temp_zone, is_internal, category, process_level, est_order_qty),
                )
                # NEW: audit log (CREATE item)
                try:
                    log_event(
                        db,
                        action="CREATE",
                        module="mst",
                        entity_table="mst_items",
                        entity_id="(new)",
                        message="Item created",
                        status_code=200,
                        meta={
                            "code": new_code,
                            "name": name,
                            "supplier_id": int(supplier_id),
                            "unit": unit_val,
                            "temp_zone": temp_zone,
                            "is_internal": is_internal,
                            "category": category,
                            "process_level": process_level,
                            "est_order_qty": est_order_qty,
                        },
                    )
                except Exception:
                    pass
                db.commit()
                flash(f"品目を登録しました。（コード: {new_code}）")
            except sqlite3.Error as e:
                db.rollback()
                flash(f"mst_items テーブルへの登録でエラーが発生しました: {e}")

            return redirect(url_for("items_master"))

        # --------- GET：表示 ----------
        return render_template(
            "mst/items_master.html",
            suppliers=suppliers,
            items=mst_items,
            temp_zones=temp_zones,
        )

    # ----------------------------------------
    # 品目編集・削除（実態は「無効化」）
    # /mst_items/<id>/edit
    # ----------------------------------------
    @app.route("/mst_items/<int:item_id>/edit", methods=["GET", "POST"], endpoint="edit_item")
    def edit_item(item_id):
        db = get_db()
        company_id = getattr(g, "current_company_id", None)
        # 温度帯マスタ（プルダウン用）
        temp_zones = db.execute(
            "SELECT code, default_name FROM inv_temp_zone_master WHERE COALESCE(is_active,TRUE)=TRUE ORDER BY sort_order, code"
        ).fetchall()
        # 仕入先一覧（プルダウン用：有効なもののみ）
        suppliers = db.execute(
            """
            SELECT id, name, code
            FROM pur_suppliers
            WHERE is_active = 1
             AND company_id = %s
            ORDER BY code
            """,
             (company_id,),
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
              i.is_active,
              i.category,
              i.process_level,
              i.est_order_qty,
              i.per_guest_rate,
              i.est_mu,
              i.est_sigma,
              i.est_calc_at
            FROM mst_items i
            WHERE i.id = %s
            AND i.company_id = %s
            """,
            (item_id,company_id,)
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
                    FROM purchases p
                    LEFT JOIN mst_stores st ON p.store_id = st.id
                    WHERE p.item_id = %s
                      AND p.is_deleted = 0
                      AND st.company_id = %s
                    """,
                    (item_id, company_id,)
                ).fetchone()

                in_stock = db.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM stock_counts sc
                    LEFT JOIN mst_stores st ON sc.store_id = st.id
                    WHERE sc.item_id = %s
                      AND st.company_id = %s
                    """,
                    (item_id, company_id,)
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
                        """
                        UPDATE mst_items
                        SET is_active = 0
                        WHERE id = %s
                          AND company_id = %s
                        """,
                        (item_id, company_id,)
                    )
                     # NEW: audit log (DISABLE item)
                    try:
                        log_event(
                            db,
                            action="DISABLE",
                            module="mst",
                            entity_table="mst_items",
                            entity_id=str(item_id),
                            message="Item disabled",
                            status_code=200,
                            meta={
                                "code": item["code"],
                                "name": item["name"],
                                "supplier_id": item["supplier_id"],
                            },
                        )
                    except Exception:
                        pass

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

            # ★ 追加：カテゴリ・加工レベル・発注目安数量
            category = (request.form.get("category") or "").strip() or None
            process_level = (request.form.get("process_level") or "").strip() or None
            est_order_qty_raw = request.form.get("est_order_qty")
            try:
                est_order_qty = int(est_order_qty_raw) if est_order_qty_raw else None
            except ValueError:
                est_order_qty = None

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
                    "mst/items_edit.html",
                    item=item,
                    suppliers=suppliers,
                    temp_zones=temp_zones,
                )

            try:
                db.execute(
                    """
                    UPDATE mst_items
                    SET
                      name              = %s,
                      unit              = %s,
                      supplier_id       = %s,
                      temp_zone         = %s,
                      purchase_unit     = %s,
                      inventory_unit    = %s,
                      min_purchase_unit = %s,
                      is_internal       = %s,
                      storage_cost      = %s,
                      category          = %s,
                      process_level     = %s,
                      est_order_qty     = %s
                    WHERE id = %s
                      AND company_id = %s
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
                        category,
                        process_level,
                        est_order_qty,
                        item_id,
                        company_id,
                    ),
                )
                # Write est-history row if est_order_qty was manually changed
                if est_order_qty != item["est_order_qty"]:
                    try:
                        db.execute(
                            """
                            UPDATE mst_items_est_history
                            SET effective_to = CURRENT_DATE
                            WHERE item_id = %s AND effective_to IS NULL
                            """,
                            (item_id,),
                        )
                        db.execute(
                            """
                            INSERT INTO mst_items_est_history
                              (item_id, est_order_qty, per_guest_rate, est_mu, est_sigma,
                               effective_from, effective_to, calc_source, calc_at, note)
                            VALUES
                              (%s, %s, %s, %s, %s, CURRENT_DATE, NULL, 'manual', NOW(), %s)
                            """,
                            (
                                item_id, est_order_qty,
                                item["per_guest_rate"], item["est_mu"], item["est_sigma"],
                                f"Manual edit: {item['est_order_qty']} → {est_order_qty}",
                            ),
                        )
                    except Exception:
                        # history is audit-only; never break the save
                        pass

                # NEW: audit log (UPDATE item)
                try:
                    log_event(
                        db,
                        action="UPDATE",
                        module="mst",
                        entity_table="mst_items",
                        entity_id=str(item_id),
                        message="Item updated",
                        status_code=200,
                        meta={
                            "name": name,
                            "unit": unit_val,
                            "supplier_id": int(supplier_id) if supplier_id else None,
                            "temp_zone": temp_zone,
                            "purchase_unit": purchase_unit,
                            "inventory_unit": inventory_unit,
                            "min_purchase_unit": min_purchase_unit,
                            "is_internal": is_internal,
                            "storage_cost": storage_cost_val,
                            "category": category,
                            "process_level": process_level,
                            "est_order_qty": est_order_qty,
                        },
                    )
                except Exception:
                    pass
                db.commit()
                flash("品目を更新しました。")
            except sqlite3.Error as e:
                db.rollback()
                flash(f"品目更新でエラーが発生しました: {e}")

            return redirect(url_for("items_master"))

        # Fetch est history (most recent first)
        est_history = db.execute(
            """
            SELECT id, est_order_qty, per_guest_rate, est_mu, est_sigma,
                   effective_from, effective_to, calc_source, calc_at, note
            FROM mst_items_est_history
            WHERE item_id = %s
            ORDER BY effective_from DESC, id DESC
            """,
            (item_id,),
        ).fetchall()

        # GET のとき：編集画面表示
        return render_template(
            "mst/items_edit.html",
            item=item,
            suppliers=suppliers,
            temp_zones=temp_zones,
            est_history=est_history,
        )


    # ----------------------------------------
    # 店舗マスタ
    # /mst_stores
    # ----------------------------------------
    @app.route("/mst_stores", methods=["GET", "POST"], endpoint="stores_master")
    def mst_stores():
        db = get_db()

        company_id = getattr(g, "current_company_id", None)
    
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
                    INSERT INTO mst_stores (company_id, code, name, seats, opened_on)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (company_id, code or None, name, seats or None, opened_on or None)
                )
                # NEW: audit log (CREATE store)
                try:
                    log_event(
                        db,
                        action="CREATE",
                        module="mst",
                        entity_table="mst_stores",
                        entity_id="(new)",
                        message="Store created",
                        status_code=200,
                        meta={
                            "code": code or None,
                            "name": name,
                            "seats": seats or None,
                            "opened_on": opened_on or None,
                        },
                    )
                except Exception:
                    pass
                db.commit()
                flash("店舗を登録しました。")
    
            return redirect(url_for("stores_master"))
    
        # GET
        mst_stores = db.execute(
            """
            SELECT id, code, name, seats, opened_on, closed_on, is_active
            FROM mst_stores
            WHERE company_id = %s
            ORDER BY code, id
            """,
            (company_id,)
        ).fetchall()
    
        return render_template("mst/stores_master.html", stores=mst_stores)


        # ----------------------------------------
    # 店舗編集・無効化 ＋ 仕入れ先紐付け
    # /mst_stores/<id>/edit
    # ----------------------------------------
    @app.route("/mst_stores/<int:store_id>/edit", methods=["GET", "POST"], endpoint="edit_store")
    def edit_store(store_id):
        db = get_db()

        company_id = getattr(g, "current_company_id", None)

        # 店舗情報
        store = db.execute(
            """
            SELECT id, code, name, seats, opened_on, closed_on, is_active
            FROM mst_stores
            WHERE id = %s
             AND company_id = %s
            """,
            (store_id,company_id,)
        ).fetchone()

        if store is None:
            flash("指定された店舗が見つかりません。")
            return redirect(url_for("stores_master"))

        # 仕入れ先（全体）※有効なものだけ
        suppliers = db.execute(
            """
            SELECT id, code, name
            FROM pur_suppliers
            WHERE is_active = 1
             AND company_id = %s
            ORDER BY code
            """,
            (company_id,)
        ).fetchall()

        # この店舗に紐付いている仕入れ先ID一覧
        linked_rows = db.execute(
            """
            SELECT supplier_id
            FROM pur_store_suppliers
            WHERE store_id = %s
              AND is_active = 1
            """,
            (store_id,),
        ).fetchall()
        linked_supplier_ids = {row["supplier_id"] for row in linked_rows}

        # ── Helper: fetch all tab data (used for both GET and failed POST render) ──
        def _tab_data():
            tz_master = db.execute(
                "SELECT code, default_name, sort_order FROM inv_temp_zone_master WHERE COALESCE(is_active,TRUE)=TRUE ORDER BY sort_order, code"
            ).fetchall()
            store_tz_rows = db.execute(
                "SELECT code, COALESCE(display_name,'') AS display_name, sort_order, is_active FROM inv_store_temp_zones WHERE store_id=%s ORDER BY sort_order, code",
                (store_id,),
            ).fetchall()
            store_tz = {r["code"]: r for r in store_tz_rows}
            areas_master = db.execute(
                "SELECT id, name, sort_order FROM inv_area_master WHERE COALESCE(is_active,TRUE)=TRUE ORDER BY sort_order, name"
            ).fetchall()
            area_map_rows = db.execute(
                "SELECT area_id, COALESCE(display_name,'') AS display_name, COALESCE(sort_order,100) AS sort_order, COALESCE(is_active,TRUE) AS is_active FROM inv_store_area_map WHERE store_id=%s",
                (store_id,),
            ).fetchall()
            map_by_area = {m["area_id"]: m for m in area_map_rows}
            temp_zones_for_shelf = db.execute(
                "SELECT code, COALESCE(display_name, code) AS name, sort_order FROM inv_store_temp_zones WHERE store_id=%s AND COALESCE(is_active,TRUE)=TRUE ORDER BY sort_order, code",
                (store_id,),
            ).fetchall()
            shelves = db.execute(
                """
                SELECT sh.id, COALESCE(sam.display_name, am.name) AS area_name,
                       sh.temp_zone, sh.code, COALESCE(sh.name,'') AS name,
                       sh.sort_order, COALESCE(sh.is_active,TRUE) AS is_active,
                       sam.sort_order AS area_sort_order
                FROM inv_store_shelves sh
                JOIN inv_store_area_map sam ON sam.id = sh.store_area_map_id
                JOIN inv_area_master am ON am.id = sam.area_id
                WHERE sh.store_id = %s
                  AND COALESCE(sam.is_active, TRUE) = TRUE
                ORDER BY sam.sort_order, COALESCE(sam.display_name, am.name), sh.sort_order, sh.code
                """,
                (store_id,),
            ).fetchall()
            profit_settings = db.execute(
                """
                SELECT effective_from, fl_ratio, food_ratio, utility_ratio, fixed_cost_yen, store_id
                FROM mst_profit_settings
                WHERE store_id=%s OR store_id IS NULL
                ORDER BY (store_id IS NOT NULL) DESC, effective_from DESC
                """,
                (store_id,),
            ).fetchall()
            return dict(
                tz_master=tz_master, store_tz=store_tz,
                areas_master=areas_master, map_by_area=map_by_area,
                temp_zones_for_shelf=temp_zones_for_shelf,
                shelves=shelves, profit_settings=profit_settings,
            )

        if request.method == "POST":

            # --------------------------
            # 仕入れ先のみ更新（supplier tab）
            # --------------------------
            if request.form.get("_tab") == "suppliers":
                form_supplier_ids = {int(sid) for sid in request.form.getlist("supplier_ids")}
                to_add    = form_supplier_ids - linked_supplier_ids
                to_remove = linked_supplier_ids - form_supplier_ids
                for sid in to_add:
                    db.execute(
                        "INSERT INTO pur_store_suppliers (store_id, supplier_id, is_active) VALUES (%s,%s,1) ON CONFLICT (store_id, supplier_id) DO UPDATE SET is_active=1",
                        (store_id, sid),
                    )
                for sid in to_remove:
                    db.execute(
                        "UPDATE pur_store_suppliers SET is_active=0 WHERE store_id=%s AND supplier_id=%s",
                        (store_id, sid),
                    )
                db.commit()
                flash("仕入れ先を更新しました。")
                return redirect(url_for("edit_store", store_id=store_id) + "#tab-supplier")

            # --------------------------
            # 無効化ボタン（delete）
            # --------------------------
            if "delete" in request.form:
                in_use = db.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM purchases p
                    LEFT JOIN mst_stores st ON p.store_id = st.id
                    WHERE p.store_id = %s
                    AND p.is_deleted = 0
                    AND st.company_id = %s
                    """,
                    (store_id,company_id,)
                ).fetchone()["cnt"] or 0

                if in_use > 0:
                    flash("この店舗は取引実績があるため無効化できません。")
                    return redirect(url_for("stores_master"))

                db.execute(
                    "UPDATE mst_stores SET is_active = 0 WHERE id = %s",
                    (store_id,),
                )
                # NEW: audit log (DISABLE store)
                try:
                    log_event(
                        db,
                        action="DISABLE",
                        module="mst",
                        entity_table="mst_stores",
                        entity_id=str(store_id),
                        message="Store disabled",
                        status_code=200,
                        meta={
                            "code": store["code"],
                            "name": store["name"],
                        },
                    )
                except Exception:
                    pass
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
                    "mst/stores_edit.html",
                    store=store,
                    suppliers=suppliers,
                    linked_supplier_ids=linked_supplier_ids,
                    **_tab_data(),
                )

            # ---- 店舗情報の更新 ----
            db.execute(
                """
                UPDATE mst_stores
                SET code = %s, name = %s, seats = %s, opened_on = %s, closed_on = %s
                WHERE id = %s
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
                    INSERT INTO pur_store_suppliers (store_id, supplier_id, is_active)
                    VALUES (%s, %s, 1)
                    ON CONFLICT (store_id, supplier_id)
                    DO UPDATE SET is_active = 1
                    """,
                    (store_id, sid),
                )

            # 削除（is_active を 0 に）
            for sid in to_remove:
                db.execute(
                    """
                    UPDATE pur_store_suppliers
                    SET is_active = 0
                    WHERE store_id = %s AND supplier_id = %s
                    """,
                    (store_id, sid),
                )
                 # NEW: audit log (UPDATE store + supplier links)
            try:
                log_event(
                    db,
                    action="UPDATE",
                    module="mst",
                    entity_table="mst_stores",
                    entity_id=str(store_id),
                    message="Store updated (including supplier links)",
                    status_code=200,
                    meta={
                        "code": code or None,
                        "name": name,
                        "seats": seats_val,
                        "opened_on": opened_on or None,
                        "closed_on": closed_on or None,
                        "supplier_links_added": sorted(list(to_add)),
                        "supplier_links_removed": sorted(list(to_remove)),
                    },
                )
            except Exception:
                pass

            db.commit()
            flash("店舗を更新しました。")
            return redirect(url_for("edit_store", store_id=store_id) + "#tab-info")

        # GET のとき：編集画面表示
        import calendar as cal_mod
        from datetime import date as date_cls
        cal_year = int(request.args.get("cal_year", date_cls.today().year))
        store_holidays = db.execute(
            """
            SELECT id, holiday_date, name FROM store_holidays
            WHERE store_id = %s AND company_id = %s
              AND EXTRACT(YEAR FROM holiday_date) = %s
            ORDER BY holiday_date
            """,
            (store_id, company_id, cal_year),
        ).fetchall()
        store_holiday_dates = {str(h["holiday_date"]) for h in store_holidays}
        store_holiday_names = {str(h["holiday_date"]): h["name"] for h in store_holidays}
        cal = cal_mod.Calendar(firstweekday=0)
        yearly_calendar = [{"month": m, "weeks": cal.monthdayscalendar(cal_year, m)} for m in range(1, 13)]

        return render_template(
            "mst/stores_edit.html",
            store=store,
            suppliers=suppliers,
            linked_supplier_ids=linked_supplier_ids,
            store_holidays=store_holidays,
            store_holiday_dates=store_holiday_dates,
            store_holiday_names=store_holiday_names,
            yearly_calendar=yearly_calendar,
            cal_year=cal_year,
            **_tab_data(),
        )
