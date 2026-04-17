# 2026-04-17 開発報告 / Daily Development Report

> 本日の改善は **発注精度の向上**、**オペレーターの使い勝手改善**、**アプリ内ヘルプの新設** の3本柱です。
> Today's highlights: **smarter ordering**, **better operator UX**, and **a brand-new in-app help manual**.

---

## 🎯 主な成果 / Highlights

| カテゴリ | 内容 | 影響 |
|---|---|---|
| 発注精度 | `est_order_qty` を統計的再計算（μ + 1.65σ） | 127件中68件を自動更新、95%のケースで欠品を防ぐ |
| 可視化 | 仕入ダッシュボードに「滞留在庫（30日以上未入荷）」表を追加 | 放置在庫に気付けるように |
| UX | スマホ棚卸し画面に「仕入頻度フィルタ」を追加 | 高頻度品目を優先的にカウント可能 |
| UX | 発注サポート画面に「表形式（スプレッドシート）表示」を追加 | 仕入先別カード・頻度順リストを切替可能 |
| UX | モバイル30日セッション保持 | 出勤のたびに再ログインが不要 |
| データ | `est_order_qty` の履歴テーブル新設（SCD Type 2） | 手動修正・自動再計算の推移を追跡可能 |
| ドキュメント | アプリ内ヘルプ `/help` を新設、PDFマニュアルを廃止 | 日英対応、スクリーンショット付き、常に最新 |

---

## 📊 発注目安数量の再計算 / `est_order_qty` Recalibration

### 背景
従来は月次平均 × 納品間隔という**静的な計算**で、週末のピークや季節変動を反映できませんでした。

### 新しい手法
過去5年分（1,729日）の喫食数データ + 仕入実績から、品目ごとに統計的に算出：

```
est_order_qty = round(per_breakfast_rate × (μ + 1.65σ))
```

- `per_breakfast_rate` = 品目総仕入量 ÷ 喫食数合計（90日ウィンドウ）
- `μ, σ` = 納品サイクル（L日）分の需要の平均・標準偏差
- **1.65σ = 95%のケースで在庫切れを防げる**（統計的安全係数）
- 低頻度品目（<20%/日）は30日移動平均で平滑化

### 結果
- **68件の品目を自動更新**（仕入履歴10日以上の品目）
- 平均 +16% / 最大 +30%（レインフォレストコーヒー）
- 日の出屋内藤商店の一部品目は手動値より下がる（新規登録で履歴不足のため）

### 履歴追跡
`mst_items_est_history` テーブルを新設（effective_from / effective_to で期間管理）。
品目編集画面下部に「発注目安の根拠（統計情報）」と「履歴」セクションを追加。
自動再計算・手動編集ともに、変更前後の値が残ります。

---

## 📋 滞留在庫リスト / Dead Stock List

仕入ダッシュボード末尾に新セクション「滞留在庫（30日以上未入荷）」を追加。

- **抽出条件**: 現在庫 > 0 AND 最終入荷 < 30日前
- **並び**: 推定金額（現在庫 × 最終単価）の大きい順
- **表示**: コード / 品名 / 仕入先 / カテゴリ / 現在庫 / 単価 / 推定金額 / 最終入荷日 / 経過日数
- **本日時点**: PRODで8件が該当（最長85日）

使い切り策・メニュー改廃の判断材料として活用してください。

---

## 📱 スマホ棚卸し画面の改善 / Mobile Inventory Count

1. **仕入頻度フィルタ**: 「超高頻度（週1回以上）」「高頻度（月2〜4回）」「低頻度（月1回以下）」「未仕入」で絞り込み可能
2. **品目カードに頻度ラベル**: 例「週1回」「月3回」（整数表示）
3. **最終棚卸日**: 各品目の最後にカウントした日付を表示
4. **ログインページ**: スマホ画面幅では「System Admin」欄を非表示

モバイルで開いた際、**30日間はログイン不要**（Secure + HttpOnly + SameSite=Lax Cookie）。

---

## 🛒 発注サポート画面の改善 / Order Support

- **表形式ビュー**（`?view=sheet`）を追加
  - 仕入頻度順にソート可能
  - 行ごとに 仕入先 / 発注〆切 / 納品日 / 頻度 / 在庫 / 目安 / 最終棚卸 / 状態
  - 将来的に発注数量入力 → 発注書自動生成のベースになります
- 既存カード表示にも **最終棚卸日** 列を追加
- 頻度表示を整数化（「週1.2回」→「週1回」）

