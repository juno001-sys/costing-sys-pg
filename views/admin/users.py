from __future__ import annotations

from flask import render_template, request, redirect, url_for, flash, g
from werkzeug.security import generate_password_hash

from utils.access_scope import (
    get_accessible_stores,
    get_user_store_grants,
    is_chief_admin,
)
from views.reports.audit_log import log_event


def init_admin_user_views(app, get_db):
    admin_required = app.extensions["admin_required"]

    def _safe_query(db, sql, params=()):
        try:
            return db.execute(sql, params).fetchall()
        except Exception:
            try:
                db.connection.rollback()
            except Exception:
                pass
            return []

    @app.route("/admin/users", methods=["GET", "POST"])
    @admin_required
    def admin_users():
        db = get_db()
        company_id = getattr(g, "current_company_id", None)
        if not company_id:
            flash("Company context is missing.")
            return redirect(url_for("index"))

        actor_is_chief = is_chief_admin()

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

            exists = db.execute(
                "SELECT 1 FROM sys_users WHERE lower(email) = %s",
                (email,),
            ).fetchone()

            if exists:
                flash("そのメールアドレスは既に登録されています。")
                return redirect(url_for("admin_users"))

            if role not in ("admin", "operator", "auditor"):
                role = "operator"

            # Per Google-style design: only Chief Admin can create another Admin.
            if role == "admin" and not actor_is_chief:
                flash("Adminを新規作成できるのはChief Adminのみです。")
                return redirect(url_for("admin_users"))

            pw_hash = generate_password_hash(password, method="pbkdf2:sha256")

            try:
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

                db.execute(
                    """
                    INSERT INTO sys_user_companies (user_id, company_id, role, is_active)
                    VALUES (%s, %s, %s, 1)
                    """,
                    (new_user_id, company_id, role),
                )

                log_event(
                    db,
                    action="USER_CREATE",
                    module="admin",
                    entity_table="sys_users",
                    entity_id=str(new_user_id),
                    company_id=company_id,
                    status_code=200,
                    message=f"User {email} created with role={role}",
                    new_data={"email": email, "role": role},
                )

                db.commit()
                flash("ユーザーを作成しました。")
            except Exception as e:
                db.rollback()
                flash(f"ユーザー作成に失敗しました: {e}")

            return redirect(url_for("admin_users"))

        # -------------------------
        # GET: list users (with chief-admin flag if available)
        # -------------------------
        try:
            users = db.execute(
                """
                SELECT u.id, u.email, u.name, uc.role, u.is_active, u.created_at,
                       uc.is_chief_admin
                FROM sys_user_companies uc
                JOIN sys_users u ON u.id = uc.user_id
                WHERE uc.company_id = %s AND uc.is_active = 1
                ORDER BY uc.is_chief_admin DESC, u.id DESC
                """,
                (company_id,),
            ).fetchall()
        except Exception:
            db.connection.rollback()
            users = db.execute(
                """
                SELECT u.id, u.email, u.name, uc.role, u.is_active, u.created_at,
                       FALSE AS is_chief_admin
                FROM sys_user_companies uc
                JOIN sys_users u ON u.id = uc.user_id
                WHERE uc.company_id = %s AND uc.is_active = 1
                ORDER BY u.id DESC
                """,
                (company_id,),
            ).fetchall()

        return render_template(
            "admin/users.html",
            users=users,
            actor_is_chief=actor_is_chief,
        )

    @app.post("/admin/users/<int:user_id>/disable")
    @admin_required
    def disable_user(user_id):
        db = get_db()
        company_id = getattr(g, "current_company_id", None)
        if not company_id:
            flash("Company context is missing.")
            return redirect(url_for("index"))

        # Prevent disabling the Chief Admin
        chief = _safe_query(db, """
            SELECT 1 FROM sys_user_companies
            WHERE user_id = %s AND company_id = %s
              AND is_chief_admin = TRUE AND is_active = 1
        """, (user_id, company_id))
        if chief:
            flash("Chief Adminは無効化できません。先に他のAdminへ移譲してください。")
            return redirect(url_for("admin_users"))

        db.execute(
            """
            UPDATE sys_user_companies
            SET is_active = 0
            WHERE user_id = %s AND company_id = %s
            """,
            (user_id, company_id),
        )
        log_event(
            db,
            action="USER_DISABLE",
            module="admin",
            entity_table="sys_user_companies",
            entity_id=str(user_id),
            company_id=company_id,
            status_code=200,
            message=f"User {user_id} disabled in company {company_id}",
        )
        db.commit()
        flash("ユーザーを無効化しました。")
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/<int:user_id>/transfer-chief")
    @admin_required
    def transfer_chief_admin(user_id):
        """Transfer Chief Admin to another Admin in the same company.
        Only the current Chief Admin can perform this transfer.
        """
        db = get_db()
        company_id = getattr(g, "current_company_id", None)
        actor_id = (getattr(g, "current_user", {}) or {}).get("id")

        if not company_id or not is_chief_admin(actor_id, company_id):
            flash("Chief Adminのみが移譲できます。")
            return redirect(url_for("admin_users"))

        # Target must already be an admin in this company
        target = _safe_query(db, """
            SELECT id, role FROM sys_user_companies
            WHERE user_id = %s AND company_id = %s AND is_active = 1
        """, (user_id, company_id))
        if not target or target[0]["role"] != "admin":
            flash("移譲先はAdminである必要があります。")
            return redirect(url_for("admin_users"))

        try:
            # Demote current chief, promote target. Partial unique index
            # uq_sys_user_companies__chief_per_company enforces single chief.
            db.execute("""
                UPDATE sys_user_companies SET is_chief_admin = FALSE
                WHERE company_id = %s AND is_chief_admin = TRUE
            """, (company_id,))
            db.execute("""
                UPDATE sys_user_companies SET is_chief_admin = TRUE
                WHERE user_id = %s AND company_id = %s
            """, (user_id, company_id))
            log_event(
                db,
                action="CHIEF_ADMIN_TRANSFER",
                module="admin",
                entity_table="sys_user_companies",
                entity_id=str(user_id),
                company_id=company_id,
                status_code=200,
                message=f"Chief Admin transferred to user_id={user_id}",
                new_data={"new_chief_user_id": user_id},
                old_data={"old_chief_user_id": actor_id},
            )
            db.commit()
            flash("Chief Adminを移譲しました。")
        except Exception as e:
            db.rollback()
            flash(f"移譲に失敗しました: {e}")

        return redirect(url_for("admin_users"))

    @app.route("/admin/users/<int:user_id>/store-grants", methods=["GET", "POST"])
    @admin_required
    def admin_user_store_grants(user_id):
        """Per-store role grant editor for one user.
        Allows the company Admin to elevate a user's role on specific stores.
        """
        db = get_db()
        company_id = getattr(g, "current_company_id", None)
        actor_id = (getattr(g, "current_user", {}) or {}).get("id")
        if not company_id:
            flash("Company context is missing.")
            return redirect(url_for("index"))

        # Verify user is in this company
        target = _safe_query(db, """
            SELECT u.id, u.email, u.name, uc.role
            FROM sys_user_companies uc
            JOIN sys_users u ON u.id = uc.user_id
            WHERE uc.user_id = %s AND uc.company_id = %s AND uc.is_active = 1
        """, (user_id, company_id))
        if not target:
            flash("ユーザーが見つかりません。")
            return redirect(url_for("admin_users"))
        target_user = target[0]

        if request.method == "POST":
            # Submitted: store_role_<store_id> = '' | 'operator' | 'admin'
            stores = get_accessible_stores()
            try:
                for s in stores:
                    sid = s["id"]
                    new_role = (request.form.get(f"store_role_{sid}") or "").strip()
                    if new_role not in ("", "operator", "admin"):
                        continue

                    if new_role == "":
                        # Revoke any active grant
                        db.execute("""
                            UPDATE sys_user_store_grants
                            SET is_active = 0, revoked_at = now()
                            WHERE user_id = %s AND company_id = %s AND store_id = %s
                              AND is_active = 1
                        """, (user_id, company_id, sid))
                    else:
                        # Upsert (active grant for this store)
                        existing = _safe_query(db, """
                            SELECT id, store_role FROM sys_user_store_grants
                            WHERE user_id = %s AND company_id = %s AND store_id = %s
                        """, (user_id, company_id, sid))
                        if existing:
                            db.execute("""
                                UPDATE sys_user_store_grants
                                SET store_role = %s, is_active = 1, revoked_at = NULL,
                                    granted_by_user_id = %s, granted_at = now()
                                WHERE id = %s
                            """, (new_role, actor_id, existing[0]["id"]))
                        else:
                            db.execute("""
                                INSERT INTO sys_user_store_grants
                                  (user_id, company_id, store_id, store_role,
                                   is_active, granted_by_user_id)
                                VALUES (%s, %s, %s, %s, 1, %s)
                            """, (user_id, company_id, sid, new_role, actor_id))
                log_event(
                    db,
                    action="STORE_GRANT_UPDATE",
                    module="admin",
                    entity_table="sys_user_store_grants",
                    entity_id=str(user_id),
                    company_id=company_id,
                    status_code=200,
                    message=f"Store grants updated for user_id={user_id}",
                )
                db.commit()
                flash("店舗権限を保存しました。")
            except Exception as e:
                db.rollback()
                flash(f"保存に失敗しました: {e}")
            return redirect(url_for("admin_user_store_grants", user_id=user_id))

        # GET: render
        stores = get_accessible_stores()
        grants = get_user_store_grants(user_id, company_id)
        return render_template(
            "admin/user_store_grants.html",
            target_user=target_user,
            stores=stores,
            grants=grants,
        )
