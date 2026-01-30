from __future__ import annotations

from flask import render_template, request, redirect, url_for, flash, g
from werkzeug.security import generate_password_hash


def init_admin_user_views(app, get_db):
    admin_required = app.extensions["admin_required"]

    @app.route("/admin/users", methods=["GET", "POST"])
    @admin_required
    def admin_users():
        db = get_db()
        current = g.current_user
        company_id = current["company_id"]

        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            name = (request.form.get("name") or "").strip()
            role = (request.form.get("role") or "operator").strip()
            password = request.form.get("password") or ""

            if not email or not password:
                flash("Emailと初期パスワードは必須です。")
                return redirect(url_for("admin_users"))

            if role not in ("admin", "operator", "auditor"):
                role = "operator"

            try:
                db.execute(
                    """
                    INSERT INTO sys_users (company_id, email, name, password_hash, role)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (company_id, email, name or None, generate_password_hash(password), role),
                )
                db.commit()
                flash("ユーザーを作成しました。")
            except Exception as e:
                db.rollback()
                flash(f"ユーザー作成に失敗しました: {e}")

            return redirect(url_for("admin_users"))

        users = db.execute(
            """
            SELECT id, email, name, role, is_active, created_at
            FROM sys_users
            WHERE company_id = %s
            ORDER BY id DESC
            """,
            (company_id,),
        ).fetchall()

        return render_template("admin/users.html", users=users)

    @app.post("/admin/users/<int:user_id>/disable")
    @admin_required
    def disable_user(user_id):
        db = get_db()
        company_id = g.current_user["company_id"]

        db.execute(
            """
            UPDATE sys_users
            SET is_active = 0, updated_at = now()
            WHERE id = %s AND company_id = %s
            """,
            (user_id, company_id),
        )
        db.commit()
        flash("ユーザーを無効化しました。")
        return redirect(url_for("admin_users"))