-- 2026-04-24 — Draft orders for the 発注サポート screen.
--
-- Captures qtys the operator types into the order-support screen so they
-- survive a page refresh / crash / hand-off. One header per
-- (company, store, supplier, order_date), many item lines. A later
-- 発注書生成 flow flips status draft → sent and stamps sent_method/sent_at.

CREATE TABLE IF NOT EXISTS pur_order_drafts (
  id              SERIAL PRIMARY KEY,
  company_id      INTEGER NOT NULL,
  store_id        INTEGER NOT NULL,
  supplier_id     INTEGER NOT NULL,
  order_date      DATE NOT NULL,
  operator_id     INTEGER,                                   -- sys_users.user_id
  status          VARCHAR(16) NOT NULL DEFAULT 'draft',      -- draft | sent
  sent_method     VARCHAR(16),                               -- fax | mail | web | phone
  sent_at         TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (company_id, store_id, supplier_id, order_date)
);

CREATE INDEX IF NOT EXISTS ix_pur_order_drafts_lookup
  ON pur_order_drafts (store_id, supplier_id, order_date);

CREATE TABLE IF NOT EXISTS pur_order_draft_items (
  id              SERIAL PRIMARY KEY,
  order_draft_id  INTEGER NOT NULL REFERENCES pur_order_drafts(id) ON DELETE CASCADE,
  item_id         INTEGER NOT NULL,
  quantity        INTEGER NOT NULL,
  UNIQUE (order_draft_id, item_id)
);

CREATE INDEX IF NOT EXISTS ix_pur_order_draft_items_draft
  ON pur_order_draft_items (order_draft_id);
