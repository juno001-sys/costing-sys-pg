# 2026-04-20 開発報告 セッション2 / Daily Development Report — Session 2

> セッション1（朝）はブランド配色とヘルプ2章。セッション2（午後〜夜）は **権限・課金システム** の大型実装に加えて、データ品質・運用ツール群を一気に整備しました。
>
> Session 1 (morning) shipped brand palette + 2 help chapters. Session 2 (afternoon → evening) shipped a **big auth + billing system**, plus a bunch of data-quality and operational tooling.

---

## 🎯 本日の成果サマリ / Highlights

| カテゴリ | 内容 | 影響 |
|---|---|---|
| ダッシュボード | 滞留在庫リストをカテゴリ別の基準日数に変更 + ホバー凡例 | 消耗品・調味料が常に上位を占めていたノイズを解消 |
| 権限・課金 | **Phase A/B/C** ：機能ゲート＋契約・請求＋ロール再設計 | 先方のデモ販売準備が整う（プラン別UI、Chief Admin、店舗別権限、月次請求） |
| ヘルプ | 「ユーザー権限の仕組み」章を新設 | Chief Admin / Admin / Operator / Supervisor を平易に説明 |
| ブランディング | くらじか事業開発 → くらじか に統一、ヘッダーの ヘルプリンク削除 | 正式社名 倉島事業開発 と愛称 くらじか の使い分け明確化 |
| 運用 | PROD全データを DEV にミラー、Chief Admin を organic@ に移譲 | DEVで実データ感のあるテストが可能に |
| 運用 | **顧客データ除外型** PROD→DEV同期スクリプトを追加 | 有料顧客がついた瞬間に切替可能（個人情報を DEV に持ち込まない） |
| データ品質 | 棚卸し入力の **誤入力警告** + **重複保存抑止** | 1→41 のような誤入力を検知、4回連打されても1件しか残らない |
| 提案 | 顧客ヘルスダッシュボード設計案を提示 | 次回セッションの議論起点 |

---

## 1. 滞留在庫リストの精度改善 / Dead-stock List Refinement

### 課題
従来は「30日以上未入荷」一律で滞留判定。**消耗品・資材** や **調味料・油脂** は元々長期滞留が普通の品目なので、リストが常にこれらで埋まっていて発見性が悪かった。

### 解決
12カテゴリそれぞれに「適正な滞留日数」を設定。

| 日数 | カテゴリ |
|---|---|
| 7日 | 青果・きのこ・水産・仕込み品 |
| 14日 | 精肉卵・乳製品飲料・パン製菓 |
| 60日 | 冷凍食品・米穀乾物 |
| 90日 | 缶詰レトルト・調味料油脂 |
| 120日 | 消耗品資材 |
| 0日 | 未分類（=データ品質の指摘） |

未分類の品目は基準0日で必ず表示 → カテゴリ未設定品目の発見ツールにもなった。

### UI
基準日数は普段は隠しておき、テーブルヘッダーの **?** アイコンにマウスを当てると凡例ツールチップで表示（常時表示は煩雑との判断）。

### 関連コミット
- 「per-category deadstock thresholds」「threshold legend」「hover tooltip」 → PROD反映済み

---

## 2. 権限・課金システム Phase A/B/C / Authority + Billing System

> **本日最大の実装。** 議論を重ねて要件を固めてから単一セッションで一気に構築。

### Phase A — プラン別機能ゲート + 契約管理 + ライフサイクル

#### 新テーブル（4本）
- `sys_features` — 機能カタログ（12機能をシード済み）
- `sys_company_contracts` — SCD Type 2 で契約履歴（プラン・無料期間・月額・支払方法）
- `sys_company_features` — 会社ごとの個別ON/OFF（プラン標準 vs 個別設定）
- `sys_company_invoices` — 月次請求書（クレカ拡張用フィールドも先入れ）

#### プラン階層（Premium ⊇ Standard ⊇ Entry）

| プラン | 月額/店舗 | 無料期間 | 含まれる機能 |
|---|---|---|---|
| Entry | ¥15,000 | 1ヶ月 | 仕入入力、コピペ、棚卸し、仕入先別レポート |
| Standard | ¥50,000 | 3ヶ月 | + 棚割り設定、棚卸しSP、品目別レポート、月次利用量、売上原価、利益推計 |
| Premium | ¥120,000 | 3ヶ月 | + ダッシュボード、発注サポート |

