-- =====================================================================
-- Sys-admin role split (super_admin / engineer / sales / accounting)
-- 2026-04-21
--
-- Adds:
--   sys_users.sys_role  -- role within the sys-admin tier
--
-- Backfill: every existing sys admin → 'super_admin' (keeps current
-- behavior). New sys admins default to 'super_admin' too; assign
-- specific roles via the /admin/system user table.
--
-- Safe to apply: ADD-ONLY. Routes that don't have role guards yet
-- continue to use is_system_admin alone, so nothing breaks.
-- =====================================================================

ALTER TABLE sys_users
    ADD COLUMN IF NOT EXISTS sys_role VARCHAR(20) NOT NULL DEFAULT 'super_admin';

-- Defensive: ensure all current sys admins are super_admin.
-- (Handles the case where the column existed but values are stale.)
UPDATE sys_users
SET sys_role = 'super_admin'
WHERE is_system_admin = TRUE
  AND (sys_role IS NULL OR sys_role NOT IN ('super_admin','engineer','sales','accounting'));

-- Optional cosmetic constraint — comment in if you want to enforce values
-- at the DB level. Skipped here to keep the migration permissive.
-- ALTER TABLE sys_users ADD CONSTRAINT chk_sys_role
--   CHECK (sys_role IN ('super_admin','engineer','sales','accounting'));
