---
name: playwright-planning
description: "Plan Playwright E2E tests: judge the page role, then pick checklists and techniques. Use when designing E2E test cases（テスト計画書・テスト観点・page role 分類）."
allowed-tools:
  - Read
  - Bash(python *)
---

# E2E テスト計画 (理論ベース)

HTSM / ISTQB / FEW HICCUPPS に基づいて E2E テストシナリオを計画する。
本 Skill は E2E ワークフローの入口であり、全体像の提示と計画フェーズの実行を担う。

## 大原則

1. **再現可能なテストスクリプトを実装してからテストを実施する**
2. **テストスクリプトは ndf plugin 非依存でプロジェクトフォルダに設置する**
3. **テスト実行はエビデンス動画を常に取得する** (オプションで明示的にスキップ可能)

## 全体ワークフロー

```
[1] テスト計画            /ndf:playwright-planning   ← 本 Skill
      │  対象 URL → page role 判定 → チェックリスト → テスト技法確定
      ▼
[2] スクリプト作成と実行  /ndf:playwright-authoring
      │  テンプレート → 実装 → 再現可能性レビュー → 実行 + エビデンス収集
      │  ※ スクリプトが完成するまでテスト実行に進まない
      ▼
[3] 証跡とレポート        /ndf:playwright-evidence
      │  reports/<run-id>/report.md 生成 → Google Drive 保管・共有
      ▼
[任意] 実行環境の運用     /ndf:playwright-kit-ops
       init_project / 単発スキャン / アップロードスクリプト (任意タイミング)
```

## クイックスタート

1. プロジェクト初期化: `/ndf:playwright-kit-ops` で `./scripts/init_project.sh /path/to/your-app` を実行
2. 設定編集: `scenario-test/scenario.config.yaml`
3. テストスクリプト作成: `scenario-test/tests/test_*.py` (→ `/ndf:playwright-authoring`)
4. テスト実行 (動画デフォルト ON): `./scenario-test/run.sh`
5. 動画スキップ: `./scenario-test/run.sh --pwk-no-video`

→ `your-app/scenario-test/` は ndf plugin 非依存。単体で完結する。

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
[F] スクリプト作成と実行へ     → /ndf:playwright-authoring
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

`playwright-planning/docs/checklists/` 配下に role 別チェックリストがある。
`checklist-common.md` が全 role 共通項目 (accessibility / Core Web Vitals / セキュリティ / i18n) で、
残りは上表の role 名に対応する `checklist-{role}.md` である。

## 方法論ドキュメント

`playwright-planning/docs/` 配下:

| ファイル | 内容 |
|---|---|
| `README.md` | 方法論ドキュメント全体の索引と利用フロー |
| `01-methodology.md` | HTSM / FEW HICCUPPS / ISO 29119-3 の概要 |
| `02-page-roles.md` | page role 分類の詳細定義 |
| `03-test-techniques.md` | テスト技法 (EP/BVA/Decision Table/Pairwise) + role 必須マッピング |
| `04-playwright-mapping.md` | Playwright API → role / 観点 マッピング |
| `05-bug-report.md` | 不具合報告書の仕様 (ISO 29119-3 + FEW HICCUPPS oracle) |
| `06-pytest-playwright.md` | pytest-playwright fixture / CLI option と NDF 拡張の対応 |

## 補助スクリプト

スクリプトの実行は `/ndf:playwright-kit-ops` を参照。計画フェーズで使う主なコマンド:

```bash
# page role を自動推定
python scripts/classify_page_role.py --url <URL>

# Playwright codegen で操作を記録 → テストコードに変換
python scripts/record_scenario.py <URL>
```

> 上記は `playwright-kit-ops/` ディレクトリ内での実行を想定。

## 用語集

用語 (accessibility, web vitals, LCP, CLS, TTFB, HAR, trace, overlay, body_check, page role, pwk) は
`docs/README.md` の「用語」節、および playwright_kit ランタイムの README (`playwright-kit-ops/templates/runtime-README.md`) を参照。

## 関連 Skill

- `/ndf:playwright-authoring` — スクリプト作成と実行 (次フェーズ)
- `/ndf:playwright-evidence` — 証跡とレポート
- `/ndf:playwright-kit-ops` — 実行環境の運用 (init_project / スキャン / アップロード)
