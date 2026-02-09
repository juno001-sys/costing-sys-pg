from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from flask import render_template, request, redirect, url_for, flash, session, g
from werkzeug.security import generate_password_hash


MAX_SESSION_DAYS = 30
MAX_SESSIONS_PER_USER = 5


def init_auth_invite_views(app, get_db):
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

    @app.route("/invite/<token>", methods=["GET", "POST"])
    def accept_invite(token):
        db = get_db()

        inv = db.execute(
            """
            SELECT id, company_id, email, role, expires_at, used_at
            FROM sys_company_invites
            WHERE token = %s
            """,
            (token,),
        ).fetchone()

        if not inv:
            flash("Invalid invite link.")
            return redirect(url_for("login"))

        now = datetime.now(timezone.utc)
        if inv["used_at"] is not None:
            flash("This invite has already been used.")
            return redirect(url_for("login"))

        if inv["expires_at"] < now:
            flash("This invite has expired.")
            return redirect(url_for("login"))

        if request.method == "GET":
            return render_template("auth/accept_invite.html", email=inv["email"], role=inv["role"])

        name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""

        if not name:
            flash("Name is required.")
            return redirect(request.path)

        # upsert user by email
        u = db.execute(
            "SELECT id, is_active FROM sys_users WHERE email = %s",
            (inv["email"],),
        ).fetchone()

        if u and u["is_active"] == 0:
            flash("This user is disabled. Contact admin.")
            return redirect(url_for("login"))

        if not u:
            db.execute(
                """
                INSERT INTO sys_users (email, name, password_hash)
                VALUES (%s, %s, %s)
                """,
                (inv["email"], name, generate_password_hash(password) if password else None),
            )
            u = db.execute("SELECT id FROM sys_users WHERE email=%s", (inv["email"],)).fetchone()
        else:
            db.execute("UPDATE sys_users SET name=%s, updated_at=now() WHERE id=%s", (name, u["id"]))
            if password:
                db.execute(
                    "UPDATE sys_users SET password_hash=%s, updated_at=now() WHERE id=%s",
                    (generate_password_hash(password), u["id"]),
                )

        user_id = u["id"]

        # membership upsert
        db.execute(
            """
            INSERT INTO sys_user_companies (user_id, company_id, role, is_active)
            VALUES (%s, %s, %s, 1)
            ON CONFLICT (user_id, company_id)
            DO UPDATE SET role=EXCLUDED.role, is_active=1
            """,
            (user_id, inv["company_id"], inv["role"]),
        )

        # mark invite used
        db.execute("UPDATE sys_company_invites SET used_at=now() WHERE id=%s", (inv["id"],))
        db.commit()

        # login (session)
        _create_session(db, user_id, inv["company_id"])

        # set g (optional; next request will load)
        g.current_user = {"id": user_id, "email": inv["email"], "name": name}
        g.current_company_id = inv["company_id"]
        g.current_role = inv["role"]

        flash("Welcome! Your account is ready.")
        return redirect(url_for("index"))