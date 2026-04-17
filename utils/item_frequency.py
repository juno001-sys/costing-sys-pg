"""
Item purchase-frequency classifier.

Buckets an item into one of five frequency tiers based on purchase activity in
the last N days (default 90). Used on:
  - Smartphone inventory count screen  (to prioritize counting)
  - Order support spreadsheet view     (sortable column)

Thresholds (purchases per month, over the rolling window):
  - very_high:  ≥ 4.3   (1x/week or more — daily/weekly perishables)
  - high:       2.0 – 4.3   (2x/month to 1x/week — the operator's defined band)
  - low:        0.01 – 2.0  (monthly or less)
  - none:       0 purchases in the window
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List

WINDOW_DAYS = 90
DAYS_PER_MONTH = 30


# Bucket definitions: (code, i18n_key, monthly_rate_lower, monthly_rate_upper)
# Upper bound is exclusive. None = open-ended.
BUCKETS: List[tuple] = [
    ("very_high", "items.frequency.very_high", 4.3, None),
    ("high",      "items.frequency.high",      2.0, 4.3),
    ("low",       "items.frequency.low",       0.01, 2.0),
    ("none",      "items.frequency.none",      0.0, 0.01),
]


def classify(purchase_days_in_window: int, window_days: int = WINDOW_DAYS) -> str:
    """Return bucket code for the given purchase-day count."""
    if purchase_days_in_window <= 0:
        return "none"
    per_month = (purchase_days_in_window / window_days) * DAYS_PER_MONTH
    for code, _, lo, hi in BUCKETS:
        if per_month >= lo and (hi is None or per_month < hi):
            return code
    return "none"


def format_rate(per_month: float) -> str:
    """
    Human-friendly rate label. Chooses 週/月 scale based on magnitude.
      >= 1/week  → '週X回'  (X = per_month / 4.3, rounded to 1 decimal if needed)
      >= 1/month → '月X回'
      > 0        → '月1回未満'
      0 or less  → '—'
    """
    if per_month <= 0:
        return "—"
    per_week = per_month / (DAYS_PER_MONTH / 7)  # per_month * 7/30
    if per_week >= 1:
        # Round nicely: integer when whole, else 1 decimal
        n = round(per_week, 1)
        n_str = str(int(n)) if n == int(n) else f"{n:.1f}"
        return f"週{n_str}回"
    if per_month >= 1:
        n = round(per_month, 1)
        n_str = str(int(n)) if n == int(n) else f"{n:.1f}"
        return f"月{n_str}回"
    return "月1回未満"


def bucket_order(code: str) -> int:
    """Sort order: very_high(0) → high(1) → low(2) → none(3)."""
    for i, (b_code, *_) in enumerate(BUCKETS):
        if b_code == code:
            return i
    return 99


def fetch_item_frequency(db, item_ids: List[int], as_of: date | None = None) -> Dict[int, dict]:
    """
    Compute purchase frequency for the given item_ids over WINDOW_DAYS ending at as_of.

    Returns: {item_id: {'purchase_days': int, 'bucket': str, 'per_month': float}}
    Items with no purchases in the window get bucket='none'.
    """
    if not item_ids:
        return {}
    as_of = as_of or date.today()
    since = as_of - timedelta(days=WINDOW_DAYS)

    rows = db.execute(
        """
        SELECT item_id, COUNT(DISTINCT delivery_date) AS purchase_days
        FROM purchases
        WHERE is_deleted = 0
          AND delivery_date >= %s
          AND delivery_date <= %s
          AND item_id = ANY(%s)
        GROUP BY item_id
        """,
        (since, as_of, item_ids),
    ).fetchall()

    counts = {r["item_id"]: r["purchase_days"] for r in rows}

    result: Dict[int, dict] = {}
    for iid in item_ids:
        days = counts.get(iid, 0)
        per_month = (days / WINDOW_DAYS) * DAYS_PER_MONTH if days else 0.0
        result[iid] = {
            "purchase_days": days,
            "bucket": classify(days),
            "per_month": round(per_month, 2),
            "rate_label": format_rate(per_month),
        }
    return result
