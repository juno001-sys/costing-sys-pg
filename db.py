import os
import sqlite3
import psycopg2
from flask import g

def get_db():
    mode = os.environ.get("DB_MODE", "sqlite")

    # ---- Postgresモード ----
    if mode == "postgres":
        if "pg" not in g:
            g.pg = psycopg2.connect(os.environ["DATABASE_URL"])
        return g.pg

    # ---- SQLiteモード（デフォルト）----
    if "sqlite" not in g:
        g.sqlite = sqlite3.connect("costing.sqlite3")
        g.sqlite.row_factory = sqlite3.Row
    return g.sqlite
