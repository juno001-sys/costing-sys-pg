from __future__ import annotations

import json
import os
from datetime import datetime, date

from flask import Flask, g, render_template

from db import get_db, close_db
from views.inventory import init_inventory_views
from views.masters import init_master_views
from views.purchases import init_purchase_views
from views.reports import init_report_views


# ----------------------------------------
# Flask app
# ----------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "kurajika-dev")
app.config["JSON_AS_ASCII"] = False

APP_VERSION = os.getenv("RAILWAY_GIT_COMMIT_SHA", "dev")[:7]
APP_ENV = os.getenv("APP_ENV", "development")  # dev / production / staging etc.


@app.context_processor
def inject_env():
    # Use APP_ENV consistently (instead of ENV)
    return {"env": APP_ENV}


@app.before_request
def inject_version():
    g.app_version = APP_VERSION


@app.teardown_appcontext
def teardown_db(exc):
    close_db(exc)


# ----------------------------------------
# Helpers
# ----------------------------------------
def log_purchase_change(db, purchase_id, action, old_row, new_row, changed_by=None):
    """
    purchases changes -> purchase_logs
    (Works for both sqlite row-like and dict rows)
    """
    def row_to_dict(row):
        if row is None:
            return None
        if isinstance(row, dict):
            data = row
        else:
            try:
                data = dict(row)
            except TypeError:
                return {"_raw": str(row)}

        def convert(v):
            if isinstance(v, datetime):
                return v.isoformat(timespec="seconds")
            if isinstance(v, date):
                return v.isoformat()
            return v

        return {k: convert(v) for k, v in data.items()}

    old_data = row_to_dict(old_row)
    new_data = row_to_dict(new_row)

    db.execute(
        """
        INSERT INTO purchase_logs
          (purchase_id, action, old_data, new_data, changed_by, changed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            purchase_id,
            action,
            json.dumps(old_data, ensure_ascii=False) if old_data is not None else None,
            json.dumps(new_data, ensure_ascii=False) if new_data is not None else None,
            changed_by,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


# ----------------------------------------
# Home
# ----------------------------------------
@app.route("/")
def index():
    return render_template("home.html")


# ----------------------------------------
# Register views (blueprint-style init)
# ----------------------------------------
init_purchase_views(app, get_db, log_purchase_change)
init_report_views(app, get_db)
init_master_views(app, get_db)
init_inventory_views(app, get_db)


# ----------------------------------------
# Run
# ----------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
