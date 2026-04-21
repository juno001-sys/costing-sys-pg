-- =====================================================================
-- Per-company nav visibility policy
-- 2026-04-21
--
-- Each company's Chief Admin decides which nav menu items are visible
-- to operator / auditor users in that company. Admins (function role
-- 'admin') always see everything, subject to the tier feature flags.
--
-- Lookup key: (company_id, role, nav_key).
--   - role    : 'operator' | 'auditor'
--   - nav_key : one of NAV_KEYS in utils/access_scope.py
--   - visible : the effective policy (TRUE = show, FALSE = hide)
--
-- Missing rows fall back to role-specific defaults in code (see
-- NAV_DEFAULT_VISIBILITY).  The table is ADD-ONLY / reversible.
-- =====================================================================
CREATE TABLE IF NOT EXISTS sys_company_nav_policies (
    company_id          BIGINT      NOT NULL REFERENCES mst_companies(id),
    role                VARCHAR(20) NOT NULL,
    nav_key             VARCHAR(50) NOT NULL,
    visible             BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by_user_id  BIGINT      NULL,
    PRIMARY KEY (company_id, role, nav_key)
);

CREATE INDEX IF NOT EXISTS ix_sys_company_nav_policies__company_role
    ON sys_company_nav_policies(company_id, role);