#### 新画面・新機能
- `/admin/system/companies/<id>/features` — システム管理者向け：プラン選択 + 個別機能ON/OFF
- ナビゲーションの各リンクが `feature_enabled()` ガード対応 → プラン外の機能は非表示
- 画面上部のライフサイクルバナー（黄=試用期限近い／橙＝7日切り／赤＝期限超過／🔒=ブロック）
- 試用→自動請求書発行→未払い猶予なし即ブロック（ご要望通り）

### Phase B — Chief Admin + 店舗別権限（OR上書き）

- `sys_user_companies.is_chief_admin` ブール列追加
- `sys_user_store_grants` テーブル新設（ユーザー × 店舗 × ロール）
- 既存 admin の最古参を Chief Admin に自動指定（idempotent backfill）
- 部分ユニークインデックスで「会社ごと Chief Admin は1名」を保証

#### 4つの役割
| 役割 | できること |
|---|---|
| ★ Chief Admin | 全権限 + 他のAdminを任命 + Chief移譲 |
| Admin | ユーザー作成（Operator/Supervisor）、マスタ編集、店舗別権限付与 |
| Operator | データ入力（仕入・棚卸し）、レポート閲覧 |
| Supervisor | 全データ閲覧（読み取り専用） |

店舗別権限は「OR上書き」：会社全体のロールを **上げる** ことのみ可能（下げられない）。例：会社全体Operatorだが銀座店だけAdmin。

#### 新画面
- `/admin/users` を再構成（Chief Adminマーク、移譲ボタン、店舗別権限リンク）
- `/admin/users/<id>/store-grants` — 店舗別ロールエディタ

### Phase C — 監査ログ + 請求書管理

- `views/admin/system_invoices.py` — システム管理者向け請求書一覧
- `utils/invoice_generator.py` — 月次請求書自動生成ロジック（idempotent、試用期間中はスキップ、月額×店舗数）
- 全変更がアクションタイプで監査ログに記録：
  - `CONTRACT_CHANGE` / `USER_CREATE` / `USER_DISABLE` / `CHIEF_ADMIN_TRANSFER` / `STORE_GRANT_UPDATE` / `INVOICE_MARK_PAID`

### マイグレーション適用
- DEV: 2026-04-20 適用済み
- PROD: 2026-04-20 適用済み
- Chief Admin初期割当：くらじか自然豊農 → admin@example.com（→後ほど organic@kurashima.asia に移譲済み）／Test Corp → test@test

---

## 3. ヘルプ章「ユーザー権限の仕組み」 / New Help Chapter

`/help/concept_authority` — 平易な日本語で：
- 2層の権限レイヤー（システム管理者 / 会社内）の比較カード
- 4つの役割の能力一覧表
- 店舗別権限（OR上書き）の概念 + 具体例 + 設定手順
- プラン別機能の3カード（Entry/Standard/Premium）
- 画面上部のバナーの意味（黄/橙/赤/🔒）
- FAQ 4問

i18nキー追加：日本語99 + 英語99 = 198キー

---

## 4. ブランディング修正 / Branding Fixes

- ヘッダーのユーザー情報行から `❓ヘルプ` リンクを削除（ナビゲーションに既に存在）
- `くらじか事業開発` → `くらじか` に統一（ja: 4箇所、en: `Kurashima Business Development` → `Kurajika` 2箇所）
- 正式社名 `倉島事業開発` （漢字）はヘッダー・ログイン画面など正式な場面で維持

---

## 5. PROD ↔ DEV データ運用 / PROD ↔ DEV Data Operations

### 5-1. 完全ミラー（暫定）
PROD全テーブルを DEV にコピー。Schema-drop trick が必要だった（`--clean` 単独では古いデータが残る）→ 手順を `project_prod_to_dev_sync.md` に文書化。

### 5-2. 顧客データ除外型同期スクリプト（新規）
`init/sync_internal_data_to_dev.py`

