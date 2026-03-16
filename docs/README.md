# CMS Engineering Documentation
Project: 原価計算SYS  
Repository: costing-sys-pg  

This directory contains the official engineering documentation for the CMS.

## Document Index

1. 01-Architecture.md  
   System architecture and design principles.

2. 02-Folder-Structure.md  
   Actual repository structure and domain organization.

3. 03-Database-Naming.md  
   Database naming conventions and schema governance.

4. 04-Domain-Refactor-Plan.md  
   Controlled refactor and domain prefix alignment strategy.

---

## Architecture Summary

- Flask monolithic application
- PostgreSQL database
- Raw SQL (no ORM)
- Domain-based modularization (mst, pur, inv, loc)
- Manual migration scripts
- Multi-company scope support
