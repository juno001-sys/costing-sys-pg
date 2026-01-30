#app.py
from __future__ import annotations

import json
import os
import uuid
import time

from datetime import datetime, date
from flask import Flask, g, render_template, session, request, redirect, url_for
from db import get_db, close_db
from views.inventory import init_inventory_views
from views.inventory_v2 import init_inventory_views_v2
from views.masters import init_master_views
from views.purchases import init_purchase_views
from views.reports import init_report_views
from labels import label
from views.loc import init_location_views


from views.reports.audit_log import log_event


from views.auth.invite import init_auth_invite_views

from views.admin.users import init_admin_user_views
from views.admin.invites import init_admin_invites_views
from views.admin.profit_settings import bp as admin_profit_settings_bp

from views.auth.login import init_auth_login_views



# ----------------------------------------
# Flask app
# ----------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "kurajika-dev")
app.config["JSON_AS_ASCII"] = False

app.register_blueprint(admin_profit_settings_bp)

APP_VERSION = os.getenv("RAILWAY_GIT_COMMIT_SHA", "dev")[:7]
APP_ENV = os.getenv("APP_ENV", "development")  # dev / mail / prod etc.
SUPPORTED_LANGS = ["ja", "en", "hi", "id"]
DEFAULT_LANG = "ja"

@app.context_processor
def inject_env():
    # For templates: {{ env }}
    return {"env": APP_ENV}


@app.before_request
def inject_version():
    g.app_version = APP_VERSION


@app.teardown_appcontext
def teardown_db(exc):
    close_db(exc)


@app.context_processor
def inject_labels():
    # Usage in Jinja: {{ L("form.store") }}
    return {"L": label}

@app.before_request
def inject_request_id():
    g.request_id = uuid.uuid4().hex[:16]

@app.before_request
def start_timer():
    g.req_start = time.perf_counter()

@app.context_processor
def inject_t():
    try:
        lang = get_lang()
        translations = get_translations(lang)
    except Exception:
        lang = DEFAULT_LANG
        translations = {}

    def t(key: str, default: str | None = None) -> str:
        return translations.get(key, default or f"__{key}__")

    return {
        "t": t,
        "lang": lang,
        "supported_langs": SUPPORTED_LANGS,
    }

# ----------------------------------------
# PERF logging (slow requests + watch list)
# ----------------------------------------
WATCH_ENDPOINTS = {
    # reports
    "reports.purchase_report",
    "reports.usage_report",
    "reports.cost_report",
    "reports.work_logs",
    # inventory
    "inventory_count",
    "inventory_count_v2",
    # purchases
    "new_purchase",
    "edit_purchase",
}

WATCH_PATH_PREFIXES = (
    "/reports",          # catch all report pages
    "/inventory/count",  # v1
    "/inventory/count_v2",
)

SLOW_MS = 800  # default threshold

@app.after_request
def log_slow_request(response):
    try:
        start = getattr(g, "req_start", None)
        if start is None:
            return response

        elapsed_ms = (time.perf_counter() - start) * 1000.0

        endpoint = request.endpoint or ""
        path = request.path or ""

        watched = (
            endpoint in WATCH_ENDPOINTS
            or any(path.startswith(pfx) for pfx in WATCH_PATH_PREFIXES)
        )

        should_log = watched or elapsed_ms >= SLOW_MS or response.status_code >= 400
        if not should_log:
            return response

        db = get_db()
        try:
            log_event(
                db,
                action="PERF",
                module="system",
                message=("Watched request" if watched else "Slow request"),
                status_code=response.status_code,
                meta={
                    "elapsed_ms": round(elapsed_ms, 1),
                    "endpoint": endpoint,
                    "method": request.method,
                    "path": path,
                    "query": request.query_string.decode("utf-8") if request.query_string else "",
                    "watched": watched,
                },
            )
            db.commit()
        except Exception:
            pass

    except Exception:
        pass

    return response

