from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import render_template, request, redirect, url_for, flash, session, g
from werkzeug.security import check_password_hash


MAX_SESSION_DAYS = 30
IDLE_DAYS = 7
MAX_SESSIONS_PER_USER = 5


def init_auth_login_views(app, get_db):
    """
    Provides:
      - before_request: load session -> g.current_user, g.current_company_id, g.current_role
      - decorators: login_required, admin_required, role_required
      - routes: /login, /logout
    """

    # -------------------------
    # decorators
    # -------------------------
    def login_required(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if getattr(g, "current_user", None) is None:
                return redirect(url_for("login", next=request.full_path))
            return fn(*args, **kwargs)
        return wrapper

    def admin_required(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if getattr(g, "current_user", None) is None:
                return redirect(url_for("login", next=request.full_path))
            if getattr(g, "current_role", None) != "admin":
                flash("管理者権限が必要です。")
                return redirect(url_for("index"))
            return fn(*args, **kwargs)
        return wrapper

    def role_required(*allowed_roles):
        def decorator(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                if getattr(g, "current_user", None) is None:
                    return redirect(url_for("login", next=request.full_path))
                if getattr(g, "current_role", None) not in allowed_roles:
                    flash("権限がありません。")
                    return redirect(url_for("index"))
                return fn(*args, **kwargs)
            return wrapper
        return decorator

    # expose decorators
    app.extensions["login_required"] = login_required
    app.extensions["admin_required"] = admin_required
    app.extensions["role_required"] = role_required

    # -------------------------
    # session loader
    # -------------------------
    def _load_session_from_db():
        token = session.get("session_token")
        if not token:
            g.current_user = None
            g.current_company_id = None
            g.current_role = None
            return

        db = get_db()
        row = db.execute(
            """
            SELECT
              s.id AS session_id,
              s.user_id,
              s.company_id,
              s.expires_at,
              s.last_seen_at,
              s.is_active,
              u.email,
              u.name,
              u.is_active AS user_active,
              uc.role,
              uc.is_active AS membership_active
            FROM sys_sessions s
            JOIN sys_users u ON u.id = s.user_id
            JOIN sys_user_companies uc
              ON uc.user_id = s.user_id AND uc.company_id = s.company_id
            WHERE s.id = %s
            """,
            (token,),
        ).fetchone()

        if not row:
            session.pop("session_token", None)
            g.current_user = None
            g.current_company_id = None
            g.current_role = None
            return

        now = datetime.now(timezone.utc)

        # hard checks
        if row["is_active"] == 0 or row["user_active"] == 0 or row["membership_active"] == 0:
            session.pop("session_token", None)
            g.current_user = None
            g.current_company_id = None
            g.current_role = None
            return

        # expiry checks
        if row["expires_at"] < now:
            session.pop("session_token", None)
            try:
                db.execute("UPDATE sys_sessions SET is_active=0 WHERE id=%s", (token,))
                db.commit()
            except Exception:
                pass
            g.current_user = None
            g.current_company_id = None
            g.current_role = None
            return

        # idle timeout
        if row["last_seen_at"] < (now - timedelta(days=IDLE_DAYS)):
            session.pop("session_token", None)
            try:
                db.execute("UPDATE sys_sessions SET is_active=0 WHERE id=%s", (token,))
                db.commit()
            except Exception:
                pass
            g.current_user = None
            g.current_company_id = None
            g.current_role = None
            return

        # mark current
        g.current_user = {
            "id": row["user_id"],
            "email": row["email"],
            "name": row["name"],
        }
        g.current_company_id = row["company_id"]
        g.current_role = row["role"]
        g.is_system_admin = session.get("is_system_admin", False)

        # refresh last_seen (cheap)
        try:
            db.execute(
                "UPDATE sys_sessions SET last_seen_at=now() WHERE id=%s",
                (token,),
            )
            db.commit()
        except Exception:
            pass

    @app.before_request
    def _inject_current_user():
        _load_session_from_db()

    @app.context_processor
    def inject_user_context():
        return {
            "current_user": getattr(g, "current_user", None),
            "current_company_id": getattr(g, "current_company_id", None),
            "current_role": getattr(g, "current_role", None),
            "is_system_admin": getattr(g, "is_system_admin", False),
        }

    # -------------------------
    # session creation
    # -------------------------
    def _create_session(db, user_id: int, company_id: int):
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=MAX_SESSION_DAYS)

        db.execute(
            """
            INSERT INTO sys_sessions
              (id, user_id, company_id, expires_at, last_seen_at, user_agent, ip, is_active)
            VALUES
              (%s, %s, %s, %s, now(), %s, %s, 1)
            """,
            (
                token,
                user_id,
                company_id,
                expires_at,
                request.headers.get("User-Agent"),
                request.headers.get("X-Forwarded-For", request.remote_addr),
            ),
        )

        # deactivate older sessions beyond limit
        db.execute(
            """
            UPDATE sys_sessions
            SET is_active = 0
            WHERE id IN (
              SELECT id
              FROM sys_sessions
              WHERE user_id = %s AND is_active = 1
              ORDER BY last_seen_at DESC
              OFFSET %s
            )
            """,
            (user_id, MAX_SESSIONS_PER_USER),
        )

        db.commit()
        session["session_token"] = token

    # -------------------------
    # routes
    # -------------------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template("auth/login.html", next=request.args.get("next") or "")

        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        next_url = request.form.get("next") or url_for("index")
        is_sys_admin_login = request.form.get("system_admin") == "1"

        if not email:
            flash("Emailを入力してください。")
            return redirect(url_for("login", next=next_url))

        db = get_db()

        u = db.execute(
            """
            SELECT id, email, name, password_hash, is_active, is_system_admin
            FROM sys_users
            WHERE lower(email)=%s
            """,
            (email,),
        ).fetchone()

        print("DEBUG USER:", u)

        if not u or u["is_active"] == 0:
            flash("ログインに失敗しました。")
            return redirect(url_for("login", next=next_url))

#        if u["password_hash"]:
#            if not password or not check_password_hash(u["password_hash"], password):
#                flash("ログインに失敗しました。")
#                return redirect(url_for("login", next=next_url))
#
#            # Determine which login form was used
#            is_sys_admin_login = request.form.get("system_admin") == "1"
#
#            # System admin login form
#            if is_sys_admin_login and not u["is_system_admin"]:
#                flash("System admin login only.")
#                return redirect(url_for("login", next=next_url))
#
#            # Normal company login form
#            if not is_sys_admin_login and u["is_system_admin"]:
#                flash("Please use the system admin login.")
#                return redirect(url_for("login", next=next_url))
        if u["password_hash"]:
            password_ok = bool(password) and check_password_hash(u["password_hash"], password)
            print("DEBUG password provided:", bool(password))
            print("DEBUG password_ok:", password_ok)

            if not password_ok:
                flash("ログインに失敗しました。")
                return redirect(url_for("login", next=next_url))

            # Determine which login form was used
            is_sys_admin_login = request.form.get("system_admin") == "1"
            print("DEBUG is_sys_admin_login(after password):", is_sys_admin_login)
            print("DEBUG user.is_system_admin:", u["is_system_admin"])

            # System admin login form
            if is_sys_admin_login and not u["is_system_admin"]:
                print("DEBUG rejected: normal user tried system admin login")
                flash("System admin login only.")
                return redirect(url_for("login", next=next_url))

            # Normal company login form
            if not is_sys_admin_login and u["is_system_admin"]:
                print("DEBUG rejected: system admin used normal login")
                flash("Please use the system admin login.")
                return redirect(url_for("login", next=next_url))


        else:
            flash("パスワード未設定です。招待リンクから有効化してください。")
            return redirect(url_for("login", next=next_url))



        mem = db.execute(
            """
            SELECT company_id, role
            FROM sys_user_companies
            WHERE user_id = %s AND is_active = 1
            ORDER BY company_id
            LIMIT 1
            """,
            (u["id"],),
        ).fetchone()

        if not mem:
            flash("所属会社がありません。管理者に招待を依頼してください。")
            return redirect(url_for("login", next=next_url))

        _create_session(db, u["id"], mem["company_id"])

        session["is_system_admin"] = bool(u["is_system_admin"])
        session.permanent = True   # 30-day persistent session (see app.py config)

        flash("ログインしました。")
        return redirect(next_url)

        if is_sys_admin_login and u["is_system_admin"]:
            return redirect("/admin/system") 

    @app.post("/logout")
    def logout():
        token = session.pop("session_token", None)
        if token:
            db = get_db()
            try:
                db.execute("UPDATE sys_sessions SET is_active=0 WHERE id=%s", (token,))
                db.commit()
            except Exception:
                pass
        return redirect(url_for("login"))