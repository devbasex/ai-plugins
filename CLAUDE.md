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

## NDF v9.2.0 の Skill 構成

Skill は 32 個で、配布は `plugins/ndf/manifests/` が唯一の基準（Claude Code 28 / Codex 26 / Kiro 27）。ブラウザ自動テストの 4 個は `playwright-kit` プラグインへ分離した（`plugins/playwright-kit/`）。frontmatter の書き方は `plugins/ndf/skills/README.md` の規約に従い、`python3 scripts/check-skill-frontmatter.py` で検査する。利用実績と維持・統合・削除の判定は `docs/specifications/ndf-skill-inventory.md` に記録する。

v6.1.0 で開発方法論レイヤーの 5 個（`development-workflow` / `requirements-design` / `tdd-cycle` / `refactoring`（当時は `safe-refactoring`）/ `quality-gates`）を追加した。モード判定の基準を持つのは `development-workflow` だけで、他の Skill とエージェント定義は判定結果を受け取る側に徹する。

v7.0.0 で playwright 系 4 個を `playwright-kit` プラグインへ分離した。対応表は予告どおり v8.0.0 で削除済み。

v8.0.0 で `safe-refactoring` を `refactoring` へ改名し、分岐・反復・定数の表現を決める観点を統合した。観点は `references/data-representation.md` に置き、言語固有の手段は `references/lang-<言語>.md` に 1 言語 1 ファイルで置く。SKILL.md が対象言語のファイルだけを読ませるため、他言語の内容はコンテキストに載らない。言語を追加するときも他のファイルは変更しない。対応表は `ndf-policies` にある（v9.0.0 で削除）。

v8.1.0 で `cross-refactoring` を追加した。あわせて収束ループの共通層を `plugins/ndf/skills/cross-review/scripts/lib/` へ切り出し、`monitor.py` は同ディレクトリへ移設して既存パスをシムにした。`cross-review` の挙動と既存テストは変えていない。

v8.2.0 で `cross-refactoring` の実機検証（PR #118）で見つかった 9 件の不具合を直した。`cross-review` と共通層は変更していない。詳細は `issues/issue-113-cross-refactoring-defect-fixes.md`。

v8.3.0 で `cross-refactoring` の公開の責務を進行側へ一本化した（**破壊的**）。実装担当は push せず、進行側が検証を通してから push する。あわせて `--sync-command` を新設し、適用で失敗した項目を対象外へ記録するようにした。詳細は `issues/issue-113-cross-refactoring-push-ownership.md`。

v8.4.0 で `markdown-writing` に「敬意と節度のある表現で書く」（ルール 4）を追加し、以降のルール番号を 1 つ繰り下げた。強い否定語・過剰な装飾語・根拠の曖昧な断定の 3 種を扱い、セルフチェックの grep も 3 種に分けた。あわせて `01-diagram-guide.md` を図表ルールの冒頭から手順として読ませ、上限値や記法は SKILL.md へ書かずガイド側に置く構成にした（実測で読み込み挙動を確認した結果）。`pr` は完了報告を `### 6. 完了報告` として手順に組み込み、テンプレートと PR URL の書き方（生の URL を書く）を定めた。

v8.5.0 で `cross-refactoring` の再々検証（PR #125）で見つかった不具合 4 件と、`cross-review` の投稿確認を直した。進行を止めていたのは生成物の同期で、`git status --porcelain` を固定幅で読む箇所が出力全体を `strip()` していたため、変更パスの先頭 1 文字が欠けていた。あわせて実装担当が残した未コミット変更を取り込みの前に捨てるようにし、提案の直前に読み取り用の作業ディレクトリを同期するようにした。`cross-review` は申告されたコメント数を GitHub 側の実数と突き合わせる。詳細は `issues/issue-113-cross-refactoring-re-retrial.md`。

v8.5.1 で `cross-refactoring` の収束ループが終わらない不具合を直した。`judge-review` の変更要求の出口は 2 つあり、差し戻し上限から落ちる側だけが修正フェーズの起点（`fix_base_sha`）を記録していなかった。起点が無いと `merge-fix` が範囲を確定できずに弾かれ、`fix_rounds` が進まないため `should-abandon` の上限へ永久に到達しない。起点の記録を 1 箇所へ集め、範囲を確定できないときも修正ラウンドを進めるようにした。あわせて、レビュー結果を残さなかった担当がいるときは変更要求にせず中断する（実装担当が直せる指摘ではないため）。詳細は `issues/issue-113-cross-refactoring-4th-trial-report.md`。

v8.5.2 で `cross-refactoring` のレビュー投稿が自分の Pull Request で `HTTP 422` になる不具合を直した。GitHub は自分の Pull Request への `APPROVE` と `REQUEST_CHANGES` をどちらも拒む。`init` が作成者を照合し、自分の Pull Request なら投稿の event だけを `COMMENT` へ倒す指示をレビュープロンプトへ渡す。判定は本文の先頭行と結果ファイルへ `APPROVE` / `REQUEST_CHANGES` のまま残るため、収束判定は変わらない。`cross-review` の `intent` と `posted_as` の分離と同じ形になる。詳細は `issues/issue-113-cross-refactoring-5th-trial-report.md`。

v8.5.3 で `cross-refactoring` の投稿の確認を入れた。投稿は AI 自身が `gh api` で行うため、失敗しても結果ファイルの判定だけは残る。そのまま採ると、実装担当が読むべき指摘が Pull Request に無いまま収束する。`judge-review` が `review_url` を必須とし、URL の識別子から GitHub 側の存在を確かめる。無ければ差し戻し、取得できないときは申告を採用して確認できなかったことを出力へ残す。あわせてレビュープロンプトが、投稿に失敗したときも `post_error` 付きの結果ファイルを書かせる。上限に達したときは投稿できなかった担当も中断の対象へ含める。`cross-review` の投稿確認と同じ考え方になる。詳細は `issues/issue-113-cross-refactoring-5th-trial-report.md`。

