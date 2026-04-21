# 2026-04-21 開発報告 / Daily Development Report

> 本日は **Customer Success の基盤** と **Sys Admin 内部組織化** に集中。Client Health Dashboard を新設し、Sys Admin を機能別ロールに分割、運用ドキュメントの内部リファレンス章を整備しました。
>
> Today: built the **Customer Success foundation** and **internal Sys Admin organization**. Shipped a Client Health Dashboard, split Sys Admin into function roles, and added an internal references section.

---

## 🎯 本日の成果サマリ / Highlights

| カテゴリ | 内容 | 影響 |
|---|---|---|
| 顧客分析 | **Client Health Dashboard** 新設（3画面） | 全顧客のエンゲージメント状況が一目で分かる、Churn検知の基盤に |
| 権限再設計 | **Sys Admin を 4ロールに分割**（super/engineer/sales/accounting） | チーム拡張時に各人が自分の領域だけ見られる構造へ |
| 多重ロール | sys_role を VARCHAR → TEXT[] に変更 | 創業期は1人が複数役割を兼任、checkbox UIで柔軟設定 |
| 内部組織化 | **/admin/system を 3セクションに再構成**（Client Companies / Internal / Sys Admins） | 顧客と社内ユーザーが視覚的に分離、混乱解消 |
| 顧客識別 | mst_companies.is_internal フラグ追加 | くらじか自身を「smoke-test customer」として明示、Client Healthから除外 |
| 運用 | + Register New Sys Admin フォーム追加 | super_admin が新スタッフを画面上で追加可能 |
| 文書 | **Sys Admin References** 新設（4章） | Onboarding / Roles / Health interpretation / Patch deployment の手順書 |
| UX | Customer Health → **Client Health** にリネーム | より B2B らしい用語へ |
| 命名 | **倉島事業開発** 維持、**くらじか** ニックネーム化 | 正式社名と通称の使い分け |
| Bug fix | Internal account の Drill-down が動かなかった | get_company_kpis() のフィルタ修正 |
| Bug fix | sys-admin 画面で Operator nav が出ていた | system_features.html / company_new.html に nav guard 追加 |

---

## 1. Client Health Dashboard 新設 / Customer Success Foundation

### 設計判断（前日の議論から確定）

| 質問 | 採用した既定値 |
|---|---|
| 期間 | 固定 30日（v2 で 7d/30d/90d 切替予定） |
| アクセス | sys-admin のみ（v1） |
| 店舗内訳 | 常に表示 |
| Critical badge | 請求書未払い OR 直近7日で5件以上のエラー |
| トレンド | Chart.js（Purchase Dashboard と統一） |
| 印刷 | CSS @media print（PDFライブラリ不要） |
| ベンチマーク | 後回し（v2） |

### 構築した3画面

#### `/admin/system/health` — 全社一覧
- 1社1行、ステータスバッジ（🟢🟡🟠🔴🔵）
- 過去30日のKPI: アクティブユーザー / ログイン数 / 仕入れ件数 / 棚卸し件数 / エラー数
- ステータスフィルターのpill
- 内部アカウント除外（既定）+ 「Include internal」トグル

#### `/admin/system/health/<company_id>` — 会社詳細
- KPI 4枚（active users, logins, purchases, stock counts）
- 6ヶ月トレンド（Chart.js bar chart）
- Feature usage table（12機能 × 30日タッチ数）
- ユーザー別アクティビティ
- 店舗別内訳（drill-down あり）
- 🖨 Print ボタン

#### `/admin/system/health/<company_id>/store/<store_id>` — 店舗詳細
- 店舗単位のKPI + Chart.js（仕入数量・棚卸し件数・¥金額の3軸）
- 🖨 Print ボタン

### Status badge logic

| Badge | Trigger | Action |
|---|---|---|
| 🔴 critical | 請求書未払い OR 7日で5+エラー | 即対応 |
| 🟠 dormant | 30日以上ログインなし | Churn risk 高 |
| 🟡 quiet | 7-30日ログインなし | チェックイン |
| 🟢 healthy | 7日以内にログイン | 通常 |
| 🔵 new | 一度もログインなし | Onboarding 確認 |

