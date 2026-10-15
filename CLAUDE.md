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

詳細は `plugins/mcp/mcp-serena/docs/serena-guide.md` を参照。

## 知識アーキテクチャ

```
AGENTS.md   → ナビゲーション + ポリシー（軽量）
docs/       → リポジトリ知識
skills/     → 実行可能なワークフロー
```

詳細は `docs/specifications/ndf-knowledge-and-kiro.md` を参照。

## NDF の Skill 構成

Skill の配布は `plugins/ndf/manifests/` が唯一の基準（数と内訳は manifests の行数が持つ）。ブラウザ自動テストの 4 個は `playwright-kit` プラグインへ分離した（`plugins/playwright-kit/`）。frontmatter の書き方は `plugins/ndf/skills/AUTHORING.md` の規約に従い、`python3 scripts/check-skill-frontmatter.py` でチェックする。利用実績と維持・統合・削除の判定は `docs/specifications/ndf-skill-inventory/` に記録する。

## 版ごとの判断の記録

**リリース済み版の判断と理由は確定仕様（[docs/specifications/](docs/specifications/)）が持つ。**
v10.17.51 までは [docs/ndf-version-decisions.md](docs/ndf-version-decisions.md) にあり、以後は書き足さない。
変更点は `CHANGELOG.md` が持つ。これらの参照に `@` を付けない（全セッションが毎回読むことになる）。

- `CLAUDE.md` に残すのは現行版で決めたことだけ。リリースした版の段落はその主題の確定仕様へ移す（手順は `release`）
- 版が決まる前の段落は「の次の版で」で書き出す（`.ndf/instructions.json` の `pending_marker`）
- チェックは `python3 plugins/ndf/scripts/instructions-check.py --root .`。設定の書き方は `release` の `references/instruction-files.md`

チェックの理由と経緯は `docs/specifications/ndf-instruction-files-check.md` に、知識の置き場所は `docs/specifications/ndf-knowledge-and-kiro.md` にある。

## cross-refactoring・cross-review

使い方と仕様は `plugins/ndf/skills/cross-refactoring/SKILL.md`・`plugins/ndf/skills/cross-review/SKILL.md` と、確定仕様の `docs/specifications/cross-refactoring-*.md`・`docs/specifications/cross-review-*.md` にある。
