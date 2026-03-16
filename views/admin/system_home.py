from __future__ import annotations

from flask import render_template, redirect, url_for, request, flash, g
from functools import wraps


def init_admin_system_home_views(app, get_db):

    def system_admin_required(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):

            if getattr(g, "current_user", None) is None:
                return redirect(url_for("login", next=request.full_path))

            if not getattr(g, "is_system_admin", False):
                flash("System admin only.")
                return redirect(url_for("index"))

            return fn(*args, **kwargs)

        return wrapper


    @app.get("/admin/system")
    @system_admin_required
    def admin_system_home():
        db = get_db()

        companies = db.execute(
            """
            SELECT
              c.id,
              c.code,
              c.name,
              c.created_at,
              COUNT(DISTINCT u.id) AS user_count,
              COUNT(DISTINCT s.id) AS store_count
            FROM mst_companies c
            LEFT JOIN sys_users u
              ON u.company_id = c.id
            LEFT JOIN mst_stores s
              ON s.company_id = c.id
             AND COALESCE(s.is_active, 1) = 1
            GROUP BY c.id, c.code, c.name, c.created_at
            ORDER BY c.id
            """
        ).fetchall()

        users = db.execute(
            """
            SELECT
              u.id,
              u.company_id,
              c.name AS company_name,
              u.email,
              u.name,
              u.role,
              u.is_system_admin,
              u.is_active,
              u.created_at
            FROM sys_users u
            LEFT JOIN mst_companies c
              ON c.id = u.company_id
            ORDER BY u.company_id, u.id
            """
        ).fetchall()

        return render_template(
            "admin/system_home.html",
            companies=companies,
            users=users,
        )