### 実装上の発見
- ログインイベントは `sys_work_logs.path = '/login' AND method = POST` だが、`company_id = NULL`（ログイン時点ではセッション未確立）
- **対処**：ログイン数の集計は `sys_sessions` テーブルから（1セッション = 1ログイン成功）

---

## 2. Sys Admin の機能別ロール分割 / Sys Admin Role Split

### 背景
`is_system_admin` 単一フラグでは、チームが拡大した時にすべての sys 画面を全員が見ることになる。Engineer に請求情報、Accounting にエラーログを見せる必要はない。

### 4ロール

| Role | アクセス可能 |
|---|---|
| **super_admin** ★ | 全画面 + 他 sys admin のロール変更 |
| **engineer** | Developer Dashboard, Client Health（技術視点） |
| **sales** | Client Health（顧客接点）, Manage Features（プラン設定） |
| **accounting** | Invoices |

### Migration の段階的進化

朝に最初の migration を打った後、ユーザーから「複数ロール持たせたい」とフィードバック → 同日中に列タイプを `VARCHAR` → `TEXT[]` に変換する2回目の migration を実施。

```sql
-- 朝: VARCHAR 単一値
ALTER TABLE sys_users ADD COLUMN sys_role VARCHAR(20) DEFAULT 'super_admin';

-- 夕方: TEXT[] 多重ロール
ALTER COLUMN sys_role DROP DEFAULT;
ALTER COLUMN sys_role TYPE TEXT[] USING ARRAY[sys_role];
SET DEFAULT ARRAY['super_admin'];
```

### Helper API

```python
# utils/sys_roles.py
get_current_sys_roles() -> List[str]
has_any_sys_role(*roles) -> bool
is_super_admin() -> bool
sys_role_required(*roles)  # decorator, super_admin always allowed
can_access_screen(endpoint) -> bool  # for template tab hiding
```

### UI: Dropdown → Checkboxes
- 各 sys admin 行にチェックボックス群 + Save ボタン
- Tab strip 右端にロールバッジ表示（"sales / accounting" のように joined）

---

## 3. /admin/system の再構成 / Sys Admin Home Restructure

### 課題
Sys admin と client 会社のユーザーが「Users by Company」セクションで混在。混乱の原因。

### 採用案
3セクションに明示分離：

```
[+ Register New Client Company]

🏢 Client Companies (paying / trial)
   - ID, Code, Name, Created, Users, Stores, Tier, Trial Ends
   - Actions: 👤 Users (→ Client Health detail), ⚙ Features

🏠 Internal Accounts (Kurajika's own — 黄色背景)
   - くらじか自然豊農 (id=1) のみ
   - 朝食スタッフが本番運用 + smoke test に使用

🛡 Sys Admins (Kurajika staff)
   - ID, Email, Name, Sys Roles (checkboxes), Active, Created
   - + Register New Sys Admin フォーム（super_admin のみ）
```

### `is_internal` フラグの導入

```sql
ALTER TABLE mst_companies ADD COLUMN is_internal BOOLEAN DEFAULT FALSE;
UPDATE mst_companies SET is_internal = TRUE WHERE id = 1;
```

- Client Health overview は既定で internal を除外
- 「Include internal」トグルで再表示可能

### 「Users by Company」削除
重複情報なので System Admin home から削除。各社のユーザー一覧は Client Health の company detail ページに集約（single source of truth）。

---

## 4. + Register New Sys Admin フォーム

- Sys Admins セクション下に collapsible `<details>` で配置
- フィールド: email, name, password, sys roles (checkboxes)
- super_admin のみアクセス可能
- 新規 sys admin は Kurajika 内部会社（id=1）に admin として参加 → 既存のセッション JOIN ロジックがそのまま使える
- 監査ログ: `action='SYS_ADMIN_CREATE'`

---

## 5. Sys Admin References — Option B 採用 / Internal Documentation

### 設計判断
Operator 向けの `/help` とは完全分離。`/admin/system/help` 配下に独立した help system を構築。

### 構造
```
📘 References (sidebar TOC)
├── 🧭 Concepts
│   ├── Sys Admin Roles & Permissions  (4ロール × 画面マトリクス)
│   └── Client Health: Reading the Badges  (各バッジの判定基準と推奨アクション)
└── ⚙️ Workflows
    ├── Onboarding a New Client Company  (6ステップ + 落とし穴表)
    └── Patch Deployment Workflow  (ブランチモデル + 5ステップ + DB migration)
```

