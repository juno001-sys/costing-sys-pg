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
    from . import work_logs  # noqa
    app.register_blueprint(reports_bp)


def get_db():
    """
    Route modules call this to get DB connection.
    """
    if _get_db is None:
        raise RuntimeError("reports.get_db() is not injected. Call init_report_views(app, get_db) first.")
    return _get_db()
