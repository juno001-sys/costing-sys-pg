"""
Tier-based feature gating.

Tiers (cumulative — Premium ⊇ Standard ⊇ Entry):
    entry      -> 1-month free trial,  ¥15,000 / month / store
    standard   -> 3-month free trial,  ¥50,000 / month / store
    premium    -> 3-month free trial, ¥120,000 / month / store

Three concepts coexist:
  1. Feature catalog            (sys_features)
  2. Per-company contract       (sys_company_contracts) — sets the tier baseline
  3. Per-company override map   (sys_company_features) — sys-admin overrides

Effective answer to "is feature X enabled for company Y?" follows this order:
  a. If sys_company_features has an explicit row -> use its `enabled` flag
  b. Otherwise compare the company's current tier to the feature's default_tier
  c. Always-on features (default_tier='always_on') are always enabled
  d. If no contract row exists at all (e.g. brand-new company before sys-admin
     onboarding), default to ENABLED so existing screens keep working — sys
     admin can lock things down once they assign a tier.

This module is import-safe before the migration is applied: every helper
catches missing-table errors and falls back to "enabled".
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from flask import g

from db import get_db


# Tier ordering for "is_at_least" comparisons
TIER_RANK = {
    "always_on": 0,
    "entry":     1,
    "standard":  2,
    "premium":   3,
}

# Default monthly fee per tier (JPY, per store) — sourced from the 2026-04-17 sales flyer
TIER_DEFAULT_FEE_JPY = {
    "entry":    15_000,
    "standard": 50_000,
    "premium": 120_000,
}

# Default trial length in months per tier — also from the flyer
TIER_DEFAULT_TRIAL_MONTHS = {
    "entry":    1,
    "standard": 3,
    "premium":  3,
}


def _safe_query(db, sql: str, params: tuple = ()):
    """Run a query; return [] if the table doesn't exist yet (pre-migration)."""
    try:
        return db.execute(sql, params).fetchall()
    except Exception:
        # Roll back the failed transaction so subsequent queries on this
        # connection still work. Flask-style get_db typically wraps a single
        # request-scoped connection.
        try:
            db.connection.rollback()
        except Exception:
            pass
        return []


def _load_company_bundle(company_id: int) -> dict:
    """Fetch contract + feature catalog + per-company overrides in ONE request.

    Cached on flask.g so a single page render only pays the DB cost once,
    no matter how many feature_enabled() calls the nav / template makes.
    """
    cache_attr = f"_fg_bundle_{company_id}"
    cached = getattr(g, cache_attr, None)
    if cached is not None:
        return cached

    db = get_db()

    contract_rows = _safe_query(db, """
        SELECT id, company_id, tier, effective_from, effective_to,
               trial_ends_at, monthly_fee, currency, payment_method
        FROM sys_company_contracts
        WHERE company_id = %s
          AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)
        ORDER BY effective_from DESC, id DESC
        LIMIT 1
    """, (company_id,))
    contract = contract_rows[0] if contract_rows else None

    feat_rows = _safe_query(db, """
        SELECT feature_key, default_tier
        FROM sys_features
        WHERE is_active = 1
    """)
    feature_tier = {r["feature_key"]: (r.get("default_tier") or "always_on") for r in feat_rows}

    override_rows = _safe_query(db, """
        SELECT feature_key, enabled
        FROM sys_company_features
        WHERE company_id = %s
    """, (company_id,))
    overrides = {r["feature_key"]: bool(r.get("enabled")) for r in override_rows}

    bundle = {
        "contract": contract,
        "feature_tier": feature_tier,   # catalog-wide tier-by-key
        "overrides": overrides,         # per-company explicit flags
    }
    setattr(g, cache_attr, bundle)
    return bundle


def get_current_contract(company_id: int) -> Optional[dict]:
    """Return the currently-active contract row, or None."""
    if not company_id:
        return None
    return _load_company_bundle(company_id)["contract"]


def get_current_tier(company_id: int) -> str:
    """Return the company's current tier, or 'premium' as a permissive default
    when no contract exists yet (so newly-created companies are not locked out
    before sys admin can configure them)."""
    contract = get_current_contract(company_id)
    if contract is None:
        return "premium"  # permissive default — see module docstring
    return contract.get("tier") or "premium"


def feature_enabled(feature_key: str, company_id: Optional[int] = None) -> bool:
    """Single source of truth for 'can this company use this feature?'."""
    if company_id is None:
        company_id = getattr(g, "current_company_id", None)
    if not company_id:
        return True  # no company context — let it through; callers handle auth separately

    bundle = _load_company_bundle(company_id)

    # 1. Explicit per-company override wins
    if feature_key in bundle["overrides"]:
        return bundle["overrides"][feature_key]

    # 2. Tier-based default from catalog
    required_tier = bundle["feature_tier"].get(feature_key)
    if required_tier is None:
        # Feature not in catalog -> assume always-on (safe default for unmigrated DB)
        return True
    if required_tier == "always_on":
        return True

    company_tier = get_current_tier(company_id)
    return TIER_RANK.get(company_tier, 0) >= TIER_RANK.get(required_tier, 0)


