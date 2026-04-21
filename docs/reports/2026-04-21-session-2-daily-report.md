# 2026-04-21 Session 2 開発報告 / Dev Report

> 本セッションは **Phase B 権限基盤の実装穴をふさぐ** ことから始まり、そのまま **統合照会レポート新設** と **差分保存型の棚卸し画面** まで一気に広がりました。11 コミット、本日後半だけで 2,500+ 行の追加。すべて LOCAL → DEV → PROD の手順で適用済み。
>
> This session started as "plug the holes in Phase B auth" and grew into a new integrated purchase + usage report and a smart-save inventory flow. 11 commits, ~2,500 LOC added this afternoon, all shipped through LOCAL → DEV → PROD.

---

## 🎯 成果サマリ / Highlights

| カテゴリ | 内容 | 影響 |
|---|---|---|
| セキュリティ | **Operator の店舗アクセス制限** を実装 | Phase B の grants テーブルが初めて効くように。くらじか自然豊農の Operator が 屋台 のみ見えるべきだった問題を修正 |
| セキュリティ | 5 画面のクロス店舗データ漏れ | dashboard / 仕入照会 / 使用量 / 売上原価 / 直近仕入れ — すべて "店舗未選択 → 空表示" に統一 |
| 管理機能 | **ナビ表示設定**（会社別・ロール別） | Chief Admin が Operator / Supervisor の nav を会社ポリシーで制御可能 |
| レポート | **統合照会** 新設 | 期首/仕入/使用/繰越 × 単価/数量/金額 × 12ヶ月のマトリクス、sticky ヘッダー＋品目列 |
| パフォーマンス | feature_gate のリクエスト内キャッシュ | 全ページで nav レンダリング 6s → <1s（Railway 実測） |
| パフォーマンス | 統合照会の N+1 クエリ解消（LATERAL） | 10s → 2-3s |
| 棚卸し | **差分保存版 (v3) 新設** laptop + SP | 変更した品目だけ保存、品目ごとにタイムスタンプ記録、最終棚卸日を各行表示 |
| UX | 全画面で 店舗ドロップダウンを「選択してください」に統一 | 操作性一貫性 |

---

## 1. Phase B の実装穴ふさぎ / Securing Phase B grants

### 1-1. `get_accessible_stores()` が grants を見ていなかった

発端はユーザー報告：「Operator on くらじか自然豊農, granted only 屋台 なのに アパ朝食 と 屋台 両方見えている」。

[utils/access_scope.py:38-47](utils/access_scope.py:38) の get_accessible_stores() は **全社店舗を返すだけで、function role も grants テーブルも見ていなかった**。docstring には Phase B の OR overlay semantics が書かれていたが、コード側が未実装。

**修正**:
- `admin` 役 → 全社店舗（従来通り）
- `operator` / `auditor` → `sys_user_store_grants` に登録された店舗のみ
- マイグレーション未適用環境への legacy fallback 維持

### 1-2. 5 画面のクロス店舗データ漏れ

`get_accessible_stores()` だけ直しても不十分。各レポートが「店舗未選択時は全社集計」フォールバックを持っていたため、Operator にも他店舗データが集計表示されていた。

**修正**: order-support パターンを 5 画面に統一適用。
- Purchase Dashboard / Purchase Report / Usage Report / Cost Report / 直近仕入れ
- 店舗未選択 → 空データ＋誘導バナー
- 店舗選択 → その店舗のみクエリ（従来の normalize_accessible_store_id が弾く）

ユーザーフィードバックで：
- 黄色バナーは冗長 → 削除
- `全店舗` / `全て` → `選択してください` に統一
- 単店舗自動選択ロジックは **削除**（複数店舗権限を持つユーザーとの一貫性のため、ユーザー希望）

---

## 2. ナビ表示設定 / Per-company Nav Policy

Chief Admin が会社ごとに「どのナビ項目を Operator / Supervisor に見せるか」を制御する管理画面を追加。

### 設計

