-- 2026-04-27 — Contact info on mst_stores for FAX 発注書 sender block.
--
-- The 発注書 form (templates/pur/order_form.html) needs to include the
-- ordering store's address / phone / FAX / email / contact-person so the
-- supplier can reach the right person. Until now mst_stores only had
-- seats/opening date — no contact details.

ALTER TABLE mst_stores
  ADD COLUMN IF NOT EXISTS address        TEXT,
  ADD COLUMN IF NOT EXISTS phone          TEXT,
  ADD COLUMN IF NOT EXISTS fax            TEXT,
  ADD COLUMN IF NOT EXISTS email          TEXT,
  ADD COLUMN IF NOT EXISTS contact_person TEXT;
