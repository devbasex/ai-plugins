# 機械的なターンのスクリプト化: 状態の取り出し・PR の照合・手順の連続実行・進行のまとめ記録

## 概要

NDF の工程を進める AI（conductor・supervisor・worker）が、判断を伴わずに行っていた 4 種類の Tool の呼び出しを、
スクリプトの 1 回の実行へ移す。4 つの操作のどれでも、AI はスクリプトの結果 JSON を 1 回読めば次の 1 手を決められる。

| 操作 | 移す前の手作業 | 移した先 |
| --- | --- | --- |
| 状態の取り出し | 状態ファイル・結果 JSON を `python -c` で読み直す | `sprint-state.py get`・`sprint-state.py loop get`・`glossary.py lookup`・`--field`・`step_result.py pick` |
| PR と CI の照合 | `gh pr view` と `gh pr checks` を続けて打つ | `lib/gh_parts.py pr-status` |
| スクリプトの往復 | NDF のスクリプトを 1 行ずつ打つ | `supervise.py advance` |
| 進行の連打 | `progress-record.sh` を工程ごとに打つ | `progress-record.sh`・`projects-sync.sh stage` に工程名を並べて 1 回 |

あわせて、ターンの型ごとの件数と割合を transcript から数える実験版の `turn-kinds.py` を置く。

`glossary.py lookup` と `--field` の副命令の表は [ndf-ubiquitous-language.md](ndf-ubiquitous-language.md) の「入出力の契約（`glossary.py`）」、
`loop get` の行は [ndf-workflow-development-loop.md](ndf-workflow-development-loop.md) の副命令の表が持つ。この文書はそれらを写さず、
4 つの操作に共通する決めごとと、ほかの文書に無い契約を持つ。

## 用語

語の定義は用語集（ [docs/glossary.md](../glossary.md) の「NDF の開発ワークフロー」）が正である。この文書で使う主な語は次のとおり。

| 用語 | この文書での使い方 |
| --- | --- |
| 機械的なターン | 移す対象の Tool の呼び出し。この文書は 4 つの候補（状態の取り出し・PR と CI の照合・スクリプトの往復・進行の連打）だけを扱う |
| ターンの型 | `turn-kinds.py` が数える分類の単位。規則は `plugins/ndf/scripts/experimental/turn-kinds.json` の `kinds` |
| 手順の連続実行 | `supervise.py advance` が行うこと |
| 結果 JSON | 手順のスクリプトが最後に 1 行で出す JSON。形の正本は `plugins/ndf/scripts/lib/README.md` の「手順のスクリプトの結果」 |

## 背景

| 決めたこと | 理由 |
| --- | --- |
| 値の指し先は JSON Pointer（RFC 6901）で書く | 取り出したいのは 1 つの値で、絞り込みは各副命令の既定の要約が引き受ける。標準ライブラリだけで書け、プラグインの依存が増えない。式の言語は AI が書き誤る余地も広い |
| `--field` の評価は `lib/json_pick.py` の 1 か所に置き、`step_result.py pick` も同じ関数を呼ぶ | 取り出しの規則をスクリプトの数だけ並べないため。`pick` があれば `--field` を持たないスクリプトの結果も `python -c` なしで読める |
| 読み取りの副命令（`get`・`loop get`・`lookup`・`pick`）は、読めた限り `ok` を返し、次の操作は `next` に置くだけにする | 読み取りは続けてよいかを判断しない。`gate` を返させると、手順の連続実行が同じ状態を読むたびに止まる |
| PR の照合は「マージへ進める」だけを `ok` にし、ほかの 3 つを `gate` にする | `ok` は目標に届いたことを表す。待つ・直すは AI が次の 1 手を決められるので LLM の判断（20 台）、人へ戻すは人の同意（10）にする。すべて `ok` だと、連続実行が失敗のある PR のままマージの待ちへ進む |
| PR の照合は `pr-info` の部品（メタ・名前ごとに畳んだチェック）を使い、スレッド・マージの状態・PR が閉じる Issue を新しい GraphQL の照会 1 回で読む | 同じ役割の取得を別に作らないため。`pr-info` の照会を書き換えると `pr-info` の出力が変わりうる |
| 手順の連続実行は `supervise.py` の副命令にする | `supervise.py` は判断の要らないステップをスクリプトで進める役割をすでに持つ。エントリポイントと契約を増やさない |
| 連続実行が打てるのは NDF のスクリプトだけで、シェルを通さず、中継の副命令も打たない | 任意のコマンドを受けると、Bash の hook の検査の外で強制 push などが打てる。NDF のスクリプトは結果 JSON を返すので、続けるかを `status` で決められる |
| 工程の起動を含む連続実行は、既存の Bash の hook が工程の起動として判定する | 判定と記録を 2 か所に分けないため。文脈が上限を超えた conductor が `advance` を通して工程を起動できないようにする |
| 進行のまとめ記録は、工程名を位置引数で並べる | 今の呼び出しの形の延長で使える。2 つ目以降の位置引数は今まで誤り（2）だったため、正しい呼び出しの出力は変わらない。カンマ区切りは工程名に区切りの文字が入ると割れる |
| ターンの型を数える手段は実験版に置く | 呼んだときだけ働く補助で、規則の原本（PR #2905 の集計）がリポジトリに無く、数え直しながら直す余地が大きい。費用の帳簿を集計する `usage-report.py` は Tool の呼び出しの分類を持たない |