v8.5.4 で `cross-refactoring` の差分予算を手法別にした。新しい定義を作って呼び出し側を書き換える手法は、抽出した本体に加えて呼び出し側の書き換え・import の追加・引数の受け渡しが固定費として乗る。実測で予算超過として落ちた 4 件はいずれも `long_method` の抽出で、見積の 2.03〜2.31 倍だった。範囲の逸脱ではなく、倍率 2 の予算をわずかに超えただけである。抽出系の 7 手法だけ倍率を 3 にした（範囲外を触った実測例は見積の 4 倍なので取り逃がさない）。あわせて提案プロンプトの見積の指示へ固定費と現状固定テストを数えることを明記し、`init` が kiro の既定 `auto` を検知して「集計から分離される」ことを着手前に知らせるようにした。詳細は `plugins/ndf/skills/cross-refactoring/docs/02-apply-and-review.md`。

v8.6.0 で `cross-refactoring` のコミット粒度を 1 改善項目 = 1 コミットに変えた。手順を 1 手ずつ進めることと、その途中経過を履歴に残すことは別である。手ごとにテストを回すのは変わらないが、残すのは項目単位の 1 コミットだけにする（現状固定テストが要る項目のみ 2 コミット）。適用と修正の両方で検証するのは、適用側だけ揃えても指摘への対応という名目で刻んだ履歴が戻ってくるためである。テストの回数も項目の単位に合わせた。進行側が申告されたコミットごとに実行するため、実装担当にも手ごとの実行を義務づけると同じテストが手数の 2 倍だけ走る（実測 44 手で 88 回）。あわせて改修計画（なぜ直すのか・どう直すのか）を `--plan-file`（既定 `issues/refactoring-plan-rf<PR>.md`）へ書き出すようにした。理由と手順は提案の時点でしか残らず、状態ファイルは差分から除外されるため Pull Request からは読めなかった。公開は生成物の同期と同じコミットに乗せる。詳細は `plugins/ndf/skills/cross-refactoring/docs/02-apply-and-review.md`。

v9.1.0 で `markdown-writing` に「指す対象が文脈で変わる語に、この文書での意味を与える」（ルール 2）を追加し、以降のルール番号を 1 つ繰り下げた。ルール 1 が読み手の知らない語（内部識別子・略語）を扱うのに対し、ルール 2 は読み手が別の意味で知っている語を扱う。知らない語ではないため読み飛ばされ、読み手は別の対象を思い浮かべたまま先へ進む。既定の対応は一意な語への言い換えで、定義は言い換えが不自然な場合に用語表と初出の本文の両方へ置く。語の一覧・出現数の閾値・候補が出たときの判断は `02-ambiguous-terms.md` に置いた。セルフチェックは語の有無ではなく、同一ファイル内で 5 回以上出た語だけを候補にする（実測では、有無だけで拾うと 130 ファイル中 91 件が候補になり、閾値 5 で 30 件へ絞れる）。詳細は `issues/issue-163-polysemy-rule.md`。

v9.1.1 で多義語のセルフチェックを `if` 文へ直した。`[ 条件 ] && echo` の形は、候補が 1 つも出ないときにループ最後の判定が偽になり、終了コードが 1 になる。候補が無いことは正常な結果であり、失敗と読める値を返さないようにした。

v9.1.2 で適用の範囲をガイドと `SKILL.md` へ明記した。新しく書く文書と改訂する文書に適用し、既存の文書を一括で直す必要はない。一括版のコマンドは改訂の対象を選ぶために使うもので、出力されたファイルはその場で直す対象の一覧ではない。

v9.2.0 で `worktree` を追加し、開発の変更を作業ツリーの中で行う運用を 3 ランタイムへ結んだ。主ディレクトリの編集は**拒否しない**。誤検知で正当な操作が止まる状態を作らないため、誘導（tool 実行前の hook）・逸脱検知（セッション開始時の hook）・是正（Skill の移送手順）の 3 層で支える。判定はすべて `plugins/ndf/scripts/lib/worktree-common.sh` に集め、入口のスクリプトは入出力の整形だけを行う。**リポジトリ側に `.ndf/worktree.json` があるときだけ動き**、無ければ何も出力せず終了コード 0 で終わる。

3 ランタイムの実機確認で、設計が拾えていなかった事実が 2 件出た。Codex CLI はファイルの編集を `apply_patch` で渡し、パスは `tool_input.file_path` ではなくパッチ本文の `*** Update File:` 行に入る。セッション開始時の出力は、平文と JSON を同時に書くと標準出力全体が JSON として読めず、Claude Code が両方をまとめて 1 つの本文として積むため、事象で分けて書く。誘導の対象になる tool 名は `WT_EDIT_TOOLS` / `WT_PATCH_TOOLS` / `WT_SHELL_TOOLS` の 1 箇所が持ち、hook の matcher もそこから作って一致をテストで検査する。詳細は `issues/issue-146-worktree-first/`。

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
- 履歴に残るのは **1 改善項目 = 1 コミット**。現状固定テストが要る項目だけ 2 コミット。テストも項目の単位で 1 回だけ求める
- 改修計画は `--plan-file`（既定 `issues/refactoring-plan-rf<PR>.md`）へ書き出され、生成物の同期と同じコミットで公開される
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
