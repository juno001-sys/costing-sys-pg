# views/pur_delivery_paste.py
# Paste-and-parse delivery note (納品書) from タノム-style email → purchase records
# Also: CSV upload path for the シーニュ/尾家産業-style multi-invoice CSV export.

import csv
import io
import json
import re
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, g, jsonify


# CMS canonical fields for CSV imports. Admin-configured profiles map
# each CSV's real header text to one of these.
CMS_FIELDS = [
    ("invoice_no",     "伝票番号",    True),   # required
    ("supplier_name",  "仕入先名",    True),
    ("delivery_place", "納品場所",    False),
    ("invoice_date",   "伝票日付",    False),
    ("delivery_date",  "納品日",      True),
    ("item_name",      "商品名",      True),
    ("unit_price",     "単価",        True),
    ("quantity",       "数量",        True),
    ("unit",           "単位",        False),
    ("line_amount",    "金額（計）",  False),
    ("item_code",      "商品コード",  False),
]
CMS_FIELD_KEYS = [k for k, _, _ in CMS_FIELDS]
CMS_FIELD_LABEL = {k: label for k, label, _ in CMS_FIELDS}
CMS_FIELD_REQUIRED = {k: req for k, _, req in CMS_FIELDS}

# Fallback hardcoded mapping — used ONLY if csv_import_profiles is empty
# or missing (pre-migration environments). Matches the original シーニュ-style
# column layout.
_FALLBACK_CSV_COL = {
    "invoice_no":     0,
    "supplier_name":  1,
    "delivery_place": 6,
    "invoice_date":   7,
    "delivery_date":  10,
    "item_name":      29,
    "unit_price":     35,
    "quantity":       37,
    "unit":           38,
    "line_amount":    39,
    "item_code":      40,
}


def _normalize_name(s):
    """Normalize a name for fuzzy matching: full-width → half-width ASCII,
    strip whitespace (incl. 全角), lowercase alphanumerics."""
    if not s:
        return ""
    out = []
    for ch in s:
        code = ord(ch)
        if 0xFF10 <= code <= 0xFF19:       # ０-９
            out.append(chr(code - 0xFEE0))
        elif 0xFF21 <= code <= 0xFF3A:     # Ａ-Ｚ
            out.append(chr(code - 0xFEE0).lower())
        elif 0xFF41 <= code <= 0xFF5A:     # ａ-ｚ
            out.append(chr(code - 0xFEE0))
        elif ch in (" ", "　", "\t"):
            continue
        else:
            out.append(ch.lower() if ch.isascii() else ch)
    return "".join(out)


def _fuzzy_match_supplier(csv_name, suppliers):
    """Match a CSV supplier name to a master supplier row. None if no match."""
    n = _normalize_name(csv_name)
    if not n:
        return None
    for s in suppliers:
        if _normalize_name(s["name"]) == n:
            return s
    for s in suppliers:
        sn = _normalize_name(s["name"])
        if sn and (sn in n or n in sn):
            return s
    return None


def _load_store_aliases(db, company_id):
    """Fetch {normalized_alias: store_id} for the given company. Cached on g
    per request. Returns empty dict on missing table (pre-migration)."""
    cache_attr = f"_store_aliases_{company_id}"
    cached = getattr(g, cache_attr, None)
    if cached is not None:
        return cached
    try:
        rows = db.execute(
            """
            SELECT normalized_alias, store_id
            FROM mst_store_aliases
            WHERE company_id = %s
            """,
            (company_id,),
        ).fetchall()
        result = {r["normalized_alias"]: r["store_id"] for r in rows}
    except Exception:
        try: db.connection.rollback()
        except Exception: pass
        result = {}
    setattr(g, cache_attr, result)
    return result


def _fuzzy_match_store(csv_place, stores, company_id, db=None):
    """Match a CSV 納品場所 to a store.
    1) Normalized-exact against mst_store_aliases (DB).
    2) Substring match on store.name.
    """
    n = _normalize_name(csv_place)
    if not n:
        return None
    if db is not None and company_id:
        alias_map = _load_store_aliases(db, company_id)
        if n in alias_map:
            sid = alias_map[n]
            return next((s for s in stores if s["id"] == sid), None)
    for s in stores:
        sn = _normalize_name(s["name"])
        if sn and (sn in n or n in sn):
            return s
    return None