# ----------------------------------------
# i18n (t function)
# ----------------------------------------
def load_lang_dict(lang: str) -> dict:
    # ✅ FIX: load from ./labels/<lang>.json
    path = os.path.join(app.root_path, "labels", f"{lang}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_LANG_CACHE: dict[str, dict] = {}


def get_translations(lang: str) -> dict:
    if lang not in _LANG_CACHE:
        _LANG_CACHE[lang] = load_lang_dict(lang)
    return _LANG_CACHE[lang]

@app.context_processor
def inject_current_company():
    company_id = getattr(g, "current_company_id", None)
    if not company_id:
        return {"current_company_name": None}

    try:
        db = get_db()
        row = db.execute(
            "SELECT name FROM mst_companies WHERE id = %s",
            (company_id,),
        ).fetchone()
        return {"current_company_name": row["name"] if row else None}
    except Exception:
        return {"current_company_name": None}

@app.context_processor
def inject_store_list():
    if hasattr(g, "_stores_cache"):
        return {"stores": g._stores_cache}

    # if not logged in, show none
    company_id = getattr(g, "current_company_id", None)
    if not company_id:
        g._stores_cache = []
        return {"stores": g._stores_cache}

    try:
        db = get_db()
        g._stores_cache = db.execute(
            """
            SELECT id, code, name
            FROM mst_stores
            WHERE COALESCE(is_active, 1) = 1
              AND company_id = %s
            ORDER BY code, id
            """,
            (company_id,),
        ).fetchall()
    except Exception:
        g._stores_cache = []

    return {"stores": g._stores_cache}
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

    # -----------------------------
    # Existing purchase_logs insert
    # -----------------------------
    db.execute(
        """
        INSERT INTO purchase_logs
          (purchase_id, action, old_data, new_data, changed_by, changed_at)
        VALUES (%s, %s, %s, %s, %s, %s)
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

    # -----------------------------
    # NEW: sys_work_logs (audit)
    # -----------------------------
    try:
        # local import avoids circular imports
        from views.reports.audit_log import log_event

        log_event(
            db,
            action=action,                     # CREATE / UPDATE / DELETE
            module="pur",
            entity_table="purchases",
            entity_id=str(purchase_id),
            message=f"Purchase {action}",
            old_data=old_data,
            new_data=new_data,
            store_id=(
                new_data.get("store_id")
                if isinstance(new_data, dict)
                else None
            ),
            status_code=200,
        )
    except Exception:
        # audit logging must NEVER break business logic
        pass
    
        
def get_lang() -> str:
    lang = session.get("lang")
    if lang in SUPPORTED_LANGS:
        return lang
    return DEFAULT_LANG

@app.post("/set-lang")
def set_lang():
    lang = request.form.get("lang", DEFAULT_LANG)
    if lang not in SUPPORTED_LANGS:
        lang = DEFAULT_LANG

    session["lang"] = lang

    next_url = request.form.get("next") or request.referrer or url_for("index")
    return redirect(next_url)

#----------------------------------------
# log unhandled exceptions (500s) automatically
#----------------------------------------
from werkzeug.exceptions import HTTPException

@app.errorhandler(Exception)
def handle_exception(e):
    # ✅ Let normal HTTP errors (404/403/etc.) pass through
    if isinstance(e, HTTPException):
        return e

    db = get_db()
    try:
        log_event(
            db,
            action="ERROR",
            module="system",
            message=str(e),
            meta={"type": type(e).__name__},
            status_code=500,
        )
        db.commit()
    except Exception:
        pass

    raise e

# ----------------------------------------
# Home
# ----------------------------------------
#@app.route("/")
#def index():
#    if getattr(g, "current_user", None) is None:
#        return redirect(url_for("login"))
#    return render_template("home.html")
@app.route("/")
def index():
    if getattr(g, "current_user", None) is None:
        return redirect(url_for("login"))
    return render_template("home.html")
# ----------------------------------------
# Register views
# ----------------------------------------

# Auth must be registered first (sets app.extensions["admin_required"])
init_auth_login_views(app, get_db)
init_auth_invite_views(app, get_db)

# Admin screens that depend on admin_required
init_admin_invites_views(app, get_db)
init_admin_user_views(app, get_db)

# Existing modules
init_purchase_views(app, get_db, log_purchase_change)
init_report_views(app, get_db)
init_master_views(app, get_db)
init_inventory_views(app, get_db)
init_inventory_views_v2(app, get_db)
init_location_views(app, get_db)

# ----------------------------------------
# Global login requirement (after auth)
# ----------------------------------------
@app.before_request
def require_login_globally():
    # allow auth + public endpoints
    if request.endpoint in ("login", "accept_invite", "set_lang", "static"):
        return None

    # allow favicon
    if request.path == "/favicon.ico":
        return None

    # enforce login
    if getattr(g, "current_user", None) is None:
        return redirect(url_for("login", next=request.full_path))

    return None

# ----------------------------------------
# Run
# ----------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
