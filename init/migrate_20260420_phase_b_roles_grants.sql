-- =====================================================================
-- Phase B: Chief Admin flag + per-store grants (Google-style + AD overlay)
-- 2026-04-20
--
-- Adds:
--   sys_user_companies.is_chief_admin  - one Chief Admin per company
--   sys_user_store_grants              - per-store role overlay (OR semantics)
--
-- Backfills:
--   - The earliest active 'admin' user per company is promoted to Chief Admin
--   - This is a one-time idempotent backfill; subsequent runs are no-ops
--
-- Function-role values stay 'admin' / 'operator' / 'auditor' (UI labels
-- 'auditor' as 'Supervisor' going forward; DB value unchanged for stability).
--
-- Safe to apply: ADD-ONLY. get_accessible_stores() falls back to existing
-- company-scoped behavior when sys_user_store_grants is empty/missing.
-- =====================================================================

-- ----------------------------------------------------------------------
-- 1. Chief Admin flag
-- ----------------------------------------------------------------------
ALTER TABLE sys_user_companies
    ADD COLUMN IF NOT EXISTS is_chief_admin BOOLEAN NOT NULL DEFAULT FALSE;

-- Backfill: oldest active admin per company becomes Chief Admin.
-- Idempotent: only runs if no Chief Admin exists for that company yet.
UPDATE sys_user_companies u
SET is_chief_admin = TRUE
WHERE u.id IN (
    SELECT DISTINCT ON (uc.company_id) uc.id
    FROM sys_user_companies uc
    WHERE uc.role = 'admin'
      AND uc.is_active = 1
      AND NOT EXISTS (
          SELECT 1 FROM sys_user_companies x
          WHERE x.company_id = uc.company_id
            AND x.is_chief_admin = TRUE
      )
    ORDER BY uc.company_id, uc.created_at, uc.id
);

-- Enforce at most one Chief Admin per company at the DB level.
-- (Using a partial unique index — multiple non-chief rows are fine.)
CREATE UNIQUE INDEX IF NOT EXISTS uq_sys_user_companies__chief_per_company
    ON sys_user_companies(company_id) WHERE is_chief_admin = TRUE;

-- ----------------------------------------------------------------------
-- 2. Per-store role grants (OR overlay)
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sys_user_store_grants (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT      NOT NULL REFERENCES sys_users(id),
    company_id      BIGINT      NOT NULL REFERENCES mst_companies(id),
    store_id        BIGINT      NOT NULL REFERENCES mst_stores(id),
    -- 'operator' or 'admin' — store-level overlay only.
    -- Per design: function role is the company-wide baseline; store grants
    -- can elevate (OR) on a specific store, never lower it.
    store_role      VARCHAR(20) NOT NULL,
    is_active       INTEGER     NOT NULL DEFAULT 1,
    granted_by_user_id BIGINT   NULL,
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ NULL,
    notes           TEXT        NULL,
    UNIQUE (user_id, company_id, store_id)
);
CREATE INDEX IF NOT EXISTS ix_sys_user_store_grants__user_company
    ON sys_user_store_grants(user_id, company_id);
CREATE INDEX IF NOT EXISTS ix_sys_user_store_grants__store
    ON sys_user_store_grants(store_id) WHERE is_active = 1;
