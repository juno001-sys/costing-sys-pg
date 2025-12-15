import os
import psycopg2
import psycopg2.extras
from flask import g


class DBWrapper:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        # Keep None as None (psycopg2 allows it)
        if params is None:
            params = []

        # Convert SQLite style placeholders
        fixed_sql = sql.replace("?", "%s")

        # ---- Guard: placeholder count vs params count ----
        placeholder_count = fixed_sql.count("%s")
        try:
            param_count = len(params)
        except TypeError:
            # if params is not sized (rare), skip
            param_count = None

        if param_count is not None and placeholder_count != param_count:
            raise ValueError(
                f"SQL placeholder mismatch: %s={placeholder_count}, params={param_count}\n"
                f"SQL: {fixed_sql}\n"
                f"params: {params}"
            )

        cur = self.conn.cursor()
        cur.execute(fixed_sql, params)
        return cur

    def __getattr__(self, name):
        return getattr(self.conn, name)

def _current_env() -> str:
    """
    Determine environment.
    Priority:
      1) APP_ENV
      2) FLASK_ENV
      3) default: production
    """
    env = (os.environ.get("APP_ENV") or os.environ.get("FLASK_ENV") or "production").strip().lower()
    if env in ("prod", "production"):
        return "production"
    if env in ("dev", "development", "local"):
        return "development"
    return env  # allow custom names like "staging"


def _db_url_for_env(env: str) -> str:
    """
    URL selection rules:
      - production: DATABASE_URL
      - development: DATABASE_URL_DEV if set else DATABASE_URL
      - staging/others: DATABASE_URL_<ENV> if set else DATABASE_URL
    """
    if env == "production":
        url = os.environ.get("DATABASE_URL")
    elif env == "development":
        url = os.environ.get("DATABASE_URL_DEV") or os.environ.get("DATABASE_URL")
    else:
        key = f"DATABASE_URL_{env.upper()}"
        url = os.environ.get(key) or os.environ.get("DATABASE_URL")

    if not url:
        raise RuntimeError(
            "No database URL found. Set DATABASE_URL (and optionally DATABASE_URL_DEV / DATABASE_URL_STAGING)."
        )
    return url


def get_db():
    if "db" not in g:
        env = _current_env()
        db_url = _db_url_for_env(env)

        conn = psycopg2.connect(
            db_url,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        g.db = DBWrapper(conn)

    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        # db is DBWrapper; ensure we close the underlying connection
        try:
            db.conn.close()
        except Exception:
            # fallback
            db.close()