## 仕様

### 構成要素

| 要素 | 責務 |
| --- | --- |
| `plugins/ndf/scripts/lib/json_pick.py` | JSON Pointer の解釈（`parse_pointer`・`resolve_pointer`・`pick_fields`）、`--field` の包み（`picked`）、`step_result.py pick` の本体（`cli_pick`）。ファイルも GitHub も触らない |
| `plugins/ndf/scripts/lib/state_get.py` | `sprint-state.py get` と `loop get` の中身 |
| `plugins/ndf/scripts/lib/glossary_lookup.py` | `glossary.py lookup` と、`check`・`diff`・`lookup` の `--field` の包み |
| `plugins/ndf/scripts/lib/gh_pr_status.py` | `gh_parts.py pr-status` の組み立てと判定（`decide_action`）。GitHub の語を次の 1 手の 4 つの語へ訳す層 |
| `plugins/ndf/scripts/lib/gh_graphql.py` の `pr_status_parts` | 未解決のスレッドの数・`mergeStateStatus`・`closingIssuesReferences` を 1 回の照会で読む |
| `plugins/ndf/scripts/supervise_lib/advance.py` | 手順の検査・実行・結果の読み取り。中継の副命令の一覧（`RELAYS`）を持つ |
| `plugins/ndf/scripts/hook_lib/shell_checks.py` の `starts_plan` | `supervise.py advance` の `--step` の値（`--step X` と `--step=X`）の中に `supervise.py queue`・`run` があれば工程の起動と判定する |
| `plugins/ndf/scripts/progress-record.sh`・`projects-sync.sh` | 工程名の並びを受ける |
| `plugins/ndf/scripts/experimental/turn-kinds.py`・`turn-kinds.json` | ターンの型と候補ごとの件数と割合を数える（実験版。台帳は [docs/ndf-experiments.md](../ndf-experiments.md) ） |

### 状態の取り出し

| 呼び出し | `--field` なしの出力 | 終了コード |
| --- | --- | --- |
| `sprint-state.py get <状態ファイル> [--field <指し先>]...` | `items` に計画ごとの `{kind: plan, name, result, pr, next}`、`metrics` に `{plans, done, open}`、`next` に先頭から見て最初の終わっていない計画（`result` が `まだ`）の `<名>: <次の欄>`。すべて終わっていれば `next` を置かない | 0 = 読めた。2 = 読めない・指し先の書き方の誤り。3 = 状態ファイルが無い |
| `sprint-state.py loop get <ループ名> [--field <指し先>]...` | `metrics` は `loop status --json` と同じ鍵。`next` は下の表で決める | 同上（3 = 記録が無い） |
| `step_result.py pick --field <指し先>... [--raw]` | 標準入力の最後の行の結果 JSON から値を取り出す。`metrics` は `{<指し先>: <値>}`、`status`・`next`・`gaps`・`presentation_path` は入力を写す。値の無い指し先は `items` に `{kind: field, name, result: missing}` | 入力が結果 JSON でない・指し先の誤り・`--raw` に `--field` が 1 つでない 2 |

