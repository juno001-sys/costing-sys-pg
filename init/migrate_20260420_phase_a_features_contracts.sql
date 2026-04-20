-- =====================================================================
-- Phase A: Tier-based feature gating, contracts, and invoicing
-- 2026-04-20
--
-- Adds:
--   sys_features          - catalog of gateable features
--   sys_company_contracts - tier history per company (SCD type 2)
--   sys_company_features  - per-company enabled features (with overrides)
--   sys_company_invoices  - monthly invoices generated from contracts
--
-- Safe to apply: ADD-ONLY. Does not modify existing tables.
-- Helpers default to "feature enabled" if these tables are empty,
-- so existing screens keep working until the catalog is seeded.
-- =====================================================================

-- ----------------------------------------------------------------------
-- 1. Feature catalog
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sys_features (
    feature_key   VARCHAR(60) PRIMARY KEY,
    name_ja       VARCHAR(120) NOT NULL,
    name_en       VARCHAR(120) NOT NULL,
    -- Lowest tier that includes this feature: 'entry' | 'standard' | 'premium' | 'always_on'
    default_tier  VARCHAR(20) NOT NULL,
    sort_order    INTEGER     NOT NULL DEFAULT 100,
    is_active     INTEGER     NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed the 12 gateable features per the 2026-04-17 sales flyer.
-- Always-on features (multi-language, work log, user management) are NOT
-- listed here — they are unconditionally available on every tier.
INSERT INTO sys_features (feature_key, name_ja, name_en, default_tier, sort_order)
VALUES
  ('purchase_entry_fuzzy',    '仕入れ入力（あいまい検索）',   'Purchase entry (fuzzy search)',  'entry',    10),
  ('purchase_entry_paste',    'コピペで一括入力',             'Bulk paste entry',               'entry',    20),
  ('inventory_count',         '棚卸し入力（前3回実施日表示）','Stock count entry',              'entry',    30),
  ('report_purchase_supplier','仕入先別仕入れ金額照会',       'Purchase report by supplier',    'entry',    40),
  ('shelf_layout',            '棚割り設定',                   'Shelf layout setup',             'standard', 50),
  ('inventory_count_sp',      '棚卸しスマホ入力UI',           'Mobile inventory count UI',      'standard', 60),
  ('report_purchase_item',    '品目別仕入れ金額照会',         'Purchase report by item',        'standard', 70),
  ('report_usage_monthly',    '月次利用量照会',               'Monthly usage report',           'standard', 80),
  ('report_cost_monthly',     '売上原価月次推移',             'Monthly cost of sales',          'standard', 90),
  ('profit_estimation',       '売上利益推計',                 'Revenue & profit estimation',    'standard', 100),
  ('purchase_dashboard',      'ダッシュボード（仕入れシェア・TOP20・死に筋）','Purchase dashboard (share/top 20/dead stock)', 'premium', 110),
  ('order_support',           '発注補助（取引先別発注期限・在庫数量）','Order support',          'premium', 120)
ON CONFLICT (feature_key) DO UPDATE SET
  name_ja      = EXCLUDED.name_ja,
  name_en      = EXCLUDED.name_en,
  default_tier = EXCLUDED.default_tier,
  sort_order   = EXCLUDED.sort_order;

-- ----------------------------------------------------------------------
-- 2. Company contracts (SCD type 2 — one open row per company)
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sys_company_contracts (
    id                  BIGSERIAL PRIMARY KEY,
    company_id          BIGINT      NOT NULL REFERENCES mst_companies(id),
    tier                VARCHAR(20) NOT NULL,        -- 'entry' | 'standard' | 'premium'
    effective_from      DATE        NOT NULL,
    effective_to        DATE        NULL,            -- NULL = current row
    -- Trial: when set, no invoice is issued before trial_ends_at
    trial_ends_at       DATE        NULL,
    monthly_fee         INTEGER     NULL,            -- in JPY (no decimals); see sales flyer
    currency            VARCHAR(3)  NOT NULL DEFAULT 'JPY',
    payment_method      VARCHAR(20) NOT NULL DEFAULT 'invoice',
                                                     -- 'invoice' | 'credit_card' | 'bank_transfer'
                                                     -- only 'invoice' active for MVP
    notes               TEXT        NULL,
    changed_by_user_id  BIGINT      NULL,            -- sys_users.id of the operator
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_sys_company_contracts__company
    ON sys_company_contracts(company_id, effective_to);

-- ----------------------------------------------------------------------
-- 3. Per-company feature toggles (with override tracking)
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sys_company_features (
    company_id        BIGINT      NOT NULL REFERENCES mst_companies(id),
    feature_key       VARCHAR(60) NOT NULL REFERENCES sys_features(feature_key),
    enabled           INTEGER     NOT NULL DEFAULT 1,
    -- 'tier_default' = filled by tier change; 'admin_override' = sys admin toggled
    source            VARCHAR(20) NOT NULL DEFAULT 'tier_default',
    set_by_user_id    BIGINT      NULL,
    set_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, feature_key)
);

-- ----------------------------------------------------------------------
-- 4. Invoices (monthly billing record per contract)
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sys_company_invoices (
    id                    BIGSERIAL PRIMARY KEY,
    company_id            BIGINT      NOT NULL REFERENCES mst_companies(id),
    contract_id           BIGINT      NOT NULL REFERENCES sys_company_contracts(id),
    invoice_number        VARCHAR(40) NULL,
    period_start          DATE        NOT NULL,
    period_end            DATE        NOT NULL,
    amount                INTEGER     NOT NULL,        -- in currency minor unit (JPY = whole yen)
    currency              VARCHAR(3)  NOT NULL DEFAULT 'JPY',
    -- 'draft' | 'issued' | 'paid' | 'void'
    status                VARCHAR(20) NOT NULL DEFAULT 'draft',
    -- snapshot at issuance — credit card support uses different fields below
    payment_method        VARCHAR(20) NOT NULL DEFAULT 'invoice',
    due_date              DATE        NULL,
    issued_at             TIMESTAMPTZ NULL,
    paid_at               TIMESTAMPTZ NULL,
    -- For future credit-card / external-processor reconciliation. NULL for MVP.
    paid_via              VARCHAR(50) NULL,
    external_payment_id   VARCHAR(100) NULL,
    notes                 TEXT        NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_sys_company_invoices__company_status
    ON sys_company_invoices(company_id, status);
CREATE INDEX IF NOT EXISTS ix_sys_company_invoices__due_date
    ON sys_company_invoices(due_date) WHERE status IN ('issued');
