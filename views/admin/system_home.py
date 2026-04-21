from __future__ import annotations

from flask import render_template, redirect, url_for, request, flash, g

from utils.sys_roles import SYS_ROLES, sys_role_required
from views.reports.audit_log import log_event


def init_admin_system_home_views(app, get_db):

    @app.get("/admin/system")
    @sys_role_required("engineer", "sales", "accounting")
    def admin_system_home():
        db = get_db()

        # Pull current contract via LATERAL so missing contracts (pre-Phase A
        # companies, or where the migration hasn't run yet) don't break the
        # JOIN. Wrap in try/except for first-load before sys_company_contracts
        # exists.
        try:
            companies = db.execute(
                """
                SELECT
                  c.id,
                  c.code,
                  c.name,
                  c.created_at,
                  COUNT(DISTINCT u.id) AS user_count,
                  COUNT(DISTINCT s.id) AS store_count,
                  cc.tier            AS tier,
                  cc.trial_ends_at   AS trial_ends_at
                FROM mst_companies c
                LEFT JOIN sys_users u
                  ON u.company_id = c.id
                LEFT JOIN mst_stores s
                  ON s.company_id = c.id
                 AND COALESCE(s.is_active, 1) = 1
                LEFT JOIN LATERAL (
                  SELECT tier, trial_ends_at
                  FROM sys_company_contracts
                  WHERE company_id = c.id AND effective_to IS NULL
                  ORDER BY effective_from DESC, id DESC
                  LIMIT 1
                ) cc ON true
                GROUP BY c.id, c.code, c.name, c.created_at, cc.tier, cc.trial_ends_at
                ORDER BY c.id
                """
            ).fetchall()
        except Exception:
            db.connection.rollback()
            companies = db.execute(
                """
                SELECT
                  c.id,
                  c.code,
                  c.name,
                  c.created_at,
                  COUNT(DISTINCT u.id) AS user_count,
                  COUNT(DISTINCT s.id) AS store_count,
                  NULL::text AS tier,
                  NULL::date AS trial_ends_at
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

        # Pull users with sys_role too. Fall back if migration not applied.
        try:
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
                  u.created_at,
                  COALESCE(u.sys_role, 'super_admin') AS sys_role
                FROM sys_users u
                LEFT JOIN mst_companies c
                  ON c.id = u.company_id
                ORDER BY u.company_id, u.id
                """
            ).fetchall()
        except Exception:
            db.connection.rollback()
            users = db.execute(
                """
                SELECT
                  u.id, u.company_id, c.name AS company_name,
                  u.email, u.name, u.role, u.is_system_admin,
                  u.is_active, u.created_at,
                  'super_admin' AS sys_role
                FROM sys_users u
                LEFT JOIN mst_companies c ON c.id = u.company_id
                ORDER BY u.company_id, u.id
                """
            ).fetchall()

        return render_template(
            "admin/system_home.html",
            companies=companies,
            users=users,
            sys_roles=SYS_ROLES,
        )

    # ──────────────────────────────────────────────────────────────────
    # Sys-role assignment — Super Admin only.
    # ──────────────────────────────────────────────────────────────────
    @app.post("/admin/system/users/<int:user_id>/sys-role")
    @sys_role_required()  # empty list = super_admin only
    def admin_system_assign_sys_role(user_id):
        from utils.sys_roles import SYS_ROLES as _ROLES
        new_role = (request.form.get("sys_role") or "").strip().lower()
        if new_role not in _ROLES:
            flash(f"Invalid sys role: {new_role}")
            return redirect(url_for("admin_system_home"))

        db = get_db()
        actor_id = (getattr(g, "current_user", {}) or {}).get("id")
        try:
            row = db.execute(
                "SELECT id, email, sys_role, is_system_admin FROM sys_users WHERE id=%s",
                (user_id,),
            ).fetchone()
            if not row:
                flash("User not found.")
                return redirect(url_for("admin_system_home"))
            if not row["is_system_admin"]:
                flash("User is not a sys admin.")
                return redirect(url_for("admin_system_home"))

            old_role = row["sys_role"]
            db.execute(
                "UPDATE sys_users SET sys_role = %s, updated_at = now() WHERE id = %s",
                (new_role, user_id),
            )
            log_event(
                db,
                action="SYS_ROLE_CHANGE",
                module="sys",
                entity_table="sys_users",
                entity_id=str(user_id),
                status_code=200,
                message=f"sys_role changed for {row['email']}: {old_role} → {new_role}",
                old_data={"sys_role": old_role},
                new_data={"sys_role": new_role, "changed_by_user_id": actor_id},
            )
            db.commit()
            flash(f"Updated {row['email']} → {new_role}")
        except Exception as e:
            db.rollback()
            flash(f"Update failed: {e}")
        return redirect(url_for("admin_system_home"))