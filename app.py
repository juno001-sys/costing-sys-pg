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

@app.context_processor
def inject_env():
    return dict(env=os.environ.get("ENV", "dev"))
    
@app.before_request
def inject_version():
    g.app_version = APP_VERSION


# ----------------------------------------
# 本番・テスト環境切り替え
# ----------------------------------------

app.config["APP_ENV"] = os.getenv("APP_ENV", "dev")

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


# =========================
# Blueprint / view 初期化
# =========================
from views.purchases import init_purchase_views
from views.reports import init_report_views
from views.masters import init_master_views
from views.inventory import init_inventory_views

# 仕入れ系ビュー
init_purchase_views(app, get_db, log_purchase_change)

# レポート系ビュー
init_report_views(app, get_db)

# マスタ系ビュー
init_master_views(app, get_db)

# 棚卸し系ビュー
init_inventory_views(app, get_db)

# ----------------------------------------
# メイン起動
# ----------------------------------------
if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
