import sqlite3
import csv
from pathlib import Path

INIT_DIR = Path(__file__).resolve().parent
BASE_DIR = INIT_DIR.parent
DB_PATH = BASE_DIR / "costing.sqlite3"
CSV_PATH = INIT_DIR / "251117_品目.csv"  # TSVだけど名前はcsvでOK


def normalize_name(name):
    if not name:
        return ""
    return str(name).strip().replace(" ", "").replace("　", "")


def num_or_none(v):
    if v is None:
        return None
    v = str(v).strip()
    if v == "":
        return None
    try:
        return float(v)
    except:
        return None


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # suppliers 読み込み
    cur.execute("SELECT id, name, code FROM suppliers")
    suppliers = cur.fetchall()

    name_to_info = {}
    for s in suppliers:
        code_raw = s["code"]
        if code_raw is None:
            continue

        code2 = str(code_raw).zfill(2)[:2]
        normalized = normalize_name(s["name"])
        name_to_info[normalized] = {
            "id": s["id"],
            "code2": code2,
            "name": s["name"],
        }

    if not name_to_info:
        print("Suppliers is empty.")
        return

    # 既存 mst_items の最大連番
    counters = {}
    for info in name_to_info.values():
        c2 = info["code2"]
        if c2 in counters:
            continue
        cur.execute(
            "SELECT MAX(code) AS max_code FROM mst_items WHERE code LIKE %s",
            (f"{c2}%",),
        )
        row = cur.fetchone()
        if row["max_code"]:
            try:
                counters[c2] = int(str(row["max_code"])[2:])
            except:
                counters[c2] = 0
        else:
            counters[c2] = 0

    print("[INFO] Existing prefix counters:")
    for c2, n in counters.mst_items():
        print(f"  Supplier {c2} → {n}")

    # TSV 読み込み（重要：delimiter="\t"）
    if not CSV_PATH.exists():
        print(f"CSV not found: {CSV_PATH}")
        return

    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    print(f"[INFO] TSV rows: {len(rows)}")

    conn.execute("BEGIN")
    inserted = 0
    skipped = 0

    for row in rows:
        raw_supplier = row.get("仕入先")
        supplier_name = normalize_name(raw_supplier)

        item_name = (row.get("品名") or "").strip()

        if not supplier_name or not item_name:
            skipped += 1
            continue

        if supplier_name not in name_to_info:
            print(f"[SKIP] Unknown supplier in TSV: {raw_supplier}")
            skipped += 1
            continue

        si = name_to_info[supplier_name]
        supplier_id = si["id"]
        code2 = si["code2"]

        # SSIII → 5桁コード生成
        counters[code2] += 1
        new_code = f"{code2}{counters[code2]:03d}"

        temp_zone = (row.get("保管温度帯") or "").strip()
        purchase_unit = (row.get("仕入れ単位") or "").strip()
        inventory_unit = (row.get("棚卸し単位") or "").strip()

        standard_inventory_unit = num_or_none(row.get("標準棚卸し単位"))
        min_purchase_unit = num_or_none(row.get("最低仕入単位"))
        is_internal = 1 if str(row.get("内製_flg")).strip() == "1" else 0

        unit = purchase_unit
        category = None
        account_title = None
        tax_category = "STANDARD_10"
        department = None

        cur.execute(
            """
            INSERT INTO mst_items
                (supplier_id, code, name, unit, category,
                 account_title, tax_category, department,
                 temp_zone, purchase_unit, inventory_unit,
                 standard_inventory_unit, min_purchase_unit,
                 is_internal, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                supplier_id,
                new_code,
                item_name,
                unit,
                category,
                account_title,
                tax_category,
                department,
                temp_zone,
                purchase_unit,
                inventory_unit,
                standard_inventory_unit,
                min_purchase_unit,
                is_internal,
                1,  # is_active
            ),
        )
        inserted += 1

    conn.commit()
    conn.close()

    print(f"[OK] Inserted {inserted} rows, skipped {skipped} rows")


if __name__ == "__main__":
    main()