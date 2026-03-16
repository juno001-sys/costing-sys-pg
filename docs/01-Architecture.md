# 01 – System Architecture

## 1. Overview

The CMS (Cost Management System) is a Flask-based monolithic web application designed to manage:

- Master data
- Purchase operations
- Inventory control
- Cost reporting
- Multi-company administration

The system prioritizes data accuracy and operational stability over heavy abstraction.

---

## 2. Technical Stack

- Framework: Flask
- Database: PostgreSQL
- DB Access: Raw SQL (no SQLAlchemy ORM)
- Templates: Jinja2
- Deployment: Railway
- Migration: Manual SQL scripts

---

## 3. Architectural Characteristics

### Monolithic Structure

- Single Flask app
- Domain grouping via view folders
- No service/repository abstraction layer
- Business logic resides inside route files

---

### Domain Prefix Model

System domains are separated logically using prefixes:

- mst_ : master data
- pur_ : purchasing
- inv_ : inventory
- loc_ : location management

Prefixes are aligned across:
- Tables
- Python modules
- Templates
- URL namespaces

---

## 4. Request Flow

Browser Request  
→ Flask Route (views/)  
→ Raw SQL execution (db.py)  
→ Data Processing in Route  
→ Jinja Template Rendering  
→ HTML Response  

---

## 5. Multi-Company Scope

- company_id used across domain tables
- Session-based company scoping
- Sys Admin role may override scope

---

## 6. Inventory Evolution

Two generations exist:

- inventory.py (legacy)
- inventory_v2.py (current)

Inventory v2 introduces:

- Zone grouping
- Shelf-level sorting
- Client-side optimization

---

## 7. Design Philosophy

Production safety > Architectural purity
