"""
Customer Health Dashboard for sys-admin.

Three screens:
  GET /admin/system/health
      Overview — one row per company, status badges, KPI snapshot.

  GET /admin/system/health/<company_id>
      Company detail — KPI cards, 6-month trend, feature usage,
      user activity, per-store breakdown. Print-friendly.

  GET /admin/system/health/<company_id>/store/<store_id>
      Store detail — KPIs + 6-month trend for one store. Print-friendly.

All routes are sys-admin-only.
"""
from __future__ import annotations

import json
from functools import wraps

from flask import flash, g, redirect, render_template, request, url_for

from utils.health_metrics import (
    get_company_feature_usage,
    get_company_kpis,
    get_company_monthly_trend,
    get_company_store_activity,
    get_company_user_activity,
    get_store_kpis,
    get_store_monthly_trend,
    list_companies_with_health,
)


def init_admin_system_health_views(app, get_db):
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

    @app.get("/admin/system/health")
    @system_admin_required
    def admin_system_health_overview():
        db = get_db()
        companies = list_companies_with_health(db)

        # Status filter
        status_filter = request.args.get("status") or "all"
        if status_filter != "all":
            filtered = [c for c in companies if c["status"] == status_filter]
        else:
            filtered = companies

        # Status counts for the filter pills
        status_counts = {}
        for c in companies:
            status_counts[c["status"]] = status_counts.get(c["status"], 0) + 1

        return render_template(
            "admin/system_health_overview.html",
            companies=filtered,
            status_counts=status_counts,
            status_filter=status_filter,
            total_companies=len(companies),
        )

    @app.get("/admin/system/health/<int:company_id>")
    @system_admin_required
    def admin_system_health_company(company_id):
        db = get_db()
        kpis = get_company_kpis(db, company_id)
        if not kpis:
            flash("Company not found.")
            return redirect(url_for("admin_system_health_overview"))

        users = get_company_user_activity(db, company_id)
        stores = get_company_store_activity(db, company_id)
        trend = get_company_monthly_trend(db, company_id, months=6)
        features = get_company_feature_usage(db, company_id)

        return render_template(
            "admin/system_health_company.html",
            kpis=kpis,
            users=users,
            stores=stores,
            trend=trend,
            trend_labels=json.dumps([r["label"] for r in trend]),
            trend_logins=json.dumps([int(r["logins"] or 0) for r in trend]),
            trend_purchases=json.dumps([int(r["purchases"] or 0) for r in trend]),
            trend_counts=json.dumps([int(r["stock_counts"] or 0) for r in trend]),
            features=features,
        )

    @app.get("/admin/system/health/<int:company_id>/store/<int:store_id>")
    @system_admin_required
    def admin_system_health_store(company_id, store_id):
        db = get_db()
        store = get_store_kpis(db, store_id)
        if not store or store.get("company_id") != company_id:
            flash("Store not found in this company.")
            return redirect(url_for("admin_system_health_company", company_id=company_id))

        trend = get_store_monthly_trend(db, store_id, months=6)

        return render_template(
            "admin/system_health_store.html",
            store=store,
            company_id=company_id,
            trend=trend,
            trend_labels=json.dumps([r["label"] for r in trend]),
            trend_purchases=json.dumps([int(r["purchases"] or 0) for r in trend]),
            trend_counts=json.dumps([int(r["stock_counts"] or 0) for r in trend]),
            trend_amounts=json.dumps([int(r["purchase_amount"] or 0) for r in trend]),
        )