| 要素 | 内容 |
|---|---|
| テーブル | `sys_company_nav_policies` (company_id, role, nav_key, visible) |
| ヘルパー | `nav_allowed(nav_key)` を Jinja global として公開 |
| デフォルト | マスタ系（仕入先/品目/店舗）は hidden、エントリ・レポート・ヘルプは visible |
| UI | `/admin/nav-policy` — Chief Admin only、トグルマトリクス |
| エントリ | `/admin/users` の上部に「⚙ ナビ表示設定」ボタン追加 |

**admin 役は常に全表示** — この設定の影響を受けない。ユーザー質問「feature flag も効く？」→ Yes、nav_allowed() AND feature_enabled() の両方通過が必要。

---

## 3. 統合照会 (Integrated Report) 新設 / New Integrated Drill-down

ユーザー要望：「Operator が仕入れと使用量を同時に見たい。amount と cost と price を一画面で」。

### マトリクス構造（ユーザー指定）

```
| 品目 | 指標 | 2025-05 (単価|数量|金額) | 2025-06 (...) | ...
| ITEM1 | 期首 |     ¥240 | 2  | ¥480    |
|       | 仕入 |     ¥240 | 6  | ¥1,440  |
|       | 使用 |     —    | —  | —       |
|       | 繰越 |     —    | —  | —       |
```

- 4 サブ行/品目（期首 / 仕入 / 使用 / 繰越）
- 3 サブ列/月（単価 / 数量 / 金額）
- デフォルト 12 ヶ月、開始月／終了月で過去データへ遡れる
- Sticky ヘッダー（上）＋ sticky 品目列（左）で長いリストでも列ラベルが見える
- 各品目に「最終棚卸日 YYYY-MM-DD HH:MM」 — データ鮮度が一目で分かる

### 計算

- `仕入単価 = 仕入金額 ÷ 仕入数量`
- `繰越` = その月の最終 stock_count、直近仕入単価で評価
- `期首` = 前月の繰越を引継
- `使用 = 期首 + 仕入 − 繰越` （両方揃った月のみ計算）

### ナビ独立化

初期版は「仕入れ照会」画面の 📊 バッジ経由でしか行けなかったが、ユーザー要望で **新ナビ項目 🧮 統合照会** として独立。supplier フィルタを任意化。

---

## 4. パフォーマンス改善 / Performance

統合照会で PROD が "Internal Server Error"（Railway の worker timeout）。プロファイリングで 2 つのホットスポット発見：

### 4-1. feature_gate N+call（全ページ共通）

`feature_enabled()` が nav の 7 項目から呼ばれ、各回 2-3 DB クエリを非キャッシュで実行 → 1 ページあたり ~23 クエリ、~6 秒。**全ページ影響**。

**修正**: `_load_company_bundle(company_id)` で contract + feature catalog + overrides を 1 回で取得、`flask.g` にキャッシュ。`get_lifecycle_state`・`inject_current_company` も同様にキャッシュ化。

### 4-2. 統合照会の N+1 単価ルックアップ

各 stock_count 行に対し「count_date 以前の最新 purchase unit_price」を個別 SELECT → 200+ 往復。

**修正**: `LEFT JOIN LATERAL` で 1 クエリ化。

### 4-3. Jinja セル条件を Python へ前処理

37K セルの条件ロジック（None チェック、¥ プレフィックス、CSS クラス）を view で事前計算、テンプレートは `{{ c.price_text }}` のみ。

**結果**（ローカル→Railway DB 実測）:
- 統合照会: 10s → **2-3s**（Railway 内部では <2s と推定）
- 全ページの nav レンダリング: 6s → **<0.5s**（ユーザーは他画面も高速化を体感）

---

## 5. 差分保存版 棚卸し画面 (v3) / Smart-Save Inventory

ユーザーの Operator が「1 品目ずつ、自分のペースで棚卸したい。毎回全件保存は重い」と。品目ごとに保存タイムスタンプを持たせる必要があった。

### 設計判断

ユーザーの提案した論理を採用：
1. 保存ボタン押下時
2. 各入力の `data-original` と現在値を比較
3. **変更された品目だけ** POST
4. サーバ側の既存ループ（`counted_qty が空なら skip`）で自動処理

