from flask import g
from db import get_db


def get_accessible_stores():
    """
    Current phase:
      - returns company-scoped stores

    Future phase:
      - can be changed to user/store-scoped stores
      - without changing every screen
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