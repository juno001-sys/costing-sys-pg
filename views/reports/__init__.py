# reports package
from __future__ import annotations

from flask import Blueprint

reports_bp = Blueprint("reports", __name__)

# These will be injected from app.py
_get_db = None


def init_report_views(app, get_db):
    """
    Register report routes via Blueprint, and inject get_db().
    """
    global _get_db
    _get_db = get_db

    # Import route modules (they attach routes to reports_bp)
    from . import usage_report  # noqa: F401
    from . import cost_report  # noqa: F401
    from . import purchase_report  # noqa: F401
    from . import purchase_report_supplier  # noqa: F401
    from . import integrated_supplier  # noqa: F401
    from . import work_logs  # noqa
    from . import purchase_dashboard  # noqa
    app.register_blueprint(reports_bp)


def get_db():
    """
    Route modules call this to get DB connection.
    """
    if _get_db is None:
        raise RuntimeError("reports.get_db() is not injected. Call init_report_views(app, get_db) first.")
    return _get_db()


def shift_ym(ym: str, delta_months: int) -> str:
    y, m = map(int, ym.split("-"))
    total = y * 12 + (m - 1) + delta_months
    ny, nm = divmod(total, 12)
    return f"{ny:04d}-{nm + 1:02d}"


def parse_to_ym(raw: str | None, fallback: str) -> str:
    if not raw:
        return fallback
    try:
        y, m = raw.split("-")
        yi, mi = int(y), int(m)
        if 1 <= mi <= 12 and 2000 <= yi <= 2100:
            return f"{yi:04d}-{mi:02d}"
    except Exception:
        pass
    return fallback


def month_keys_ending_at(to_ym: str, count: int = 12) -> list[str]:
    """List of YYYY-MM keys, `count` months ending inclusively at to_ym."""
    y, m = map(int, to_ym.split("-"))
    keys: list[str] = []
    for _ in range(count):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    keys.reverse()
    return keys
