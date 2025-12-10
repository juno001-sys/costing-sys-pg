import os
import sqlite3
import psycopg2
import urllib.parse
import json
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

# ----------------------------------------
# Flask アプリ
# ----------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = "kurajika-dev"
app.config["JSON_AS_ASCII"] = False
APP_VERSION = os.getenv("RAILWAY_GIT_COMMIT_SHA", "dev")[:7]

@app.before_request
def inject_version():
    g.app_version = APP_VERSION


# ----------------------------------------
# パス設定（SQLite 用）
# ----------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "costing.sqlite3"

# ----------------------------------------
# Postgres専用 DB ヘルパー（db.execute をそのまま使える）
# ----------------------------------------
import psycopg2
import psycopg2.extras

class DBWrapper:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        """
        SQLite の ? を Postgres の %s に自動変換して実行する。
        """
        if params is None:
            params = []

        # SQLite の ? → Postgres の %s
        fixed_sql = sql.replace("?", "%s")

        cur = self.conn.cursor()
        cur.execute(fixed_sql, params)
        return cur

    def commit(self):
        return self.conn.commit()

    def rollback(self):
        return self.conn.rollback()

    def cursor(self):
        return self.conn.cursor()

    def __getattr__(self, name):
        return getattr(self.conn, name)


def get_db():
    """
    Postgres接続＋DBWrapperを返す。
    """
    if "pg" not in g:
        db_url = os.environ["DATABASE_URL"]
        conn = psycopg2.connect(
            db_url,
            cursor_factory=psycopg2.extras.RealDictCursor
        )
        g.pg = DBWrapper(conn)
    return g.pg




# ----------------------------------------
# teardown
# ----------------------------------------
@app.teardown_appcontext
def close_db(exc):
    """
    Postgres / SQLite を両方 close できるようにする
    """
    pg = g.pop("pg", None)
    if pg is not None:
        pg.close()

    db = g.pop("db", None)
    if db is not None:
        db.close()



# ----------------------------------------
# 取引変更ログ ヘルパー
# ----------------------------------------
def _row_to_dict(row):
    """sqlite3.Row → dict（ログ用）"""
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def log_purchase_change(db, purchase_id, action, old_row, new_row, changed_by=None):
    """purchases の変更履歴を purchase_logs に記録する"""

    def row_to_dict(row):
        if row is None:
            return None
        # すでに dict ならそのまま
        if isinstance(row, dict):
            data = row
        else:
            # sqlite3.Row / psycopg Row → dict に変換
            try:
                data = dict(row)
            except TypeError:
                # 最後の保険
                return {"_raw": str(row)}

        # date / datetime など JSON できないものを文字列にしておく
        def convert(v):
            if isinstance(v, datetime):
                return v.isoformat(timespec="seconds")
            # Postgres の date 型など
            try:
                from datetime import date
                if isinstance(v, date):
                    return v.isoformat()
            except Exception:
                pass
            return v

        return {k: convert(v) for k, v in data.items()}

    old_data_dict = row_to_dict(old_row)
    new_data_dict = row_to_dict(new_row)

    old_data_json = (
        json.dumps(old_data_dict, ensure_ascii=False)
        if old_data_dict is not None
        else None
    )
    new_data_json = (
        json.dumps(new_data_dict, ensure_ascii=False)
        if new_data_dict is not None
        else None
    )

    db.execute(
        """
        INSERT INTO purchase_logs
          (purchase_id, action, old_data, new_data, changed_by, changed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            purchase_id,
            action,
            old_data_json,
            new_data_json,
            changed_by,
            datetime.now().isoformat(timespec="seconds"),
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


# =========================
# Blueprint / view 初期化
# =========================
from views.purchases import init_purchase_views
from views.reports import init_report_views  

# 仕入れ系ビュー
init_purchase_views(app, get_db, log_purchase_change)

# レポート系ビュー
init_report_views(app, get_db)

# ----------------------------------------
# メイン起動
# ----------------------------------------
if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
