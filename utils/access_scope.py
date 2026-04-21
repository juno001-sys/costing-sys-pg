from flask import g
from db import get_db


# Function-role rank for OR-overlay computation. Store grants can ELEVATE
# the function role on specific stores (admin > operator > auditor).
# Phase B preserves DB role values; UI labels 'auditor' as 'Supervisor'.
ROLE_RANK = {
    "auditor":   1,    # read-only / supervisor
    "operator":  2,    # data entry
    "admin":     3,    # full company-level admin
}


def get_accessible_stores():
    """Stores the current user can see in nav and selectors.

    Phase B semantics:
      - Function role = baseline (1 per user per company), set in
        sys_user_companies.role
      - 'admin' function role → all active stores in the current company
      - 'operator' / 'auditor' → only stores explicitly granted via
        sys_user_store_grants (OR-overlay; no company-wide store scope)

    Falls back to legacy "all company stores" behavior if
    sys_user_store_grants table does not exist (pre-migration).
    """
    if hasattr(g, "_stores_cache") and g._stores_cache is not None:
        return g._stores_cache or []

    company_id = getattr(g, "current_company_id", None)
    if not company_id:
        return []

    db = get_db()
    all_rows = db.execute(
        """
        SELECT id, code, name
        FROM mst_stores
        WHERE COALESCE(is_active, 1) = 1
          AND company_id = %s
        ORDER BY code, id
        """,
        (company_id,),
    ).fetchall()

    role = getattr(g, "current_role", None)
    if role == "admin":
        g._stores_cache = all_rows
        return all_rows

    user_id = (getattr(g, "current_user", {}) or {}).get("id")
    grant_rows = _safe_query_or_none(db, """
        SELECT store_id
        FROM sys_user_store_grants
        WHERE user_id = %s AND company_id = %s AND is_active = 1
          AND revoked_at IS NULL
    """, (user_id, company_id))

    if grant_rows is None:
        # Legacy fallback: grants table missing (pre-migration).
        g._stores_cache = all_rows
        return all_rows

    granted_ids = {r["store_id"] for r in grant_rows}
    rows = [r for r in all_rows if r["id"] in granted_ids]
    g._stores_cache = rows
    return rows


def get_accessible_store_ids():
    return {row["id"] for row in get_accessible_stores()}


def normalize_accessible_store_id(raw_store_id):
    if raw_store_id in (None, ""):
        return None

    try:
        store_id = int(raw_store_id)
    except (TypeError, ValueError):
        return None

    return store_id if store_id in get_accessible_store_ids() else None


# ----------------------------------------------------------------------
# Phase B helpers — function-role + per-store overlay (OR semantics)
# ----------------------------------------------------------------------
def _safe_query(db, sql, params=()):
    """Run a query; on missing-table or other errors, roll back and return []."""
    try:
        return db.execute(sql, params).fetchall()
    except Exception:
        try:
            db.connection.rollback()
        except Exception:
            pass
        return []


def _safe_query_or_none(db, sql, params=()):
    """Like _safe_query but returns None on error so callers can distinguish
    'query failed / table missing' from 'query succeeded with zero rows'."""
    try:
        return db.execute(sql, params).fetchall()
    except Exception:
        try:
            db.connection.rollback()
        except Exception:
            pass
        return None


def get_user_store_grants(user_id, company_id):
    """Return {store_id: role} for the user's active per-store grants in
    this company. Empty dict if migration not applied yet.
    """
    if not user_id or not company_id:
        return {}
    db = get_db()
    rows = _safe_query(db, """
        SELECT store_id, store_role
        FROM sys_user_store_grants
        WHERE user_id = %s AND company_id = %s AND is_active = 1
          AND revoked_at IS NULL
    """, (user_id, company_id))
    return {r["store_id"]: r["store_role"] for r in rows}


def get_effective_role_on_store(store_id, user_id=None, company_id=None,
                                function_role=None):
    """Compute the user's effective role on a specific store.

    Returns the higher (by ROLE_RANK) of:
      - the function role (company-wide baseline)
      - the per-store grant role, if one exists

    None inputs are pulled from `g` so this works as a no-arg call from views.
    Returns None if the user has no access at all.
    """
    if user_id is None:
        user_id = (getattr(g, "current_user", {}) or {}).get("id")
    if company_id is None:
        company_id = getattr(g, "current_company_id", None)
    if function_role is None:
        function_role = getattr(g, "current_role", None)

    base_rank = ROLE_RANK.get(function_role or "", 0)
    grants = get_user_store_grants(user_id, company_id)
    grant_role = grants.get(store_id)
    grant_rank = ROLE_RANK.get(grant_role or "", 0)

    effective_rank = max(base_rank, grant_rank)
    if effective_rank == 0:
        return None
    # Reverse-lookup rank → role name
    for role_name, rank in ROLE_RANK.items():
        if rank == effective_rank:
            return role_name
    return None