- **`--field` を渡すと、`metrics` は `{<指し先>: <値>}` だけになる。** 状態の取り出しでは状態ファイルかループの記録を根にし、値の無い指し先は値を `null` にして `items` に `missing` の行を載せる（終了コードは 0）
- **用語集の `--field` は結果 JSON を根にする。** `items` は空にし、`status` と終了コードは `--field` を渡さないときと同じにする
- **`--raw` は値だけを 1 行で出す。** 文字列は引用符なし、`null` と値の無い指し先は空の行。値の無い指し先なら終了コード 3
- 指し先は空文字（根）か `/` で始める。`~0` は `~`、`~1` は `/` に戻す。配列の添字は先頭に 0 の付かない 10 進の非負の整数だけで、`-` は値が無い扱い

**開発ループの次の操作**（上から見て最初に当たった行の文を `next` に置く）:

| ループの記録 | `next` |
| --- | --- |
| 止まった印（`stop`）がある | 止まった理由と再開のコマンド |
| 答えの無い人の判断待ちがある | `人の判断待ち: #<番号>...。答えは sprint-state.py loop answer で書く` |
| 答えの無い保留したループの 1 回がある | `ループの <回> 回目の答え待ち` |
| どれにも当たらない | 置かない |

### PR と CI の照合

`gh_parts.py pr-status <PR 番号> [--repo <所有者>/<リポジトリ>] [--issue <番号>]...`。`--repo` を省くと作業ディレクトリの origin から決め、
決められなければ 2。`--issue` は PR が閉じる Issue に加えて開閉を見る Issue である。

- `items`: チェックごとの `{kind: check, name, result}`（名前ごとの最新の実行）と、Issue ごとの `{kind: issue, name: "#<番号>", result: open|closed|unknown}`
- `metrics`: `{action, state, draft, merge_state, failed_checks, pending_checks, unresolved_threads, issues_open, issues_closed}`。`action` は `wait`・`fix`・`merge`・`escalate` のどれか
- 照会できなかったチェックかスレッドは値を `null` にし、`gaps` に `{kind: query, name: checks|threads, reason}` を載せる

**判定**（上から当て、最初に当たった行で決める）:

| 順 | 条件 | `action` | `status` と終了コード | `next` |
| --- | --- | --- | --- | --- |
| 1 | PR の状態が open でない | `escalate` | `gate` 10 | 後片付けへ進むかを人が決める |
| 2 | マージの状態が `DIRTY`（衝突） | `escalate` | `gate` 10 | `merged-steps.py base-sync` を打つかを人が決める |
| 3 | チェックかスレッドを照会できなかった（`null`。0 件と分ける） | `escalate` | `gate` 10 | `pr-status` を打ち直す |
| 4 | 失敗したチェックがある、または未解決のスレッドが 1 件以上 | `fix` | `gate` 20 | 失敗したチェックの名前と未解決のスレッドの数 |
| 5 | 実行中のチェックがある、またはチェックが 1 件も無い（push の直後） | `wait` | `gate` 21 | `merged-steps.py merge-when-green <番号>` で待つ |
| 6 | チェックがすべて成功（`success`・`skipped`・`neutral`）で、未解決のスレッドが 0 件 | `merge` | `ok` 0 | `merged-steps.py merge-when-green <番号>` |

PR の取得そのものができなければ `stopped` の 2。PR が指す Issue の開閉は判定に使わず、`items` と `metrics` に載せるだけにする。

取得の回数は、メタが `gh pr view` 1 回、チェックが REST のチェックの実行（100 件ごとに 1 回）、スレッド・マージの状態・PR が閉じる Issue が
GraphQL 1 回である。`--issue` で足した Issue だけ REST で 1 回ずつ読む。上限に当たったときは `gh_quota` の退避の規則に従う。

