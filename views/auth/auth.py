from __future__ import annotations

from functools import wraps
from flask import render_template, request, redirect, url_for, flash, session, g

from werkzeug.security import check_password_hash


def init_auth_views(app, get_db):
    # -------------------------
    # Helpers
    # -------------------------
    def load_current_user():
        user_id = session.get("user_id")
        if not user_id:
            g.current_user = None
            return None

        db = get_db()
        u = db.execute(
            """
            SELECT id, company_id, email, name, role, is_active
            FROM sys_users
            WHERE id = %s
            """,
            (user_id,),
        ).fetchone()

        if not u or (u.get("is_active") == 0):
            session.pop("user_id", None)
            g.current_user = None
            return None

        g.current_user = u
        return u

    @app.before_request
    def _inject_current_user():
        # Make user available for every request
        load_current_user()

    @app.context_processor
    def inject_current_user():
        return {"current_user": getattr(g, "current_user", None)}

    
    def admin_required(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            u = getattr(g, "current_user", None)
            if u is None:
                return redirect(url_for("login", next=request.full_path))
            if u.get("role") != "admin":
                flash("管理者権限が必要です。")
                return redirect(url_for("index"))
            return fn(*args, **kwargs)
        return wrapper

    # Expose decorators so other modules can import via app.extensions
    app.extensions["login_required"] = login_required
    app.extensions["admin_required"] = admin_required

    # -------------------------
    # Routes
    # -------------------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template("auth/login.html", next=request.args.get("next") or "")

        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        next_url = request.form.get("next") or url_for("index")

        if not email or not password:
            flash("Emailとパスワードを入力してください。")
            return redirect(url_for("login", next=next_url))

        db = get_db()
        # MVP: search user by email (across companies) if you only have 1 company now.
        # Later: add company selection or email domain mapping.
        u = db.execute(
            """
            SELECT id, company_id, email, name, role, is_active, password_hash
            FROM sys_users
            WHERE lower(email) = %s
            ORDER BY id
            LIMIT 1
            """,
            (email,),
        ).fetchone()

        if not u or u.get("is_active") == 0 or not check_password_hash(u["password_hash"], password):
            flash("ログインに失敗しました。")
            return redirect(url_for("login", next=next_url))

        session["user_id"] = u["id"]
        flash("ログインしました。")
        return redirect(next_url)

    @app.post("/logout")
    def logout():
        session.pop("user_id", None)
        flash("ログアウトしました。")
        return redirect(url_for("login"))