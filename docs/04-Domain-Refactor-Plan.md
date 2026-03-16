# 04 – Domain Refactor Plan

## Objective

Align the system under consistent domain prefixes:

mst → pur → inv → loc

---

## Phase Strategy

Refactor one domain at a time:

1. Master Data (mst)
2. Purchasing (pur)
3. Inventory (inv)
4. Location (loc)

Each phase must:

- Keep application runnable
- Maintain production stability
- Avoid large breaking changes

---

## Refactor Rules

- Small incremental commits
- No multi-domain large rewrites
- Backward compatibility via SQL views
- Validate company_id scoping after each phase

---

## Migration Safety Checklist

Before deploying:

- Confirm foreign key integrity
- Confirm index recreation
- Confirm reports still function
- Confirm purchase default logic unchanged
- Confirm inventory sorting intact

---

Refactor must prioritize:

Production safety > Architectural purity
