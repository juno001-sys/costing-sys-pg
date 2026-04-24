# views/pur_delivery_paste.py
# Paste-and-parse delivery note (納品書) from タノム-style email → purchase records
# Also: CSV upload path for the シーニュ/尾家産業-style multi-invoice CSV export.

import csv
import io
import json
import re
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, g, jsonify


# ---------------------------------------------------------------------------
# CSV column indices (0-based) for the シーニュ-style export.
# Header is: [伝票NO.],[取引先],[担当者],[自社担当者],[事業者登録番号],[保存],
#            [納品場所／名],[伝票日付],[状態],[発注日],[納品日],[件名],...,
#            [商品名],[希望単価],[課税区分],[希望数量],[希望単位],
#            [規格・入数／単位],[単価],[税込],[数量],[単位],[計],[商品コード],...
CSV_COL = {
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

# Store-name synonyms known to appear in this CSV format. Normalized via
# _normalize_name(). Per-company alias maps could be added later.
_STORE_ALIASES_BY_COMPANY = {
    1: {  # くらじか自然豊農
        # Keys must be post-_normalize_name() forms — the ASCII letters are
        # half-width lowercase, full-width spaces are stripped.
        "apahotel長野":    1,   # ＡＰＡ　ＨＯＴＥＬ長野 → APA朝食
        "アパホテル仕入れ": 1,
        "アパホテル長野":   1,
    },
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


def _fuzzy_match_store(csv_place, stores, company_id):
    """Match a CSV 納品場所 to a store. First try company-specific alias map,
    then fall back to substring matching on store.name."""
    n = _normalize_name(csv_place)
    if not n:
        return None
    alias_map = _STORE_ALIASES_BY_COMPANY.get(company_id, {})
    if n in alias_map:
        sid = alias_map[n]
        return next((s for s in stores if s["id"] == sid), None)
    for s in stores:
        sn = _normalize_name(s["name"])
        if sn and (sn in n or n in sn):
            return s
    return None


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

        # Sanity-check header (first column should be '[伝票NO.]' or similar)
        header = rows[0]
        if len(header) < len(CSV_COL) or "伝票" not in header[0]:
            return jsonify({"error": "このCSVの列構成に対応していません。"}), 400

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

        # Group rows by 伝票NO. — when col[0] is blank, the row belongs to
        # the previous invoice. When col[0] has a value, it starts a new one.
        invoices = []
        current = None
        for row in rows[1:]:
            if len(row) < len(CSV_COL):
                continue
            inv_no = (row[CSV_COL["invoice_no"]] or "").strip()
            if inv_no:
                # New invoice header
                if current:
                    invoices.append(current)
                csv_supplier = (row[CSV_COL["supplier_name"]] or "").strip()
                csv_place    = (row[CSV_COL["delivery_place"]] or "").strip()
                delivery     = _parse_csv_date(row[CSV_COL["delivery_date"]])
                matched_supplier = _fuzzy_match_supplier(csv_supplier, suppliers_list)
                matched_store    = _fuzzy_match_store(csv_place, stores_list, company_id)
                current = {
                    "invoice_no":      inv_no,
                    "csv_supplier":    csv_supplier,
                    "csv_place":       csv_place,
                    "invoice_date":    _parse_csv_date(row[CSV_COL["invoice_date"]]),
                    "delivery_date":   delivery,
                    "supplier_id":     matched_supplier["id"] if matched_supplier else None,
                    "supplier_name":   matched_supplier["name"] if matched_supplier else None,
                    "store_id":        matched_store["id"] if matched_store else None,
                    "store_name":      matched_store["name"] if matched_store else None,
                    "items":           [],
                    "existing_count":  0,
                }
            if current is None:
                # Stray row before any 伝票NO. — skip defensively.
                continue
            # Line item row (first line of invoice OR continuation)
            name = (row[CSV_COL["item_name"]] or "").strip()
            if not name:
                continue
            current["items"].append({
                "item_name":    name,
                "item_code":    (row[CSV_COL["item_code"]] or "").strip(),
                "unit":         (row[CSV_COL["unit"]] or "").strip(),
                "unit_price":   _parse_int(row[CSV_COL["unit_price"]]),
                "quantity":     _parse_int(row[CSV_COL["quantity"]]),
                "line_amount":  _parse_int(row[CSV_COL["line_amount"]]),
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
            "invoices":  invoices,
            "suppliers": suppliers_list,
            "stores":    stores_list,
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
