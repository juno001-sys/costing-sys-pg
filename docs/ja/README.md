# CMS 技術ドキュメント
プロジェクト: 原価計算SYS  
リポジトリ: costing-sys-pg  

本ディレクトリは、CMS（原価計算SYS）の公式技術ドキュメントです。

## ドキュメント一覧

1. 01-Architecture.md  
   システム全体のアーキテクチャと設計思想

2. 02-Folder-Structure.md  
   実際のリポジトリ構造とドメイン構成

3. 03-Database-Naming.md  
   データベース命名規則およびスキーマ統制

4. 04-Domain-Refactor-Plan.md  
   ドメイン接頭辞統一のための段階的リファクタ計画

---

## システム概要

- Flaskによるモノリシック構成
- PostgreSQLデータベース
- ORM不使用（Raw SQL方式）
- ドメイン単位の整理（mst / pur / inv / loc）
- 手動マイグレーション管理
- マルチカンパニー対応
