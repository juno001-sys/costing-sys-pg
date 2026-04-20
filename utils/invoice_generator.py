"""
Monthly invoice generator.

For each company with an active contract:
  - If the contract is in trial (trial_ends_at >= period_end), skip
  - If an invoice already exists for the period, skip (idempotent)
  - Otherwise insert a draft+issued invoice with NET30 due date

Amount = monthly_fee × number of active stores in the company for the period.

Run via cron (recommended) or via the manual button at
/admin/system/invoices/generate-month.

Usage as standalone:
    DATABASE_URL=... python3 -c "from db import get_db; from utils.invoice_generator import generate_monthly_invoices; ..."
or via the Flask CLI / app context.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Tuple


def _month_period(year: int, month: int) -> Tuple[date, date]:
    """Return (period_start, period_end_inclusive) for a given month."""
    period_start = date(year, month, 1)
    if month == 12:
        period_end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        period_end = date(year, month + 1, 1) - timedelta(days=1)
    return period_start, period_end


def generate_monthly_invoices(db, year: int, month: int) -> Tuple[int, int]:
    """Generate invoices for the given month for all eligible companies.

    Returns (created_count, skipped_count).
    """
    period_start, period_end = _month_period(year, month)

    # Eligible contracts:
    #   - effective on period_start (or earlier)
    #   - effective_to is null OR after period_start
    contracts = db.execute("""
        SELECT id, company_id, tier, monthly_fee, currency,
               payment_method, trial_ends_at
        FROM sys_company_contracts
        WHERE effective_from <= %s
          AND (effective_to IS NULL OR effective_to >= %s)
    """, (period_start, period_start)).fetchall()

    created = 0
    skipped = 0

    for c in contracts:
        company_id = c["company_id"]
        contract_id = c["id"]

        # Skip if invoice already exists for this period (idempotent re-runs)
        existing = db.execute("""
            SELECT 1 FROM sys_company_invoices
            WHERE company_id = %s AND period_start = %s AND period_end = %s
            LIMIT 1
        """, (company_id, period_start, period_end)).fetchone()
        if existing:
            skipped += 1
            continue

        # Skip if entire period is within trial
        trial_end = c.get("trial_ends_at")
        if trial_end and trial_end >= period_end:
            skipped += 1
            continue

        # Count active stores (basis for fee × stores)
        store_count_row = db.execute("""
            SELECT COUNT(*) AS n FROM mst_stores
            WHERE company_id = %s AND COALESCE(is_active, 1) = 1
        """, (company_id,)).fetchone()
        store_count = (store_count_row["n"] if store_count_row else 0) or 1

        monthly_fee = c.get("monthly_fee") or 0
        amount = monthly_fee * store_count

        # NET30 due date
        due_date = period_end + timedelta(days=30)

        db.execute("""
            INSERT INTO sys_company_invoices
              (company_id, contract_id, period_start, period_end,
               amount, currency, status, payment_method, due_date, issued_at)
            VALUES
              (%s, %s, %s, %s, %s, %s, 'issued', %s, %s, now())
        """, (company_id, contract_id, period_start, period_end,
              amount, c.get("currency") or "JPY",
              c.get("payment_method") or "invoice", due_date))
        created += 1

    return created, skipped
