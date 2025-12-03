import os
from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# -------------------------------------------------------
# Flask 基本設定
# -------------------------------------------------------
def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "kurajika-dev"
    app.config["JSON_AS_ASCII"] = False

    # Postgres URL 補正
    db_url = os.environ.get("DATABASE_URL")
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif db_url and db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    # ---- ルート登録（Blueprint読み込み） ----
    from views.purchase import purchase_bp
    from views.stock import stock_bp
    from views.delivery import delivery_bp
    from views.report import report_bp

    app.register_blueprint(purchase_bp)
    app.register_blueprint(stock_bp)
    app.register_blueprint(delivery_bp)
    app.register_blueprint(report_bp)

    # ---- ホーム ----
    @app.route("/")
    def home():
        return render_template("home.html")

    return app


# -------------------------------------------------------
# 本番起動
# -------------------------------------------------------
app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


"""
import os
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    g,
    flash,
    jsonify,
)
import json
from datetime import datetime

# ----------------------------------------
# 基本設定
# ----------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "costing.sqlite3"

app = Flask(__name__)
app.config["SECRET_KEY"] = "kurajika-dev"  # flash用。必要ならあとで変更可
app.config["JSON_AS_ASCII"] = False


# ----------------------------------------
# DB ヘルパー
# ----------------------------------------
def get_db():
    if "db" not in g:
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ----------------------------------------
# 取引変更ログ ヘルパー
# ----------------------------------------
def _row_to_dict(row):
    """
    sqlite3.Row を想定しつつ、dict や想定外の型でも落ちないようにする。
    """
    if row is None:
        return None

    # row が dict ならそのまま
    if isinstance(row, dict):
        return row

    # sqlite3.Row 想定
    try:
        return {k: row[k] for k in row.keys()}
    except Exception:
        # row が想定外の型でも落ちずに返す
        return {"value": str(row)}


def log_purchase_change(db, purchase_id, action, old_row=None, new_row=None, changed_by=None):
    """
    purchases の変更を purchase_logs に記録する。
    action: 'INSERT', 'UPDATE', 'DELETE' など
    old_row / new_row は sqlite3.Row または dict または None
    changed_by は将来用（今は None でOK）
    """
    if old_row is not None and not isinstance(old_row, dict):
        old_row = _row_to_dict(old_row)
    if new_row is not None and not isinstance(new_row, dict):
        new_row = _row_to_dict(new_row)

    db.execute(
        """
        INSERT INTO purchase_logs
            (purchase_id, action, changed_at, changed_by, old_values, new_values)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            purchase_id,
            action,
            datetime.now().isoformat(timespec="seconds"),
            changed_by,
            json.dumps(old_row, ensure_ascii=False) if old_row is not None else None,
            json.dumps(new_row, ensure_ascii=False) if new_row is not None else None,
        ),
    )


