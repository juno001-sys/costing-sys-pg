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
        return render_template("home2.html")

    return app


# -------------------------------------------------------
# 本番起動
# -------------------------------------------------------
app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
