"""
Customer Health metrics aggregator.

Pulls per-company (and per-store) engagement metrics from existing tables.
No new tables needed — everything is derived from sys_work_logs,
sys_users, sys_user_companies, sys_company_contracts/invoices, pur_purchases,
inv_stock_counts.

All "recent activity" windows default to 30 days (last 30 days inclusive).

The status badge logic (locked in 2026-04-21):
  🟢 healthy  : login within 7d
  🟡 quiet    : no login 7-30d
  🟠 dormant  : no login 30+ days
  🔴 critical : invoice overdue OR ≥5 errors in last 7d
                (critical wins over dormant/quiet/healthy)
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional


WINDOW_DAYS = 30
CRITICAL_ERROR_COUNT = 5
CRITICAL_ERROR_WINDOW_DAYS = 7


def _safe_query(db, sql, params=()):
    """Run a query; on error (missing table etc.), roll back and return []."""
    try:
        return db.execute(sql, params).fetchall()
    except Exception:
        try:
            db.connection.rollback()
        except Exception:
            pass
        return []


# ─────────────────────────────────────────────────────────────────────
# Per-company aggregations
# ─────────────────────────────────────────────────────────────────────
def list_companies_with_health(db, include_internal: bool = False) -> list[dict]:
    """Return all companies with their basic health KPIs for the overview screen.

    By default excludes internal accounts (mst_companies.is_internal = TRUE)
    so the Client Health view focuses on actual paying / trial customers.
    Pass include_internal=True to also see Kurajika's own house account.

    Columns: id, name, is_internal, tier, trial_ends_at, total_users,
             active_users_30d, logins_30d, purchases_30d, stock_counts_30d,
             errors_7d, last_login_at, last_action_at, has_overdue_invoice,
             status
    """
    # Filter clause is built so it works even before the is_internal column
    # exists (try/except in _safe_query rolls back if it fails).
    where_clause = "" if include_internal else \
        "WHERE COALESCE(c.is_internal, FALSE) = FALSE"
    rows = _safe_query(db, f"""
        WITH base AS (
          SELECT c.id, c.name, COALESCE(c.is_internal, FALSE) AS is_internal
          FROM mst_companies c
          {where_clause}
          ORDER BY c.id
        )
        SELECT
          b.id,
          b.name,
          b.is_internal,
          -- Current contract tier
          (SELECT tier FROM sys_company_contracts cc
            WHERE cc.company_id = b.id AND cc.effective_to IS NULL
            ORDER BY effective_from DESC LIMIT 1) AS tier,
          (SELECT trial_ends_at FROM sys_company_contracts cc
            WHERE cc.company_id = b.id AND cc.effective_to IS NULL
            ORDER BY effective_from DESC LIMIT 1) AS trial_ends_at,
          -- User counts
          (SELECT COUNT(*) FROM sys_user_companies uc
            WHERE uc.company_id = b.id AND uc.is_active = 1) AS total_users,
          -- Distinct users active in last 30d (via work logs)
          (SELECT COUNT(DISTINCT actor_user_id) FROM sys_work_logs wl
            WHERE wl.company_id = b.id AND wl.actor_user_id IS NOT NULL
              AND wl.created_at >= now() - interval '{WINDOW_DAYS} days') AS active_users_30d,
          -- Login count in last 30d.
          -- Note: /login POST events have company_id=NULL (logged BEFORE the
          -- session is established). sys_sessions is the authoritative source
          -- — one row per successful login.
          (SELECT COUNT(*) FROM sys_sessions ss
            WHERE ss.company_id = b.id
              AND ss.created_at >= now() - interval '{WINDOW_DAYS} days') AS logins_30d,
          -- Purchase entries in last 30d
          (SELECT COUNT(*) FROM pur_purchases p
            JOIN mst_stores s ON s.id = p.store_id
            WHERE s.company_id = b.id
              AND p.is_deleted = 0
              AND p.created_at >= now() - interval '{WINDOW_DAYS} days') AS purchases_30d,
          -- Stock count entries in last 30d
          (SELECT COUNT(*) FROM inv_stock_counts c
            JOIN mst_stores s ON s.id = c.store_id
            WHERE s.company_id = b.id
              AND c.created_at >= now() - interval '{WINDOW_DAYS} days') AS stock_counts_30d,
          -- Error count in last 7d
          (SELECT COUNT(*) FROM sys_work_logs wl
            WHERE wl.company_id = b.id
              AND wl.status_code >= 500
              AND wl.created_at >= now() - interval '{CRITICAL_ERROR_WINDOW_DAYS} days') AS errors_7d,
          -- Last login (any user) — sourced from sys_sessions
          (SELECT MAX(created_at) FROM sys_sessions ss
            WHERE ss.company_id = b.id) AS last_login_at,
          -- Last action of any kind
          (SELECT MAX(created_at) FROM sys_work_logs wl
            WHERE wl.company_id = b.id) AS last_action_at,
          -- Overdue invoices
          EXISTS (
            SELECT 1 FROM sys_company_invoices i
            WHERE i.company_id = b.id AND i.status = 'issued'
              AND i.due_date IS NOT NULL AND i.due_date < CURRENT_DATE
          ) AS has_overdue_invoice
        FROM base b
    """)
    result = []
    for r in rows:
        d = dict(r)
        d["status"] = _compute_status(d)
        result.append(d)
    return result


def _compute_status(row: dict) -> str:
    """Compute the status badge: critical | dormant | quiet | healthy | new."""
    if row.get("has_overdue_invoice") or (row.get("errors_7d") or 0) >= CRITICAL_ERROR_COUNT:
        return "critical"
    last_login = row.get("last_login_at")
    if last_login is None:
        # Never logged in — likely a brand-new account
        return "new"
    if isinstance(last_login, datetime):
        days_since = (datetime.now(last_login.tzinfo) - last_login).days
    else:
        # date type
        days_since = (date.today() - last_login).days
    if days_since >= 30:
        return "dormant"
    if days_since >= 7:
        return "quiet"
    return "healthy"


# ─────────────────────────────────────────────────────────────────────
# Per-company detail
# ─────────────────────────────────────────────────────────────────────
def get_company_kpis(db, company_id: int) -> dict:
    """Return KPI dict for the company detail screen header."""
    rows = list_companies_with_health(db)
    for r in rows:
        if r["id"] == company_id:
            return r
    return {}


def get_company_user_activity(db, company_id: int) -> list[dict]:
    """Per-user breakdown: name, role, last login, action count (30d)."""
    return _safe_query(db, f"""
        SELECT
          u.id, u.email, u.name,
          uc.role, uc.is_chief_admin, uc.is_active,
          (SELECT MAX(created_at) FROM sys_sessions ss
            WHERE ss.company_id = %s AND ss.user_id = u.id) AS last_login_at,
          (SELECT COUNT(*) FROM sys_work_logs wl
            WHERE wl.company_id = %s AND wl.actor_user_id = u.id
              AND wl.created_at >= now() - interval '{WINDOW_DAYS} days') AS actions_30d
        FROM sys_user_companies uc
        JOIN sys_users u ON u.id = uc.user_id
        WHERE uc.company_id = %s
        ORDER BY uc.is_chief_admin DESC, uc.is_active DESC, u.id
    """, (company_id, company_id, company_id))


def get_company_store_activity(db, company_id: int) -> list[dict]:
    """Per-store breakdown for a company."""
    return _safe_query(db, f"""
        SELECT
          s.id, s.code, s.name,
          (SELECT COUNT(*) FROM pur_purchases p
            WHERE p.store_id = s.id AND p.is_deleted = 0
              AND p.created_at >= now() - interval '{WINDOW_DAYS} days') AS purchases_30d,
          (SELECT COUNT(*) FROM inv_stock_counts c
            WHERE c.store_id = s.id
              AND c.created_at >= now() - interval '{WINDOW_DAYS} days') AS stock_counts_30d,
          (SELECT MAX(created_at) FROM pur_purchases p
            WHERE p.store_id = s.id AND p.is_deleted = 0) AS last_purchase_at,
          (SELECT MAX(created_at) FROM inv_stock_counts c
            WHERE c.store_id = s.id) AS last_count_at
        FROM mst_stores s
        WHERE s.company_id = %s AND COALESCE(s.is_active, 1) = 1
        ORDER BY s.code, s.id
    """, (company_id,))


def get_company_monthly_trend(db, company_id: int, months: int = 6) -> list[dict]:
    """Return one row per month for the last N months with logins/purchases/counts."""
    return _safe_query(db, f"""
        WITH months AS (
          SELECT generate_series(
            date_trunc('month', CURRENT_DATE) - interval '{months - 1} months',
            date_trunc('month', CURRENT_DATE),
            interval '1 month'
          )::date AS month_start
        )
        SELECT
          to_char(m.month_start, 'YYYY-MM') AS label,
          (SELECT COUNT(*) FROM sys_sessions ss
            WHERE ss.company_id = %s
              AND ss.created_at >= m.month_start
              AND ss.created_at <  m.month_start + interval '1 month') AS logins,
          (SELECT COUNT(*) FROM pur_purchases p
            JOIN mst_stores s ON s.id = p.store_id
            WHERE s.company_id = %s AND p.is_deleted = 0
              AND p.created_at >= m.month_start
              AND p.created_at <  m.month_start + interval '1 month') AS purchases,
          (SELECT COUNT(*) FROM inv_stock_counts c
            JOIN mst_stores s ON s.id = c.store_id
            WHERE s.company_id = %s
              AND c.created_at >= m.month_start
              AND c.created_at <  m.month_start + interval '1 month') AS stock_counts
        FROM months m
        ORDER BY m.month_start
    """, (company_id, company_id, company_id))


def get_company_feature_usage(db, company_id: int) -> list[dict]:
    """Which features have been touched in the last 30d?

    Maps feature_key → (touched, last_touched_at). We use simple endpoint
    pattern matching against sys_work_logs.path. Adjust EP_MAP as new
    features ship.
    """
    EP_MAP = {
        "purchase_entry_fuzzy":     ["/purchases/new", "/purchases/edit"],
        "purchase_entry_paste":     ["/pur/delivery_paste"],
        "inventory_count":          ["/inventory/count_v2"],
        "inventory_count_sp":       ["/inventory/count_sp"],
        "shelf_layout":             ["/loc/", "/admin/store-shelves"],
        "report_purchase_supplier": ["/reports/purchase"],
        "report_purchase_item":     ["/reports/purchase"],
        "report_usage_monthly":     ["/reports/usage"],
        "report_cost_monthly":      ["/reports/cost"],
        "profit_estimation":        ["/admin/stores", "/profit"],
        "purchase_dashboard":       ["/reports/dashboard"],
        "order_support":            ["/order-support"],
    }

    catalog = _safe_query(db, """
        SELECT feature_key, name_ja, name_en, default_tier
        FROM sys_features WHERE is_active = 1 ORDER BY sort_order
    """)
    result = []
    for f in catalog:
        key = f["feature_key"]
        prefixes = EP_MAP.get(key, [])
        if not prefixes:
            result.append({**dict(f), "touch_count": 0, "last_touched_at": None})
            continue
        # Build OR clause from prefixes
        like_clauses = " OR ".join("path LIKE %s" for _ in prefixes)
        params = [f"{p}%" for p in prefixes] + [company_id]
        rows = _safe_query(db, f"""
            SELECT COUNT(*) AS touch_count, MAX(created_at) AS last_touched_at
            FROM sys_work_logs
            WHERE ({like_clauses})
              AND company_id = %s
              AND created_at >= now() - interval '{WINDOW_DAYS} days'
        """, tuple(params))
        if rows:
            result.append({
                **dict(f),
                "touch_count": rows[0]["touch_count"] or 0,
                "last_touched_at": rows[0]["last_touched_at"],
            })
        else:
            result.append({**dict(f), "touch_count": 0, "last_touched_at": None})
    return result


# ─────────────────────────────────────────────────────────────────────
# Per-store detail
# ─────────────────────────────────────────────────────────────────────
def get_store_kpis(db, store_id: int) -> dict:
    """KPIs for the per-store screen."""
    rows = _safe_query(db, f"""
        SELECT
          s.id, s.code, s.name, s.company_id,
          c.name AS company_name,
          (SELECT COUNT(*) FROM pur_purchases p
            WHERE p.store_id = s.id AND p.is_deleted = 0
              AND p.created_at >= now() - interval '{WINDOW_DAYS} days') AS purchases_30d,
          (SELECT COUNT(*) FROM inv_stock_counts cc
            WHERE cc.store_id = s.id
              AND cc.created_at >= now() - interval '{WINDOW_DAYS} days') AS stock_counts_30d,
          (SELECT COALESCE(SUM(p.amount), 0) FROM pur_purchases p
            WHERE p.store_id = s.id AND p.is_deleted = 0
              AND p.delivery_date >= CURRENT_DATE - interval '{WINDOW_DAYS} days') AS purchase_amount_30d,
          (SELECT MAX(created_at) FROM pur_purchases p
            WHERE p.store_id = s.id AND p.is_deleted = 0) AS last_purchase_at,
          (SELECT MAX(created_at) FROM inv_stock_counts cc
            WHERE cc.store_id = s.id) AS last_count_at
        FROM mst_stores s
        LEFT JOIN mst_companies c ON c.id = s.company_id
        WHERE s.id = %s
    """, (store_id,))
    return dict(rows[0]) if rows else {}


def get_store_monthly_trend(db, store_id: int, months: int = 6) -> list[dict]:
    """Per-store monthly trend (purchases + counts only — login is per-company)."""
    return _safe_query(db, f"""
        WITH months AS (
          SELECT generate_series(
            date_trunc('month', CURRENT_DATE) - interval '{months - 1} months',
            date_trunc('month', CURRENT_DATE),
            interval '1 month'
          )::date AS month_start
        )
        SELECT
          to_char(m.month_start, 'YYYY-MM') AS label,
          (SELECT COUNT(*) FROM pur_purchases p
            WHERE p.store_id = %s AND p.is_deleted = 0
              AND p.created_at >= m.month_start
              AND p.created_at <  m.month_start + interval '1 month') AS purchases,
          (SELECT COUNT(*) FROM inv_stock_counts c
            WHERE c.store_id = %s
              AND c.created_at >= m.month_start
              AND c.created_at <  m.month_start + interval '1 month') AS stock_counts,
          (SELECT COALESCE(SUM(p.amount), 0) FROM pur_purchases p
            WHERE p.store_id = %s AND p.is_deleted = 0
              AND p.delivery_date >= m.month_start
              AND p.delivery_date <  m.month_start + interval '1 month') AS purchase_amount
        FROM months m
        ORDER BY m.month_start
    """, (store_id, store_id, store_id))
