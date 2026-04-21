-- =====================================================================
-- Mark Kurajika's own company as internal (not a paying client)
-- 2026-04-21
--
-- Adds:
--   mst_companies.is_internal  -- TRUE for Kurajika's own company /
--                                 demo / smoke-test accounts.
--                                 FALSE (default) for paying clients.
--
-- Used by:
--   /admin/system home — splits "Client Companies" (paying) from
--                         "Internal" (Kurajika).
--   /admin/system/health — filters out internal by default
--                          (toggle to include).
--   Future: invoice generator already skips trial; could also skip
--           internal so we don't auto-bill our own house account.
--
-- Backfill: company id=1 (くらじか自然豊農) marked internal.
-- This is also the smoke-test account (breakfast staff use it daily,
-- and we patch-test against it before broader PROD rollout).
-- =====================================================================

ALTER TABLE mst_companies
    ADD COLUMN IF NOT EXISTS is_internal BOOLEAN NOT NULL DEFAULT FALSE;

-- Backfill: mark Kurajika's company as internal.
UPDATE mst_companies SET is_internal = TRUE WHERE id = 1;
