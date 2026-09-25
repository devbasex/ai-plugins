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

Skill の配布は `plugins/ndf/manifests/` が唯一の基準（数と内訳は manifests の行数が持つ）。ブラウザ自動テストの 4 個は `playwright-kit` プラグインへ分離した（`plugins/playwright-kit/`）。frontmatter の書き方は `plugins/ndf/skills/AUTHORING.md` の規約に従い、`python3 scripts/check-skill-frontmatter.py` で検査する。利用実績と維持・統合・削除の判定は `docs/specifications/ndf-skill-inventory/` に記録する。

## 版ごとの判断の記録

**出た版の判断は [docs/ndf-version-decisions.md](docs/ndf-version-decisions.md) にある。** 版ごとに
「何を決め、なぜそう決めたか」を残す。変更点の列挙は `CHANGELOG.md` が持つ。**過去の版の決定を
たどるとき**（同じ判断を繰り返しそうなとき、規約の理由が分からないとき）に読む。

**この参照に `@` を付けない。** 付けると内容そのものが毎回コンテキストへ載り、出た版の記録を
全セッション・全サブエージェントが毎回読むことになる。

**`CLAUDE.md` に残すのは現行版で決めたことだけである。** 版を配布した時点でその段落は過去の
記録になるため、退避先へ移す（手順は `release` にある）。

**版が決まる前の段落は「の次の版で」で書き出す。** 版数はミッションをマージするまで決まらない
ため、書く時点では直前の正式版しか書けない。この印を付けておくと、配布で新しい版が出た
時点で検査が拾う（`.ndf/instructions.json` の `pending_marker`）。

```bash
python3 plugins/ndf/scripts/instructions-check.py --root .
```

出た版の段落・許可していない即時読み込み・読み込みの量の上限を見る。判定の強さは
`.ndf/instructions.json` が決め、宣言の書き方は `release` の
`references/instruction-files.md` にある。

## cross-refactoring

**`/ndf:cross-refactoring` は、想定最大時間（`--budget-minutes`、既定 30 分）に収まる計画を 1 回だけ実行する（#933）。** 参加者の全員が 1 度だけ多面的に提案し、実装担当 1 者（`--implementer` → ホスト → 参加者の先頭）が計画・テスト追加・実装・検証/修正を通す。参加者の既定は **codex / kiro とホスト（ホストが codex / kiro なら 2 者）** で、`--exclude` / `--include` で名指しで変える（agy は `--include agy` で戻す）。レビューは最終ゲートの `cross-review` が担う。

```bash
/ndf:cross-refactoring 130 --scope src/services tests/services --round-test "pytest tests/services -q" --baseline-test "pytest -q"
/ndf:cross-refactoring 130 --scope src tests --baseline-test "pytest -q" --budget-minutes 30
/ndf:cross-refactoring 130 --scope src tests --baseline-test "pytest -q" --include agy --exclude kiro
```

- `--scope` は必須。提案が発散して PR が肥大するのを防ぐ。**検証にも効く**ので、現状固定テストの置き場所も含める
- 計画は配分テーブル（履歴の直近 10 回から集計。初期値は #917）で見積もり、「想定最大時間 − 経過 − 控え」に収まる件数だけを採る。見送った提案は理由（`budget` / `rank` / `duplicate` / `vocabulary` / `threshold` / `no_target` / `test_failed` / `not_done`）とともに改修計画に残る
- 項目の検証は**限ったテスト**（`--round-test` か `--baseline-test` の対象を計画の `test_targets` へ差し替えたもの）で走らせる。全体のテストは着手前・危険の印（D1〜D5）が立ったときの 1 回・最終ゲートだけ。印の 1 回が落ちたら落ちたテストだけを走らせ直して揺れ・元からの失敗を除き、変更が原因なら締め切りまで直す。直らなければ印の項目を新しい順に絞って取り消す。`--baseline-test` が pytest / jest / vitest でなければ `--round-test` は必須
- 時間に関わる数値（段の上限・テスト 1 回の上限・無音の打ち切り・直しの打ち切り）はすべて `--budget-minutes` から算術で出し、計画の終わりまでに状態ファイルと改修計画へ書き出す。監視はその段の終わり + 余裕で CLI を止め、修正は回数でなく締め切りまで試みる。計画の後で LLM が動くのは作業の CLI だけ
- 廃止: `--max-test-rounds` / `--max-outer-rounds` / `--max-items-per-round` / `--max-fix-rounds` / `--test-timeout` は知らせて無視する
- ホストと同じランタイムが実装担当になる場合も、サブエージェントではなく **CLI プロセス**として起動する
- 収束しない改善項目は **項目単位で取り消す**。同一ファイルの隣接行を触る項目どうしは git だけでは分離できないため、同じファイルを触った項目まで取り消しを広げる
- 生成物・配布物の同期は **進行側の責務**。同期の手順は `--sync-command "bash scripts/build-runtime-plugins.sh"` のように渡す
- 公開するのは **進行側だけ**。実装担当は push しない
- 履歴に残るのは **1 改善項目 = 1 コミット**。計画がテストを足すと決めた項目だけ 2 コミット
- Jev は `AI_GATEWAY_API_KEY` があり公開リポジトリのときだけ使う（段・同じ変更か・D5）。使えなければ実装担当が判断する（`NDF_JEV=0` で止める）
- `init` が参加者の認証状態を確認し、通らない者を外して続ける。全員が揃わないなら止めたいときは `--require-all`。誤検知するときは `NDF_SKIP_AUTH_CHECK=1`

## cross-review

`/ndf:cross-review` は既定の母集合（claude / codex / kiro とホスト）のうち使える者から毎ラウンド 2 席を選んで PR レビューを委譲し、新しい指摘が出なくなるまで修正ループを回す。使える者が 2 者に満たなければ同じランタイムの 2 つ目が席を埋める。ホストのランタイムも CLI プロセスとして起動する。agy は既定から外してあり、`--include agy` で戻す。外すなら `--exclude` で名指しする（既定の母集合に無い者の指定は止めずに無視する）。2 ラウンド目以降は既存コメントの控えを取り直す。agy の progress log を heartbeat に表示するため、無言に見える時間でも `scan` / `analyze` / `post` / `done` などの作業段階を確認できる。

追加レビュー観点は以下のどちらかで渡す:

```bash
/ndf:cross-review 123 --focus "ドキュメントとコードの整合性を重点的に確認"
/ndf:cross-review 123 --extra-instructions-file /tmp/review-focus.md
```

PR の変更ファイルから docs only / 設計（`issues/` の要求・設計・決定の記録）/ code / DB migration / test / dependency / CI設定 / API契約 / 認証認可 / frontend / performance / deletion / generated / i18n / infra を自動分類し、該当するレビュー観点テンプレートも両席に渡す。
