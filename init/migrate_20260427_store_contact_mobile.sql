-- 2026-04-27 — Add 担当者携帯 (contact_mobile) to mst_stores.
-- Sibling to contact_person, prints on the FAX 発注書.

ALTER TABLE mst_stores
  ADD COLUMN IF NOT EXISTS contact_mobile TEXT;