def _load_csv_profiles(db, company_id):
    """Return active CSV import profiles for the company.

    Returns list of dicts: {id, name, encoding, description, mappings}
    where mappings is {cms_field: csv_header_text}. Global (company_id
    NULL) profiles are included as shared fallbacks. Empty list if the
    table is missing."""
    cache_attr = f"_csv_profiles_{company_id}"
    cached = getattr(g, cache_attr, None)
    if cached is not None:
        return cached
    try:
        prof_rows = db.execute(
            """
            SELECT id, company_id, name, description, encoding
            FROM csv_import_profiles
            WHERE is_active = 1
              AND (company_id = %s OR company_id IS NULL)
            ORDER BY company_id NULLS LAST, name
            """,
            (company_id,),
        ).fetchall()
        profiles = [dict(r) for r in prof_rows]
        if profiles:
            ids = [p["id"] for p in profiles]
            map_rows = db.execute(
                """
                SELECT profile_id, cms_field, csv_header_text
                FROM csv_import_mappings
                WHERE profile_id = ANY(%s)
                """,
                (ids,),
            ).fetchall()
            map_by_profile = {pid: {} for pid in ids}
            for r in map_rows:
                map_by_profile[r["profile_id"]][r["cms_field"]] = r["csv_header_text"]
            for p in profiles:
                p["mappings"] = map_by_profile.get(p["id"], {})
        result = profiles
    except Exception:
        try: db.connection.rollback()
        except Exception: pass
        result = []
    setattr(g, cache_attr, result)
    return result


def _detect_csv_profile(header_row, profiles):
    """Pick the profile whose csv_header_text values best overlap with the
    uploaded CSV's header row.

    Returns (profile_dict, col_index_map) where col_index_map is
    {cms_field: column_index} built by looking up each mapped header in
    header_row. Score = matched required fields. Falls back to the top
    scorer if no profile fully matches; returns (None, None) if nothing
    scored above 0.
    """
    header_norm = [(h or "").strip() for h in header_row]
    best = None
    best_score = -1
    for p in profiles:
        col_map = {}
        matched = 0
        matched_required = 0
        for cms_field, header_text in p["mappings"].items():
            try:
                idx = header_norm.index(header_text)
            except ValueError:
                continue
            col_map[cms_field] = idx
            matched += 1
            if CMS_FIELD_REQUIRED.get(cms_field):
                matched_required += 1
        # Score: weight required fields heavily so a profile missing a
        # required column can't beat one that has all of them.
        score = matched_required * 100 + matched
        if score > best_score:
            best_score = score
            best = (p, col_map)
    if best and best_score > 0:
        return best
    return (None, None)


def _parse_csv_date(raw):
    """Accept 'YYYY/MM/DD' or 'YYYY-MM-DD'. Return ISO 'YYYY-MM-DD' or ''."""
    if not raw:
        return ""
    raw = raw.strip()
    m = re.match(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})$", raw)
    if not m:
        return ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _parse_int(raw, default=0):
    if raw is None or raw == "":
        return default
    try:
        return int(str(raw).replace(",", "").strip())
    except ValueError:
        return default


