echo "================ DEV STATE SNAPSHOT (CODE ONLY) ================"

echo
echo "=== 1) BRANCH + STATUS (no env noise) ==="
git branch --show-current
git status -sb -- ':!**/.venv/**' ':!**/venv/**' ':!**/__pycache__/**' ':!**/*.pyc' ':!app.py' ':!run_local.sh'

echo
echo "=== 2) CHANGED FILES (Python / Templates / Static) ==="
git diff --name-only -- \
  'views/**/*.py' \
  'templates/**/*.html' \
  'static/**/*.js' \
  'static/**/*.css' \
  'labels/**/*.json' \
| sed 's/^/ - /' || true

echo
echo "=== 3) DIFF SUMMARY (how big are the changes) ==="
git diff --stat -- \
  'views/**/*.py' \
  'templates/**/*.html' \
  'static/**/*.js' \
  'static/**/*.css' \
  'labels/**/*.json' \
|| true

echo
echo "=== 4) TOP DIFFS (first 200 lines total, code only) ==="
git diff -- \
  'views/**/*.py' \
  'templates/**/*.html' \
  'static/**/*.js' \
  'static/**/*.css' \
  'labels/**/*.json' \
| sed -n '1,200p' || true

echo
echo "=== 5) KEY FILE HEADERS (fast orientation) ==="
for f in \
  views/masters.py \
  templates/mst/stores_edit.html \
  templates/layout/base.html \
  views/loc/locations_page.py \
  templates/loc/partials/_locations_table.html \
  static/inventory/locations.js
do
  if [ -f "$f" ]; then
    echo
    echo "--- $f (first 40 lines) ---"
    sed -n '1,40p' "$f"
  fi
done

echo
echo "=== 6) GREP HOTSPOTS (tabs / locations / save endpoints) ==="
grep -RIn --line-number \
  -e "tab-panel" \
  -e "data-tab=" \
  -e "inventory/locations/save" \
  -e "inventory_locations_save" \
  -e "KURAJIKA\.LOCATIONS" \
  -e "locations\.js" \
  templates views static 2>/dev/null | sed -n '1,120p'

echo
echo "================ END DEV SNAPSHOT =============================="