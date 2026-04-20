"""
Sys-admin invoice management.

Two views:
  GET /admin/system/invoices                       — list & filter all invoices
  POST /admin/system/invoices/<id>/mark-paid       — mark a single invoice paid

Plus a tiny helper module + view to manually trigger the monthly generator
(typically the script is run as a cron, but a manual button is useful while
the cron isn't set up yet).
"""
from __future__ import annotations

from datetime import date
from functools import wraps

from flask import flash, g, redirect, render_template, request, url_for

from views.reports.audit_log import log_event


def init_admin_system_invoices_views(app, get_db):
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

    @app.get("/admin/system/invoices")
    @system_admin_required
    def admin_system_invoices():
        db = get_db()
        status_filter = request.args.get("status") or "all"

        sql = """
            SELECT i.id, i.company_id, c.name AS company_name,
                   i.invoice_number, i.period_start, i.period_end,
                   i.amount, i.currency, i.status,
                   i.payment_method, i.due_date, i.issued_at, i.paid_at,
                   (CURRENT_DATE - i.due_date) AS days_overdue
            FROM sys_company_invoices i
            JOIN mst_companies c ON c.id = i.company_id
        """
        params = ()
        if status_filter in ("draft", "issued", "paid", "void"):
            sql += " WHERE i.status = %s"
            params = (status_filter,)
        sql += " ORDER BY i.created_at DESC LIMIT 200"

        try:
            invoices = db.execute(sql, params).fetchall()
        except Exception:
            db.connection.rollback()
            invoices = []

        # Summary counts (for the filter pills)
        try:
            counts = db.execute("""
                SELECT status, COUNT(*) AS n
                FROM sys_company_invoices
                GROUP BY status
            """).fetchall()
            count_map = {r["status"]: r["n"] for r in counts}
        except Exception:
            db.connection.rollback()
            count_map = {}

        return render_template(
            "admin/system_invoices.html",
            invoices=invoices,
            count_map=count_map,
            status_filter=status_filter,
        )

    @app.post("/admin/system/invoices/<int:invoice_id>/mark-paid")
    @system_admin_required
    def admin_system_invoice_mark_paid(invoice_id):
        db = get_db()
        actor_id = (getattr(g, "current_user", {}) or {}).get("id")

        try:
            row = db.execute(
                "SELECT id, company_id, status FROM sys_company_invoices WHERE id = %s",
                (invoice_id,),
            ).fetchone()
            if not row:
                flash("Invoice not found.")
                return redirect(url_for("admin_system_invoices"))

            if row["status"] == "paid":
                flash("Already marked paid.")
                return redirect(url_for("admin_system_invoices"))

            db.execute("""
                UPDATE sys_company_invoices
                SET status = 'paid',
                    paid_at = now(),
                    paid_via = COALESCE(paid_via, 'bank_transfer'),
                    updated_at = now()
                WHERE id = %s
            """, (invoice_id,))

            log_event(
                db,
                action="INVOICE_MARK_PAID",
                module="sys",
                entity_table="sys_company_invoices",
                entity_id=str(invoice_id),
                company_id=row["company_id"],
                status_code=200,
                message=f"Invoice {invoice_id} marked paid by user_id={actor_id}",
            )
            db.commit()
            flash("Marked paid.")
        except Exception as e:
            db.rollback()
            flash(f"Failed: {e}")
        return redirect(url_for("admin_system_invoices"))

    @app.post("/admin/system/invoices/generate-month")
    @system_admin_required
    def admin_system_invoices_generate():
        """Manual trigger of the monthly invoice generator. In production
        this would be a cron job — for now sys admin clicks a button."""
        from utils.invoice_generator import generate_monthly_invoices

        target_year = int(request.form.get("year") or date.today().year)
        target_month = int(request.form.get("month") or date.today().month)

        try:
            db = get_db()
            count, skipped = generate_monthly_invoices(db, target_year, target_month)
            db.commit()
            flash(f"Generated {count} invoice(s). Skipped {skipped} (already exists or trial).")
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            flash(f"Generation failed: {e}")

        return redirect(url_for("admin_system_invoices"))
