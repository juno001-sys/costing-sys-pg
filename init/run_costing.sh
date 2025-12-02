#!/usr/bin/env bash
set -e

cd "$(dirname "$0")/.."

echo "=== Flask を起動します ==="
echo "止める時は Ctrl + C です"

./venv/bin/python app.py
