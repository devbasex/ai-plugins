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

Skill の配布は `plugins/ndf/manifests/` が唯一の基準（数と内訳は manifests の行数が持つ）。ブラウザ自動テストの 4 個は `playwright-kit` プラグインへ分離した（`plugins/playwright-kit/`）。frontmatter の書き方は `plugins/ndf/skills/README.md` の規約に従い、`python3 scripts/check-skill-frontmatter.py` で検査する。利用実績と維持・統合・削除の判定は `docs/specifications/ndf-skill-inventory/` に記録する。

## 版ごとの判断の記録

**出た版の判断は [docs/ndf-version-decisions.md](docs/ndf-version-decisions.md) にある。** 版ごとに
「何を決め、なぜそう決めたか」を残す。変更点の列挙は `CHANGELOG.md` が持つ。**過去の版の決定を
たどるとき**（同じ判断を繰り返しそうなとき、規約の理由が分からないとき）に読む。

**この参照に `@` を付けない。** 付けると内容そのものが毎回コンテキストへ載り、出た版の記録を
全セッション・全サブエージェントが毎回読むことになる。

**`CLAUDE.md` に残すのは現行版で決めたことだけである。** 版を配布した時点でその段落は過去の
記録になるため、退避先へ移す（手順は `release` にある）。

## cross-refactoring

`/ndf:cross-refactoring` は codex / agy / kiro / claude のうち **ホストを除く 3 者**に構造改善を提案させ、**参加する 4 者**から輪番で選んだ 1 者が適用し、残り 2 者がレビューする。新しい提案が出なくなるまで繰り返す。

```bash
/ndf:cross-refactoring 130 --scope src/services --baseline-test "pytest -q"
/ndf:cross-refactoring 130 --scope src --model codex=gpt-5.5 --model claude=opus-5
```

- `--scope` は必須。提案が発散して PR が肥大するのを防ぐ。**検証にも効く**ので、現状固定テストの置き場所も含める
- ホストと同じランタイムが適用担当になる場合も、サブエージェントではなく **CLI プロセス**として起動する
- モデルを比べるなら `--model <ランタイム>=<name>` を 4 つとも指定する。実際に動いたモデルを取得できるのは claude だけで、残り 3 つは指定値で代用する。指定が無いラウンドは集計から分離される
- 適用担当は 4 ラウンドで 1 周する。`--max-outer-rounds` の既定が 4 なのは、上限 3 では 4 者目の順番へ届かないため
- 収束しない改善項目は **項目単位で取り消す**。合意済みの項目は PR に残る。ただし同一ファイルの隣接行を触る項目どうしは git だけでは分離できないため、そのラウンドは全件取り消しへ退避する
- 生成物・配布物の同期は **進行側の責務**。実装担当にはさせない（範囲外の変更になる）。同期の手順は `--sync-command "bash scripts/build-runtime-plugins.sh"` のように渡す
- 公開するのは **進行側だけ**。実装担当は push しない。進行側が検証を通した後に push するので、未検証の変更が公開されない
- 履歴に残るのは **1 改善項目 = 1 コミット**。現状固定テストが要る項目だけ 2 コミット。テストも項目の単位で 1 回だけ求める
- 改修計画は `--plan-file`（既定 `issues/refactoring-plan-rf<PR>.md`）へ書き出され、生成物の同期と同じコミットで公開される
- `init` が参加 CLI の認証状態を確認する。誤検知するときは `NDF_SKIP_AUTH_CHECK=1`

## cross-review

`/ndf:cross-review` は codex / agy の両方に PR レビューを委譲し、両者が `APPROVE` するまで修正ループを回す。agy の progress log を heartbeat に表示するため、無言に見える時間でも `scan` / `analyze` / `post` / `done` などの作業段階を確認できる。

追加レビュー観点は以下のどちらかで渡す:

```bash
/ndf:cross-review 123 --focus "ドキュメントとコードの整合性を重点的に確認"
/ndf:cross-review 123 --extra-instructions-file /tmp/review-focus.md
```

PR の変更ファイルから docs only / code / DB migration / test / dependency / CI設定 / API契約 / 認証認可 / frontend / performance / deletion / generated / i18n / infra を自動分類し、該当するレビュー観点テンプレートも codex / agy 両方に渡す。
