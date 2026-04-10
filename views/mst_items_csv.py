# views/mst_items_csv.py
# CSV import for mst_items — upload → header match → preview → import

import csv
import io
import json
import os
import tempfile
import uuid

from flask import render_template, request, redirect, url_for, flash, g, session

# ── System field definitions ──────────────────────────────────────────────────
SYSTEM_FIELDS = [
    {"key": "name",              "label": "品名",          "required": True},
    {"key": "supplier",          "label": "仕入先",         "required": True},
    {"key": "temp_zone",         "label": "保管温度帯",     "required": False},
    {"key": "unit",              "label": "ケース入数",     "required": False},
    {"key": "purchase_unit",     "label": "仕入れ単位",     "required": False},
    {"key": "inventory_unit",    "label": "棚卸し単位",     "required": False},
    {"key": "min_purchase_unit", "label": "最低仕入単位",   "required": False},
    {"key": "is_internal",       "label": "内製フラグ",     "required": False},
]

FIELD_ALIASES = {
    "name":              ["品名", "品目名", "商品名", "アイテム名", "item_name", "name", "品目"],
    "supplier":          ["仕入先", "仕入先名", "取引先", "メーカー", "supplier", "vendor"],
    "temp_zone":         ["温度帯", "保管温度帯", "保管区分", "temp_zone", "zone", "温度"],
    "unit":              ["ケース入数", "入数", "unit", "case_size"],
    "purchase_unit":     ["仕入れ単位", "仕入単位", "purchase_unit"],
    "inventory_unit":    ["棚卸し単位", "棚卸単位", "inventory_unit"],
    "min_purchase_unit": ["最低仕入単位", "最小単位", "最小仕入", "min_purchase_unit"],
    "is_internal":       ["内製", "内製_flg", "is_internal", "内製フラグ", "内製品"],
}

TEMP_DIR = tempfile.gettempdir()


def _auto_match(headers):
    """Try to auto-match CSV headers to system field keys."""
    mapping = {}   # csv_header -> field_key
    used_keys = set()
    for header in headers:
        h = header.strip()
        h_lower = h.lower()
        for field_key, aliases in FIELD_ALIASES.items():
            if field_key in used_keys:
                continue
            if h in aliases or h_lower in [a.lower() for a in aliases]:
                mapping[h] = field_key
                used_keys.add(field_key)
                break
    return mapping


def _detect_delimiter(content: str) -> str:
    sample = content[:2000]
    return "\t" if sample.count("\t") > sample.count(",") else ","


def _load_temp(temp_id: str):
    path = os.path.join(TEMP_DIR, f"cms_csv_{temp_id}.json")
    if not os.path.exists(path):
        return None, path
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp), path


def _build_supplier_map(db, company_id):
    rows = db.execute(
        "SELECT id, name, code FROM pur_suppliers WHERE is_active = 1 AND company_id = %s",
        (company_id,),
    ).fetchall()
    result = {}
    for s in rows:
        key = s["name"].strip().lower().replace(" ", "").replace("\u3000", "")
        result[key] = {"id": s["id"], "code": s["code"], "name": s["name"]}
    return result


def _build_tz_map(db):
    rows = db.execute(
        "SELECT code, default_name FROM inv_temp_zone_master WHERE COALESCE(is_active,TRUE)=TRUE"
    ).fetchall()
    result = {}
    for tz in rows:
        result[tz["default_name"]] = tz["code"]
        result[tz["code"]] = tz["code"]
    return result