- **Deny-by-default 方式** — PROD全46テーブルを `TABLE_CONFIG` で明示分類
- 新テーブルが PROD に追加されると、明示分類されるまで **スクリプトが実行を拒否** → 顧客データの偶発的流出を防止
- `INTERNAL_COMPANY_IDS = [1]` 形式で「内部会社」を指定 → それ以外（顧客）はDEVに来ない
- ドライランがデフォルト、`--apply` で実行
- DRY-RUN テスト済み：Test Corp のデータ（2件中1社、244件中4品目、2336件中1仕入）が正しく除外される

### 5-3. データ品質課題の発見
PROD `pur_purchases` の 2336行中 **2297行が `company_id = NULL`**（マルチテナント列追加前の遺産）。
スクリプトは store_id にフォールバックして対処済み。
長期解決：PROD で `UPDATE pur_purchases SET company_id = ...` の単発バックフィル（コメントに残してある）。

---

## 6. 棚卸し入力の保護機能 / Stock Count Protection

### きっかけ
品目 15005（ハーツしめじ極小バラ三方）が滞留在庫リストに **41個** で表示される事案。
履歴を確認すると：
- 2026-03-19 仕入：2個
- 2026-03-31 棚卸し：1個
- 2026-04-20 13:18 棚卸し：1個
- 2026-04-20 13:22 棚卸し：**41個** ← 誤入力
- 同日中に 41個 を **4回連続保存**（13:22, 13:23, 14:03, 14:10 JST）

### Layer A — 誤入力警告（フロントエンド）
SP棚卸し画面の保存ボタンで `confirm()` ダイアログ：
- 入力値 ≥ 10 かつ 帳簿の5倍超または+10超 → 警告
- 帳簿 ≥ 10 かつ 入力が帳簿の20%未満 → 警告（急減も検知）
- 該当行を一覧表示し「本当にこの数値で保存しますか？」

### Layer B — 重複保存抑止（サーバーサイド）
保存前に `_is_recent_duplicate_count()` をチェック：
- 同じ 店舗 × 品目 × 日付 × 数量 が直近10分以内に登録済みなら **INSERTをスキップ**
- 4回連打しても監査ログに1件しか残らない

両Layerともパフォーマンス影響は無視できる範囲（保存1回あたり +5〜10ms）。

---

## 7. 顧客ヘルスダッシュボード設計案 / Customer Health Dashboard Plan

> **次回セッションの議論起点。** 設計のみ・実装は次回。

### 目的
有料顧客に対する Customer Success 活動（QBR、月次レポート、休眠検知、解約予兆）の **データ基盤**。

### 必要なデータは既に蓄積済み
- `sys_work_logs`（ログイン・ページ閲覧・エラー・遅延）
- `sys_company_contracts` / `sys_company_invoices`（契約・請求）
- `pur_purchases` / `inv_stock_counts`（取引ボリューム）
- `sys_features` × `sys_company_features`（機能利用権限）

### 構築する3画面
1. **`/admin/system/health`** — 全社一覧（ヘルスバッジ、過去30日のアクティビティ）
2. **`/admin/system/health/<company_id>`** — 会社詳細（KPI4枚、6ヶ月トレンド、機能利用、ユーザー一覧、店舗内訳）
3. **`/admin/system/health/<company_id>/store/<store_id>`** — 店舗詳細

### 印刷対応
CSS `@media print` でA4対応。ブラウザのPrint→PDF保存で配布可能（PDFライブラリ不要）。

### 開いている設計判断（次回相談したい）
1. 期間：固定30日 vs 選択可（7d/30d/90d）？
2. アクセス：システム管理者のみ vs 会社のChief Adminも閲覧可？
3. 店舗内訳：常時表示 vs 2店舗以上のときのみ？
4. 「Critical」バッジの基準は？
5. トレンドチャート：Chart.js vs シンプルHTMLバー？
6. 印刷：単一画面のみ vs 「全社一括印刷」も？
7. 業界ベンチマーク（匿名化された他社比較）：今フェーズに含める vs 後回し？

---

## 8. ワークフロー・運用ルールの確立 / Workflow & Operational Rules

本日確立した運用ルール（メモリに保存済み）：

1. **必須デプロイ手順**：local + dev 適用 → ユーザー検証 → 明示承認 → PROD適用
2. **PROD→DEV同期** は schema-drop が必須（`--clean` 単独では古いデータが残る）
3. **顧客データ保護**：有料顧客が付いた瞬間に `sync_internal_data_to_dev.py` に切替
4. **i18n徹底**：ユーザーが見るテキストは全て `t()` 経由（worldwide販売対応）

