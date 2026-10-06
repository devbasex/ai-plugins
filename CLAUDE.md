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

**リリース済み版の判断は [docs/ndf-version-decisions.md](docs/ndf-version-decisions.md) にある。** 版ごとに
「何を決め、なぜそう決めたか」を残す。変更点の列挙は `CHANGELOG.md` が持つ。**過去の版の決定を
たどるとき**（同じ判断を繰り返しそうなとき、規約の理由が分からないとき）に読む。

**この参照に `@` を付けない。** 付けると内容そのものが毎回コンテキストへ載り、リリース済み版の記録を
全セッション・全サブエージェントが毎回読むことになる。

**`CLAUDE.md` に残すのは現行版で決めたことだけである。** 版をリリースした時点でその段落は過去の
記録になるため、退避先へ移す（手順は `release` にある）。

**版が決まる前の段落は「の次の版で」で書き出す。** 版数はスプリントをマージするまで決まらない
ため、書く時点では直前の正式版しか書けない。このマーカーを付けておくと、リリースで新しい版が出た
時点でチェックが拾う（`.ndf/instructions.json` の `pending_marker`）。

```bash
python3 plugins/ndf/scripts/instructions-check.py --root .
```

リリース済み版の段落・許可していない @インポート・読み込み量の上限を見る。判定の強さは
`.ndf/instructions.json` が決め、設定の書き方は `release` の
`references/instruction-files.md` にある。

## cross-refactoring

**`/ndf:cross-refactoring` は、想定最大時間（`--budget-minutes`、既定 30 分）に収まるリファクタリング計画を 1 回だけ実行する。** 参加者の全員が 1 度だけ多面的に提案し、実装担当 1 者（`--implementer` → ホスト → 参加者の先頭）が計画・テスト追加・実装・検証/修正を通す。参加者の既定は cross-review と同じ **claude / codex / kiro とホスト** で、`--exclude` / `--include` で名指しで変える（agy は `--include agy` で戻す）。レビューは最終ゲートの `cross-review` が担う。

```bash
/ndf:cross-refactoring 130 --scope src/services tests/services
/ndf:cross-refactoring 130 --scope src tests --budget-minutes 30
```

- `--scope` は必須。提案が発散して PR が肥大するのを防ぐ。**検証にも効く**ので、現状固定テストの置き場所も含める
- テストは**宣言（`.ndf/project.json` の `test`）の戦略**で走らせる。無ければ `--round-test` を渡す
- 計画は配分テーブル（履歴の直近 10 回から集計。初期値は #917）で見積もり、「想定最大時間 − 経過 − バッファ」に収まる件数だけを採る。検証の後に時間が残れば、`budget` で見送った候補と実装の終わりまでにコミットが無かった項目を計画の時点の値のまま採り直す（LLM を呼ばない）。見送った提案は理由（`budget` / `rank` / `duplicate` / `vocabulary` / `threshold` / `no_target` / `test_failed` / `not_done`）とともに計画に残る。`not_done` は採り直しにも入らなかった項目だけに付く
- 項目の検証は**範囲テスト**（`scope_command` の `{paths}` へ計画の `test_targets`）で走らせる。全体テストは着手前・危険フラグ（D1〜D5）の 1 回・最終ゲートだけ（CI に任せる戦略では危険フラグの 1 回を最終ゲートへ寄せる）。落ちたら JUnit から落ちたテストを走らせ直してフレーキー・既存失敗を除き、変更起因なら締め切りまで直す。直らなければ原因の項目から取り消す（10.17.57 から）。着手前の失敗は既存失敗として記録する
- 時間の数値（上限・打ち切り）は `--budget-minutes` から算術で出し、計画までに状態ファイルと計画へ書く。提案と計画の枠は着手前のテストの後から数え、枠の後に 1 件も入らなければ要る予算を出して提案の前に止まる。項目ごとの期限は持たず、実装を止めるのは実装の終わり（最終ゲート修正の打ち切り − バッファ − 未検証の項目の検証）だけ。修正は締め切りまで試みる。想定最大時間は動いていた時間で数え、計画の後に中断して再開すると止まっていた時間の分だけ締め切りがずれる。計画の後で LLM が動くのは作業の CLI だけ
- 廃止: `--max-test-rounds` / `--max-outer-rounds` / `--max-items-per-round` / `--max-fix-rounds` / `--test-timeout` は知らせて無視する
- ホストと同じランタイムが実装担当になる場合も、サブエージェントではなく **CLI プロセス**として起動する
- 収束しない改善項目は **項目単位で取り消す**。積み直しが衝突したらそのコミットの項目だけを外し、残りは範囲テストで確かめ直す。直さなかった項目はその回で取り消す
- 生成物・配布物の同期は **オーケストレーターの責務**。同期の手順は `--sync-command "bash scripts/build-runtime-plugins.sh"` のように渡す
- 公開するのは **オーケストレーターだけ**。実装担当は push しない
- 履歴に残るのは **1 改善項目 = 1 コミット**。計画がテストを足すと決めた項目だけ 2 コミット
- Jev は `AI_GATEWAY_API_KEY` があり公開リポジトリのときだけ使う（グレード・同じ変更か・D5）。使えなければ実装担当が判断する（`NDF_JEV=0` で止める）
- `init` が参加者の認証状態を確認し、通らない者を外して続ける。全員が揃わないなら止めたいときは `--require-all`。誤検知するときは `NDF_SKIP_AUTH_CHECK=1`

## cross-review

`/ndf:cross-review` は既定の参加者プール（claude / codex / kiro とホスト）の利用可能な参加者から毎ラウンド 2 スロットを選んで PR レビューを委譲し、新しい指摘が出なくなるまで修正ループを回す。2 者に満たなければ同じランタイムの 2 つ目で埋める。ホストのランタイムも CLI プロセスとして起動する。agy は既定から外してあり、`--include agy` で戻す。外すなら `--exclude` で名指しする（既定に無い者の指定は止めずに無視する）。2 ラウンド目以降はコメントのスナップショットを取り直す。agy の progress log を heartbeat に表示し、無言に見える時間も `scan` / `analyze` / `post` / `done` などの作業段階が分かる。

追加レビュー観点は以下のどちらかで渡す:

```bash
/ndf:cross-review 123 --focus "ドキュメントとコードの整合性を重点的に確認"
/ndf:cross-review 123 --extra-instructions-file /tmp/review-focus.md
```

PR の変更ファイルから docs only / 設計（`issues/` の要求・設計・決定の記録）/ code / DB migration / test / dependency / CI設定 / API契約 / 認証認可 / frontend / performance / deletion / generated / i18n / infra を自動分類し、該当するレビュー観点テンプレートも両スロットに渡す。
