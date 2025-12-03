import os
from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy

# --- SQLite 用の DB 初期化 ---
db = SQLAlchemy()

# -------------------------------------------------------
# Flask アプリ工場（SQLite 前提）
# -------------------------------------------------------
def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "kurajika-dev"
    app.config["JSON_AS_ASCII"] = False

    # --- SQLite の DB ファイルを使用 ---
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///costing.sqlite3"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    # ---- Blueprint 読み込み ----
    from views.purchase import purchase_bp
    from views.stock import stock_bp
    from views.delivery import delivery_bp
    from views.report import report_bp

    app.register_blueprint(purchase_bp)
    app.register_blueprint(stock_bp)
    app.register_blueprint(delivery_bp)
    app.register_blueprint(report_bp)


    @app.route("/_alias/new_purchase")
    def new_purchase():
        # 実体は Blueprint 内の関数
        return app.view_functions["purchase.purchase_new_purchase"]()

    # ---- ホーム ----
    @app.route("/")
    def home():
        return render_template("home2.html")   # ← home2 は使わない

    return app


# -------------------------------------------------------
# ローカル起動
# -------------------------------------------------------
app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000)
