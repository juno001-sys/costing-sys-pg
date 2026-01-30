from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from flask import render_template, request, redirect, url_for, flash, g


def init_admin_invites_views(app, get_db):
    admin_required = app.extensions["admin_required"]

    @app.route("/admin/invites", methods=["GET", "POST"])
    @admin_required
    def admin_invites():
        db = get_db()
        company_id = g.current_company_id

        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            role = (request.form.get("role") or "operator").strip()
            days = int(request.form.get("expires_days") or "7")

            if not email:
                flash("Email is required.")
                return redirect(url_for("admin_invites"))

            if role not in ("admin", "operator", "auditor"):
                role = "operator"

            token = secrets.token_urlsafe(24)
            expires_at = datetime.now(timezone.utc) + timedelta(days=days)

            db.execute(
                """
                INSERT INTO sys_company_invites
                  (company_id, email, role, token, invited_by_user_id, expires_at)
                VALUES
                  (%s, %s, %s, %s, %s, %s)
                """,
                (company_id, email, role, token, g.current_user["id"], expires_at),
            )
            db.commit()

            flash("Invite created. Copy the text and send it.")
            return redirect(url_for("admin_invites"))

        invites = db.execute(
            """
            SELECT id, email, role, token, expires_at, used_at, created_at
            FROM sys_company_invites
            WHERE company_id = %s
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (company_id,),
        ).fetchall()

        base_url = request.host_url.rstrip("/")
        return render_template("admin/invites.html", invites=invites, base_url=base_url)