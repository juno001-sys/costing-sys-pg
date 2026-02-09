from __future__ import annotations

from flask import render_template, request, redirect, url_for, flash, g
from werkzeug.security import generate_password_hash

from views.reports.audit_log import log_event


def init_admin_system_company_views(app, get_db):
    # MVP gate: system home is already admin-only; reuse the same logic
    # Later you can switch this to sys_users.role == 'system_admin'
    role_required = app.extensions["role_required"]

    @app.route(
        "/admin/system/companies/new",
        methods=["GET", "POST"],
        endpoint="admin_system_company_new",
    )
    @role_required("admin")
    def admin_system_company_new():
        db = get_db()

        if request.method == "GET":
            return render_template("admin/company_new.html")

        company_name = (request.form.get("company_name") or "").strip()
        admin_email = (request.form.get("admin_email") or "").strip().lower()
        admin_name = (request.form.get("admin_name") or "").strip() or "Admin"
        temp_password = (request.form.get("temp_password") or "").strip()

        if not company_name or not admin_email or not temp_password:
            flash("Company name, admin email, temp password are required.")
            return redirect(url_for("admin_system_company_new"))

        # global email uniqueness (UX)
        exists = db.execute(
            "SELECT 1 FROM sys_users WHERE lower(email)=%s",
            (admin_email,),
        ).fetchone()
        if exists:
            flash("That email already exists.")
            return redirect(url_for("admin_system_company_new"))

        pw_hash = generate_password_hash(temp_password, method="pbkdf2:sha256")

        try:
            # create company
            crow = db.execute(
                "INSERT INTO mst_companies (name, created_at) VALUES (%s, now()) RETURNING id",
                (company_name,),
            ).fetchone()
            company_id = crow["id"]

            # create first user
            urow = db.execute(
                """
                INSERT INTO sys_users (company_id, email, name, password_hash, role, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, 'admin', 1, now(), now())
                RETURNING id
                """,
                (company_id, admin_email, admin_name, pw_hash),
            ).fetchone()
            user_id = urow["id"]

            # membership
            db.execute(
                """
                INSERT INTO sys_user_companies (user_id, company_id, role, is_active)
                VALUES (%s, %s, 'admin', 1)
                """,
                (user_id, company_id),
            )

            log_event(
                db,
                action="CREATE",
                module="sys",
                entity_table="mst_companies",
                entity_id=str(company_id),
                message="Company created + first admin",
                company_id=company_id,
                status_code=200,
                meta={"admin_email": admin_email, "admin_user_id": user_id},
            )

            db.commit()
            flash("Company + first admin created.")
            return redirect(url_for("admin_system_company_new"))

        except Exception as e:
            db.rollback()
            flash(f"Failed: {e}")
            return redirect(url_for("admin_system_company_new"))