def init_delivery_paste_views(app, get_db):

    # ── GET: show paste screen ────────────────────────────────────────────────
    @app.route("/pur/delivery_paste", methods=["GET"], endpoint="delivery_paste")
    def delivery_paste():
        db         = get_db()
        company_id = getattr(g, "current_company_id", None)

        # All active items (for client-side matching dropdown)
        items = db.execute(
            """
            SELECT i.id, i.code, i.name,
                   s.name AS supplier_name, s.id AS supplier_id
            FROM mst_items i
            JOIN pur_suppliers s ON i.supplier_id = s.id
            WHERE i.is_active = 1
              AND i.company_id = %s
            ORDER BY i.name
            """,
            (company_id,),
        ).fetchall()

        from utils.access_scope import get_accessible_stores
        stores = get_accessible_stores()

        suppliers = db.execute(
            "SELECT id, name, code FROM pur_suppliers "
            "WHERE is_active = 1 AND company_id = %s ORDER BY code",
            (company_id,),
        ).fetchall()

        items_list = [
            {
                "id":            r["id"],
                "code":          r["code"],
                "name":          r["name"],
                "supplier_name": r["supplier_name"],
                "supplier_id":   r["supplier_id"],
            }
            for r in items
        ]

        return render_template(
            "pur/delivery_paste.html",
            items_json=json.dumps(items_list, ensure_ascii=False),
            stores=stores,
            suppliers=suppliers,
        )

    # ── POST: parse uploaded CSV (Shift-JIS / cp932) → JSON by invoice ──────
    @app.route("/pur/delivery_paste/csv_upload", methods=["POST"],
               endpoint="delivery_paste_csv_upload")
    def delivery_paste_csv_upload():
        db         = get_db()
        company_id = getattr(g, "current_company_id", None)
        if not company_id:
            return jsonify({"error": "No company context"}), 400

        f = request.files.get("file")
        if not f:
            return jsonify({"error": "ファイルが選択されていません。"}), 400

        # Decode: シーニュ/尾家産業 CSVs are cp932 (Shift-JIS + MS extensions).
        raw = f.read()
        try:
            text = raw.decode("cp932")
        except UnicodeDecodeError:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                return jsonify({"error": "文字コードを判定できませんでした（Shift-JIS または UTF-8 のみ対応）。"}), 400

        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if len(rows) < 2:
            return jsonify({"error": "CSVが空、またはヘッダー行のみです。"}), 400

        # Detect which admin-configured profile matches this CSV's header row.
        header = rows[0]
        profiles = _load_csv_profiles(db, company_id)
        if not profiles:
            # Pre-migration fallback: the original hardcoded column map.
            col_map = dict(_FALLBACK_CSV_COL)
            profile_name = "(組込み既定)"
        else:
            profile, col_map = _detect_csv_profile(header, profiles)
            if not profile:
                # No profile matched. Tell the user which profiles exist
                # so they can edit /admin/csv-profiles to add the new headers.
                return jsonify({
                    "error": "どのCSVプロファイルにも一致しませんでした。",
                    "detail": "「CSV取込プロファイル管理」画面で、このCSVの列名を登録してください。",
                    "header_row": header,
                    "available_profiles": [p["name"] for p in profiles],
                }), 400
            # Required-field completeness check.
            missing_required = [
                f for f in CMS_FIELD_KEYS
                if CMS_FIELD_REQUIRED.get(f) and f not in col_map
            ]
            if missing_required:
                labels = [CMS_FIELD_LABEL[f] for f in missing_required]
                return jsonify({
                    "error": f"プロファイル『{profile['name']}』で必須列が不足しています：{', '.join(labels)}",
                    "detail": "「CSV取込プロファイル管理」画面でマッピングを修正してください。",
                }), 400
            profile_name = profile["name"]

        # Load master lists for fuzzy matching
        suppliers = db.execute(
            "SELECT id, code, name FROM pur_suppliers "
            "WHERE is_active = 1 AND company_id = %s ORDER BY code",
            (company_id,),
        ).fetchall()
        suppliers_list = [dict(r) for r in suppliers]

        from utils.access_scope import get_accessible_stores
        stores = get_accessible_stores()
        stores_list = [dict(r) for r in stores]

        def col(row, field):
            idx = col_map.get(field)
            if idx is None or idx >= len(row):
                return ""
            return row[idx]

        # Group rows by 伝票NO. — when invoice_no column is blank, the row
        # belongs to the previous invoice. When non-blank, it starts a new one.
        invoices = []
        current = None
        max_col = max(col_map.values()) if col_map else 0
        for row in rows[1:]:
            if len(row) <= max_col:
                continue
            inv_no = (col(row, "invoice_no") or "").strip()
            if inv_no:
                if current:
                    invoices.append(current)
                csv_supplier = (col(row, "supplier_name") or "").strip()
                csv_place    = (col(row, "delivery_place") or "").strip()
                delivery     = _parse_csv_date(col(row, "delivery_date"))
                matched_supplier = _fuzzy_match_supplier(csv_supplier, suppliers_list)
                matched_store    = _fuzzy_match_store(csv_place, stores_list, company_id, db=db)
                current = {
                    "invoice_no":      inv_no,
                    "csv_supplier":    csv_supplier,
                    "csv_place":       csv_place,
                    "invoice_date":    _parse_csv_date(col(row, "invoice_date")),
                    "delivery_date":   delivery,
                    "supplier_id":     matched_supplier["id"] if matched_supplier else None,
                    "supplier_name":   matched_supplier["name"] if matched_supplier else None,
                    "store_id":        matched_store["id"] if matched_store else None,
                    "store_name":      matched_store["name"] if matched_store else None,
                    "items":           [],
                    "existing_count":  0,
                }
            if current is None:
                continue
            name = (col(row, "item_name") or "").strip()
            if not name:
                continue
            current["items"].append({
                "item_name":    name,
                "item_code":    (col(row, "item_code") or "").strip(),
                "unit":         (col(row, "unit") or "").strip(),
                "unit_price":   _parse_int(col(row, "unit_price")),
                "quantity":     _parse_int(col(row, "quantity")),
                "line_amount":  _parse_int(col(row, "line_amount")),
            })
        if current:
            invoices.append(current)

        # Duplicate check per invoice: any existing purchase for
        # (store, supplier, delivery_date) means "already saved".
        for inv in invoices:
            if inv["store_id"] and inv["supplier_id"] and inv["delivery_date"]:
                dup = db.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM purchases
                    WHERE store_id = %s AND supplier_id = %s
                      AND delivery_date = %s AND is_deleted = 0
                    """,
                    (inv["store_id"], inv["supplier_id"], inv["delivery_date"]),
                ).fetchone()
                inv["existing_count"] = int(dup["n"] or 0) if dup else 0

        return jsonify({
            "invoices":      invoices,
            "suppliers":     suppliers_list,
            "stores":        stores_list,
            "profile_name":  profile_name,
        })

    # ── POST: save parsed rows as purchase records ───────────────────────────
    @app.route("/pur/delivery_paste/save", methods=["POST"], endpoint="delivery_paste_save")
    def delivery_paste_save():
        db         = get_db()
        company_id = getattr(g, "current_company_id", None)

        store_id      = request.form.get("store_id") or None
        supplier_id   = request.form.get("supplier_id")
        delivery_date = request.form.get("delivery_date")
        rows_json     = request.form.get("rows_json", "[]")

        if not supplier_id or not delivery_date:
            flash("仕入先と納品日は必須です。")
            return redirect(url_for("delivery_paste"))

        try:
            rows = json.loads(rows_json)
        except Exception:
            flash("データの読み込みに失敗しました。")
            return redirect(url_for("delivery_paste"))

        if not rows:
            flash("保存するデータがありません。品目マスタを選択してください。")
            return redirect(url_for("delivery_paste"))

        inserted = 0
        skipped  = 0

        try:
            for row in rows:
                item_id    = row.get("item_id")
                quantity   = row.get("quantity", 0)
                unit_price = row.get("unit_price", 0)

                if not item_id or not quantity:
                    skipped += 1
                    continue

                amount = int(quantity) * int(unit_price)

                db.execute(
                    """
                    INSERT INTO purchases
                        (store_id, supplier_id, item_id,
                         delivery_date, quantity, unit_price, amount, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        store_id,
                        int(supplier_id),
                        int(item_id),
                        delivery_date,
                        int(quantity),
                        int(unit_price),
                        amount,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                inserted += 1

            db.commit()

        except Exception as e:
            db.rollback()
            flash(f"保存中にエラーが発生しました: {e}")
            return redirect(url_for("delivery_paste"))

        if inserted == 0:
            flash("⚠️ 保存できる行がありませんでした。品目マスタの選択を確認してください。")
        else:
            flash(f"✅ {inserted}件の仕入れ記録を登録しました。"
                  + (f"（{skipped}件スキップ）" if skipped else ""))

        return redirect(url_for("new_purchase"))