---

## 📘 アプリ内ヘルプ `/help` / In-App Help System

旧来の PDF マニュアルを廃止し、ブラウザから直接読めるヘルプシステムを新設。

### 構成（目次）
- **はじめに**
  - トップ（目次）
  - 画面マップ（全NAVの関係図）
- **基本の考え方**
  - 棚卸しと発注の関係（場所順 vs 仕入先順の違い）
- **初期設定**
  - 棚・温度帯・エリアの設定
  - 仕入先マスタの使い方
  - 品目マスタの使い方
  - 売上推計の設定

### 特徴
- **🌐 日英バイリンガル**: 画面右上の言語切替で英語表示可能（海外スタッフ向け）
- **📸 実画面のスクリーンショット付き**: Playwrightで自動撮影 → UI変更時は再実行で更新
- **🔍 クリックで拡大**: 画像クリックで全画面表示、Escまたはクリックで閉じる
- **🔗 ライブリンク**: 「品目マスタを開く」等のリンクから直接該当画面へ

### 技術メモ
- Jinja2テンプレート（`templates/help/*.html`）
- 本文は `labels/ja.json` / `labels/en.json` で完全i18n管理（335+キー）
- 再撮影: `DEV_USER=... DEV_PASS=... python3 init/capture_help_screenshots.py`

---

## 🧪 確認いただきたいこと / Please Verify

1. **/reports/dashboard** — 滞留在庫リストの内容が実運用と合うか
2. **/inventory/count_sp** — スマホで頻度フィルタを使った際の操作感
3. **/order-support?view=sheet** — 表形式のソート・並び替え
4. **/mst_items/<任意のid>/edit** — 統計情報カードと履歴テーブル
5. **/help** — ヘルプ全般の内容・誤字脱字・スクリーンショットの妥当性

---

## 🔜 次回予定 / Next Up

- 自動撮影できなかった3画面のスクリーンショット（品目編集の統計情報 / 仕入先休業日 / 利益率設定）
- 1ヶ月後の `est_order_qty` 再計算（新しい仕入データが蓄積されてから）
- Phase 1 候補: **日次喫食数の手入力画面** 発注サポートが将来の需要を予測できるように
- Phase 2 候補（将来）: 予約システム連携で喫食数を自動取込

---

## 🔗 参考 / References

### 本日のコミット（11件）
| Hash | 内容 |
|---|---|
| `052bb1f` | est_order_qty 統計的再計算（BF計データ使用） |
| `5484039` | 滞留在庫リスト（ダッシュボード） |
| `aaef191` | 頻度フィルタ + 表形式 + 最終棚卸日 + 30日セッション |
| `ccaec41` | ラベル整形 + モバイル管理者非表示 |
| `0471d49` | `est_order_qty` 履歴テーブル + 品目編集に統計情報 |
| `95369a1` | ラベル改称（喫食数あたり消費量） |
| `cebdda4` | 頻度表示を整数化 + i18n準拠 |
| `59d1132` | アプリ内ヘルプ新設 |
| `9144705` | ヘルプ全章完成 + 英語対応 |
| `e6e63ff` | スクリーンショット + Playwright 自動撮影 |
| `91d1523` | キャプション整理 + クリック拡大 |

### 主要ファイル
- バックエンド: [views/order_support.py](../../views/order_support.py), [views/inventory_v2.py](../../views/inventory_v2.py), [views/masters.py](../../views/masters.py), [views/help.py](../../views/help.py), [views/reports/purchase_dashboard.py](../../views/reports/purchase_dashboard.py)
- ユーティリティ: [utils/item_frequency.py](../../utils/item_frequency.py)
- 計算スクリプト: [init/recalc_est_order_qty.py](../../init/recalc_est_order_qty.py), [init/capture_help_screenshots.py](../../init/capture_help_screenshots.py)
- テンプレート: [templates/help/](../../templates/help/), [templates/pur/order_support_sheet.html](../../templates/pur/order_support_sheet.html), [templates/mst/items_edit.html](../../templates/mst/items_edit.html)
- i18n: [labels/ja.json](../../labels/ja.json), [labels/en.json](../../labels/en.json)

### デプロイ状況
- DEV: `test-costing-sys.up.railway.app`（自動デプロイ）
- PROD: 本番環境（自動デプロイ）
- 両環境に反映済み

---

_生成: Claude Opus 4.7（1M context）／ 2026-04-17_