def _validate_row(row, mapping, supplier_map, tz_map):
    """Return a dict with parsed values + errors list."""
    def get(key):
        col = mapping.get(key, "")
        return row.get(col, "").strip() if col else ""

    item_name      = get("name")
    supplier_raw   = get("supplier")
    temp_zone_raw  = get("temp_zone")
    unit_raw       = get("unit")
    purchase_unit  = get("purchase_unit")
    inventory_unit = get("inventory_unit")
    min_pu_raw     = get("min_purchase_unit")
    is_int_raw     = get("is_internal")

    errors = []

    if not item_name:
        errors.append("品名が空")

    supplier_info = None
    if not supplier_raw:
        errors.append("仕入先が空")
    else:
        key = supplier_raw.lower().replace(" ", "").replace("\u3000", "")
        supplier_info = supplier_map.get(key)
        if not supplier_info:
            errors.append(f"仕入先「{supplier_raw}」が未登録")

    unit_val = None
    if unit_raw:
        try:
            unit_val = int(unit_raw)
        except ValueError:
            errors.append(f"ケース入数「{unit_raw}」は整数で入力")

    min_pu_val = None
    if min_pu_raw:
        try:
            min_pu_val = float(min_pu_raw)
        except ValueError:
            errors.append(f"最低仕入単位「{min_pu_raw}」は数値で入力")

    temp_zone_code = tz_map.get(temp_zone_raw, temp_zone_raw) if temp_zone_raw else None
    is_internal    = 1 if is_int_raw in ("1", "yes", "true", "◯", "○", "内製") else 0

    return {
        "name":              item_name,
        "supplier_raw":      supplier_raw,
        "supplier_info":     supplier_info,
        "temp_zone_raw":     temp_zone_raw,
        "temp_zone_code":    temp_zone_code,
        "unit":              unit_val,
        "purchase_unit":     purchase_unit or None,
        "inventory_unit":    inventory_unit or None,
        "min_purchase_unit": min_pu_val,
        "is_internal":       is_internal,
        "errors":            errors,
        "ok":                len(errors) == 0,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

def init_items_csv_views(app, get_db):

    # ── Step 1: Upload ────────────────────────────────────────────────────────
    @app.route("/mst_items/csv/upload", methods=["GET", "POST"], endpoint="items_csv_upload")
    def items_csv_upload():
        if request.method == "GET":
            return render_template("mst/items_csv_upload.html")

        f = request.files.get("csv_file")
        if not f or not f.filename:
            flash("CSVファイルを選択してください。")
            return render_template("mst/items_csv_upload.html")

        try:
            content = f.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            f.seek(0)
            content = f.read().decode("cp932", errors="replace")

        delimiter = _detect_delimiter(content)
        reader    = csv.DictReader(io.StringIO(content), delimiter=delimiter)
        headers   = list(reader.fieldnames or [])

        if not headers:
            flash("CSVのヘッダー行が読み取れませんでした。")
            return render_template("mst/items_csv_upload.html")

        rows = list(reader)
        if not rows:
            flash("データ行が0件です。")
            return render_template("mst/items_csv_upload.html")

        # Save to temp file
        temp_id   = str(uuid.uuid4())
        temp_path = os.path.join(TEMP_DIR, f"cms_csv_{temp_id}.json")
        with open(temp_path, "w", encoding="utf-8") as fp:
            json.dump({"headers": headers, "rows": rows}, fp, ensure_ascii=False)

        session["csv_temp_id"] = temp_id
        auto_mapping = _auto_match(headers)

        return render_template(
            "mst/items_csv_match.html",
            headers=headers,
            auto_mapping=auto_mapping,
            system_fields=SYSTEM_FIELDS,
            row_count=len(rows),
        )

    # ── Step 2: Preview ───────────────────────────────────────────────────────
    @app.route("/mst_items/csv/preview", methods=["POST"], endpoint="items_csv_preview")
    def items_csv_preview():
        temp_id = session.get("csv_temp_id")
        if not temp_id:
            flash("セッションが切れました。もう一度アップロードしてください。")
            return redirect(url_for("items_csv_upload"))

        data, _ = _load_temp(temp_id)
        if not data:
            flash("一時ファイルが見つかりません。もう一度アップロードしてください。")
            return redirect(url_for("items_csv_upload"))

        # Build mapping: field_key -> csv_column_name
        mapping = {
            f["key"]: request.form.get(f"map_{f['key']}", "")
            for f in SYSTEM_FIELDS
        }
        session["csv_mapping"] = mapping

        db           = get_db()
        company_id   = getattr(g, "current_company_id", None)
        supplier_map = _build_supplier_map(db, company_id)
        tz_map       = _build_tz_map(db)

        rows         = data["rows"]
        preview_rows = []
        for i, row in enumerate(rows[:200]):
            parsed = _validate_row(row, mapping, supplier_map, tz_map)
            parsed["row_num"] = i + 2
            preview_rows.append(parsed)

        ok_count    = sum(1 for r in preview_rows if r["ok"])
        ng_count    = len(preview_rows) - ok_count
        total_count = len(rows)

        return render_template(
            "mst/items_csv_preview.html",
            preview_rows=preview_rows,
            ok_count=ok_count,
            ng_count=ng_count,
            total_count=total_count,
        )

    # ── Step 3: Import ────────────────────────────────────────────────────────
    @app.route("/mst_items/csv/import", methods=["POST"], endpoint="items_csv_import")
    def items_csv_import():
        temp_id = session.get("csv_temp_id")
        mapping = session.get("csv_mapping", {})

        if not temp_id or not mapping:
            flash("セッションが切れました。もう一度アップロードしてください。")
            return redirect(url_for("items_csv_upload"))

        data, temp_path = _load_temp(temp_id)
        if not data:
            flash("一時ファイルが見つかりません。もう一度アップロードしてください。")
            return redirect(url_for("items_csv_upload"))

        db           = get_db()
        company_id   = getattr(g, "current_company_id", None)
        supplier_map = _build_supplier_map(db, company_id)
        tz_map       = _build_tz_map(db)

        counters  = {}   # code2 -> current max seq
        inserted  = 0
        skipped   = 0
        err_msgs  = []

        try:
            for i, row in enumerate(data["rows"]):
                parsed = _validate_row(row, mapping, supplier_map, tz_map)
                if not parsed["ok"]:
                    skipped += 1
                    err_msgs.append(f"行{i+2}: {' / '.join(parsed['errors'])} → スキップ")
                    continue

                si     = parsed["supplier_info"]
                code2  = str(si["code"]).zfill(2)[:2]

                if code2 not in counters:
                    r = db.execute(
                        "SELECT MAX(code) AS mx FROM mst_items WHERE code LIKE %s AND company_id = %s",
                        (f"{code2}%", company_id),
                    ).fetchone()
                    try:
                        counters[code2] = int(str(r["mx"])[2:]) if r["mx"] else 0
                    except Exception:
                        counters[code2] = 0

                counters[code2] += 1
                new_code = f"{code2}{counters[code2]:03d}"

                db.execute(
                    """
                    INSERT INTO mst_items
                        (company_id, code, name, unit, supplier_id, temp_zone,
                         purchase_unit, inventory_unit, min_purchase_unit,
                         is_internal, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                    """,
                    (
                        company_id,
                        new_code,
                        parsed["name"],
                        parsed["unit"],
                        si["id"],
                        parsed["temp_zone_code"],
                        parsed["purchase_unit"],
                        parsed["inventory_unit"],
                        parsed["min_purchase_unit"],
                        parsed["is_internal"],
                    ),
                )
                inserted += 1

            db.commit()

        except Exception as e:
            db.rollback()
            flash(f"インポート中にエラーが発生しました: {e}")
            return redirect(url_for("items_master"))

        # Cleanup
        try:
            os.remove(temp_path)
        except Exception:
            pass
        session.pop("csv_temp_id", None)
        session.pop("csv_mapping", None)

        flash(f"✅ {inserted}件をインポートしました。{skipped}件はスキップしました。")
        for msg in err_msgs[:5]:
            flash(f"⚠️ {msg}")

        return redirect(url_for("items_master"))
