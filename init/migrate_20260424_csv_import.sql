-- =====================================================================
-- CSV import infrastructure
-- 2026-04-24
--
-- Two concepts:
--   1) mst_store_aliases — free-text synonyms for a store name, used to
--      auto-match "納品場所" strings in supplier CSVs to our mst_stores.
--      Replaces the hardcoded _STORE_ALIASES_BY_COMPANY map in Python.
--
--   2) csv_import_profiles + csv_import_mappings — per-company named
--      profiles that describe how each CSV vendor labels its columns.
--      When a CSV is uploaded, we read its header row and pick the
--      profile whose header texts best overlap; no more hardcoded
--      CSV_COL dict.
--
-- Safe to apply: ADD-ONLY. The Python code falls back to the old
-- hardcoded behavior when either table is empty / missing.
-- =====================================================================

-- ----------------------------------------------------------------------
-- 1) Store aliases
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mst_store_aliases (
    id                  BIGSERIAL PRIMARY KEY,
    company_id          BIGINT      NOT NULL REFERENCES mst_companies(id),
    store_id            BIGINT      NOT NULL REFERENCES mst_stores(id) ON DELETE CASCADE,
    alias_text          VARCHAR(200) NOT NULL,
    normalized_alias    VARCHAR(200) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by_user_id  BIGINT      NULL,
    UNIQUE (company_id, normalized_alias)
);
CREATE INDEX IF NOT EXISTS ix_mst_store_aliases__store
    ON mst_store_aliases(store_id);

-- Seed: くらじか's existing hardcoded aliases → DB
-- (company_id=1 = くらじか自然豊農, store_id=1 = APA朝食)
INSERT INTO mst_store_aliases (company_id, store_id, alias_text, normalized_alias)
VALUES
    (1, 1, 'ＡＰＡ　ＨＯＴＥＬ長野',  'apahotel長野'),
    (1, 1, 'アパホテル仕入れ',       'アパホテル仕入れ'),
    (1, 1, 'アパホテル長野',         'アパホテル長野')
ON CONFLICT (company_id, normalized_alias) DO NOTHING;

-- ----------------------------------------------------------------------
-- 2) CSV import profiles
-- ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS csv_import_profiles (
    id            BIGSERIAL PRIMARY KEY,
    company_id    BIGINT      NULL REFERENCES mst_companies(id) ON DELETE CASCADE,
    name          VARCHAR(100) NOT NULL,
    description   TEXT         NULL,
    encoding      VARCHAR(20)  NOT NULL DEFAULT 'cp932',
    is_active     INTEGER      NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (company_id, name)
);

CREATE TABLE IF NOT EXISTS csv_import_mappings (
    id                BIGSERIAL PRIMARY KEY,
    profile_id        BIGINT       NOT NULL REFERENCES csv_import_profiles(id) ON DELETE CASCADE,
    cms_field         VARCHAR(50)  NOT NULL,
    csv_header_text   TEXT         NOT NULL,
    UNIQUE (profile_id, cms_field)
);
CREATE INDEX IF NOT EXISTS ix_csv_import_mappings__profile
    ON csv_import_mappings(profile_id);

-- Seed: the default profile for くらじか, matching the CSV we've already
-- tested against. Same 11 header mappings that used to live in CSV_COL.
INSERT INTO csv_import_profiles (company_id, name, description, encoding)
VALUES (1, 'B2Bフォーマット取引伝票一覧',
        'KEY COFFEE ONLINE / 受発注ライト / 他 汎用B2B納品書CSVエクスポート',
        'cp932')
ON CONFLICT (company_id, name) DO NOTHING;

-- Fill mappings (referenced by profile name so we can re-run idempotently).
WITH p AS (
    SELECT id FROM csv_import_profiles
    WHERE company_id = 1 AND name = 'B2Bフォーマット取引伝票一覧'
)
INSERT INTO csv_import_mappings (profile_id, cms_field, csv_header_text)
SELECT p.id, v.cms_field, v.csv_header_text
FROM p,
     (VALUES
         ('invoice_no',     '[伝票NO.]'),
         ('supplier_name',  '[取引先]'),
         ('delivery_place', '[納品場所／名]'),
         ('invoice_date',   '[伝票日付]'),
         ('delivery_date',  '[納品日]'),
         ('item_name',      '[商品名]'),
         ('unit_price',     '[単価]'),
         ('quantity',       '[数量]'),
         ('unit',           '[単位]'),
         ('line_amount',    '[計]'),
         ('item_code',      '[商品コード]')
     ) AS v(cms_field, csv_header_text)
ON CONFLICT (profile_id, cms_field) DO NOTHING;
