-- =====================================================================
-- Convert sys_users.sys_role from VARCHAR(20) → TEXT[] (multi-role)
-- 2026-04-21 (afternoon)
--
-- Reason: at the early stage, one sys admin often wears multiple hats
-- (e.g., founder is both sales AND accounting). Forcing a single role
-- means giving them super_admin or making them re-assign repeatedly.
-- An array column lets us check membership: "does this user have any
-- of these roles?".
--
-- Backfill: every existing single value becomes a 1-element array.
-- After migration:
--   sys_role IS NULL          → no access (block)
--   sys_role = '{}'           → no roles (block — same as NULL)
--   sys_role = '{super_admin}'→ super_admin only (existing default)
--   sys_role = '{sales,accounting}' → sales AND accounting access
--
-- Safe to apply: ALTER ... USING ARRAY[...] preserves data.
-- =====================================================================

-- Step 1: drop the old VARCHAR default ('super_admin') so the type
--          conversion below can run.
ALTER TABLE sys_users ALTER COLUMN sys_role DROP DEFAULT;

-- Step 2: convert the column type, lifting each scalar into a 1-element array.
ALTER TABLE sys_users
    ALTER COLUMN sys_role TYPE TEXT[]
    USING (
      CASE
        WHEN sys_role IS NULL OR sys_role = '' THEN ARRAY[]::TEXT[]
        ELSE ARRAY[sys_role]
      END
    );

-- Step 3: set the new array-typed default for sys-admin rows.
ALTER TABLE sys_users
    ALTER COLUMN sys_role SET DEFAULT ARRAY['super_admin']::TEXT[];

-- Defensive: any sys admin without roles should fall back to super_admin.
UPDATE sys_users
SET sys_role = ARRAY['super_admin']::TEXT[]
WHERE is_system_admin = TRUE
  AND (sys_role IS NULL OR cardinality(sys_role) = 0);
