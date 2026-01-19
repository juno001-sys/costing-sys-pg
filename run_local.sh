#!/bin/bash
set -e

source .venv/bin/activate

export APP_ENV=local
export DATABASE_URL="postgresql://junokurashima@localhost:5432/costing_dev"
export PORT=5050

# Ahead count vs dev (before Flask starts)
git fetch -q || true
git rev-list --left-right --count dev...HEAD 2>/dev/null | awk '{print $2}' > static/git_ahead.txt || echo "0" > static/git_ahead.txt

# Real commit hash for footer version
export RAILWAY_GIT_COMMIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo dev)"

python3 app.py