→ DB マイグレーション不要、既存の `stock_counts.created_at` が各行のタイムスタンプになる。

### 実装（Option B: 新画面を並設）

| 画面 | URL | 既存との関係 |
|---|---|---|
| Desktop v3 | `/inventory/count_v3` | 既存 v2 画面はそのまま残置（safety net） |
| SP v3 | `/inventory/count_sp_v3` | 既存 SP 画面もそのまま残置 |
| Nav | ✨ 棚卸し入力（差分保存） | 既存「棚卸し入力」と並べて追加 |

### UI

**Desktop**: 従来のテーブル + `最終棚卸` 列追加 + 上部に「変更: N 件」ライブカウンタ + フッター sticky 保存ボタン。

**SP**: 既存 SP の **片手操作レイアウトをそのまま踏襲**（BIG 赤 − / 小灰 ＋ / ゾーン折り畳み / 品目選択）。「変更数」バッジを topbar に追加。`data-touched` フラグで「ステッパータップしたかどうか」を明示追跡し、初期表示で「全部 changed」に見える誤検知を回避。

保存時：
1. JS が outlier（typo）チェック — 変更行のみ対象
2. 未変更入力を `disabled` に → ブラウザが送信しない
3. サーバは既存コードのまま、送られてきた行だけ INSERT
4. 各行は独立した `created_at` を持つ

---

## 6. 小さめの UX 改善

- 統合照会のクエリが空データのとき「全仕入先」表示で 3.3MB HTML → gzip 込みで転送重かった。pre-computed cells で 1.8MB に縮小。
- Operator 用の nav で「仕入先マスタ/品目マスタ/店舗マスタ」を**デフォルト非表示**に（NAV_DEFAULT_VISIBILITY）。Chief Admin が必要なら会社ごとに on にできる。
- `common.select_store_option` 新設、8 テンプレートで統一。

---

## 7. デプロイ記録 / Deploy Record

| 項目 | DEV | PROD |
|---|---|---|
| `sys_company_nav_policies` マイグレーション | ✅ 適用済 | ✅ 適用済 |
| 全コードチェンジ | ✅ dev ブランチ | ✅ main ブランチ |
| マージコミット | — | `d51df9f` merge: dev → main — 2026-04-21 session 3 |
| 動作確認 | 統合照会＋差分保存の両方 OK | ユーザー確認後 |

### 本セッションの主要コミット

```
7303605 fix(inventory/sp-v3): restore single-hand layout (big － / small ＋, zones, selection)
263b880 feat(inventory): smart-save v3 screens (desktop + sp) save only changed items
82a2d71 perf: cache company name + pre-format integrated report cells
39c44cf perf(reports): cache feature_gate lookups + collapse N+1 price query
76d27fe refactor(reports): integrated view becomes top-level nav item, new matrix layout
69aec55 feat(reports): integrated drill-down — purchase + usage per item per month
2e3495b ui(nav): unify store dropdown default label to 「選択してください」
4ba2c66 revert(reports): drop single-store auto-select — always require explicit pick
4f392ac ui(reports): drop empty-state banner, use "選択してください" as store dropdown default
48a198e feat(admin): per-company nav visibility policy for operator/auditor
37bc0d9 fix(reports): require store selection to prevent cross-store data leaks
ce0e5b3 fix(access): operators now see only granted stores, not all company stores
```

---

## 8. 翌営業日への申し送り / Handoff

- [ ] PROD 動作確認：くらじか自然豊農 の Operator で屋台のみ見える / 統合照会が実データで表示される / 差分保存版で 1 品目だけ保存 → 最終棚卸日が記録される
- [ ] 必要なら `/admin/nav-policy` で他の会社向けにナビ調整
- [ ] 統合照会の評価ロジック（繰越金額 = 直近仕入単価 × 数量）が cost_report の FIFO 評価と微差になることがある。気になる場合は整合を検討

---

以上、本日はここまで。お疲れさまでした 🎉 / That's all for today — nice work.
