# 02 – フォルダ構成

## ルート構造

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

## 主要ファイル

app.py  
- Flaskアプリ起動
- Blueprint登録
- アプリ初期化

db.py  
- DB接続管理
- Raw SQL実行
- カーソル管理

---

## views/（ビジネスロジック層）

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

### ドメイン説明

masters.py → マスターデータ  
purchases.py → 購買管理  
inventory_v2.py → 現行在庫ロジック  
admin/ → システム管理  
auth/ → 認証  
loc/ → ロケーション管理  
reports/ → レポート  
inv_sort/ → 在庫表示順制御  

---

## templates/

ドメイン単位で整理：

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

- JavaScript
- CSS
- 画像
- 在庫画面ロジック

---

## init/

手動マイグレーション用：

- スキーマ修正
- テーブル追加
- シーケンス修正
- CSVインポート

自動マイグレーション機構は未使用。
