"""
Sync PROD → DEV with customer-data filtering.

Use this once you have a real paying customer on PROD: copy only your own
"internal" company data (くらじか自然豊農) to DEV; never copy customer data.

This replaces the unrestricted full-mirror approach in
project_prod_to_dev_sync.md once paying customers are onboarded.

USAGE
=====
    # Dry-run (default — shows what would happen, doesn't change DEV):
    python3 init/sync_internal_data_to_dev.py

    # Apply for real:
    python3 init/sync_internal_data_to_dev.py --apply

URLs are read from environment:
    DATABASE_URL_PROD  (required — source)
    DATABASE_URL_DEV   (required — destination, gets wiped)

SAFETY GUARANTEES
=================
1. Dry-run by default. Must pass --apply to actually mutate DEV.
2. URL sanity check: refuses to run if PROD and DEV URLs are the same.
3. DENY-BY-DEFAULT classification: every PROD table must be explicitly
   classified in TABLE_CONFIG below. New tables on PROD will cause the
   script to fail until they're explicitly classified.
4. Data direction is one-way only (PROD → DEV). Cannot accidentally
   copy DEV → PROD (URLs are not interchangeable in the code).
5. Local dump file (containing real PROD data) is deleted at the end.

CONFIGURING WHO IS "INTERNAL"
=============================
Update the INTERNAL_COMPANY_IDS list below. Default is [1] (Kurajika's own
company). Add IDs for any other companies you consider "internal" — e.g.,
a Kurajika test/demo account.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime


# ─────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────
# Companies whose data is OK to copy to DEV. Customer data NEVER copies.
# This list is the single source of truth for what's "internal vs customer".
INTERNAL_COMPANY_IDS = [1]  # 1 = くらじか自然豊農

# Pre-formatted SQL snippet for "company_id is one of the internal IDs"
INTERNAL_IDS_SQL = "(" + ",".join(str(i) for i in INTERNAL_COMPANY_IDS) + ")"


# ─────────────────────────────────────────────────────────────────────
# TABLE CLASSIFICATION (deny by default)
# ─────────────────────────────────────────────────────────────────────
# Each PROD table must appear here with one of:
#   "all"         → copy every row
#   "skip"        → don't copy (legacy / empty / sessions / logs)
#   <SQL string>  → copy rows where the WHERE clause is true; the placeholder
#                   {ids} is replaced with INTERNAL_IDS_SQL
#
# Views are always recreated by the schema dump and do not appear here.
TABLE_CONFIG: dict[str, str] = {
    # ── Reference / catalog (no PII, global config) ────────────────
    "sys_features":             "all",
    "inv_temp_zone_master":     "all",
    "inv_area_master":          "all",
    "inv_shelf_templates":      "all",
    "mst_temp_groups":          "all",
    "pur_purchase_logs":        "all",  # 0 rows currently anyway

    # ── Direct company_id filter ───────────────────────────────────
    "mst_companies":            "id IN {ids}",
    "mst_stores":               "company_id IN {ids}",
    "mst_items":                "company_id IN {ids}",
    "mst_profit_settings":      "company_id IN {ids}",
    "pur_suppliers":            "company_id IN {ids}",
    # Note on the OR clause: 2297 of ~2336 historical pur_purchases rows on
    # PROD (2026-04-20) have company_id = NULL — they predate the multi-tenant
    # column. Falling back to store_id keeps legitimate internal data; rows
    # linked to a customer's store are still excluded. Same for inv_stock_counts.
    # Long-term fix: backfill company_id on PROD via:
    #   UPDATE pur_purchases p SET company_id = s.company_id
    #     FROM mst_stores s WHERE p.store_id = s.id AND p.company_id IS NULL;
    "pur_purchases":            ("company_id IN {ids} OR "
                                 "(company_id IS NULL AND store_id IN "
                                 "(SELECT id FROM mst_stores WHERE company_id IN {ids}))"),
    "inv_stock_counts":         ("company_id IN {ids} OR "
                                 "(company_id IS NULL AND store_id IN "
                                 "(SELECT id FROM mst_stores WHERE company_id IN {ids}))"),
    "store_holidays":           "company_id IN {ids}",
    "supplier_holidays":        "company_id IN {ids}",

    # User-related: filter by membership in an internal company.
    # sys_users.company_id is legacy single-company; the real authority
    # is sys_user_companies.
    "sys_users":                ("id IN (SELECT user_id FROM sys_user_companies "
                                 "WHERE company_id IN {ids})"),
    "sys_user_companies":       "company_id IN {ids}",
    "sys_user_store_grants":    "company_id IN {ids}",

    # New auth/billing tables (Phase A/B/C)
    "sys_company_contracts":    "company_id IN {ids}",
    "sys_company_features":     "company_id IN {ids}",
    "sys_company_invites":      "company_id IN {ids}",
    "sys_company_invoices":     "company_id IN {ids}",

    # Audit log: include both internal-company events AND system-level
    # events (company_id IS NULL — these are sys-admin actions, no PII).
    "sys_work_logs":            "company_id IN {ids} OR company_id IS NULL",

    # ── Indirect: filter via store_id of internal companies ────────
    "inv_inventory_counts":     ("store_id IN (SELECT id FROM mst_stores "
                                 "WHERE company_id IN {ids})"),
    "inv_item_location_prefs":  ("store_id IN (SELECT id FROM mst_stores "
                                 "WHERE company_id IN {ids})"),
    "inv_item_shelf_map":       ("store_id IN (SELECT id FROM mst_stores "
                                 "WHERE company_id IN {ids})"),
    "inv_store_area_map":       ("store_id IN (SELECT id FROM mst_stores "
                                 "WHERE company_id IN {ids})"),
    "inv_store_shelves":        ("store_id IN (SELECT id FROM mst_stores "
                                 "WHERE company_id IN {ids})"),
    "inv_store_temp_zones":     ("store_id IN (SELECT id FROM mst_stores "
                                 "WHERE company_id IN {ids})"),
    "inventory_item_sort_config": ("store_id IN (SELECT id FROM mst_stores "
                                   "WHERE company_id IN {ids})"),
    "pur_store_suppliers":      ("store_id IN (SELECT id FROM mst_stores "
                                 "WHERE company_id IN {ids})"),

    # ── Indirect: filter via item_id of internal companies ─────────
    "mst_items_est_history":    ("item_id IN (SELECT id FROM mst_items "
                                 "WHERE company_id IN {ids})"),

    # ── Skipped tables (legacy backups, sessions, unused) ──────────
    "_companies_old":           "skip",
    "_items_old":               "skip",
    "_stores_old":              "skip",
    "_temp_groups_old":         "skip",
    "items":                    "skip",   # legacy table preceding mst_items
    "stores":                   "skip",   # legacy table preceding mst_stores
    "store_suppliers":          "skip",   # legacy preceding pur_store_suppliers
    "suppliers_old":            "skip",
    "purchase_logs":            "skip",   # legacy logs
    "purchases__old_table":     "skip",
    "stock_counts__old_table":  "skip",
    "delivery_note_lines":      "skip",   # unused feature
    "delivery_notes":           "skip",   # unused feature
    "sys_sessions":             "skip",   # session tokens — let users re-login
}


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _connect(url: str):
    import psycopg2
    return psycopg2.connect(url)


def _list_base_tables(conn) -> list[str]:
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    return [r[0] for r in cur.fetchall()]


def _classify_or_die(prod_tables: list[str]) -> dict[str, str]:
    """Return {table: strategy} for every PROD table. Refuses to proceed
    if PROD has any table missing from TABLE_CONFIG (deny-by-default)."""
    unknown = [t for t in prod_tables if t not in TABLE_CONFIG]
    if unknown:
        print()
        print("✗ REFUSING TO PROCEED — unclassified tables on PROD:")
        for t in unknown:
            print(f"    {t}")
        print()
        print("Edit init/sync_internal_data_to_dev.py and add each one to")
        print("TABLE_CONFIG with an explicit strategy ('skip', 'all', or a WHERE).")
        sys.exit(1)
    return {t: TABLE_CONFIG[t] for t in prod_tables}


def _row_count(conn, table: str, where: str | None = None) -> int:
    cur = conn.cursor()
    sql = f'SELECT COUNT(*) FROM "{table}"'
    if where:
        sql += f" WHERE {where}"
    cur.execute(sql)
    return cur.fetchone()[0]


def _preview(prod, classification: dict[str, str]) -> tuple[int, int, int]:
    """Print preview of what would be copied. Returns (n_all, n_filtered, n_skip)."""
    n_all = n_filtered = n_skip = 0
    print()
    print(f'{"TABLE":35s} {"STRATEGY":12s} {"PROD ROWS":>10s} → {"WILL COPY":>10s}')
    print("─" * 75)
    for table, strategy in classification.items():
        prod_total = _row_count(prod, table)
        if strategy == "skip":
            print(f"{table:35s} {'skip':12s} {prod_total:>10} → {'(none)':>10}")
            n_skip += 1
        elif strategy == "all":
            print(f"{table:35s} {'all':12s} {prod_total:>10} → {prod_total:>10}")
            n_all += 1
        else:
            where = strategy.format(ids=INTERNAL_IDS_SQL)
            n = _row_count(prod, table, where)
            marker = "✓" if n <= prod_total else "?"
            print(f"{table:35s} {'filter':12s} {prod_total:>10} → {n:>10}  {marker}")
            n_filtered += 1
    return n_all, n_filtered, n_skip


def _dump_schema_only(prod_url: str, dest_path: str):
    """Use pg_dump to get the full PROD schema (no data)."""
    print(f"  Running pg_dump --schema-only → {dest_path}")
    result = subprocess.run(
        ["pg_dump", "--schema-only", "--no-owner", "--no-acl",
         "--no-publications", "--no-subscriptions", prod_url],
        stdout=open(dest_path, "w"),
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        print(f"  pg_dump failed:\n{result.stderr}")
        sys.exit(1)


def _drop_and_apply_schema(dev_url: str, schema_path: str):
    """Drop DEV public schema, then apply PROD schema."""
    print("  Dropping public schema on DEV...")
    subprocess.run(
        ["psql", dev_url, "-c",
         "DROP SCHEMA public CASCADE; "
         "CREATE SCHEMA public; "
         "GRANT ALL ON SCHEMA public TO postgres; "
         "GRANT ALL ON SCHEMA public TO public;"],
        check=True, stdout=subprocess.DEVNULL,
    )
    print("  Applying PROD schema to DEV...")
    with open(schema_path) as f:
        result = subprocess.run(
            ["psql", dev_url, "-v", "ON_ERROR_STOP=0"],
            stdin=f, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
    # We don't fail on errors here — view constraint errors are expected
    err_lines = [l for l in (result.stderr or "").splitlines()
                 if "ERROR" in l or "FATAL" in l]
    if err_lines:
        print(f"  Schema apply produced {len(err_lines)} error(s) (FK/view "
              f"constraints — usually OK):")
        for l in err_lines[:5]:
            print(f"    {l}")


def _copy_table(prod, dev, table: str, strategy: str) -> int:
    """Copy data for one table using the configured strategy. Returns rows copied."""
    if strategy == "skip":
        return 0

    where = ""
    if strategy != "all":
        where = "WHERE " + strategy.format(ids=INTERNAL_IDS_SQL)

    # Use COPY TO/FROM via a server-side cursor for bulk transfer
    src_cur = prod.cursor()
    dst_cur = dev.cursor()

    # Get column list (preserves order; matches PROD schema exactly)
    src_cur.execute(f"""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s
        ORDER BY ordinal_position
    """, (table,))
    cols = [r[0] for r in src_cur.fetchall()]
    col_list = ", ".join(f'"{c}"' for c in cols)

    # Use COPY for efficiency
    import io
    buf = io.StringIO()
    src_cur.copy_expert(
        f'COPY (SELECT {col_list} FROM "{table}" {where}) TO STDOUT WITH CSV',
        buf,
    )
    buf.seek(0)
    n = sum(1 for _ in buf)
    buf.seek(0)
    if n == 0:
        return 0
    dst_cur.copy_expert(
        f'COPY "{table}" ({col_list}) FROM STDIN WITH CSV',
        buf,
    )
    return n


def _reset_sequences(dev):
    """After copy, reset all sequences to MAX(id)+1 so future inserts don't collide."""
    cur = dev.cursor()
    cur.execute("""
        SELECT n.nspname, t.relname, a.attname,
               pg_get_serial_sequence(quote_ident(n.nspname)||'.'||quote_ident(t.relname),
                                      a.attname) AS seq
        FROM pg_class t
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a ON a.attrelid = t.oid
        WHERE t.relkind = 'r' AND n.nspname = 'public'
          AND pg_get_serial_sequence(quote_ident(n.nspname)||'.'||quote_ident(t.relname),
                                     a.attname) IS NOT NULL
    """)
    rows = cur.fetchall()
    print(f"  Resetting {len(rows)} sequence(s)...")
    for nsp, tbl, col, seq in rows:
        cur.execute(f'SELECT setval(%s, COALESCE((SELECT MAX("{col}") FROM "{tbl}"), 1))',
                    (seq,))
    dev.commit()


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="Actually mutate DEV. Without this flag, runs in dry-run mode.")
    args = parser.parse_args()

    prod_url = os.environ.get("DATABASE_URL_PROD")
    dev_url  = os.environ.get("DATABASE_URL_DEV")
    if not prod_url or not dev_url:
        print("✗ Set DATABASE_URL_PROD and DATABASE_URL_DEV environment variables.")
        sys.exit(1)
    if prod_url == dev_url:
        print("✗ DATABASE_URL_PROD and DATABASE_URL_DEV are identical — refusing.")
        sys.exit(1)

    print("=" * 75)
    print(f"PROD → DEV filtered sync   {datetime.now().isoformat(timespec='seconds')}")
    print(f"Internal company IDs (will be copied): {INTERNAL_COMPANY_IDS}")
    print(f"Mode: {'APPLY (will modify DEV)' if args.apply else 'DRY-RUN (no changes)'}")
    print("=" * 75)

    prod = _connect(prod_url)
    dev = _connect(dev_url)

    prod_tables = _list_base_tables(prod)
    classification = _classify_or_die(prod_tables)

    # Print preview
    n_all, n_filt, n_skip = _preview(prod, classification)
    print()
    print(f"Summary: {n_all} reference, {n_filt} filtered, {n_skip} skipped, "
          f"{len(classification)} total tables.")

    if not args.apply:
        print()
        print("DRY-RUN complete. Nothing was changed on DEV.")
        print("Re-run with --apply to actually perform the sync.")
        prod.close(); dev.close()
        sys.exit(0)

    # ─── APPLY mode ───
    print()
    print("─── Step 1: Schema sync ───")
    with tempfile.NamedTemporaryFile(suffix=".sql", delete=False, mode="w") as f:
        schema_path = f.name
    try:
        _dump_schema_only(prod_url, schema_path)
        _drop_and_apply_schema(dev_url, schema_path)
    finally:
        os.unlink(schema_path)
        print("  Schema dump file deleted.")

    # Re-connect (DEV schema was just recreated)
    dev.close()
    dev = _connect(dev_url)

    print()
    print("─── Step 2: Data copy ───")
    # Disable triggers/FK checks during the bulk copy
    dev_cur = dev.cursor()
    dev_cur.execute("SET session_replication_role = 'replica'")

    total_copied = 0
    for table, strategy in classification.items():
        try:
            n = _copy_table(prod, dev, table, strategy)
            total_copied += n
            if n > 0:
                print(f"  {table:35s} {n:>8} rows")
        except Exception as e:
            print(f"  ✗ {table}: {str(e)[:80]}")
            dev.rollback()

    dev_cur.execute("SET session_replication_role = 'origin'")
    dev.commit()
    print(f"  Total rows copied: {total_copied}")

    print()
    print("─── Step 3: Reset sequences ───")
    _reset_sequences(dev)

    print()
    print("─── Step 4: Verification ───")
    print(f"{'TABLE':35s} {'EXPECTED':>10s} {'ACTUAL':>10s}  STATUS")
    print("─" * 70)
    for table, strategy in classification.items():
        if strategy == "skip":
            continue
        if strategy == "all":
            expected = _row_count(prod, table)
        else:
            expected = _row_count(prod, table, strategy.format(ids=INTERNAL_IDS_SQL))
        actual = _row_count(dev, table)
        ok = "✓" if expected == actual else "✗"
        print(f"{table:35s} {expected:>10} {actual:>10}  {ok}")

    prod.close(); dev.close()
    print()
    print("✓ Sync complete.")


if __name__ == "__main__":
    main()
