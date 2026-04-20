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
      - sys_user_store_grants overlays additional stores (or elevates the
        role on stores already accessible via the company)
      - Result = union of: all stores in the user's current company
        (filtered by function role's company-wide scope) + all stores
        explicitly granted to the user

    Falls back to legacy "all company stores" behavior if
    sys_user_store_grants table does not exist (pre-migration).
    """
    if hasattr(g, "_stores_cache") and g._stores_cache is not None:
        return g._stores_cache or []

    company_id = getattr(g, "current_company_id", None)
    if not company_id:
        return []

    db = get_db()
    rows = db.execute(
        """
        SELECT id, code, name
        FROM mst_stores
        WHERE COALESCE(is_active, 1) = 1
          AND company_id = %s
        ORDER BY code, id
        """,
        (company_id,),
    ).fetchall()

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