### v2に持ち越し
- Tier & feature override 詳細
- Invoicing workflow
- Database & sync deep-dive
- Emergency runbook（Chief Admin SQL recovery, audit log query集）

---

## 6. UX: 命名・カラー・小さい修正

| 修正 | Before | After |
|---|---|---|
| 名称統一 | "Customer Health" | **"Client Health"** |
| 命名 | くらじか事業開発（正しくない） | くらじか（愛称） |
| 命名 | 正式社名は **倉島事業開発** で維持 | (no change) |
| Tab重複 | 4箇所に inline tabs | `_admin_tabs.html` partial に統一 |
| 隠れバグ | sys-admin 画面に Operator nav が表示 | nav guard 追加（system_features, company_new） |
| Drill-down | Internal account の Users/Features ボタンが「Company not found」 | get_company_kpis() に include_internal=True |

---

## 📊 本日の統計 / Today's Stats

| 指標 | 数値 |
|---|---|
| 新規 SQL migrations | 3 (sys_roles, internal_companies, sys_roles_array) |
| 新規 Python ファイル | 4 (health_metrics, system_health, system_help, sys_roles) |
| 修正 Python ファイル | 6 (system_home, system_features, system_invoices, dev_dashboard, login.py, app.py) |
| 新規 templates | 11 (Client Health × 3, References × 6, _admin_tabs partial × 1, system_home rewrite) |
| 監査ログ・新 action | 2 (SYS_ROLE_CHANGE, SYS_ADMIN_CREATE) |
| 本日のコミット | 9 (worktree → dev → main) |
| Migration 適用 | DEV + PROD 両方完了（3本） |
| Lines added | ~2700 |

---

## 🔜 次回開始点 / Next Session Start

明示的な「次やる」議題はまだ確定していません。候補：

- **Sys Admin の Client Companies アクション「Users」の挙動最終確認**（fix 直後、未検証）
- **Test Corp の整理** — まだ PROD/DEV に残っている。客なのか、削除すべきか確認
- **Client Health の v2** — 期間切替（7d/30d/90d）、業界ベンチマーク準備
- **Email 送信基盤** — Resend 等、月次ヘルスレポート PDF 自動配信のため
- **未開発機能リスト**（営業フライヤー記載）の優先順位付け
  - 納品書OCR / 検品AIカウント / 喫食数入力 / 棚卸しAIカウント / 会計CSV出力 / 単価推移 / 価格変動アラート

---

## 🔗 参考 / References

### 本日の主要コミット（worktree → dev → main）
| Hash | 内容 |
|---|---|
| `4348470` | feat(sys-admin): Customer Health Dashboard |
| `0788145` | fix(sys-admin): restore admin tabs on Invoices + add to Dev Dashboard |
| `6c12a88` | feat(sys-admin): split sys-admin into 4 roles + Customer→Client rename |
| `09a4534` | refactor(sys-admin): split internal vs client + sys-admin section |
| `0ddb4a6` | feat(sys-admin): References section — internal runbooks |
| `bb88615` | feat(sys-admin): multi-role + new sys-admin form + drill-down fix |

### 主要ファイル
- 集計ロジック: [utils/health_metrics.py](../../utils/health_metrics.py)
- ロール helper: [utils/sys_roles.py](../../utils/sys_roles.py)
- Health views: [views/admin/system_health.py](../../views/admin/system_health.py)
- References views: [views/admin/system_help.py](../../views/admin/system_help.py)
- 共通 tabs: [templates/admin/_admin_tabs.html](../../templates/admin/_admin_tabs.html)
- Migrations:
  - [init/migrate_20260421_sys_roles.sql](../../init/migrate_20260421_sys_roles.sql)
  - [init/migrate_20260421_internal_companies.sql](../../init/migrate_20260421_internal_companies.sql)
  - [init/migrate_20260421_sys_roles_array.sql](../../init/migrate_20260421_sys_roles_array.sql)

### デプロイ状況
- DEV: `test-costing-sys.up.railway.app` → 本日の全変更が反映済み
- PROD: 本番環境 → 本日の全変更 + 3本の migration 適用済み
- 本日のレポートのみ DEV 止まり（次回 PROD 承認待ち）

---

_生成: Claude Opus 4.7（1M context）／ 2026-04-21_
