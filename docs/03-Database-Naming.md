# 03 – Database Naming Rules

## Table Prefixes

- mst_* : master data
- pur_* : purchasing
- inv_* : inventory
- loc_* : location management

---

## Primary Keys

Recommended:

- id as primary key
- <entity>_id for foreign keys

Example:
- store_id
- supplier_id
- item_id

---

## Common Columns

- created_at
- updated_at
- deleted_at (optional soft delete)

---

## Constraint Naming

- pk_<table>
- fk_<table>__<ref_table>
- uq_<table>__<columns>
- ix_<table>__<columns>

Example:
- uq_inv_counts__store_id_count_date
- ix_pur_lines__purchase_id

---

## Refactor Compatibility Strategy

1. Rename real table to new prefixed name.
2. Create SQL VIEW using old table name.
3. Update application code.
4. Remove compatibility view after migration is complete.

Ensures zero production downtime during refactor.
