-- 2026-04-24 — is_orderable flag for items + suppliers
--
-- Lets operators hide an item or an entire supplier from the 発注サポート
-- screen without disabling it elsewhere (reports, purchase entry, historical
-- edits all still work). Flag auto-resets to TRUE whenever a new purchase
-- row is inserted, so the next real order puts the item/supplier back in
-- rotation without manual intervention.

-- 1. Columns (both default TRUE so existing rows remain visible)
ALTER TABLE mst_items
  ADD COLUMN IF NOT EXISTS is_orderable BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE pur_suppliers
  ADD COLUMN IF NOT EXISTS is_orderable BOOLEAN NOT NULL DEFAULT TRUE;

-- 2. Auto-reset trigger on purchases INSERT
CREATE OR REPLACE FUNCTION reset_is_orderable_on_purchase() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.item_id IS NOT NULL THEN
    UPDATE mst_items
       SET is_orderable = TRUE
     WHERE id = NEW.item_id
       AND is_orderable = FALSE;
  END IF;

  IF NEW.supplier_id IS NOT NULL THEN
    UPDATE pur_suppliers
       SET is_orderable = TRUE
     WHERE id = NEW.supplier_id
       AND is_orderable = FALSE;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- `purchases` is a view over `pur_purchases` — put the trigger on the base table.
DROP TRIGGER IF EXISTS tr_reset_is_orderable ON pur_purchases;
CREATE TRIGGER tr_reset_is_orderable
AFTER INSERT ON pur_purchases
FOR EACH ROW
EXECUTE FUNCTION reset_is_orderable_on_purchase();
