from flask import Flask, render_template, request, redirect, url_for, flash, g
import sqlite3
from datetime import date

DATABASE = "costing.sqlite3"

app = Flask(__name__)
app.secret_key = "change-this-to-something-random"  # フラッシュメッセージ用


# --- DB 接続ヘルパー -------------------------------------------------
def get_db():
    if "db" not in g:
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# --- 取引データ入力画面（納品書） ------------------------------------
@app.route("/purchases/new", methods=["GET", "POST"])
def new_purchase():
    db = get_db()

    if request.method == "POST":
        # ヘッダ部
        store_id = request.form.get("store_id")
        supplier_id = request.form.get("supplier_id")
        delivery_date = request.form.get("delivery_date")
        slip_no = request.form.get("slip_no")

        # 必須チェック（最低限）
        if not store_id or not supplier_id or not delivery_date or not slip_no:
            flash("店舗、仕入先、納品日、納品書番号は必須です。")
            return redirect(url_for("new_purchase"))

        # 納品書ヘッダの登録
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO delivery_notes (store_id, supplier_id, delivery_date, slip_no)
            VALUES (?, ?, ?, ?)
            """,
            (store_id, supplier_id, delivery_date, slip_no),
        )
        delivery_note_id = cur.lastrowid

        # 明細部の登録（最大5行を想定）
        line_count = 0
        for i in range(1, 6):
            item_id = request.form.get(f"item_id_{i}")
            qty = request.form.get(f"quantity_{i}")
            unit_price = request.form.get(f"unit_price_{i}")

            # 全部空ならスキップ
            if not item_id and not qty and not unit_price:
                continue

            # 数量・単価はどちらか欠けたらスキップ（ゆるい運用）
            if not item_id or not qty or not unit_price:
                # 本当はエラーにして戻す方が安全
                continue

            try:
                quantity = float(qty)
                unit_price_val = float(unit_price)
            except ValueError:
                # 数値変換できなければスキップ
                continue

            amount = quantity * unit_price_val

            cur.execute(
                """
                INSERT INTO delivery_note_lines
                    (delivery_note_id, item_id, quantity, unit_price, amount)
                VALUES (?, ?, ?, ?, ?)
                """,
                (delivery_note_id, item_id, quantity, unit_price_val, amount),
            )
            line_count += 1

        db.commit()

        flash(f"納品書を登録しました（明細 {line_count} 行）。")
        return redirect(url_for("new_purchase"))

    # GET のとき：フォーム表示用にマスタを取得
    stores = db.execute(
        "SELECT id, name FROM stores ORDER BY id"
    ).fetchall()
    suppliers = db.execute(
        "SELECT id, name FROM suppliers ORDER BY name"
    ).fetchall()
    items = db.execute(
        "SELECT id, name FROM items WHERE is_active = 1 ORDER BY name"
    ).fetchall()

    today = date.today().isoformat()

    return render_template(
        "purchase_form.html",
        stores=stores,
        suppliers=suppliers,
        items=items,
        today=today,
    )


# --- テスト用トップページ --------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("new_purchase"))


if __name__ == "__main__":
    app.run(debug=True)