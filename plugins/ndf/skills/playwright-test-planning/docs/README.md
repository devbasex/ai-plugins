# Web シナリオテスト方法論ドキュメント

このディレクトリは Playwright シナリオテスト Skill が依拠する**テスト方法論**をまとめている。
SKILL.md は実行手順とナビゲーションに集中し、「なぜそう書くか」「どこまで網羅するか」の理論はここに集約する。

AI/人間どちらが計画書を書く場合も、まず該当 page role のチェックリストを参照し、
記載の oracle (FEW HICCUPPS の何軸) を脚注として残すこと。経験で書かない。

## 構成

| パス | 内容 | 想定読者 |
|---|---|---|
| `01-methodology.md` | テスト方法論総論 (HTSM / ISTQB / FEW HICCUPPS の本 Skill 適用) | テスト計画立案者 |
| `02-page-roles.md` | page role 分類体系と識別ヒューリスティック | 計画立案者 / 自動分類スクリプト |
| `03-test-techniques.md` | テスト技法ライブラリ (EP / BVA / Decision Table / State Transition / Pairwise) | テストケース設計者 |
| `04-playwright-mapping.md` | Playwright API → page role / 観点 マッピング | 自動化実装者 |
| `05-bug-report.md` | bug report 仕様 (ISO/IEC/IEEE 29119-3 ベース) + エビデンス自動収集 | テスト実行者 / 報告者 |
| `06-pytest-playwright.md` | pytest-playwright fixture / CLI option / NDF 拡張との対応関係 | 自動化実装者 / 利用者 |
| `checklists/checklist-common.md` | 全 role 共通: accessibility (axe-core) / Core Web Vitals / セキュリティ / i18n | 全テスト |
| `checklists/checklist-lp.md` | Landing Page (外部到達ページ) | LP 担当 |
| `checklists/checklist-list.md` | 一覧ページ | 一覧担当 |
| `checklists/checklist-item.md` | 詳細ページ | 詳細担当 |
| `checklists/checklist-edit.md` | 編集ページ | 編集担当 |
| `checklists/checklist-form.md` | 申込フォーム (複数ステップ) | フォーム担当 |
| `checklists/checklist-search.md` | 検索ページ | 検索担当 |
| `checklists/checklist-dashboard.md` | ダッシュボード | dashboard 担当 |
| `checklists/checklist-auth.md` | 認証 (login / logout / 2FA) | auth 担当 |
| `checklists/checklist-cart-checkout.md` | カート / チェックアウト / 決済 | EC 担当 |
| `checklists/checklist-modal-wizard.md` | モーダル / ウィザード | UI コンポーネント担当 |

## 利用フロー

```
1. 対象 URL を見て page_role を判定
   → docs/02-page-roles.md の識別ヒューリスティックを使用
   → スクリプト: playwright-kit-ops/scripts/classify_page_role.py

2. 該当 role の checklist を開く
   → docs/checklists/checklist-{role}.md を全項目走査
   → 各項目には適用すべきテスト技法 (EP/BVA/...) と oracle が併記されている

3. pytest テストを書く
   → templates/test_<role>.py.template を起点に test 関数を追加
   → `playwright codegen` で操作録画 → そのまま test に貼ってもよい
   → `@pytest.mark.page_role(...)` を付ければ accessibility / web_vitals が autouse で走る

4. 実行 → エビデンス収集
   → ./scenario-test/run.sh で実行 (init_project.sh 後の標準パス)
     ※ 直接 `cd scenario-test && uv run pytest --pwk-config=./scenario.config.yaml` でも可
   → trace.zip / video / screenshot / HAR / console log / accessibility / web_vitals を自動収集
   → scenario-test/reports/<run-id>/report.md が ``pytest_terminal_summary`` で生成される

5. bug 発見 → docs/05-bug-report.md に従って報告
   → 全 bug に oracle (FEW HICCUPPS) と severity を付与
```

## 出典

このドキュメント群は次の一次資料に基づく。詳細は各文書末尾の「参考文献」節を参照:

- **HTSM** (Heuristic Test Strategy Model) v6.3 — James Bach, Satisfice
- **ISTQB CTFL 4.2** — Black-box Test Techniques
- **ISO/IEC/IEEE 29119-3:2021** — Test documentation (Incident Report)
- **WCAG 2.2** — W3C Web Content Accessibility Guidelines
- **OWASP Top 10:2025** + ASVS + WSTG
- **web.dev Core Web Vitals** — LCP / INP / CLS
- **Test Heuristics Cheat Sheet** — Hendrickson / Lyndsay / Emery
- **FEW HICCUPPS** — Bach / Bolton oracle heuristic

## 用語

| 用語 | 定義 |
|---|---|
| **page role** | ページの**機能的役割** (LP / list / item / edit / form / ...)。URL や SEO 構造とは別に、ユーザの目的・必要なテスト観点で分類する単位 |
| **oracle** | 「これは不具合だ」と判定する根拠 (仕様 / 過去版 / 標準 / ユーザ期待 / 内部一貫性 等) |
| **FEW HICCUPPS** | oracle の 11 軸 (Familiarity, Explainability, World, History, Image, Comparable products, Claims, User expectations, Product, Purpose, Statutes/Standards) |
| **HTSM** | テスト戦略を Mission / Environment / Product Elements (SFDIPOT) / Quality Criteria の 4 軸で構造化するモデル |
| **SFDIPOT** | Product Elements の 7 因子 (Structure / Function / Data / Interfaces / Platform / Operations / Time) |
| **EP / BVA** | Equivalence Partitioning / Boundary Value Analysis |