# ----------------------------------------------------------------------
# Per-company nav visibility policy
# ----------------------------------------------------------------------
# Nav items that can be toggled per (company, role). The key is used in
# both the sys_company_nav_policies table and the Jinja template guards
# (`{% if nav_allowed('items_master') %}`).
NAV_KEYS = [
    "dashboard",
    "order_support",
    "purchase_form",
    "inventory_count",
    "inventory_count_smart",
    "purchase_report",
    "usage_report",
    "cost_report",
    "integrated_report",
    "suppliers_master",
    "items_master",
    "stores_master",
    "help",
    "work_logs",
]

# Default visibility when no policy row exists. Master data is hidden
# from operators/auditors by default — typical expectation for a
# restaurant operator who should not be editing supplier/item/store
# definitions. Company admins can flip these on explicitly.
NAV_DEFAULT_VISIBILITY = {
    "operator": {
        "dashboard": True,
        "order_support": True,
        "purchase_form": True,
        "inventory_count": True,
        "inventory_count_smart": True,
        "purchase_report": True,
        "usage_report": True,
        "cost_report": True,
        "integrated_report": True,
        "suppliers_master": False,
        "items_master": False,
        "stores_master": False,
        "help": True,
        "work_logs": True,
    },
    "auditor": {
        "dashboard": True,
        "order_support": True,
        "purchase_form": True,
        "inventory_count": True,
        "inventory_count_smart": True,
        "purchase_report": True,
        "usage_report": True,
        "cost_report": True,
        "integrated_report": True,
        "suppliers_master": False,
        "items_master": False,
        "stores_master": False,
        "help": True,
        "work_logs": True,
    },
}


def get_company_nav_policy(company_id, role):
    """Return {nav_key: visible} for (company, role). Cached on `g`.

    Missing table or missing row → empty dict; callers should fall back
    to NAV_DEFAULT_VISIBILITY.
    """
    cache_key = f"_nav_policy_{company_id}_{role}"
    cached = getattr(g, cache_key, None)
    if cached is not None:
        return cached

    if not company_id or not role:
        setattr(g, cache_key, {})
        return {}

    db = get_db()
    rows = _safe_query(db, """
        SELECT nav_key, visible
        FROM sys_company_nav_policies
        WHERE company_id = %s AND role = %s
    """, (company_id, role))
    policy = {r["nav_key"]: bool(r["visible"]) for r in rows}
    setattr(g, cache_key, policy)
    return policy


def nav_allowed(nav_key):
    """Template helper: should this nav item be shown to the current user?

    Rules:
      - admin function role → always visible (feature-flag gates still
        apply separately in the template).
      - operator / auditor → look up the per-company policy; fall back
        to NAV_DEFAULT_VISIBILITY when no row exists.
      - no role / no company → hide (defensive).
    """
    role = getattr(g, "current_role", None)
    if role == "admin":
        return True
    if role not in NAV_DEFAULT_VISIBILITY:
        return False

    company_id = getattr(g, "current_company_id", None)
    if not company_id:
        return False

    policy = get_company_nav_policy(company_id, role)
    if nav_key in policy:
        return policy[nav_key]
    return NAV_DEFAULT_VISIBILITY[role].get(nav_key, True)


def is_chief_admin(user_id=None, company_id=None):
    """Is the given (user, company) the Chief Admin? Defaults to current user."""
    if user_id is None:
        user_id = (getattr(g, "current_user", {}) or {}).get("id")
    if company_id is None:
        company_id = getattr(g, "current_company_id", None)
    if not user_id or not company_id:
        return False
    db = get_db()
    rows = _safe_query(db, """
        SELECT 1 FROM sys_user_companies
        WHERE user_id = %s AND company_id = %s
          AND is_chief_admin = TRUE AND is_active = 1
        LIMIT 1
    """, (user_id, company_id))
    return bool(rows)