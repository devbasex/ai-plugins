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

## NDF v5.0.0 の Skill 構成

Skill は 29 個で、配布は `plugins/ndf-shared/manifests/` が唯一の基準（Claude Code 25 / Codex 23 / Kiro 24）。frontmatter の書き方は `plugins/ndf-shared/skills/README.md` の規約に従い、`python3 scripts/check-skill-frontmatter.py` で検査する。利用実績と維持・統合・削除の判定は `docs/specifications/ndf-skill-inventory.md` に記録する。

v4.20.1 から統合・削除で消えたコマンド名の対応表は `ndf-policies` skill にある（v6.0.0 で削除）。

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