def log_purchase(db, purchase_row, action, changed_by=None):
    """
    purchases の1レコードを、purchase_logs にスナップショットとして保存する。
    """
    db.execute(
        """
        INSERT INTO purchase_logs
          (purchase_id, action, changed_at, changed_by,
           store_id, supplier_id, item_id,
           delivery_date, quantity, unit_price, amount, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            purchase_row["id"],
            action,
            datetime.now().isoformat(timespec="seconds"),
            changed_by,
            purchase_row["store_id"],
            purchase_row["supplier_id"],
            purchase_row["item_id"],
            purchase_row["delivery_date"],
            purchase_row["quantity"],
            purchase_row["unit_price"],
            purchase_row["amount"],
            purchase_row["created_at"],
        ),
    )


# ----------------------------------------
# ホーム画面
# ----------------------------------------
@app.route("/")
def index():
    return render_template("home.html")


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
    # -----------------------------------------
    # NameError 対策：常に変数を初期化しておく
    # -----------------------------------------
    purchases = []
    results_count = 0

    # ----------------------------------------------------
    # POST: 登録（新規 INSERT）処理
    # ----------------------------------------------------
    if request.method == "POST":
        store_id = request.form.get("store_id") or None

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

        # 明細 1〜5 行をループ
        for i in range(1, 6):
            delivery_date = request.form.get(f"detail_date_{i}") or ""
            supplier_id = request.form.get(f"supplier_id_{i}") or ""
            item_id = request.form.get(f"item_id_{i}") or ""
            qty_raw = request.form.get(f"quantity_{i}") or ""
            unit_price_raw = request.form.get(f"unit_price_{i}") or ""

            # 数量・単価を整数に正規化
            qty_val = to_int(qty_raw)
            unit_price_val = to_int(unit_price_raw)
            amount_val = qty_val * unit_price_val

            # 完全に空行ならスキップ
            if (
                not delivery_date
                and not supplier_id
                and not item_id
                and qty_val == 0
                and unit_price_val == 0
            ):
                continue

            # 最低限の必須チェック
            if not delivery_date or not item_id:
                # ここでは「その行だけスキップ」
                continue

            # INSERT
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
            new_id = cur.lastrowid

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

        # store_id をクエリに付けて一覧も同じ店舗で再表示
        if store_id:
            return redirect(url_for("new_purchase", store_id=store_id))
        else:
            return redirect(url_for("new_purchase"))

    # ----------------------------------------------------
    # GET: 画面表示（フォーム + 検索付き直近50件）
    # ----------------------------------------------------
    store_id = request.args.get("store_id") or ""
    selected_store_id = int(store_id) if store_id else None

    # ★ 条件クリアボタンが押されたとき
    if request.args.get("clear") == "1":
        if store_id:
            return redirect(url_for("new_purchase", store_id=store_id))
        else:
            return redirect(url_for("new_purchase"))

    # 検索条件（テンプレ側と名前を揃える）
    from_date = request.args.get("from_date") or ""
    to_date = request.args.get("to_date") or ""
    search_q = (request.args.get("q") or "").strip()

    # フィルタ条件を組み立て
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


# API: 仕入先に紐づく品目一覧を返す
# /api/items/by_supplier/<supplier_id>
# ----------------------------------------
@app.route("/api/items/by_supplier/<int:supplier_id>")
def api_items_by_supplier(supplier_id):
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

    return jsonify(
        [
            {"id": r["id"], "code": r["code"], "name": r["name"], "unit": r["unit"]}
            for r in rows
        ]
    )


# ----------------------------------------
# 取引編集・削除（ソフトデリート対応）
# /purchases/<id>/edit
# ----------------------------------------
@app.route("/purchases/<int:purchase_id>/edit", methods=["GET", "POST"])
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
        # =========================
        # 削除ボタン（ソフトデリート）
        # =========================
        if "delete" in request.form:
            # 削除前の値を取得（ログ用）
            old_row = db.execute(
                "SELECT * FROM purchases WHERE id = ?",
                (purchase_id,),
            ).fetchone()

            # is_deleted フラグを 1 に更新
            db.execute(
                """
                UPDATE purchases
                SET is_deleted = 1
                WHERE id = ?
                """,
                (purchase_id,),
            )

            # 削除後（is_deleted=1）の行を取得
            new_row = db.execute(
                "SELECT * FROM purchases WHERE id = ?",
                (purchase_id,),
            ).fetchone()

            # ログに DELETE として記録
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

            # 元の店舗で一覧に戻る
            return redirect(
                url_for("new_purchase", store_id=purchase["store_id"])
            )

        # =========================
        # ここから更新処理
        # =========================
        store_id = request.form.get("store_id") or None
        delivery_date = request.form.get("delivery_date") or ""
        supplier_id = request.form.get("supplier_id") or None
        item_id = request.form.get("item_id") or None
        quantity = (request.form.get("quantity") or "").replace(",", "")
        unit_price = (request.form.get("unit_price") or "").replace(",", "")

        # 必須チェック
        if not delivery_date or not item_id:
            flash("納品日と品目は必須です。")
            return render_template(
                "purchase_edit.html",
                purchase=purchase,
                stores=stores,
                suppliers=suppliers,
            )

        # 数値変換
        try:
            qty_val = int(quantity) if quantity else 0
        except ValueError:
            qty_val = 0

        try:
            unit_price_val = int(unit_price) if unit_price else 0
        except ValueError:
            unit_price_val = 0

        amount_val = qty_val * unit_price_val

        # --- UPDATE 前の値を取得 ---
        old_row = db.execute(
            "SELECT * FROM purchases WHERE id = ?",
            (purchase_id,),
        ).fetchone()

        try:
            # --- UPDATE 実行 ---
            db.execute(
                """
                UPDATE purchases
                SET
                  store_id    = ?,
                  supplier_id = ?,
                  item_id     = ?,
                  delivery_date = ?,
                  quantity    = ?,
                  unit_price  = ?,
                  amount      = ?
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

            # --- UPDATE 後の値を取得 ---
            new_row = db.execute(
                "SELECT * FROM purchases WHERE id = ?",
                (purchase_id,),
            ).fetchone()

            # --- ログを記録 ---
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

        except sqlite3.OperationalError as e:
            db.rollback()
            flash(f"purchases テーブルの更新でエラーが発生しました: {e}")

        return redirect(url_for("new_purchase", store_id=store_id))

    # GET のとき：編集画面表示
    return render_template(
        "purchase_edit.html",
        purchase=purchase,
        stores=stores,
        suppliers=suppliers,
    )


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

    # GET
    suppliers = db.execute(
        "SELECT id, code, name, phone, email, address FROM suppliers ORDER BY code, id"
    ).fetchall()

    return render_template(
        "suppliers_master.html",
        suppliers=suppliers,
    )

# ----------------------------------------
# 仕入先編集・削除
# /suppliers/<id>/edit
# ----------------------------------------
@app.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
def edit_supplier(supplier_id):
    db = get_db()

    # 対象仕入先を取得
    supplier = db.execute(
        """
        SELECT id, code, name, phone, email, address
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
        # 削除ボタン押下時
        # ----------------------
        if "delete" in request.form:
            try:
                db.execute(
                    "DELETE FROM suppliers WHERE id = ?",
                    (supplier_id,),
                )
                db.commit()
                flash(f"仕入先（{supplier['name']}）を削除しました。")
            except sqlite3.Error as e:
                db.rollback()
                flash(f"仕入先削除でエラーが発生しました: {e}")

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
# /items
# 仕入先ごとに SSIII（5桁）コード自動採番
# ----------------------------------------
@app.route("/items", methods=["GET", "POST"])
def items_master():
    db = get_db()

    # 仕入先一覧（プルダウン用）
    suppliers = db.execute(
        "SELECT id, name, code FROM suppliers ORDER BY code"
    ).fetchall()

    # 登録済み品目一覧（コード / 仕入先名 / 品名 / ケース入数 / 温度帯 / 内製）
    items = db.execute(
        """
        SELECT
          i.id,
          i.code,
          i.name,
          i.unit,
          i.temp_zone,
          i.is_internal,
          s.name AS supplier_name
        FROM items i
        LEFT JOIN suppliers s ON i.supplier_id = s.id
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
                items=items,
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
                items=items,
            )

        code2 = str(supplier["code"]).zfill(2)[:2]

        # 既存コードの最大値（SSIII の III 部分）を取得
        row = db.execute(
            "SELECT MAX(code) AS max_code FROM items WHERE code LIKE ?",
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
                INSERT INTO items
                    (code, name, unit, supplier_id, temp_zone, is_internal)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (new_code, name, unit_val, supplier_id, temp_zone, is_internal),
            )
            db.commit()
            flash(f"品目を登録しました。（コード: {new_code}）")
        except sqlite3.Error as e:
            db.rollback()
            flash(f"items テーブルへの登録でエラーが発生しました: {e}")

        return redirect(url_for("items_master"))

    # --------- GET：表示 ----------
    return render_template(
        "items_master.html",
        suppliers=suppliers,
        items=items,
    )

# ----------------------------------------
# 品目編集
# /items/<id>/edit
# ----------------------------------------
@app.route("/items/<int:item_id>/edit", methods=["GET", "POST"])
def edit_item(item_id):
    db = get_db()

    # 仕入先一覧（プルダウン用）
    suppliers = db.execute(
        "SELECT id, name, code FROM suppliers ORDER BY code"
    ).fetchall()

    # 対象品目を取得
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
          i.storage_cost
        FROM items i
        WHERE i.id = ?
        """,
        (item_id,),
    ).fetchone()

    if item is None:
        flash("指定された品目が見つかりません。")
        return redirect(url_for("items_master"))

    if request.method == "POST":

        # ==============================
        # 削除ボタンが押された場合
        # ==============================
        if "delete" in request.form:
            try:
                db.execute(
                    "DELETE FROM items WHERE id = ?",
                    (item_id,),
                )
                db.commit()
                flash(f"品目（コード: {item['code']}）を削除しました。")
            except sqlite3.Error as e:
                db.rollback()
                flash(f"品目削除でエラーが発生しました: {e}")

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
                UPDATE items
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
# 仕入れ照会（月次・直近13ヶ月）
# /purchases/report
# ----------------------------------------
@app.route("/purchases/report", methods=["GET"])
def purchase_report():
    db = get_db()

    # 店舗一覧（プルダウン用）
    stores = db.execute(
        "SELECT id, name FROM stores ORDER BY code"
    ).fetchall()

    # 店舗（クエリパラメータ）
    store_id = request.args.get("store_id") or ""

    # 今日を基準に直近13ヶ月 (今月 + 過去12ヶ月)
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
    month_keys = list(reversed(month_keys))  # 古い→新しい順

    # SQL用の日付範囲
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

    # is_deleted = 0 を必ず条件に入れる
    where_clauses = [
        "p.is_deleted = 0",
        "p.delivery_date >= ?",
        "p.delivery_date < ?",
    ]
    params = [start_date, end_date]

    if store_id:
        where_clauses.append("p.store_id = ?")
        params.append(store_id)

    where_sql = " AND ".join(where_clauses)

    sql = f"""
        SELECT
            s.id   AS supplier_id,
            s.name AS supplier_name,
            strftime('%Y-%m', p.delivery_date) AS ym,
            SUM(p.amount) AS total_amount
        FROM purchases p
        LEFT JOIN items i     ON p.item_id = i.id
        LEFT JOIN suppliers s ON i.supplier_id = s.id
        WHERE {where_sql}
        GROUP BY s.id, s.name, ym
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

    # 各列（各月）の合計
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
# /purchases/report/supplier/<supplier_id>
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

    # 仕入先名（表示用）
    if supplier_id == 0:
        supplier_name = "（仕入先を選択してください）"
    else:
        supplier_row = db.execute(
            "SELECT id, name FROM suppliers WHERE id = ?",
            (supplier_id,),
        ).fetchone()
        if supplier_row is None:
            return redirect(url_for("purchase_report"))
        supplier_name = supplier_row["name"]

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

    # SQL用の日付範囲
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
            "p.delivery_date >= ?",
            "p.delivery_date < ?",
            "i.supplier_id = ?",
        ]
        params = [start_date, end_date, supplier_id]

        if store_id:
            where_clauses.append("p.store_id = ?")
            params.append(store_id)

        where_sql = " AND ".join(where_clauses)

        sql = f"""
            SELECT
                i.id   AS item_id,
                i.code AS item_code,
                i.name AS item_name,
                strftime('%Y-%m', p.delivery_date) AS ym,
                SUM(p.quantity) AS total_qty,
                SUM(p.amount)   AS total_amount
            FROM purchases p
            LEFT JOIN items i ON p.item_id = i.id
            WHERE {where_sql}
            GROUP BY i.id, i.code, i.name, ym
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

    # 単価
    for item in item_map.values():
        for ym in month_keys:
            a = item["amount"][ym]
            q = item["qty"][ym]
            item["unit_price"][ym] = (a / q) if q else 0

    item_rows = list(item_map.values())

    # 各月の金額・数量合計
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
# 棚卸し履歴ヘルパー（最新日＋過去2回）
# ----------------------------------------
def get_latest_stock_count_dates(db, store_id, limit=3):
    rows = db.execute(
        """
        SELECT DISTINCT count_date
        FROM stock_counts
        WHERE store_id = ?
        ORDER BY count_date DESC
        LIMIT ?
        """,
        (store_id, limit),
    ).fetchall()
    return [r["count_date"] for r in rows]


# ----------------------------------------
# 棚卸し入力
# /inventory/count
# ----------------------------------------
@app.route("/inventory/count", methods=["GET", "POST"])
def inventory_count():
    db = get_db()

    # 店舗一覧
    stores = db.execute(
        "SELECT id, name FROM stores ORDER BY code"
    ).fetchall()

    # 今日の日付をデフォルトに
    today = datetime.today().strftime("%Y-%m-%d")

    # -----------------------------
    # POST：棚卸し登録
    # -----------------------------
    if request.method == "POST":
        store_id = request.form.get("store_id") or None
        count_date = request.form.get("count_date") or today

        if not store_id:
            flash("店舗を選択してください。")
            return redirect(url_for("inventory_count"))

        row_count = int(request.form.get("row_count", 0))

        for i in range(1, row_count + 1):
            item_id = request.form.get(f"item_id_{i}")
            system_qty = request.form.get(f"system_qty_{i}")
            counted_qty = request.form.get(f"count_qty_{i}")

            if not item_id:
                continue

            if counted_qty is None or counted_qty == "":
                continue

            try:
                sys_val = int(system_qty or 0)
                cnt_val = int(counted_qty or 0)
            except ValueError:
                continue

            diff = cnt_val - sys_val

            db.execute(
                """
                INSERT INTO stock_counts
                    (store_id, item_id, count_date,
                     system_qty, counted_qty, diff_qty, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    store_id,
                    item_id,
                    count_date,
                    sys_val,
                    cnt_val,
                    diff,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

        db.commit()
        flash("棚卸し結果を登録しました。")
        return redirect(
            url_for("inventory_count", store_id=store_id, count_date=count_date)
        )

    # -----------------------------
    # GET：表示
    # -----------------------------
    store_id = request.args.get("store_id") or ""
    count_date = request.args.get("count_date") or today

    items = []
    selected_store_id = int(store_id) if store_id else None

    # ★ 最新棚卸日（＋過去2回）を取得
    latest_dates = []
    latest_date = None
    if selected_store_id:
        latest_dates = get_latest_stock_count_dates(db, selected_store_id, limit=3)
        latest_date = latest_dates[0] if latest_dates else None

    # ★ 温度帯ラベルと入れ物を先に用意しておく（store_id が空でも定義されるように）
    zones = ["冷凍", "冷蔵", "常温", "その他"]
    grouped_items = {z: [] for z in zones}

    if store_id:
        # まず、内製品（is_internal=1）は仕入がなくても拾う
        # 通常品（is_internal=0）は、指定店舗・指定日までに一度でも仕入がある品目だけ拾う
        base_rows = db.execute(
            """
            SELECT
                i.id   AS item_id,
                i.code AS item_code,
                i.name AS item_name,
                COALESCE(i.temp_zone, 'その他') AS storage_type,
                i.is_internal
            FROM items i
            WHERE i.is_internal = 1

            UNION

            SELECT DISTINCT
                i.id   AS item_id,
                i.code AS item_code,
                i.name AS item_name,
                COALESCE(i.temp_zone, 'その他') AS storage_type,
                i.is_internal
            FROM items i
            JOIN purchases p
              ON p.item_id = i.id
             AND p.store_id = ?
             AND p.delivery_date <= ?
             AND p.is_deleted = 0
            WHERE i.is_internal = 0

            ORDER BY storage_type, item_code
            """,
            (store_id, count_date),
        ).fetchall()

        for row in base_rows:
            item_id = row["item_id"]
            item_code = row["item_code"]
            item_name = row["item_name"]

            # ---------- システム在庫の計算 ----------
            # 最新の棚卸し（count_date 以前）を取得
            last_cnt = db.execute(
                """
                SELECT counted_qty, count_date
                FROM stock_counts
                WHERE store_id = ?
                  AND item_id  = ?
                  AND count_date <= ?
                ORDER BY count_date DESC, id DESC
                LIMIT 1
                """,
                (store_id, item_id, count_date),
            ).fetchone()

            if last_cnt:
                opening_qty = last_cnt["counted_qty"]
                start_date = last_cnt["count_date"]

                pur_row = db.execute(
                    """
                    SELECT COALESCE(SUM(quantity), 0) AS qty
                    FROM purchases
                    WHERE store_id = ?
                      AND item_id  = ?
                      AND delivery_date > ?
                      AND delivery_date <= ?
                      AND is_deleted = 0
                    """,
                    (store_id, item_id, start_date, count_date),
                ).fetchone()
            else:
                opening_qty = 0
                pur_row = db.execute(
                    """
                    SELECT COALESCE(SUM(quantity), 0) AS qty
                    FROM purchases
                    WHERE store_id = ?
                      AND item_id  = ?
                      AND delivery_date <= ?
                      AND is_deleted = 0
                    """,
                    (store_id, item_id, count_date),
                ).fetchone()

            pur_qty = pur_row["qty"] if pur_row else 0
            end_qty = opening_qty + pur_qty   # システム在庫

            # ---------- 単価（加重平均） ----------
            price_row = db.execute(
                """
                SELECT
                  CASE
                    WHEN SUM(quantity) > 0 THEN
                      CAST(SUM(quantity * unit_price) AS REAL) / SUM(quantity)
                    ELSE 0
                  END AS unit_price
                FROM purchases
                WHERE store_id = ?
                  AND item_id  = ?
                  AND delivery_date <= ?
                  AND is_deleted = 0
                """,
                (store_id, item_id, count_date),
            ).fetchone()

            unit_price = price_row["unit_price"] or 0.0
            stock_amount = end_qty * unit_price

            # ---------- この棚卸し日の棚卸数量を取得 ----------
            counted_row = db.execute(
                """
                SELECT counted_qty
                FROM stock_counts
                WHERE store_id   = ?
                  AND item_id    = ?
                  AND count_date = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (store_id, item_id, count_date),
            ).fetchone()

            counted_qty = counted_row["counted_qty"] if counted_row else None

            # 在庫ゼロは通常は表示しないが、
            # 内製品（is_internal=1）は在庫ゼロでも表示する
            is_internal = row["is_internal"] == 1

            if end_qty > 0 or is_internal:
                items.append(
                    {
                        "item_id": item_id,
                        "item_code": item_code,
                        "item_name": item_name,
                        "system_qty": end_qty,
                        "unit_price": unit_price,
                        "stock_amount": stock_amount,
                        "counted_qty": counted_qty,
                        "storage_type": row["storage_type"],
                        "is_internal": row["is_internal"],
                    }
                )
        # ★ items を温度帯ごとにグルーピング
        for it in items:
            z = it.get("storage_type") or "その他"
            if z not in grouped_items:
                grouped_items[z] = []
            grouped_items[z].append(it)

    return render_template(
        "inventory_count.html",
        stores=stores,
        selected_store_id=selected_store_id,
        count_date=count_date,
        items=items,
        latest_date=latest_date,
        latest_dates=latest_dates,
        zones=zones,
        grouped_items=grouped_items,
    )


# ----------------------------------------
# 月次利用量レポート
# /usage/report
# ----------------------------------------
@app.route("/usage/report", methods=["GET"])
def usage_report():
    db = get_db()

    # 店舗一覧
    stores = db.execute(
        "SELECT id, name FROM stores ORDER BY code"
    ).fetchall()
    store_id = request.args.get("store_id") or ""

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

    # ① 月内仕入数量
    where_pur = [
        "p.delivery_date >= ?",
        "p.delivery_date < ?",
        "p.is_deleted = 0",
    ]
    params_pur = [start_date, end_date]

    if store_id:
        where_pur.append("p.store_id = ?")
        params_pur.append(store_id)

    where_pur_sql = " AND ".join(where_pur)

    sql_pur = f"""
        SELECT
            p.item_id,
            strftime('%Y-%m', p.delivery_date) AS ym,
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

    # ② 各月の最新棚卸数量
    where_inv = ["sc.count_date >= ?", "sc.count_date < ?"]
    params_inv = [start_date, end_date]
    if store_id:
        where_inv.append("sc.store_id = ?")
        params_inv.append(store_id)
    where_inv_sql = " AND ".join(where_inv)

    sql_inv = f"""
        WITH last_counts AS (
          SELECT
            sc.store_id,
            sc.item_id,
            strftime('%Y-%m', sc.count_date) AS ym,
            MAX(sc.count_date)               AS max_date
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
        SELECT
          item_id,
          ym,
          counted_qty
        FROM month_end_inventory
    """
    rows_inv = db.execute(sql_inv, params_inv).fetchall()

    end_inv_map = {}
    for r in rows_inv:
        iid = r["item_id"]
        ym = r["ym"]
        qty = int(r["counted_qty"] or 0)
        end_inv_map.setdefault(iid, {})[ym] = qty

    # ③ アイテム情報
    item_ids = set(pur_map.keys()) | set(end_inv_map.keys())
    if item_ids:
        placeholders = ",".join(["?"] * len(item_ids))
        sql_items = f"""
            SELECT id, code, name
            FROM items
            WHERE id IN ({placeholders})
            ORDER BY code
        """
        items = db.execute(sql_items, list(item_ids)).fetchall()
    else:
        items = []

    item_meta = {row["id"]: row for row in items}

    # ④ 品目ごとに期首・仕入・期末・利用量を計算
    item_rows = []

    for iid in sorted(item_ids):
        meta = item_meta.get(iid)
        if not meta:
            continue

        code = meta["code"]
        name = meta["name"]

        per_month = {}
        total_pur = 0
        total_used = 0
        total_end = 0

        prev_end_qty = 0

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

    selected_store_id = int(store_id) if store_id else None

    return render_template(
        "usage_report.html",
        stores=stores,
        selected_store_id=selected_store_id,
        month_keys=month_keys,
        item_rows=item_rows,
    )


# ----------------------------------------
# 月次 利用量照会（仕入＋棚卸ベース）
# /inventory/usage
# ----------------------------------------
@app.route("/inventory/usage", methods=["GET"])
def inventory_usage():
    db = get_db()

    # 店舗一覧
    stores = db.execute(
        "SELECT id, name FROM stores ORDER BY code"
    ).fetchall()

    # クエリパラメータ（店舗）
    store_id = request.args.get("store_id") or ""
    selected_store_id = int(store_id) if store_id else None

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

    # 仕入数量（月別）
    where_p = [
        "p.delivery_date >= ?",
        "p.delivery_date < ?",
        "p.is_deleted = 0",
    ]
    params_p = [start_date, end_date]

    if store_id:
        where_p.append("p.store_id = ?")
        params_p.append(store_id)

    where_p_sql = " AND ".join(where_p)

    purchases_sql = f"""
        SELECT
          i.id   AS item_id,
          i.name AS item_name,
          strftime('%Y-%m', p.delivery_date) AS ym,
          SUM(p.quantity) AS qty
        FROM purchases p
        JOIN items i ON i.id = p.item_id
        WHERE {where_p_sql}
        GROUP BY i.id, i.name, ym
    """
    purchase_rows = db.execute(purchases_sql, params_p).fetchall()

    # 棚卸数量（月末）
    where_s = ["sc.count_date >= ?", "sc.count_date < ?"]
    params_s = [start_date, end_date]
    if store_id:
        where_s.append("sc.store_id = ?")
        params_s.append(store_id)
    where_s_sql = " AND ".join(where_s)

    stock_sql = f"""
        SELECT
          sc.item_id AS item_id,
          i.name     AS item_name,
          strftime('%Y-%m', sc.count_date) AS ym,
          MAX(sc.counted_qty) AS stock_qty
        FROM stock_counts sc
        JOIN items i ON i.id = sc.item_id
        WHERE {where_s_sql}
        GROUP BY sc.item_id, i.name, ym
    """
    stock_rows = db.execute(stock_sql, params_s).fetchall()

    # Python側でピボット＆利用量計算
    item_map = {}

    def ensure_item(iid, name):
        if iid not in item_map:
            item_map[iid] = {
                "item_id": iid,
                "item_name": name,
                "purchases": {},
                "stocks": {},
                "usage": {},
                "total_purchases": 0,
                "total_usage": 0,
            }
        return item_map[iid]

    # 仕入
    for r in purchase_rows:
        iid = r["item_id"]
        name = r["item_name"]
        ym = r["ym"]
        qty = r["qty"] or 0
        item = ensure_item(iid, name)
        item["purchases"][ym] = qty

    # 棚卸
    for r in stock_rows:
        iid = r["item_id"]
        name = r["item_name"]
        ym = r["ym"]
        stock_qty = r["stock_qty"] or 0
        item = ensure_item(iid, name)
        item["stocks"][ym] = stock_qty

    # 利用量 = 前月在庫 + 当月仕入 - 当月在庫
    for item in item_map.values():
        prev_stock = 0
        for ym in month_keys:
            purch = item["purchases"].get(ym, 0)
            closing = item["stocks"].get(ym, 0)
            usage = prev_stock + purch - closing

            item["usage"][ym] = usage
            item["total_purchases"] += purch
            item["total_usage"] += usage

            prev_stock = closing

    item_rows = list(item_map.values())
    item_rows.sort(key=lambda x: x["total_usage"], reverse=True)

    return render_template(
        "inventory_usage.html",
        stores=stores,
        selected_store_id=selected_store_id,
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

    # 店舗（クエリ / 空文字なら全店舗）
    store_id = request.args.get("store_id") or ""
    selected_store_id = int(store_id) if store_id else None

    # 対象月（直近13ヶ月）
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
    end_year, end_month = map(int, end_last.split("-"))
    if end_month == 12:
        end_year += 1
        end_month = 1
    else:
        end_month += 1
    end_date = f"{end_year:04d}-{end_month:02d}-01"

    # 1. 当月仕入高
    sql_pur = """
        SELECT
          strftime('%Y-%m', p.delivery_date) AS ym,
          SUM(p.amount) AS total_amount
        FROM purchases p
        WHERE p.delivery_date >= ?
          AND p.delivery_date < ?
          AND p.is_deleted = 0
          AND (? = '' OR p.store_id = ?)
        GROUP BY ym
    """
    pur_rows = db.execute(
        sql_pur, [start_date, end_date, store_id, store_id]
    ).fetchall()

    purchases_by_month = {ym: 0 for ym in month_keys}
    for r in pur_rows:
        ym = r["ym"]
        amt = r["total_amount"] or 0
        if ym in purchases_by_month:
            purchases_by_month[ym] = amt

    # 2. 期末棚卸高（FIFO評価）
    sql_inv_fifo = """
        WITH latest AS (
            SELECT
              sc.store_id,
              sc.item_id,
              date(sc.count_date) AS count_date,
              strftime('%Y-%m', sc.count_date) AS ym,
              sc.counted_qty,
              ROW_NUMBER() OVER (
                PARTITION BY sc.store_id, sc.item_id, strftime('%Y-%m', sc.count_date)
                ORDER BY sc.count_date DESC, sc.id DESC
              ) AS rn
            FROM stock_counts sc
            WHERE sc.count_date >= ?
              AND sc.count_date < ?
              AND (? = '' OR sc.store_id = ?)
        ),
        end_stock AS (
            SELECT
              store_id,
              item_id,
              ym,
              count_date,
              counted_qty AS end_qty
            FROM latest
            WHERE rn = 1
              AND counted_qty > 0
        ),
        fifo_base AS (
            SELECT
              e.store_id,
              e.item_id,
              e.ym,
              e.end_qty,
              p.id AS purchase_id,
              date(p.delivery_date) AS delivery_date,
              p.quantity,
              p.unit_price,
              SUM(p.quantity) OVER (
                PARTITION BY e.store_id, e.item_id, e.ym
                ORDER BY date(p.delivery_date) DESC, p.id DESC
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
              ) AS running_qty
            FROM end_stock e
            JOIN purchases p
              ON p.store_id = e.store_id
             AND p.item_id  = e.item_id
             AND date(p.delivery_date) <= e.count_date
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
        [start_date, end_date, store_id, store_id],
    ).fetchall()

    end_inv_by_month = {ym: 0 for ym in month_keys}
    for r in inv_rows:
        ym = r["ym"]
        amt = r["inv_amount"] or 0
        if ym in end_inv_by_month:
            end_inv_by_month[ym] = float(amt)

    # 3. 期首棚卸高（前月期末）
    beg_inv_by_month = {}
    prev_end = 0.0
    for ym in month_keys:
        beg_inv_by_month[ym] = prev_end
        prev_end = end_inv_by_month.get(ym, 0.0)

    # 4. 売上原価 = 期首 + 仕入 - 期末
    cogs_by_month = {}
    for ym in month_keys:
        beg = beg_inv_by_month.get(ym, 0.0)
        pur = purchases_by_month.get(ym, 0.0)
        end = end_inv_by_month.get(ym, 0.0)
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


# ----------------------------------------
# メイン起動
# ----------------------------------------
if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    app.run(host="0.0.0.0", port=port, debug=debug)
"""