### 手順の連続実行

`supervise.py advance --step "<コマンド>"... [--from <番号>]`。

- **打つ前にすべての手順を検査する。** 各手順は `shlex` で語に割り、先頭は NDF のプラグインの `scripts/` か `skills/<名>/scripts/` の
  スクリプトのパスか、`python3`・`python`・`bash`・`sh`（フラグを挟んでよい）の後にそのパスである。プラグインの根は
  `scripts/supervise.py` と `skills/` を持つディレクトリで判定する。外のコマンドか中継の副命令（`RELAYS`: `lib/gh_parts.py body-lock`・
  `lib/post_queue.py retry`・`supervise.py advance`）が 1 つでもあれば、何も打たずに `stopped` の 2 で、該当する手順を `gaps` に載せる
- **シェルを通さずに 1 つずつ打ち、子の結果 JSON の `status` が `ok` の間だけ続ける。** 子の結果は `step_read.read_completed` で読む
- `--from` は 1 から数えた番号で、それより前の手順を打たずに `process` へ `{run, result: "飛ばした（--from）"}` で載せる。範囲の外は 2

| 止まり方 | `status` と終了コード | `next` |
| --- | --- | --- |
| すべて `ok` | `ok` 0 | 置かない |
| 子が `gate` | `gate`。子の終了コード（10〜29） | 子の `next`（無ければ `summary`）と、続きの `advance ... --from <止まった番号 + 1>`（最後の手順なら続きを付けない） |
| 子が `stopped` か結果 JSON を返さない | `stopped` 1。`gaps` は子の `gaps`（無ければ止まった手順と理由） | 子の理由と、打ち直しの `advance ... --from <止まった番号>` |

`process` は打った手順ごとに `{run, result: <子の summary>, status, code}`、`metrics` は `{steps, ran, stopped_at}` である。
`advance` は自分の上限の時間を持たない。長く待つ手順（`supervise.py wait`・`merged-steps.py merge-when-green`）を含む並びは、
呼ぶ側が背景で打つ。

### 進行のまとめ記録

`progress-record.sh <Issue 番号> <工程名> [<工程名>...] [既存の選択肢]`、`projects-sync.sh <Issue 番号> stage <工程名> [<工程名>...]`。

- 並べた工程名は、何かを書く前にすべて工程表と照らす。1 つでも工程表に無ければ、本文も通過記録も書かずに 2
- 本文の `## 進行` の節は 1 回の書き換えで、すべての工程の行に印を付ける。`--note` はすべての工程の行に付く
- 通過記録へは工程ごとに 1 件ずつ積む。`--mode`・`--pace` は最初の工程と一緒に 1 回だけ積む
- `-`（見出し行だけの更新）は 1 つだけで渡したときだけ受ける
- `projects-sync.sh` は工程名の並びを `progress-record.sh` へ 1 回で渡し、ボードの「工程」の列には最後の工程名を書く

### ターンの型を数える手段（実験版）

`python3 plugins/ndf/scripts/experimental/turn-kinds.py --since <YYYY-MM-DD> --until <YYYY-MM-DD> [--root <transcript の根>] [--rules <規則のファイル>]`。

- 期間は JST の日付で、両端を含む。`--root` を省くと `transcript_agents.config_root()` で、根の下の `projects/` と
  `ndf/accounts/*/projects/` の `*.jsonl`（サブエージェントを含む）を読む
- 数えるのは `type` が `assistant` の行の `tool_use` だけで、`tool_use` の id で 1 度だけ数える。分母は全 Tool の呼び出し
- 型は Bash のコマンドの先頭（コメントの行・`cd`・変数の代入・`env`・`nohup`・`time`・`timeout` を飛ばしたもの）へ規則を上から当て、
  最初に当たったものを採る。`scope` が `all` の型はコマンド全体へ当てる
- 候補は `kinds`（どの型か）と `all`（コマンドに当たるべき正規表現のすべて）で決める。`repeat` の付いた候補（進行の連打）は、
  同じ会話の直前の Tool の呼び出しも同じ候補に当たったときだけ数える
