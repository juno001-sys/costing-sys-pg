"""
Recalibrate mst_items.est_order_qty using daily breakfast-count statistics.

Formula:
    est_order_qty[item] = round(per_guest_rate × (μ + 1.65 × σ))

Where:
    per_guest_rate = total_item_qty_in_period ÷ total_breakfasts_in_same_period
                     (despite the name, the driver is BF計 / breakfasts, not total guests —
                      we kept the column name for flexibility; future per-category drivers
                      can repurpose it.)
    μ              = mean of L-day rolling consumption (L = supplier delivery cycle days)
    σ              = stdev of same series
    1.65σ          = one-sided 95% safety coverage

Low-frequency items (purchased on <20% of days in the period) get smoothed:
    - 30-day rolling average on consumption before computing σ
    - σ capped at μ to prevent bulk-order noise from inflating the estimate

Usage:
    DATABASE_URL_DEV=postgres://... python init/recalc_est_order_qty.py --dry-run
    DATABASE_URL_DEV=postgres://... python init/recalc_est_order_qty.py --apply

Outputs:
    init/recalc_report.csv  (always)
    Updates mst_items       (only with --apply)

Written: 2026-04-17
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import date, datetime, timedelta
from statistics import mean, pstdev
from typing import Optional

import psycopg2
import psycopg2.extras


# ─── tunable parameters ──────────────────────────────────────────────────────
Z_SAFETY = 1.65          # 95% one-sided coverage
FREQ_THRESHOLD = 0.20    # below this, smooth + cap σ at μ
SMOOTH_WINDOW = 30       # days (rolling average for low-frequency items)
MIN_DATA_POINTS = 10     # skip items with fewer purchase records than this
DEFAULT_OCCUPANCY_CSV = (
    "/Users/junokurashima/Downloads/2512_原価計算CMS開発RdMap/"
    "2512_原価計算CMS開発RdMap/喫食数日別-Table 1-1-1.csv"
)


# ─── occupancy loader ────────────────────────────────────────────────────────
def _clean_num(s: str) -> int:
    """Strip spaces and thousand-separator commas. Returns 0 for blank."""
    s = re.sub(r"[\s,]", "", s or "")
    return int(s) if s else 0


def load_occupancy(path: str) -> dict[date, dict]:
    """
    Parse the comma-separated daily-stats CSV. Uses BF計 (last column) as the demand driver.
    Returns {date: {rooms, guests, breakfasts}}.
    """
    result: dict[date, dict] = {}
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            return result
        # Index the columns we need
        try:
            i_year = header.index("西暦")
            i_month = header.index("月")
            i_day = header.index("日")
            i_rooms = header.index("販売室数")
            i_guests = header.index("販売人数")
            i_bf = header.index("BF計")
        except ValueError as e:
            sys.exit(f"Expected header column missing: {e}")

        for line_no, row in enumerate(reader, 2):
            if not row or len(row) <= i_bf:
                continue
            try:
                year = int(row[i_year])
                month = int(row[i_month])
                day = int(row[i_day])
                bf = _clean_num(row[i_bf])
                guests = _clean_num(row[i_guests])
                rooms = _clean_num(row[i_rooms])
                if bf == 0 and guests == 0:
                    continue  # blank / closed day
                d = date(year, month, day)
                result[d] = {"rooms": rooms, "guests": guests, "breakfasts": bf}
            except (ValueError, IndexError) as e:
                print(f"[warn] skipping line {line_no}: {e}", file=sys.stderr)
    return result


# ─── DB helpers ──────────────────────────────────────────────────────────────
def connect(url: str):
    conn = psycopg2.connect(url)
    conn.autocommit = False
    return conn


def delivery_cycle_days(schedule: dict | None) -> int:
    """
    Average days between deliveries, derived from supplier's delivery_schedule JSON.
    e.g., {mon: {...}, wed: {...}, fri: {...}} → 3 deliveries/week → 7/3 ≈ 2.33 days
    Fallback: 7 days if schedule empty.
    """
    if not schedule:
        return 7
    n = len(schedule)
    return max(1, round(7 / n))


# ─── core calculation ────────────────────────────────────────────────────────
def compute_item(
    purchases: list[tuple[date, float]],
    occupancy: dict[date, dict],
    cycle_days: int,
) -> dict | None:
    """
    Returns dict with per_guest_rate, mu, sigma, est_order_qty, frequency, data_points.
    Returns None if insufficient data.
    """
    if len(purchases) < MIN_DATA_POINTS:
        return None

    purchase_dates = sorted({p[0] for p in purchases})
    first_day = purchase_dates[0]
    last_day = purchase_dates[-1]
    span_days = (last_day - first_day).days + 1
    if span_days < cycle_days * 2:
        return None

    # overlap with occupancy — use BF計 (breakfasts) as the demand driver
    overlap_bf = [
        occupancy[d]["breakfasts"]
        for d in (first_day + timedelta(n) for n in range(span_days))
        if d in occupancy and occupancy[d]["breakfasts"] > 0
    ]
    if len(overlap_bf) < MIN_DATA_POINTS:
        return None

    total_qty = sum(q for _, q in purchases)
    total_bf_in_span = sum(overlap_bf)
    if total_bf_in_span == 0:
        return None

    per_guest_rate = total_qty / total_bf_in_span   # units per breakfast served
    frequency = len(purchase_dates) / span_days

    # Build a daily consumption proxy aligned to dates where we have BF data
    daily_consumption = [per_guest_rate * bf for bf in overlap_bf]

    # Low-frequency smoothing
    if frequency < FREQ_THRESHOLD:
        smoothed = []
        for i in range(len(daily_consumption)):
            lo = max(0, i - SMOOTH_WINDOW // 2)
            hi = min(len(daily_consumption), i + SMOOTH_WINDOW // 2 + 1)
            smoothed.append(mean(daily_consumption[lo:hi]))
        daily_consumption = smoothed

    # L-day rolling sum (cycle consumption)
    L = cycle_days
    cycle_sums = [
        sum(daily_consumption[i : i + L])
        for i in range(len(daily_consumption) - L + 1)
    ]
    if len(cycle_sums) < 2:
        return None

    mu = mean(cycle_sums)
    sigma = pstdev(cycle_sums)

    # Cap σ for low-frequency items
    if frequency < FREQ_THRESHOLD and sigma > mu:
        sigma = mu

    # Floor at 1 — an item with enough purchase history to be computed should never
    # round down to 0 order quantity.
    est_qty = max(1, round(mu + Z_SAFETY * sigma))

    return {
        "per_guest_rate": per_guest_rate,
        "mu": mu,
        "sigma": sigma,
        "est_order_qty": est_qty,
        "frequency": frequency,
        "data_points": len(purchase_dates),
        "span_days": span_days,
        "cycle_days": cycle_days,
    }


# ─── main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write new values to DB")
    ap.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    ap.add_argument("--occupancy", default=DEFAULT_OCCUPANCY_CSV)
    ap.add_argument("--report", default="init/recalc_report.csv")
    args = ap.parse_args()

    if not args.apply and not args.dry_run:
        ap.error("Specify --dry-run or --apply")

    db_url = os.environ.get("DATABASE_URL_DEV") or os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("DATABASE_URL_DEV (or DATABASE_URL) not set")

    print(f"[info] loading occupancy from {args.occupancy}")
    occupancy = load_occupancy(args.occupancy)
    if not occupancy:
        sys.exit(f"No occupancy rows parsed from {args.occupancy}")
    print(f"[info] {len(occupancy)} occupancy rows loaded "
          f"({min(occupancy)} → {max(occupancy)})")

    conn = connect(db_url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT i.id, i.code, i.name, i.supplier_id, i.est_order_qty,
                       s.delivery_schedule, s.name AS supplier_name
                FROM mst_items i
                LEFT JOIN pur_suppliers s ON s.id = i.supplier_id
                WHERE i.is_active = 1
                ORDER BY i.code
            """)
            items = cur.fetchall()

            rows = []
            for item in items:
                cur.execute(
                    """
                    SELECT delivery_date, quantity
                    FROM purchases
                    WHERE item_id = %s AND is_deleted = 0
                    ORDER BY delivery_date
                    """,
                    (item["id"],),
                )
                purchases = [(r["delivery_date"], float(r["quantity"])) for r in cur.fetchall()]

                L = delivery_cycle_days(item["delivery_schedule"])
                result = compute_item(purchases, occupancy, L)

                purchase_days = len({p[0] for p in purchases})
                row = {
                    "id": item["id"],
                    "code": item["code"],
                    "name": item["name"],
                    "supplier": item["supplier_name"] or "",
                    "old_est": item["est_order_qty"],
                    "new_est": result["est_order_qty"] if result else None,
                    "per_guest_rate": f"{result['per_guest_rate']:.5f}" if result else "",
                    "mu": f"{result['mu']:.3f}" if result else "",
                    "sigma": f"{result['sigma']:.3f}" if result else "",
                    "frequency": f"{result['frequency']:.3f}" if result else "",
                    "cycle_days": result["cycle_days"] if result else "",
                    "data_points": result["data_points"] if result else purchase_days,
                    "status": "computed" if result else (
                        "skipped (no purchases)" if purchase_days == 0
                        else f"skipped ({purchase_days} purchase days, need {MIN_DATA_POINTS}+)"
                    ),
                }
                if result and item["est_order_qty"]:
                    row["pct_change"] = (
                        (result["est_order_qty"] - item["est_order_qty"])
                        / max(1, item["est_order_qty"]) * 100
                    )
                    row["pct_change"] = f"{row['pct_change']:+.1f}%"
                else:
                    row["pct_change"] = ""
                rows.append(row)

            # ─── write report ────────────────────────────────────────────────
            fieldnames = [
                "id", "code", "name", "supplier",
                "old_est", "new_est", "pct_change",
                "per_guest_rate", "mu", "sigma",
                "frequency", "cycle_days", "data_points", "status",
            ]
            with open(args.report, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)
            print(f"[info] report written to {args.report}")

            n_computed = sum(1 for r in rows if r["new_est"] is not None)
            print(f"[info] {n_computed}/{len(rows)} items computed")

            if args.apply:
                updated = 0
                for r in rows:
                    if r["new_est"] is None:
                        continue
                    cur.execute(
                        """
                        UPDATE mst_items
                        SET est_order_qty = %s,
                            per_guest_rate = %s,
                            est_mu = %s,
                            est_sigma = %s,
                            est_calc_at = NOW()
                        WHERE id = %s
                        """,
                        (r["new_est"], r["per_guest_rate"], r["mu"], r["sigma"], r["id"]),
                    )
                    updated += 1
                conn.commit()
                print(f"[info] applied updates to {updated} items")
            else:
                print("[info] dry run — no DB writes")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
