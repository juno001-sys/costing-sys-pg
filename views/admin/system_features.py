"""
Sys-admin screen for managing each company's contract tier and per-feature
overrides. Implements the design agreed on 2026-04-20:

- One screen per company
- Tier picker: ENTRY / STANDARD / PREMIUM
- Free-trial end date (optional)
- Monthly fee (defaults to tier baseline)
- Per-feature checkbox grid with explicit override tracking

Tier change behavior (option A): syncs `tier_default` rows in
sys_company_features to match the new tier; admin overrides are preserved.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import wraps

from flask import flash, g, redirect, render_template, request, url_for

from utils.feature_gate import (
    TIER_DEFAULT_FEE_JPY,
    TIER_DEFAULT_TRIAL_MONTHS,
    TIER_RANK,
    get_company_feature_map,
    get_current_contract,
)
from utils.sys_roles import sys_role_required
from views.reports.audit_log import log_event


VALID_TIERS = ("entry", "standard", "premium")
VALID_PAYMENT_METHODS = ("invoice", "credit_card", "bank_transfer")


def init_admin_system_features_views(app, get_db):

    @app.get("/admin/system/companies/<int:company_id>/features")
    @sys_role_required("sales")
    def admin_system_company_features(company_id):
        db = get_db()
        company = db.execute(
            "SELECT id, name FROM mst_companies WHERE id = %s",
            (company_id,),
        ).fetchone()
        if not company:
            flash("Company not found.")
            return redirect(url_for("admin_system_home"))

        contract = get_current_contract(company_id)
        feature_map = get_company_feature_map(company_id)

        # Group features by tier for nicer rendering
        feature_rows = sorted(
            feature_map.values(), key=lambda r: (r.get("sort_order", 100), r["feature_key"])
        )

        return render_template(
            "admin/system_features.html",
            company=company,
            contract=contract,
            feature_rows=feature_rows,
            valid_tiers=VALID_TIERS,
            valid_payment_methods=VALID_PAYMENT_METHODS,
            tier_default_fees=TIER_DEFAULT_FEE_JPY,
            tier_default_trial_months=TIER_DEFAULT_TRIAL_MONTHS,
        )

    @app.post("/admin/system/companies/<int:company_id>/features")
    @sys_role_required("sales")
    def admin_system_company_features_save(company_id):
        db = get_db()

        company = db.execute(
            "SELECT id, name FROM mst_companies WHERE id = %s",
            (company_id,),
        ).fetchone()
        if not company:
            flash("Company not found.")
            return redirect(url_for("admin_system_home"))

        # ---------- 1. Read form ----------
        new_tier = (request.form.get("tier") or "").strip().lower()
        if new_tier not in VALID_TIERS:
            flash("Invalid tier.")
            return redirect(url_for("admin_system_company_features", company_id=company_id))

        trial_ends_raw = (request.form.get("trial_ends_at") or "").strip()
        trial_ends_at = None
        if trial_ends_raw:
            try:
                trial_ends_at = date.fromisoformat(trial_ends_raw)
            except ValueError:
                trial_ends_at = None

        monthly_fee_raw = (request.form.get("monthly_fee") or "").strip()
        try:
            monthly_fee = int(monthly_fee_raw) if monthly_fee_raw else TIER_DEFAULT_FEE_JPY[new_tier]
        except ValueError:
            monthly_fee = TIER_DEFAULT_FEE_JPY[new_tier]

        payment_method = (request.form.get("payment_method") or "invoice").strip()
        if payment_method not in VALID_PAYMENT_METHODS:
            payment_method = "invoice"

        notes = (request.form.get("notes") or "").strip() or None

        # Per-feature checkboxes are submitted only when checked.
        # Parse into a set; anything in the catalog but not in the form = unchecked.
        all_keys = [r["feature_key"] for r in db.execute(
            "SELECT feature_key FROM sys_features WHERE is_active = 1"
        ).fetchall()]
        checked_keys = set(request.form.getlist("feature"))

        # ---------- 2. Detect & write contract change ----------
        current = get_current_contract(company_id)
        actor_id = getattr(g, "current_user", {}).get("id") if getattr(g, "current_user", None) else None

        contract_changed = (
            current is None
            or current.get("tier") != new_tier
            or current.get("payment_method") != payment_method
            or (current.get("monthly_fee") or 0) != (monthly_fee or 0)
            or current.get("trial_ends_at") != trial_ends_at
        )

        try:
            if contract_changed:
                # Close out the current open contract (if any)
                db.execute(
                    """
                    UPDATE sys_company_contracts
                    SET effective_to = CURRENT_DATE
                    WHERE company_id = %s AND effective_to IS NULL
                    """,
                    (company_id,),
                )
                # Open a new contract row
                db.execute(
                    """
                    INSERT INTO sys_company_contracts
                      (company_id, tier, effective_from, trial_ends_at,
                       monthly_fee, currency, payment_method, notes, changed_by_user_id)
                    VALUES
                      (%s, %s, CURRENT_DATE, %s, %s, 'JPY', %s, %s, %s)
                    """,
                    (company_id, new_tier, trial_ends_at, monthly_fee,
                     payment_method, notes, actor_id),
                )
                log_event(
                    db,
                    action="CONTRACT_CHANGE",
                    module="sys",
                    entity_table="sys_company_contracts",
                    entity_id=str(company_id),
                    company_id=company_id,
                    status_code=200,
                    message=f"Tier set to {new_tier}",
                    new_data={
                        "tier": new_tier,
                        "trial_ends_at": trial_ends_at.isoformat() if trial_ends_at else None,
                        "monthly_fee": monthly_fee,
                        "payment_method": payment_method,
                    },
                    old_data={
                        "tier": current.get("tier") if current else None,
                        "monthly_fee": current.get("monthly_fee") if current else None,
                        "payment_method": current.get("payment_method") if current else None,
                    },
                )

            # ---------- 3. Reconcile per-feature toggles ----------
            new_company_rank = TIER_RANK.get(new_tier, 0)
            for key in all_keys:
                checked = key in checked_keys

                # Existing row?
                existing = db.execute(
                    """
                    SELECT enabled, source FROM sys_company_features
                    WHERE company_id = %s AND feature_key = %s
                    """,
                    (company_id, key),
                ).fetchone()

                # Tier baseline for this feature
                feat_default_tier = db.execute(
                    "SELECT default_tier FROM sys_features WHERE feature_key = %s",
                    (key,),
                ).fetchone()
                required_rank = TIER_RANK.get(
                    (feat_default_tier or {}).get("default_tier", "always_on"), 0
                )
                tier_says_enabled = new_company_rank >= required_rank

                # Decide source: if checked == tier baseline, this is tier_default;
                # otherwise it's an admin_override.
                if checked == tier_says_enabled:
                    source = "tier_default"
                else:
                    source = "admin_override"

                if existing:
                    # Only write if something actually changed
                    if (bool(existing["enabled"]) != checked) or (existing["source"] != source):
                        db.execute(
                            """
                            UPDATE sys_company_features
                            SET enabled = %s, source = %s,
                                set_by_user_id = %s, set_at = now()
                            WHERE company_id = %s AND feature_key = %s
                            """,
                            (1 if checked else 0, source, actor_id, company_id, key),
                        )
                else:
                    db.execute(
                        """
                        INSERT INTO sys_company_features
                          (company_id, feature_key, enabled, source, set_by_user_id)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (company_id, key, 1 if checked else 0, source, actor_id),
                    )

            db.commit()
            flash("Saved.")
        except Exception as e:
            db.rollback()
            flash(f"Save failed: {e}")

        return redirect(url_for("admin_system_company_features", company_id=company_id))
