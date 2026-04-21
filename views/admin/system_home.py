from __future__ import annotations

from flask import render_template, redirect, url_for, request, flash, g
from werkzeug.security import generate_password_hash

from utils.sys_roles import SYS_ROLES, _normalize_roles, sys_role_required
from views.reports.audit_log import log_event


def init_admin_system_home_views(app, get_db):

    @app.get("/admin/system")
    @sys_role_required("engineer", "sales", "accounting")
    def admin_system_home():
        db = get_db()

        # Pull current contract via LATERAL so missing contracts (pre-Phase A
        # companies, or where the migration hasn't run yet) don't break the
        # JOIN. Wrap in try/except for first-load before sys_company_contracts
        # exists. Also pulls is_internal (falls back to FALSE if pre-migration).
        try:
            companies = db.execute(
                """
                SELECT
                  c.id,
                  c.code,
                  c.name,
                  c.created_at,
                  COALESCE(c.is_internal, FALSE) AS is_internal,
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
                GROUP BY c.id, c.code, c.name, c.created_at, c.is_internal,
                         cc.tier, cc.trial_ends_at
                ORDER BY c.is_internal, c.id
                """
            ).fetchall()
        except Exception:
            db.connection.rollback()
            companies = db.execute(
                """
                SELECT
                  c.id, c.code, c.name, c.created_at,
                  FALSE AS is_internal,
                  COUNT(DISTINCT u.id) AS user_count,
                  COUNT(DISTINCT s.id) AS store_count,
                  NULL::text AS tier, NULL::date AS trial_ends_at
                FROM mst_companies c
                LEFT JOIN sys_users u ON u.company_id = c.id
                LEFT JOIN mst_stores s
                  ON s.company_id = c.id AND COALESCE(s.is_active, 1) = 1
                GROUP BY c.id, c.code, c.name, c.created_at
                ORDER BY c.id
                """
            ).fetchall()

        # Split companies: client (paying/trial) vs internal (Kurajika house)
        client_companies = [c for c in companies if not c["is_internal"]]
        internal_companies = [c for c in companies if c["is_internal"]]

        # Load sys admins (is_system_admin=TRUE) — independent of company membership
        try:
            sys_admins = db.execute(
                """
                SELECT
                  u.id, u.email, u.name, u.is_active, u.created_at,
                  u.sys_role
                FROM sys_users u
                WHERE u.is_system_admin = TRUE
                ORDER BY u.id
                """
            ).fetchall()
        except Exception:
            db.connection.rollback()
            sys_admins = []

        # Normalize each sys_role into a list for the template.
        sys_admins = [
            {**dict(r), "sys_role_list": _normalize_roles(r["sys_role"])}
            for r in sys_admins
        ]

        return render_template(
            "admin/system_home.html",
            client_companies=client_companies,
            internal_companies=internal_companies,
            sys_admins=sys_admins,
            sys_roles=SYS_ROLES,
        )

    # ──────────────────────────────────────────────────────────────────
    # Sys-role assignment — Super Admin only.
    # Accepts multiple roles via checkboxes (form sends multiple
    # 'sys_role' values).
    # ──────────────────────────────────────────────────────────────────
    @app.post("/admin/system/users/<int:user_id>/sys-role")
    @sys_role_required()  # empty list = super_admin only
    def admin_system_assign_sys_role(user_id):
        new_roles = [r.strip().lower() for r in request.form.getlist("sys_role")
                     if r.strip().lower() in SYS_ROLES]
        if not new_roles:
            flash("Select at least one sys role (cannot leave empty).")
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

            old_roles = _normalize_roles(row["sys_role"])
            db.execute(
                "UPDATE sys_users SET sys_role = %s, updated_at = now() WHERE id = %s",
                (new_roles, user_id),
            )
            log_event(
                db,
                action="SYS_ROLE_CHANGE",
                module="sys",
                entity_table="sys_users",
                entity_id=str(user_id),
                status_code=200,
                message=(f"sys_role changed for {row['email']}: "
                         f"{old_roles} → {new_roles}"),
                old_data={"sys_role": old_roles},
                new_data={"sys_role": new_roles, "changed_by_user_id": actor_id},
            )
            db.commit()
            flash(f"Updated {row['email']} → {', '.join(new_roles)}")
        except Exception as e:
            db.rollback()
            flash(f"Update failed: {e}")
        return redirect(url_for("admin_system_home"))

    # ──────────────────────────────────────────────────────────────────
    # Register a new sys admin — Super Admin only.
    # ──────────────────────────────────────────────────────────────────
    @app.post("/admin/system/sys-admins/new")
    @sys_role_required()  # super_admin only
    def admin_system_sys_admin_new():
        email = (request.form.get("email") or "").strip().lower()
        name = (request.form.get("name") or "").strip() or None
        password = request.form.get("password") or ""
        roles = [r.strip().lower() for r in request.form.getlist("sys_role")
                 if r.strip().lower() in SYS_ROLES]

        if not email or not password:
            flash("Email and initial password are required.")
            return redirect(url_for("admin_system_home"))
        if not roles:
            flash("Pick at least one sys role.")
            return redirect(url_for("admin_system_home"))

        db = get_db()
        actor_id = (getattr(g, "current_user", {}) or {}).get("id")

        # Reject duplicate email
        existing = db.execute(
            "SELECT 1 FROM sys_users WHERE lower(email) = %s",
            (email,),
        ).fetchone()
        if existing:
            flash(f"Email already registered: {email}")
            return redirect(url_for("admin_system_home"))

        # New sys admins join Kurajika's internal company (id=1) as 'admin'
        # so the existing session-loading JOIN works (it requires a
        # sys_user_companies row).
        internal_co = db.execute(
            "SELECT id FROM mst_companies WHERE COALESCE(is_internal, FALSE) = TRUE ORDER BY id LIMIT 1"
        ).fetchone()
        if not internal_co:
            flash("No internal company found — mark one as is_internal first.")
            return redirect(url_for("admin_system_home"))
        internal_company_id = internal_co["id"]

        pw_hash = generate_password_hash(password, method="pbkdf2:sha256")

        try:
            urow = db.execute(
                """
                INSERT INTO sys_users
                  (company_id, email, name, password_hash, role, is_active,
                   is_system_admin, sys_role, created_at, updated_at)
                VALUES
                  (%s, %s, %s, %s, 'admin', 1, TRUE, %s, now(), now())
                RETURNING id
                """,
                (internal_company_id, email, name, pw_hash, roles),
            ).fetchone()
            new_user_id = urow["id"]

            db.execute(
                """
                INSERT INTO sys_user_companies
                  (user_id, company_id, role, is_active)
                VALUES (%s, %s, 'admin', 1)
                """,
                (new_user_id, internal_company_id),
            )

            log_event(
                db,
                action="SYS_ADMIN_CREATE",
                module="sys",
                entity_table="sys_users",
                entity_id=str(new_user_id),
                status_code=200,
                message=f"Sys admin created: {email} with roles {roles}",
                new_data={"email": email, "sys_role": roles,
                          "created_by_user_id": actor_id},
            )
            db.commit()
            flash(f"Created sys admin: {email} ({', '.join(roles)})")
        except Exception as e:
            db.rollback()
            flash(f"Failed: {e}")
        return redirect(url_for("admin_system_home"))