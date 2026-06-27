---
name: playwright-test-planning
description: "Plan E2E tests and classify page roles."
when_to_use: "E2E テストの計画立案 / page role 分類 / テスト技法の選定 / チェックリスト活用が必要なとき。Triggers: 'テスト計画', 'テスト計画立案', 'page role', 'HTSM', 'ISTQB', 'FEW HICCUPPS', 'チェックリスト', 'テスト技法', 'テスト設計'"
allowed-tools:
  - Read
  - Bash(python *)
---

# E2E テスト計画 (理論ベース)

HTSM / ISTQB / FEW HICCUPPS に基づいて E2E テストシナリオを計画する。

## 計画ワークフロー

```
[A] 対象 URL を渡される
      │
[B] page role を判定           → scripts/classify_page_role.py --url <URL>
      ▼
[C] 該当チェックリストを開く   → docs/checklists/checklist-{role}.md
      │  全項目を「適用」or「不適用 (理由付き)」で判定
      ▼
[D] 必須テスト技法を確定       → docs/03-test-techniques.md § 11
      ▼
[E] pytest テストを書く        → templates/test_<role>.py.template を起点に
      ▼
[F] スクリプト作成へ → /ndf:playwright-script-creation
      テスト計画が確定したら、テストスクリプトの作成に進む。
      テスト計画が完了するまでスクリプト作成には進まない。
```

## page role 一覧

| role | 説明 | 例 |
|---|---|---|
| lp | ランディングページ | トップ、LP |
| list | 一覧ページ | 商品一覧、記事一覧 |
| item | 詳細ページ | 商品詳細、記事詳細 |
| edit | 編集ページ | プロフィール編集 |
| form | 申込・入力フォーム | 会員登録、問い合わせ |
| search | 検索ページ | サイト内検索 |
| dashboard | ダッシュボード | 管理画面トップ |
| auth | 認証ページ | ログイン、パスワードリセット |
| cart-checkout | カート・決済 | ショッピングカート |
| modal-wizard | モーダル・ウィザード | ステップ型入力 |

## チェックリスト

`playwright-test-planning/docs/checklists/` 配下に role 別チェックリストがある:

```
docs/checklists/
├── checklist-common.md        # 全 role 共通項目
├── checklist-lp.md
├── checklist-list.md
├── checklist-item.md
├── checklist-edit.md
├── checklist-form.md
├── checklist-search.md
├── checklist-dashboard.md
├── checklist-auth.md
├── checklist-cart-checkout.md
└── checklist-modal-wizard.md
```

## 方法論ドキュメント

`playwright-test-planning/docs/` 配下:

| ファイル | 内容 |
|---|---|
| `01-methodology.md` | HTSM / FEW HICCUPPS / ISO 29119-3 の概要 |
| `02-page-roles.md` | page role 分類の詳細定義 |
| `03-test-techniques.md` | テスト技法 (EP/BVA/Decision Table/Pairwise) + role 必須マッピング |
| `04-playwright-mapping.md` | Playwright API → role / 観点 マッピング |
| `05-bug-report.md` | 不具合報告書の仕様 (ISO 29119-3 + FEW HICCUPPS oracle) |

## 補助スクリプト

スクリプトの実行は `/ndf:playwright-kit-ops` skill を参照。主なコマンド:

```bash
# page role を自動推定 (playwright-kit-ops/scripts/ 配下)
python scripts/classify_page_role.py --url <URL>

# Playwright codegen で操作を記録 → テストコードに変換
python scripts/record_scenario.py <URL>
```

> 上記は `playwright-kit-ops/` ディレクトリ内での実行を想定。詳細は `/ndf:playwright-kit-ops` を参照。

## 関連 Skill

- `/ndf:playwright-script-creation` — テストスクリプト作成 (次のフェーズ)
- `/ndf:playwright-execution` — テスト実行 + エビデンス収集
- `/ndf:playwright-scenario-test` — 全機能を統括したフルワークフロー