- 出力の `metrics` は `{tool_calls, kinds: {<型>: {count, share}}, candidates: {<候補>: {count, share}}, files, unreadable_lines}`、
  `items` は候補ごとの `{kind: candidate, name, result: <割合>}`。読めない行は飛ばして件数だけを載せる
- 終了コードは 0 = 数えた、2 = 期間の書き方の誤り・規則を読めない、3 = 根が無い

| 候補 | 数える呼び出し |
| --- | --- |
| 1. 状態の取り出し | 型「変換: JSON・出力を python -c / jq / cut で読む」のうち、`python -c` か `jq` で状態ファイル（`sprint.json`・`loop.json`・`queue-done.json`・`*.plans.json` など）・用語集・NDF のスクリプトの出力を読むもの。grep・cat などのコード探索は数えない |
| 2. PR・CI の照合 | 型「照合: gh で PR・Issue を読む」と「照合: CI の状態を gh で見る」 |
| 3. スクリプトの往復 | 型「往復: NDF スクリプトを 1 行打つ」 |
| 4. 進行の連打 | `projects-sync.sh ... stage` か `progress-record.sh` の呼び出しで、直前の呼び出しも同じ候補のもの |

### 常に成り立つ条件

| 条件 | 破れたときの扱い |
| --- | --- |
| 新しい副命令・選択肢を渡さない呼び出しの標準出力と終了コードは、変更の前と同じである | 既存のテストが落ちる |
| `get`・`loop get`・`lookup`・`pick` と `--field` は、状態ファイルと用語集を書き換えない | テストが落ちる |
| 手順の連続実行は、前の手順が `ok` のときだけ次を打ち、1 回の実行で同じ手順を 2 度打たない | 止まった手順で終わり、`process` には打った手順だけが載る |
| 手順の連続実行は、NDF のスクリプトの外のコマンドと中継の副命令を打たない。`argparse.REMAINDER` を持つ副命令はすべて `RELAYS` に載る | 何も打たずに 2。一覧の漏れはテストが落とす |
| PR の照合の `action` は 4 つのうち 1 つだけで、判定の表の順で決まる | テストが落ちる |
| 複数の工程を渡した進行の記録は、本文の書き換えが 1 回で、通過記録が工程ごとに 1 件である | 本文を書けなければ今と同じ `stopped`（6）。通過記録を積めなければ標準エラーへ `NOTE:` を残して続ける |
| `turn-kinds.py` は会話の本文・会話の ID・コマンドの文字列を出さず、ファイルへ書かない | テストが落ちる |
| `--step` に `supervise.py queue`・`run` を含む `advance` は、文脈の上限の hook が手順を打つ前に工程の起動として判定する | hook が `advance` の呼び出しを拒否し、どの手順も打たれない |
| 4 つの手段はリポジトリ名・ブランチ名・テストのコマンドを既定に持たず、引数か origin から得る | 検索で見つけて直す |

## 運用

### Skill の本文とテンプレート

手作業の型を指示していた手順書は新しい手段を指す（`development-workflow` の `references/` の `waiting.md`・`relay.md`・`projects-tracking.md`・
`stage-completeness.md`・`loop.md`・`interrupt-resume.md`、`progress-tracking` の `SKILL.md`、`plugins/ndf/scripts/data/handoff-template.md`）。
シェルから結果 JSON の値を読む規則は `plugins/ndf/scripts/lib/README.md` の「手順のスクリプトの結果」にある。
`design` の `SKILL.md` の `stage` の 2 行は、別々の時点で 1 つずつ打つ 2 つの契機の例であり、続けて打つ指示ではないため残す。

### 基準の割合

2026-11-10 に `turn-kinds.py --since 2026-10-13 --until 2026-11-09` で数えた値である。分母は Tool の呼び出し 70,331 件
（ファイル 18,204 本・読めない行 0）。

