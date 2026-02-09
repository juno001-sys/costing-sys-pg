from __future__ import annotations

from flask import render_template, request, redirect, url_for, flash, g
from werkzeug.security import generate_password_hash


def init_admin_user_views(app, get_db):
    admin_required = app.extensions["admin_required"]

    @app.route("/admin/users", methods=["GET", "POST"])
    @admin_required
    def admin_users():
        db = get_db()
        company_id = getattr(g, "current_company_id", None)
        if not company_id:
            flash("Company context is missing.")
            return redirect(url_for("index"))

        # -------------------------
        # POST: create user
        # -------------------------
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            name = (request.form.get("name") or "").strip()
            role = (request.form.get("role") or "operator").strip()
            password = request.form.get("password") or ""

            if not email or not password:
                flash("Emailと初期パスワードは必須です。")
                return redirect(url_for("admin_users"))

            # ✅ ADD THIS BLOCK HERE
            exists = db.execute(
                "SELECT 1 FROM sys_users WHERE lower(email) = %s",
                (email,),
            ).fetchone()

            if exists:
                flash("そのメールアドレスは既に登録されています。")
                return redirect(url_for("admin_users"))
            # ✅ END ADDITION

            if role not in ("admin", "operator", "auditor"):
                role = "operator"

            pw_hash = generate_password_hash(password, method="pbkdf2:sha256")

            try:
                # 1) create sys_users
                urow = db.execute(
                    """
                    INSERT INTO sys_users
                    (company_id, email, name, password_hash, role, is_active, created_at, updated_at)
                    VALUES
                    (%s, %s, %s, %s, %s, 1, now(), now())
                    RETURNING id
                    """,
                    (company_id, email, name or None, pw_hash, role),
                ).fetchone()
                new_user_id = urow["id"]

                # 2) create membership
                db.execute(
                    """
                    INSERT INTO sys_user_companies (user_id, company_id, role, is_active)
                    VALUES (%s, %s, %s, 1)
                    """,
                    (new_user_id, company_id, role),
                )

                db.commit()
                flash("ユーザーを作成しました。")
            except Exception as e:
                db.rollback()
                flash(f"ユーザー作成に失敗しました: {e}")

            return redirect(url_for("admin_users"))

        # -------------------------
        # GET: list users
        # -------------------------
        users = db.execute(
            """
            SELECT u.id, u.email, u.name, uc.role, u.is_active, u.created_at
            FROM sys_user_companies uc
            JOIN sys_users u ON u.id = uc.user_id
            WHERE uc.company_id = %s AND uc.is_active = 1
            ORDER BY u.id DESC
            """,
            (company_id,),
        ).fetchall()

        return render_template("admin/users.html", users=users)

    @app.post("/admin/users/<int:user_id>/disable")
    @admin_required
    def disable_user(user_id):
        db = get_db()
        company_id = getattr(g, "current_company_id", None)
        if not company_id:
            flash("Company context is missing.")
            return redirect(url_for("index"))

        # disable membership for this company
        db.execute(
            """
            UPDATE sys_user_companies
            SET is_active = 0
            WHERE user_id = %s AND company_id = %s
            """,
            (user_id, company_id),
        )
        db.commit()
        flash("ユーザーを無効化しました。")
        return redirect(url_for("admin_users"))