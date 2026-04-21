from __future__ import annotations

from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, g

from utils.access_scope import (
    NAV_KEYS,
    NAV_DEFAULT_VISIBILITY,
    is_chief_admin,
)
from views.reports.audit_log import log_event


ROLES = ("operator", "auditor")


def init_admin_nav_policy_views(app, get_db):
    admin_required = app.extensions["admin_required"]

    @app.route("/admin/nav-policy", methods=["GET", "POST"])
    @admin_required
    def admin_nav_policy():
        db = get_db()
        company_id = getattr(g, "current_company_id", None)
        if not company_id:
            flash("Company context is missing.")
            return redirect(url_for("index"))

        # Chief Admin only — matches the existing pattern for company-wide
        # configuration screens (grants management, billing, etc.).
        if not is_chief_admin():
            flash("この設定はチーフ管理者のみが変更できます。")
            return redirect(url_for("admin_users"))

        if request.method == "POST":
            actor_id = (getattr(g, "current_user", {}) or {}).get("id")
            now = datetime.now().isoformat(timespec="seconds")

            # Rebuild the full policy from form state. A checkbox absent
            # from the POST body means "unchecked" → visible=FALSE.
            for role in ROLES:
                for nav_key in NAV_KEYS:
                    field = f"policy__{role}__{nav_key}"
                    visible = request.form.get(field) == "1"
                    db.execute(
                        """
                        INSERT INTO sys_company_nav_policies
                            (company_id, role, nav_key, visible,
                             updated_at, updated_by_user_id)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (company_id, role, nav_key) DO UPDATE SET
                            visible = EXCLUDED.visible,
                            updated_at = EXCLUDED.updated_at,
                            updated_by_user_id = EXCLUDED.updated_by_user_id
                        """,
                        (company_id, role, nav_key, visible, now, actor_id),
                    )

            db.commit()
            log_event(
                db,
                action="NAV_POLICY_UPDATE",
                module="admin",
                entity_table="sys_company_nav_policies",
                entity_id=str(company_id),
                message="Per-company nav visibility policy updated",
                company_id=company_id,
            )
            flash("ナビ表示設定を更新しました。")
            return redirect(url_for("admin_nav_policy"))

        # GET: load current policy rows, overlay onto defaults.
        rows = db.execute(
            """
            SELECT role, nav_key, visible
            FROM sys_company_nav_policies
            WHERE company_id = %s
            """,
            (company_id,),
        ).fetchall()
        policy_by_role = {role: dict(NAV_DEFAULT_VISIBILITY[role]) for role in ROLES}
        for r in rows:
            role = r["role"]
            if role in policy_by_role and r["nav_key"] in policy_by_role[role]:
                policy_by_role[role][r["nav_key"]] = bool(r["visible"])

        return render_template(
            "admin/nav_policy.html",
            nav_keys=NAV_KEYS,
            roles=ROLES,
            policy_by_role=policy_by_role,
        )
