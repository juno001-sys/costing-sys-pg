# 02 – Folder Structure Reference

## Root Directory

costing-sys-pg/
├── app.py
├── db.py
├── Procfile
├── requirements.txt
├── init/
├── docs/
├── labels/
├── static/
├── templates/
└── views/

---

## Core Files

app.py  
- Flask app entry  
- Blueprint registration  
- Application bootstrap  

db.py  
- Database connection  
- Raw SQL execution  
- Cursor handling  

---

## views/ (Business Logic Layer)

views/
├── inventory.py
├── inventory_v2.py
├── purchases.py
├── masters.py
├── admin/
├── auth/
├── loc/
├── reports/
├── inv_sort/

### Domain Meaning

masters.py → Master data  
purchases.py → Purchase entry & reporting  
inventory_v2.py → Current inventory logic  
admin/ → System-level management  
auth/ → Authentication flow  
loc/ → Location management  
reports/ → Reporting layer  
inv_sort/ → Inventory ordering logic  

---

## templates/

Grouped by domain:

admin/
auth/
inv/
loc/
mst/
pur/
rpt/
layout/

---

## static/

Contains:
- JavaScript
- CSS
- Images
- Inventory client logic

---

## init/

Manual migration and setup scripts:
- Schema patches
- Missing table creation
- Sequence fixes
- CSV import utilities

No automatic migration framework is used.
