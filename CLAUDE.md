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

## NDF v8.4.0 の Skill 構成

Skill は 31 個で、配布は `plugins/ndf-shared/manifests/` が唯一の基準（Claude Code 27 / Codex 25 / Kiro 26）。ブラウザ自動テストの 4 個は `playwright-kit` プラグインへ分離した（`plugins/playwright-kit-shared/`）。frontmatter の書き方は `plugins/ndf-shared/skills/README.md` の規約に従い、`python3 scripts/check-skill-frontmatter.py` で検査する。利用実績と維持・統合・削除の判定は `docs/specifications/ndf-skill-inventory.md` に記録する。

v6.1.0 で開発方法論レイヤーの 5 個（`development-workflow` / `requirements-design` / `tdd-cycle` / `refactoring`（当時は `safe-refactoring`）/ `quality-gates`）を追加した。モード判定の基準を持つのは `development-workflow` だけで、他の Skill とエージェント定義は判定結果を受け取る側に徹する。

v7.0.0 で playwright 系 4 個を `playwright-kit` プラグインへ分離した。対応表は予告どおり v8.0.0 で削除済み。

v8.0.0 で `safe-refactoring` を `refactoring` へ改名し、分岐・反復・定数の表現を決める観点を統合した。観点は `references/data-representation.md` に置き、言語固有の手段は `references/lang-<言語>.md` に 1 言語 1 ファイルで置く。SKILL.md が対象言語のファイルだけを読ませるため、他言語の内容はコンテキストに載らない。言語を追加するときも他のファイルは変更しない。対応表は `ndf-policies` にある（v9.0.0 で削除）。

v8.1.0 で `cross-refactoring` を追加した。あわせて収束ループの共通層を `plugins/ndf-shared/skills/cross-review/scripts/lib/` へ切り出し、`monitor.py` は同ディレクトリへ移設して既存パスをシムにした。`cross-review` の挙動と既存テストは変えていない。

v8.2.0 で `cross-refactoring` の実機検証（PR #118）で見つかった 9 件の不具合を直した。`cross-review` と共通層は変更していない。詳細は `issues/issue-113-cross-refactoring-defect-fixes.md`。

v8.3.0 で `cross-refactoring` の公開の責務を進行側へ一本化した（**破壊的**）。実装担当は push せず、進行側が検証を通してから push する。あわせて `--sync-command` を新設し、適用で失敗した項目を対象外へ記録するようにした。詳細は `issues/issue-113-cross-refactoring-push-ownership.md`。

v8.4.0 で `markdown-writing` に「敬意と節度のある表現で書く」（ルール 4）を追加し、以降のルール番号を 1 つ繰り下げた。強い否定語・過剰な装飾語・根拠の曖昧な断定の 3 種を扱い、セルフチェックの grep も 3 種に分けた。あわせて `01-diagram-guide.md` を図表ルールの冒頭から手順として読ませ、上限値や記法は SKILL.md へ書かずガイド側に置く構成にした（実測で読み込み挙動を確認した結果）。`pr` は完了報告を `### 6. 完了報告` として手順に組み込み、テンプレートと PR URL の書き方（生の URL を書く）を定めた。

v6.0.0 の対応表（`review` → `pr-review`）は予告どおり削除済み。v6.0.0 以前から移行する場合は v6.1.0 の `ndf-policies` を参照する。

## cross-refactoring

`/ndf:cross-refactoring` は codex / gemini / kiro / claude のうち **ホストを除く 3 者**に構造改善を提案させ、**gemini を除く 3 者**から輪番で選んだ 1 者が適用し、残り 2 者がレビューする。新しい提案が出なくなるまで繰り返す。

```bash
/ndf:cross-refactoring 130 --scope src/services --baseline-test "pytest -q"
/ndf:cross-refactoring 130 --scope src --model codex=gpt-5.5 --model claude=opus-5
```

- `--scope` は必須。提案が発散して PR が肥大するのを防ぐ。**検証にも効く**ので、現状固定テストの置き場所も含める
- ホストと同じランタイムが適用担当になる場合も、サブエージェントではなく **CLI プロセス**として起動する
- モデルを比べるなら `--model kiro=<name>` を必ず指定する（既定 `auto` は実際に動いたモデルを取得できない）
- 収束しない改善項目は **項目単位で取り消す**。合意済みの項目は PR に残る。ただし同一ファイルの隣接行を触る項目どうしは git だけでは分離できないため、そのラウンドは全件取り消しへ退避する
- 生成物・配布物の同期は **進行側の責務**。実装担当にはさせない（範囲外の変更になる）。同期の手順は `--sync-command "bash scripts/build-runtime-plugins.sh"` のように渡す
- 公開するのは **進行側だけ**。実装担当は push しない。進行側が検証を通した後に push するので、未検証の変更が公開されない
- `init` が参加 CLI の認証状態を確認する。誤検知するときは `NDF_SKIP_AUTH_CHECK=1`

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
