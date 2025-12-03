import os
import sqlite3
import psycopg2
import psycopg2.extras
from flask import g

# --- 共通ラッパー（SQLite/PG 両方用） ---
class DBWrapper:
    def __init__(self, conn):
        self.conn = conn

    # SQLiteのように execute() を直接使えるようにする
    def execute(self, *args, **kwargs):
        cur = self.conn.cursor()
        cur.execute(*args, **kwargs)
        return cur

    # commit(), close() などは元の connection に委譲
    def __getattr__(self, name):
        return getattr(self.conn, name)


def get_db():
    mode = os.environ.get("DB_MODE", "sqlite")

    # ---- Postgresモード ----
    if mode == "postgres":
        if "pg" not in g:
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                raise RuntimeError("DATABASE_URL が設定されていません。")

            # RealDictCursor にすると row["col"] が使える（SQLite の Row と同様）
            conn = psycopg2.connect(
                db_url,
                cursor_factory=psycopg2.extras.RealDictCursor
            )

            g.pg = DBWrapper(conn)

        return g.pg

    # ---- SQLiteモード（デフォルト）----
    if "sqlite" not in g:
        conn = sqlite3.connect("costing.sqlite3")
        conn.row_factory = sqlite3.Row
        g.sqlite = DBWrapper(conn)

    return g.sqlite
