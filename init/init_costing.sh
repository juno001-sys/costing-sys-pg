#!/usr/bin/env bash
set -e

cd "$(dirname "$0")/.."

echo "=== 1. Python仮想環境の準備 ==="
if [ ! -d "venv" ]; then
  python3 -m venv venv
fi

echo "=== 2. Flaskのインストール ==="
./venv/bin/pip install --upgrade pip
./venv/bin/pip install flask

echo "=== 3. SQLite データベース作成 ==="
./venv/bin/python - << 'PYCODE'
import sqlite3
from pathlib import Path

db_path = Path("costing.sqlite3")
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.executescript("""
CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    seats INTEGER,
    opened_on DATE,
    closed_on DATE
);

CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    address TEXT
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    unit TEXT NOT NULL,
    category TEXT,
    pl_account_id INTEGER,
    tax_category TEXT NOT NULL DEFAULT 'STANDARD_10',
    department TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_items_supplier_code
ON items (supplier_id, code);

CREATE TABLE IF NOT EXISTS delivery_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL,
    supplier_id INTEGER NOT NULL,
    slip_no TEXT NOT NULL,
    delivery_date DATE NOT NULL,
    total_amount REAL,
    tax_amount REAL,
    note TEXT,
    FOREIGN KEY (store_id) REFERENCES stores(id),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
);

CREATE INDEX IF NOT EXISTS idx_dn_delivery_date
ON delivery_notes(delivery_date);

CREATE TABLE IF NOT EXISTS delivery_note_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_note_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    quantity REAL NOT NULL,
    unit_price REAL NOT NULL,
    amount REAL NOT NULL,
    FOREIGN KEY (delivery_note_id) REFERENCES delivery_notes(id),
    FOREIGN KEY (item_id) REFERENCES items(id)
);

CREATE INDEX IF NOT EXISTS idx_dnl_item ON delivery_note_lines(item_id);
""")

conn.commit()
conn.close()
print("=== DBとテーブルの準備が完了 ===")
PYCODE

echo "=== 初期セットアップ完了 ==="
