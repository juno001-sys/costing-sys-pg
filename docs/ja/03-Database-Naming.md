# 03 – データベース命名規則

## テーブル接頭辞

- mst_* : マスターデータ
- pur_* : 購買
- inv_* : 在庫
- loc_* : ロケーション

---

## 主キー

推奨：

- id を主キー
- 外部キーは <entity>_id

例：
- store_id
- supplier_id
- item_id

---

## 共通カラム

- created_at
- updated_at
- deleted_at（任意）

---

## 制約命名

- pk_<table>
- fk_<table>__<ref_table>
- uq_<table>__<columns>
- ix_<table>__<columns>

例：
- uq_inv_counts__store_id_count_date
- ix_pur_lines__purchase_id

---

## リファクタ互換戦略

1. 実テーブルを新接頭辞へリネーム
2. 旧テーブル名のVIEWを作成
3. アプリ側修正
4. 移行完了後VIEW削除

本番停止なしで移行可能。
