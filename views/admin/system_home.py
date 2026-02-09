from __future__ import annotations

from flask import render_template, redirect, url_for, request, flash, g
from functools import wraps


def init_admin_system_home_views(app, get_db):
    def system_admin_required(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if getattr(g, "current_user", None) is None:
                return redirect(url_for("login", next=request.full_path))
            # MVP: treat company-admin as "system admin" until you add sys_users.role
            if getattr(g, "current_role", None) != "admin":
                flash("System admin only.")
                return redirect(url_for("index"))
            return fn(*args, **kwargs)
        return wrapper

    @app.get("/admin/system")
    @system_admin_required
    def admin_system_home():
        return render_template("admin/system_home.html")