def get_company_feature_map(company_id: int) -> dict:
    """Return {feature_key: {'enabled': bool, 'source': str, 'name_ja': str,
    'name_en': str, 'default_tier': str}} for sys-admin UI rendering.

    Result includes ALL catalog features, with effective state computed.
    """
    if not company_id:
        return {}

    db = get_db()

    catalog = _safe_query(db, """
        SELECT feature_key, name_ja, name_en, default_tier, sort_order
        FROM sys_features
        WHERE is_active = 1
        ORDER BY sort_order, feature_key
    """)
    if not catalog:
        return {}

    overrides = _safe_query(db, """
        SELECT feature_key, enabled, source
        FROM sys_company_features
        WHERE company_id = %s
    """, (company_id,))
    override_map = {r["feature_key"]: r for r in overrides}

    company_tier = get_current_tier(company_id)
    company_rank = TIER_RANK.get(company_tier, 0)

    result = {}
    for feat in catalog:
        key = feat["feature_key"]
        ov = override_map.get(key)
        if ov is not None:
            enabled = bool(ov["enabled"])
            source = ov["source"]
        else:
            required_rank = TIER_RANK.get(feat["default_tier"], 0)
            enabled = company_rank >= required_rank
            source = "tier_default"

        result[key] = {
            "feature_key": key,
            "name_ja": feat["name_ja"],
            "name_en": feat["name_en"],
            "default_tier": feat["default_tier"],
            "enabled": enabled,
            "source": source,
            "sort_order": feat["sort_order"],
        }
    return result


def is_feature_in_contract_tier(feature_key: str, tier: str) -> bool:
    """Check (without DB) whether a given tier includes the feature by default."""
    db = get_db()
    rows = _safe_query(db, """
        SELECT default_tier FROM sys_features
        WHERE feature_key = %s LIMIT 1
    """, (feature_key,))
    if not rows:
        return True
    required = rows[0].get("default_tier") or "always_on"
    return TIER_RANK.get(tier, 0) >= TIER_RANK.get(required, 0)


def get_lifecycle_state(company_id: int) -> dict:
    """Compute the current trial/billing state for a company. Used by the
    in-app banner. Returns a dict with keys:
        state:       'no_contract' | 'trial' | 'active' | 'overdue' | 'blocked'
        days_left:   days until next critical event (trial end / due date)
        next_event:  human-readable label for what's about to happen
    """
    cache_attr = f"_fg_lifecycle_{company_id}"
    cached = getattr(g, cache_attr, None)
    if cached is not None:
        return cached

    contract = get_current_contract(company_id)
    if contract is None:
        result = {"state": "no_contract", "days_left": None, "next_event": None}
        setattr(g, cache_attr, result)
        return result

    today = date.today()

    # Trial active?
    trial_ends = contract.get("trial_ends_at")
    if trial_ends and trial_ends >= today:
        result = {
            "state": "trial",
            "days_left": (trial_ends - today).days,
            "next_event": "trial_ends",
            "trial_ends_at": trial_ends,
        }
        setattr(g, cache_attr, result)
        return result

    # Check unpaid overdue invoices
    db = get_db()
    overdue = _safe_query(db, """
        SELECT id, due_date,
               (CURRENT_DATE - due_date) AS days_overdue
        FROM sys_company_invoices
        WHERE company_id = %s
          AND status = 'issued'
          AND due_date IS NOT NULL
          AND due_date < CURRENT_DATE
        ORDER BY due_date ASC
        LIMIT 1
    """, (company_id,))
    if overdue:
        days_overdue = overdue[0].get("days_overdue") or 0
        # Per design: "prior alerts + immediate block at trigger".
        # Trigger fires when the invoice is overdue (any positive days).
        result = {
            "state": "overdue" if days_overdue < 30 else "blocked",
            "days_left": -days_overdue,
            "next_event": "invoice_overdue",
            "due_date": overdue[0].get("due_date"),
        }
        setattr(g, cache_attr, result)
        return result

    result = {"state": "active", "days_left": None, "next_event": None}
    setattr(g, cache_attr, result)
    return result


def is_company_blocked(company_id: Optional[int] = None) -> bool:
    """Hard-block check: returns True if the company is in a state where the
    app should be read-only (trial expired with no payment, invoice severely
    overdue). Sys admins are never blocked."""
    if getattr(g, "current_user", None) and getattr(g, "current_user", {}).get("is_system_admin"):
        return False
    if company_id is None:
        company_id = getattr(g, "current_company_id", None)
    if not company_id:
        return False
    return get_lifecycle_state(company_id).get("state") == "blocked"
