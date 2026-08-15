# AI Plugins - Claude Code開発ガイドライン

## 基本ガイドライン

プロジェクトの基本的な開発ガイドラインは **@AGENTS.md** を参照してください。

このファイルには、Claude Code固有の設定のみを記載します。

## Serena MCP（コードインテリジェンス）

Serena MCPは**mcp-serena**プラグインとして提供されます（NDFとは別プラグイン）。

用途はコードインテリジェンスのみ:
- シンボル検索・リファレンス検索
- セマンティックコードナビゲーション
- シンボル単位のリファクタリング

**Serena memoryは使用禁止**。知識は `docs/` に、手順は `skills/` に配置してください。

詳細は `plugins/mcp/shared/mcp-serena/docs/serena-guide.md` を参照。

## 知識アーキテクチャ

```
AGENTS.md   → ナビゲーション + ポリシー（軽量）
docs/       → リポジトリ知識
skills/     → 実行可能なワークフロー
```

詳細は `docs/specifications/ndf-knowledge-and-kiro.md` を参照。

## NDF v8.0.0 の Skill 構成

Skill は 30 個で、配布は `plugins/ndf-shared/manifests/` が唯一の基準（Claude Code 26 / Codex 24 / Kiro 25）。ブラウザ自動テストの 4 個は `playwright-kit` プラグインへ分離した（`plugins/playwright-kit-shared/`）。frontmatter の書き方は `plugins/ndf-shared/skills/README.md` の規約に従い、`python3 scripts/check-skill-frontmatter.py` で検査する。利用実績と維持・統合・削除の判定は `docs/specifications/ndf-skill-inventory.md` に記録する。

v6.1.0 で開発方法論レイヤーの 5 個（`development-workflow` / `requirements-design` / `tdd-cycle` / `refactoring`（当時は `safe-refactoring`）/ `quality-gates`）を追加した。モード判定の基準を持つのは `development-workflow` だけで、他の Skill とエージェント定義は判定結果を受け取る側に徹する。

v7.0.0 で playwright 系 4 個を `playwright-kit` プラグインへ分離した。対応表は予告どおり v8.0.0 で削除済み。

v8.0.0 で `safe-refactoring` を `refactoring` へ改名し、分岐・反復・定数の表現を決める観点を統合した。観点は `references/data-representation.md` に置き、言語固有の手段は `references/language-notes.md` にだけ置く。言語を追加するときも他のファイルは変更しない。対応表は `ndf-policies` にある（v9.0.0 で削除）。

v6.0.0 の対応表（`review` → `pr-review`）は予告どおり削除済み。v6.0.0 以前から移行する場合は v6.1.0 の `ndf-policies` を参照する。

## cross-review

`/ndf:cross-review` は codex / gemini の両方に PR レビューを委譲し、両者が `APPROVE` するまで修正ループを回す。Gemini の progress log を heartbeat に表示するため、無言に見える時間でも `scan` / `analyze` / `post` / `done` などの作業段階を確認できる。

追加レビュー観点は以下のどちらかで渡す:

```bash
/ndf:cross-review 123 --focus "ドキュメントとコードの整合性を重点的に確認"
/ndf:cross-review 123 --extra-instructions-file /tmp/review-focus.md
```

PR の変更ファイルから docs only / code / DB migration / test / dependency / CI設定 / API契約 / 認証認可 / frontend / performance / deletion / generated / i18n / infra を自動分類し、該当するレビュー観点テンプレートも codex / gemini 両方に渡す。

## 検証

```bash
claude plugin validate
```
