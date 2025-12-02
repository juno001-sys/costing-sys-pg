#!/bin/bash
echo "=== Kurajika CMS: 品目CSV → 5桁コード自動登録 ==="

cd "$(dirname "$0")/.."

# venv を active
source venv/bin/activate

echo "[1] CSV を読み込み、仕入先別に5桁コードで登録..."
python3 init/import_items_from_csv.py

echo "=== 完了 ==="