| 候補 | 件数 | 全 Tool の呼び出しに占める割合 |
| --- | --- | --- |
| 1. 状態の取り出し | 1,556 | 2.21% |
| 2. PR・CI の照合 | 2,497 | 3.55% |
| 3. スクリプトの往復 | 4,472 | 6.36% |
| 4. 進行の連打 | 158 | 0.22% |

分類の規則の原本（PR #2905 の集計）はリポジトリに無い。同じ型の件数を PR #2905 の 5 週の CSV と比べ、PR・CI の照合
（2,497 / 2,401）・スクリプトの往復（4,472 / 1,907）・変換（8,792 / 5,039）が同じ桁に収まることで再現を確かめた。
スクリプトの往復が 2 倍あまり多い理由は確かめていない。会話を残さない `claude -p` の呼び出しは transcript が無く数えられない。

### 効果の確かめ方

v10.27.53 のリリースの翌日から 14 日間を `turn-kinds.py` で数え、4 つの候補それぞれの割合を上の基準の割合と比べる。
件数は開発の量で上下するため、割合で比べる。半分を超えた候補の扱いは、結果を見て利用者が決める。
結果は実験版の台帳の `turn-kinds.py` の行に書き、本体へ移すか消すかもそこで決める。

## テスト観点

- 固定の状態ファイルに `sprint-state.py get` を打つと、計画の状態と次の欄が、`python -c` で読んでいた値（`plans[i].next` など）と同じ値で返り、結果が `validate_result` を通ること
- 止まった記録・人の判断待ちの記録・どちらも無い記録で、`loop get` の `next` が表の行どおりになり、`metrics` が `loop status --json` と同じ値になること
- `lookup --term` が部分一致で語を返し、`check --field /metrics/hits` が `check` の `hits` と同じ値を返すこと
- 値のある指し先・無い指し先・`~1` を含む指し先で、値・`null` と `missing`・エスケープを解いた値が返ること
- `get`・`loop get`・`lookup` の前後で、状態ファイルと用語集の内容と更新時刻が変わらないこと
- GitHub の呼び出しを差し替え、判定の表の 6 行それぞれで `action`・終了コード・`next` が表どおりになること。失敗と実行中が同時にあれば「直す」になること
- `ok`・`ok`・`gate` を返す 3 つのスクリプトを並べると 3 つ目で止まり、`process` が 3 件になること。2 つ目が `stopped` なら `gaps` に子の理由、`next` に `--from 2` が載ること。`--from 2` で 1 つ目が打たれないこと
- NDF のスクリプトの外のコマンドか中継の副命令を 1 つ混ぜると、どの手順も打たれずに 2 で止まること
- 文脈が上限を超えた conductor の入力で、`advance --step "supervise.py queue ..."` が拒否され、`advance --step "sprint-state.py get ..."` は拒否されないこと
- 工程名を 2 つ渡すと、本文の書き換えが 1 回だけで、節の 2 つの行に印が付き、通過記録が 2 件増えること。工程表に無い名を混ぜると何も書かれないこと
- 合成した transcript で、型と候補の件数と割合が期待どおりになり、入れた会話の ID とコマンドの文字列が標準出力に現れないこと
- 新しい選択肢を渡さない既存の呼び出しで、全体の pytest が通ること
- 効果は「効果の確かめ方」の手順で、4 つの候補の割合がそれぞれ基準の半分以下になることを確かめること

## 関連リンク

- 要求: https://github.com/devbasex/ai-plugins/issues/2909 （分析の元は https://github.com/devbasex/ai-plugins/pull/2905 ）
- 実装: https://github.com/devbasex/ai-plugins/pull/2956
- 用語集の副命令の表: [ndf-ubiquitous-language.md](ndf-ubiquitous-language.md)
- 開発ループの副命令の表: [ndf-workflow-development-loop.md](ndf-workflow-development-loop.md)
- 結果 JSON の欠けの扱いと読み方: [ndf-step-result-gaps-and-reading.md](ndf-step-result-gaps-and-reading.md)
- 文脈の上限で工程の起動を止める hook: [ndf-context-restart-policy.md](ndf-context-restart-policy.md)
- 実験版の台帳: [docs/ndf-experiments.md](../ndf-experiments.md)