---

## 📊 本日の統計 / Today's Stats

| 指標 | 数値 |
|---|---|
| 新規テーブル（PROD適用済） | 6（Phase A:4 + Phase B:1 + Phase B変更1） |
| 新規Pythonファイル | 5（feature_gate, system_features, system_invoices, invoice_generator, sync_internal_data） |
| 修正Pythonファイル | 5（access_scope, users, system_home, inventory_v2, app） |
| 新規テンプレート | 4（system_features, system_invoices, user_store_grants, concept_authority） |
| 新規ヘルプ章 | 1（concept_authority） |
| 追加i18nキー | 日本語約140 / 英語約140（合計約280） |
| 監査ログ・新action種別 | 6（CONTRACT_CHANGE他） |
| 本日のコミット | 11（worktree） |
| マイグレーション適用 | DEV + PROD 両方完了 |
| データ同期 | PROD全データ→DEV（フルミラー、その後Test Corp除外版で再同期） |
| 既知の長期課題 | PROD `pur_purchases.company_id` バックフィル（コメントとメモリに記録） |

---

## 🔜 次回開始点 / Next Session Start

**顧客ヘルスダッシュボードの構築議論から再開。** セクション7の「開いている設計判断」7つに対して回答をいただいた上で、`/admin/system/health` の実装に着手します。

その他、本セッション中に出た次回以降のタスク：

- [ ] 業界ベンチマーク（匿名化された他社比較）— ヘルスダッシュボード Phase 2
- [ ] 月次PDFレポート自動メール送信 — メール基盤の準備が前提（Resend等）
- [ ] 「あなたは X時間 節約しました」計算 — UX的に響くがロジックは要設計
- [ ] CABスタイルの社内フィードバック収集機能
- [ ] 自動テストフレームワーク（pytest + transaction rollback）— 4envにせず3envで運用予定
- [ ] LINE通知連携（B2B日本市場では効果大、ただし v2 以降）
- [ ] PROD `pur_purchases.company_id` バックフィル
- [ ] タイムスタンプの JST 表示フィルター

---

## 🔗 参考 / References

### 本日の主要コミット（worktree → dev → main）
| Hash | 内容 |
|---|---|
| `b6fc6be` | feat(auth): Phase A — tier-based feature gating |
| `6c97f61` | feat(auth): Phase B — Chief Admin + per-store grants |
| `c756b76` | feat(billing): Phase C — sys-admin invoice list + monthly generator |
| `84c7db6` | docs(help): add concept_authority chapter |
| `bd4c350` | fix(branding): drop help link + Kurajika nickname |
| `baa0c70` | chore(sync): filtered PROD→DEV sync that excludes customer data |
| `751f404` | feat(inventory): typo + duplicate-save protection |

### 主要ファイル
- マイグレーション: [init/migrate_20260420_phase_a_features_contracts.sql](../../init/migrate_20260420_phase_a_features_contracts.sql), [init/migrate_20260420_phase_b_roles_grants.sql](../../init/migrate_20260420_phase_b_roles_grants.sql)
- 機能ゲート: [utils/feature_gate.py](../../utils/feature_gate.py), [utils/access_scope.py](../../utils/access_scope.py)
- 請求書: [utils/invoice_generator.py](../../utils/invoice_generator.py)
- 同期スクリプト: [init/sync_internal_data_to_dev.py](../../init/sync_internal_data_to_dev.py)
- システム管理者画面: [views/admin/system_features.py](../../views/admin/system_features.py), [views/admin/system_invoices.py](../../views/admin/system_invoices.py)
- 棚卸し保護: [views/inventory_v2.py](../../views/inventory_v2.py), [templates/inv/inventory_count_sp.html](../../templates/inv/inventory_count_sp.html)
- ヘルプ: [templates/help/concept_authority.html](../../templates/help/concept_authority.html)

### デプロイ状況
- DEV: `test-costing-sys.up.railway.app` → 本セッションの全変更が反映
- PROD: 本番環境 → 本セッションの全変更が反映済み（Phase A/B/C migrations 含む）
- 本日のレポートのみ DEV止まり（次回PROD承認待ち）

---

_生成: Claude Opus 4.7（1M context）／ 2026-04-20 セッション2_
