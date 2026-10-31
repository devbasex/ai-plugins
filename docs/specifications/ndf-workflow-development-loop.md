# 開発ループ（`--loop`）: 棚卸しの順位から次のスプリントを機械で選び、レッドラインと止まる条件のときだけ止まって周回を続ける

## 目的

- **スプリントの境で人の手が止まらない。** `/goal /ndf:development-workflow --loop <名>` を 1 回起動すれば、NDF が
  「選ぶ → 設計 → 実装 → 検査 → 配布 → 振り返り → 棚卸し → 次を選ぶ」を繰り返す。周回のスプリントは `pace: auto` で組み、
  承認ゲート 1・2 は `mvv-gate.py check` の判定だけで通る
- **人が手を止めるのはレッドラインと止まる条件のときだけである。** 承認ゲート 1 で「従う」以外になった課題は人の判断待ちへ
  外して残りで続け、承認ゲート 2 で人へ戻したときだけ問う。予算・同じ失敗・着手できる課題が無い・MVV の外しか残らない・
  利用者の止める指示では、問わずに理由と再開のコマンドを残して止まる
- **どの周回も後から追える。** 何を選び（外した課題と理由を含む）、承認ゲートでどう判定され、何の版を出し、いくら使い、
  なぜ止まったかが、ループの記録 1 か所に残る
- **`--loop` を付けない起動は今と同じに動く。** ループの記録を作らず、棚卸しの後に次のスプリントを始めない

例: マイルストーン「01 近い」の順位が上から #11・#12・#13 で、切り出しの境界が #12 のとき、今の実装は次のように動く。

| 順 | 起きること |
| --- | --- |
| 1 | `sprint-state.py loop start l20261025` が使ってよい条件を確かめ、`.ndf/loop/l20261025/loop.json` を作る（`status: new`） |
| 2 | `loop select` が `upkeep.py rank` を打ち、#11・#12 を「順位」で選び、#13 を「順位（切り出しの境界の外）」で外して周回 1 を足す |
| 3 | conductor がモードと版を決め、`loop sprint --mode standard --dev 10.20.0-dev.1` が `sprint-state.py init`・`new sprint --pace auto`・`new close --pace auto` を打って `pipeline` を返す |
| 4 | #12 の設計が承認ゲート 1 で「判定できない」。`loop lap --done` が #12 を人の判断待ちへ移し、課題の本文に `## 人の判断待ち（開発ループ l20261025）` を書き、#11 だけの続きの `pipeline` を返す |
| 5 | 本番とまとめの後、`loop lap` が周回 1 を `完了` で締め、止まる条件に当たらないので `ndf_next` を返す。ラッパーが次のセッションを起動する |
| 6 | 3 周を終えた境で `budget_laps` に当たり、終了コード 3 と `[ndf-loop] stopped l20261025 budget_laps` の 1 行で止まる |

**手順は Skill が持つ。** ここに書き写さない。

| 何を | 正本 |
| --- | --- |
| `--loop` を受けたら手順を移す入口 | `development-workflow` の [SKILL.md](../../plugins/ndf/skills/development-workflow/SKILL.md) の「開発ループ（`--loop`）」 |
| conductor が周回ごとに打つ順・止まったときの出し方・人の判断待ちの答え方 | [references/loop.md](../../plugins/ndf/skills/development-workflow/references/loop.md) |
| `.ndf/pace.json` の `loop` 節の項目の表 | [references/pace.md](../../plugins/ndf/skills/development-workflow/references/pace.md) の「設定（`.ndf/pace.json`）」 |
| 引継ぎ文書の名 `loop-<ループ名>` | [references/handoff.md](../../plugins/ndf/skills/development-workflow/references/handoff.md) |
| 棚卸しの `quality` 節（品質の枠）の書き方 | `backlog-refinement` の [references/ranking.md](../../plugins/ndf/skills/backlog-refinement/references/ranking.md) |

この文書が扱うのは、`sprint-state.py loop` の副命令の契約、選定・止まる条件・再開の規則、ループの記録の形、
常に成り立つ条件、決定の理由、テスト観点である。

## 用語

定義は [用語集](../glossary.md) が正である（`.ndf/glossary.json` の `document`）。この文書で使う語は
「開発ループ」「周回」「ループの予算」「止まる条件」「ループの記録」「品質の枠」「承認ゲート」「人の判断待ち」である。

## 対象範囲

| 扱う | 扱わない |
| --- | --- |
| `sprint-state.py loop` の副命令（`start` / `select` / `exclude` / `sprint` / `gate` / `answer` / `approve-mvv` / `lap` / `stop` / `status` / `list`） | 今の 1 スプリントの進め方（`normal` / `auto` / `fast`）の振る舞い |
| `.ndf/pace.json` の `loop` 節と `read_loop` | `mvv-gate.py` の判定の規則・レッドラインの定義・`boundary_paths` の既定（ループのために緩めない） |
| `supervise.py new close` の `--pace auto`・`--carry`・`--record-pr`、`new sprint` の `--designed` | 持ち越しをマシンをまたいで残す置き場（#1640） |
| `upkeep.py rank` の品質の枠（`.ndf/backlog.json` の `quality` 節） | Codex・Kiro・agy でのラッパーと同じ自動の切り替え（ラッパーは Claude Code だけ） |
| 引継ぎ文書の名 `loop-<ループ名>`（`handoff.py`） | このリポジトリの `.ndf/pace.json` でループを有効にすること（共通原則の C7。利用者が別に決める） |

ループは NDF の安定版の経路にあり、配布先のリポジトリでも働く。自動の切り替えは Claude Code のラッパー（`relay.py`）の
下だけで働く。ほかのランタイムとラッパーの外では、周回の境とカットポイントで `ndf-next` を出して止まり、利用者が貼り直せば
同じ周回から続く。`loop` 節の無いリポジトリでは何も変わらない。

## 背景

`/goal /ndf:development-workflow` は 1 スプリントの振り返り・棚卸しで終わっていた。次のスプリントを始めるには、人が順位を
読んで課題を決め、`/goal` の文面を書き直していた。止まる条件と選んだ理由は、会話ごとに手で書く `relay.md` のテンプレートの
文面にしか無かった。利用者の依頼（2026-10-25）は「振り返りと Issue の見直しの後、見直しの結果から次のスプリントを設定して
自動で始める」「`pace: auto` を基本とし、MVV のレッドライン以外で承認を一切求めない」「今の goal を決めた進め方も引数で
切り替えて残す」であり、#2089 で入れた（ndf 10.21.0）。

位置付けは Shapiro の 5 段階の Level 4（人は仕様と MVV の水準で関わり、後から確かめる）で、Level 5（dark factory）ではない。
MVV のレッドラインで人へ戻すという要求がそれを決める。

## 仕様

### 構成要素と責務

| 要素 | 責務 |
| --- | --- |
| [`sprint-state.py`](../../plugins/ndf/scripts/sprint-state.py) の副命令 `loop` | 入口。引数の解析と振り分けは [`lib/sprint_loop_cli.py`](../../plugins/ndf/scripts/lib/sprint_loop_cli.py)。`loop sprint` の `--` から後は解析せず `new sprint` へそのまま渡す。既存の副命令は変えない |
| [`lib/sprint_loop.py`](../../plugins/ndf/scripts/lib/sprint_loop.py) | `start`（使ってよい条件・再開）・`sprint`・`answer`・`approve-mvv`・`stop`・`lap`・`status`・`list` |
| [`lib/sprint_loop_select.py`](../../plugins/ndf/scripts/lib/sprint_loop_select.py) | 選定（`select` / `exclude`）。入力は `rank` の出力・記録・マイルストーンの説明だけ |
| [`lib/sprint_loop_gate.py`](../../plugins/ndf/scripts/lib/sprint_loop_gate.py) | 承認ゲートで止まった done の扱い（`gate`。`lap --done` も同じに扱う）・続きの queue・まとめのステージ（`new close --pace auto`）の組み立て・周回の締め |
| [`lib/sprint_loop_record.py`](../../plugins/ndf/scripts/lib/sprint_loop_record.py) | 記録の読み書き（排他・原子的な書き込み・終わった周回の書き換えの拒否）・消費・止まる条件の判定・再開のコマンド・引継ぎ文書・`laps.md` の描画。外の世界（gh・`upkeep.py rank`・`supervise.py`）は `Env` の関数を通して呼び、テストで差し替える |
| [`lib/pace.py`](../../plugins/ndf/scripts/lib/pace.py) の `read_loop` | `loop` 節を読み、既定（`LOOP_DEFAULTS`）を埋めて形を確かめる。`read_pace` は `loop` 節を検証しない |
| [`supervise_lib/sprint.py`](../../plugins/ndf/scripts/supervise_lib/sprint.py)・[`new_args.py`](../../plugins/ndf/scripts/supervise_lib/new_args.py) | `new close --pace auto` でまとめのステージだけを組む。`--carry`・`--record-pr`、`new sprint --designed` |
| [`handoff.py`](../../plugins/ndf/scripts/handoff.py) | 名の形（`NAME_RE`）に `loop-<名>` を持つ |
| `backlog-refinement` の [`upkeep_rank.py`](../../plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank.py) の `quality_slot`・[`upkeep_rank_config.py`](../../plugins/ndf/skills/backlog-refinement/scripts/upkeep_rank_config.py) の `_parse_quality` | 品質の枠を取り、切り出しの境界から枠の分を除く |
| 既存のまま使うもの | `relay.py`（セッションの切り替え）・`mvv-gate.py`（判定とレッドライン）・`supervise.py new sprint --pace auto` / `queue` / `wait` / `run` |

**判断は conductor に残す。** モードの判定と版の決め方はスクリプトにしない。`auto` が許さないモード（`operation` /
`documentation`）の課題と MVV に反する疑いのある課題は、conductor が `loop exclude` で外して選定をやり直す。
スクリプトは LLM を呼ばない。

### 常に成り立つ条件

| # | 条件（保つこと） | 破れたときの扱い |
| --- | --- | --- |
| I1 | 周回は追記だけで、番号は 1 から 1 ずつ増える。終わった周回（`outcome` が `進行中` でない）の行は書き換えない | 書き込みを拒み、記録が壊れているとして `stopped`（終了コード 1） |
| I2 | 進行中の周回は高々 1 つ | `select` が「進行中の周回がある」で 1 |
| I3 | 予算・同じ失敗・止める指示は周回の境（選定の前と周回を締めた後）でだけ判定する。周回の途中で `stop` を書くのは `gate2_returned`・`mvv_changed`・`refused` だけで、周回は進行中のまま残す | 途中の止める指示は次の境まで効かない |
| I4 | 選定は入力（`rank` の出力・記録の人の判断待ちと除外・`approved` の行の設計 PR の状態・ループの設定・スプリント MVV の承認）だけで決まり、同じ入力からは同じ組と並びが出る | テストで落とす |
| I5 | 答えの無い人の判断待ちの課題と、差し戻した課題は選ばない | 選定の行に理由つきで外す |
| I6 | ループは承認ゲートを自分で通さない。`by: mvv` の承認の記録を書くのは `mvv-gate.py` だけで、「従う」以外の判定を通過として扱わない | テストで落とす |
| I7 | 止まったループは、`loop start` が止まった理由を判定し直して解けたと確かめるまで、周回を始めない | `select` が止まったままの結果（3）を返す |
| I8 | 予算の消費（周回の数・費用・動いていた時間）は再開をまたいで引き継ぐ。動いていた時間は、終わった周回の `started_at`〜`ended_at` の和から、`stops` の `at`〜`resumed_at` のうち各周回の区間と重なる部分を引いたもの。周回の境で止まっていた時間は引かない | テストで落とす |
| I9 | 周回を始めるときと承認ゲートで止まったときのプロジェクト MVV の sha256 は、ループの開始時の値と一致し、承認済みである | `mvv_changed` で止まる |

### 起動の引数と再開のコマンド

```text
/goal /ndf:development-workflow --loop [<ループ名>] [--milestones <M>,<M>...] [--budget-laps N] [--budget-usd X] [--budget-hours H]
```

| 引数 | 意味 | 無いとき |
| --- | --- | --- |
| `<ループ名>` | 記録の名前（英数字・`-`・`_`。先頭は英数字）。同じ名前の記録があれば再開する | `l<UTC の YYYYMMDD>` |
| `--milestones` | 選定の対象を絞る | 開いているマイルストーンすべて（`rank` の `order`） |
| `--budget-*` | 予算を上書きする。変わったときだけ `budget_history` に `{at, from, to}` を 1 行足す | `loop.budget` |

再開のコマンド（`ndf-next` の中身。`resume_command`）は
`/goal /ndf:development-workflow --loop <名> -- 終わり: [ndf-loop] stopped <名> の行を出したとき` である。
名前を必ず含め、`loop start --no-goal` で始めた記録（`goal: false`）では先頭の `/goal ` を付けない。`--` から後ろは
`/goal` の終わりの条件で、Skill は引数として読まない。

### 設定（`.ndf/pace.json` の `loop` 節）

```json
{"loop": {"enabled": false, "budget": {"laps": 3, "usd": 60, "hours": 24}, "same_failure": 2, "max_issues": 3}}
```

| 項目 | 形 | 無いとき | 誤っているとき |
| --- | --- | --- | --- |
| `loop.enabled` | `true` だけが真 | 偽（ループを断る） | — |
| `loop.budget` | オブジェクト。`laps`・`usd`・`hours` の正の数（bool は不可）。欠けたキーは既定で埋め、ほかのキーは不可 | 3 / 60 / 24 | `PaceError` |
| `loop.same_failure` / `loop.max_issues` | 1 以上の整数（bool は不可） | 2 / 3 | `PaceError` |

`PaceError` は `loop start` だけを断る。`read_pace` を使う `fast` / `auto` / `check-trigger` / `mvv-gate` は節の誤りの
影響を受けない。

### 副命令の契約（`sprint-state.py loop`）

結果は既存と同じ 1 行の JSON（`tool: sprint-state`）。終了コードは 0 = 進める / 1 = 断った・呼び出しの順の誤り /
3 = 止まる条件に当たった（`step_result` の「正常な否定の結果」。失敗ではない）。1 と 3 は `metrics.status` の
`refused` / `stopped` でも見分ける。4 は `merge-when-green` が使っているため使わない。

| 副命令 | 主な入力 | 0 のときの `metrics` | 1 / 3 |
| --- | --- | --- | --- |
| `start [<名>] [--milestones] [--budget-*] [--no-goal]` | `loop` 節・`auto` 節・プロジェクト MVV・記録 | `status`（`new` / `resumed` / `in_lap`）・`next`（`in_lap` は引継ぎ文書の「次にやること」を指す。`resumed` は `loop select` か、止まった理由ごとの続き） | 1 = 使ってよい条件に当たらない（`items` に理由、`next` に `/ndf:development-workflow #<課題>`。記録を作らない）/ 3 = 止まった理由が解けていない |
| `select <名>` | `upkeep.py rank`（その場で打つ）・マイルストーンの説明・記録 | `status: selected`・`selected`・`excluded`・`milestone`・`rank_digest` | 1 = 進行中の周回がある・`rank` が 0 と 20 以外を返した / 3 = 止まったまま・境の判定で止まった・`no_issue` / `outside_mvv` |
| `exclude <名> <課題> --reason <文>` | 選定の後・`sprint` の前の進行中の周回 | `select` と同じ形（選定をやり直す） | 1 = スプリントを組む前の周回が無い / 3 = すべて外れた（その周回を取り下げ、除外の行は残す） |
| `sprint <名> --mode M --dev V [--prod V] [-- <new sprint に足す引数>]` | 進行中の周回の選定・前の周回の `refine.deferred` | `status: sprint`・`state_path`・`out`・`manifest`・`close_command`・`queue`・`pipeline` | 1 = 進行中の周回が無い・承認ゲート 1 の後の周回 / 3 = `init`・`new sprint`・`new close` のどれかが断った（`refused`） |
| `gate <名> --done <D>` | 承認ゲートで止まった done・`mvv-gate.jsonl`・スプリント状態 | `status: continue`・`gate: 1`・`waiting`・`pipeline`（残りで続く）、または周回を締めて `ndf_next` | 1 = 組んだ周回が無い・done に関門が無い / 3 = `gate2_returned`・`mvv_changed`・続きを組めない（`refused`）・締めた後の境の判定 |
| `answer <名> <課題> --approved\|--rejected [--pr N]` | 答えの無い人の判断待ちの行 | `status: answered` | 1 = 待っていない課題 |
| `approve-mvv <名> --milestone M` | マイルストーンの説明 | `status: approved`（`sprint_mvv_approvals` に `{milestone, sha256, at, by: user}`） | 3 = 説明に `## Mission` / `## Vision` / `## Value` がそろわない |
| `lap <名> [--failed] [--done D] [--deferred-issues N...]` | スプリント状態（`update` を内で打つ）・done・upkeep の記録 | `status: continue`・`ndf_next`（`ndf-next` のブロック） | done に関門があれば `gate` と同じ / 1 = 進行中の周回が無い / 3 = 境の判定で止まった |
| `stop <名>` | — | `path`（`.ndf/loop/<名>/stop` に時刻を書く） | — |
| `status <名> [--json]` / `list <名> [--json]` | 記録・最後の周回の `relay_dir` の `log.jsonl` | `status`・`consumed`・`budget`・`waiting`・止まっていれば `stop` と `stop_line`・ラッパーが上限で止めていれば `relay_max_starts` / 周回の一覧（何周目・選んだ課題・版・費用・秒・結果・止まった理由・後へ回した Issue） | 1 = 記録が無い。`--json` が無ければ文で出す |

**止まった結果は形が決まっている。** `items` は `[{condition, values, resume}]`、`metrics` は `status: stopped`・`stop`・
`stop_line`（`[ndf-loop] stopped <名> <condition>`）・`consumed`。止まるたびに引継ぎ文書の「次に実行するコマンド」を
再開のコマンドへ置き換える。

**`ndf_next` は周回を締めて続くときだけ返る。** 止まったときは返らない。conductor は `ndf-next` のブロックを出さず、
`stop_line` を最後の行に写す（手順は `loop.md` の「止まったとき」）。

### 選定（`select` / `exclude`）

マイルストーンを `rank` の `order` の順（`--milestones` で絞ったときはその中だけ）に見て、1 件でも選べた最初の
マイルストーンで決める。1 つのスプリントは 1 つのマイルストーンから組む。

1. 候補は、切り出しの境界（`boundary.number`）までの行（境界が無ければ先頭から `max_issues` 件。`boundary.empty` なら
   0 件）、大きい課題の枠が当てたとき（`large_slot.status` が `当てた`）の `large_slot.slice`、品質の枠の課題（`quality_slot.issues`）である
2. 説明にスプリント MVV（`## Mission` / `## Vision` / `## Value`）があり、その sha256 が `sprint_mvv_approvals` に無い
   マイルストーンは、候補をすべて「スプリント MVV が未承認」で外す
3. 候補ごとに外す理由を順に見る: 「人の判断待ち（棚卸し）」（`waiting_decision`）→「人の判断待ち」（答えの無い行）→
   「差し戻し」（`rejected`）→「設計 PR がマージ待ち」（`approved` で `merged_at` が無く、`gh pr view` が `MERGED` でない。
   `MERGED` なら `merged_at` を足して以後打たない）→ この周回の `excluded` の理由 →
   「周回の中で起票された」（周回の `created`）→「容量（尺度を超える）」
4. 外れなかった候補に `max_issues` 件の枠を、品質の枠の課題から先に、残りを順位の順に割り当てる。選んだ行の理由は
   「品質の枠」か「順位」、枠からあふれた行は「容量」で外す
5. 選べたら、そのマイルストーンの候補でない行を「順位（切り出しの境界の外）」で外した行として足す。外した行も
   `selection` に載せ、`rank` の `metrics.digest` を `rank_digest` に書く。スプリント MVV があれば周回のディレクトリの
   `mvv.md` に写し、`loop sprint` が `sprint-state.py init --mvv` へ渡して承認ゲート `MVV` の記録を状態へ写す
6. どのマイルストーンでも選べず、候補が 0 件なら `no_issue`、候補があってすべて外れたなら `outside_mvv` で止まる
   （`values` は対象のマイルストーンか理由ごとの件数）

`rank` の 20（段階の欠けた課題がある）は、欠けた課題が `rows` に載らないだけなので続ける。

### 周回のスプリントとまとめのステージ（`sprint`）

`loop sprint` は周回のディレクトリ（`.ndf/loop/<名>/lap-<n>/`）に状態ファイル `state.json` を置き、次の 3 つを順に打つ。

| 打つもの | 契約 |
| --- | --- |
| `sprint-state.py init <state> --name <名>-<n> --pace auto --issue ... --dev V --prod V --goal <再開のコマンド> [--mvv mvv.md --milestone M]` | `--prod` を省けば開発版の接尾辞を外した版 |
| `supervise.py new sprint --pace auto --issue <選んだ課題> --design <承認済みでない課題> [--designed <approved の課題>] ... [<-- の後の引数>]` | `loop answer --approved` で答えた課題は設計のプランを作らず、設計の結果のステージが設計 PR の設計の結果を読む |
| `supervise.py new close --pace auto --issue <選んだ課題> --state <state> [--carry <前の周回の refine.deferred>] [--milestone M]` | まとめのステージ（確定仕様化 → Pull Request → 振り返り → 課題を閉じる → 棚卸し。#2228）だけを組む。最終の検査・開発版・本番は置かず、`fast` 節を読まない |

`pipeline` は、最初のステージの queue の最後に `--then <まとめのプラン>` をつなぎ、`supervise.py wait <D>` →
`loop lap <名> --done <D>` を続けた 1 行である。手で行うステージか別の queue のステージが残るときは、それを終えてから
まとめを流す文になる。

`new close` に足した引数の契約:

| 引数 | 意味 | 無いとき |
| --- | --- | --- |
| `--pace auto` | まとめのステージだけを組む（上の表） | 今の形（`fast` のスプリントの終わり）。出力は変わらない |
| `--carry N...` | 棚卸しのステップの指示へ前の周回の持ち越しを `--add` で渡す | 指示は今のまま |
| `--record-pr N` | 課題を閉じるステップ（`sprint-close.py --record-pr`）へ本番の記録の Pull Request を渡す | 配布の経路から導く印（`{queue_pr:release-prod}` など）を残し、queue が前のステージの本番のプランの Pull Request で埋める |

周回の中では `--record-pr` を渡さない。渡すのは承認ゲート 2 の承認の後にまとめを組み直すとき（`stop.values.pr`）だけである。

### 承認ゲートで止まったとき（`gate`、`lap --done`）

| 止まった所 | 扱い |
| --- | --- |
| どこでも、プロジェクト MVV が開始時と違う・未承認 | `mvv_changed` で止まる |
| 設計のプラン（承認ゲート 1）。判定が「従う」以外・`boundary` が空でない・機械のチェック・判定を読めない | 計画の課題を `waiting` へ移し（`{number, reason, verdict, basis, gate: 1, pr, lap, at}`。`pr` は報告の設計 PR、判定は `mvv-gate.jsonl` のその PR の最後の行）、課題の本文の節 `## 人の判断待ち（開発ループ <名>）` を書き直す。設計が完了も関門もしなかった課題は続きから外す。残った課題（`issues_kept`）で、関門 1 の後のステージから外した課題だけの計画を除き、共通の計画の `課題` と `closes` からも外し、まとめを組み直して最後の `--then` につなぐ |
| 同上で残りが無い | 周回を `人へ戻した`（設計が完了しなかった課題があれば `失敗`）で締め、境の判定へ進む |
| 同上で続きを組めない | `refused`（`step: 続きの queue`）で止まる |
| 承認ゲート 1 以外（承認ゲート 2） | 判定の写しを周回へ足し、止まった計画を `gate2_plan` に控え、`gate2_returned`（`values: {pr, verdict, reasons}`）で止まる。本番は出さない |

承認ゲート 2 の答えは、止まった時刻より後の、周回の状態の `関門 2` の利用者の記録（`by` が `mvv` でない）と
`gate-rejections.jsonl` の差し戻しのうち最新のもので決める。

`loop answer --approved` は `sprint-state.py gate <その周回の状態> 関門 1 --by user --outcome approved [--pr N]` を内で打つ。
`--rejected` は `--outcome rejected` で差し戻しの記録を残す。どちらも課題の本文の節を書き直す。

### 止まる条件と再開

周回の境の判定（`boundary_stop`）は次の順で、最初に当たったものを止まった理由にする。判定に使った値は `values` に残す。

| 順 | `condition` | 判定 | `values` |
| --- | --- | --- | --- |
| 1 | `user_stop` | `.ndf/loop/<名>/stop` がある | `{at}` |
| 2 | `mvv_changed` | プロジェクト MVV が未承認か、sha256 が開始時と違う | `{started, now}` |
| 3 | `same_failure` | 終わった周回を新しい順に見て、`失敗` の周回が同じ `failure` の鍵で `same_failure` 回以上続く。`完了` の周回で数え直す。`same_failure` で止まった後の最後の再開（`resumed_at`）より前に始まった周回は数えない。ほかの理由の再開は区切りにしない | `{key, count}` |
| 4 | `budget_laps` / `budget_usd` / `budget_hours` | 終わった周回の数・`cost` の和・動いていた時間（I8）が予算以上 | `{consumed, budget}` |

選定の時点では `no_issue` / `outside_mvv`、周回の途中では `gate2_returned` / `mvv_changed` / `refused`
（`{step, exit, summary}`）が加わる。`stop` は `{condition, values, lap, at}` の形で、解けたら `resumed_at` を足して
`stops` へ移す。

失敗の鍵は、スプリント状態の計画の行のうち結果が `まだ`・`流さなかった`・`完了`・`関門` でない最初の行の
`{issues, kind, step}` である。理由の文は含めない。`loop lap --failed` で締めて該当の行が無ければ
`{issues: 選んだ課題, kind: 周回, step: conductor}` にする。周回は、`--failed`・done に完了でも関門でも流さなかったでもない
結果がある・失敗の鍵がある、のどれかで `失敗`、ほかは `完了` で締まる。

`loop start` の再開は止まった理由ごとに次のとおりである。

| 止まった理由 | 解けたかの見方と、その後 |
| --- | --- |
| `user_stop` | ファイルを消して解ける |
| 予算 | 再開の引数で上書きした予算で判定し直す。まだ予算以上なら 3 |
| `same_failure` | 再開の起動そのものを解けた印とし、失敗の数を数え直す |
| `gate2_returned` | 答えが無ければ 3。差し戻しなら周回を `人へ戻した` で締める。承認なら `next` に、止まった計画を経路に合うステップ（雛形の本番は `--from bump`、昇格は `--from promote-approved`、マージで止まった計画は `--from merge-approved`）から `supervise.py run` で続け → 同じ queue の残りのステージ → `--record-pr <stop.values.pr>` で組み直したまとめ → `wait` → `loop lap` の 1 行を載せる。続けるステップが無い計画は、手で行う本番を先に示す |
| `refused`（続きの queue） | 人の判断待ちへ移した課題を戻さず、残した課題で続きを組み直す。組めなければ 3 |
| `refused`（ほか） | `next` に同じ周回での `loop sprint` のやり直しを載せる（モードと版は conductor が決め直す） |
| `mvv_changed` | 解けない。新しい名前でループを始め直すよう示す（開始時の値を今の値へ置き換えない） |

### ループの記録（データ）

**置き場はメインディレクトリで、コミットしない。** worktree の後片付けで消えず、`git status` に `?? .ndf/loop/` と出る。
`.ndf/` の設定（C7）ではなく、スクリプトが作る記録である。読み書きは `locks.exclusive`（60 秒）で囲み、
`jsonio.write_atomic` で書く。

| 置き場 | 中身 |
| --- | --- |
| `.ndf/loop/<名>/loop.json` | 正本（下の表） |
| `.ndf/loop/<名>/laps.md` | 人が読む描画（周回の一覧・周回ごとの詳細・人の判断待ち・止まった理由）。記録を書くたびに作り直す |
| `.ndf/loop/<名>/lap-<n>/` | 周回のスプリント状態・プランの出力・done・`mvv.md` |
| `.ndf/loop/<名>/stop` | 止める指示（`loop stop` が置き、`user_stop` の再開で消す） |
| `.ndf/handoff/loop-<名>.md` | 引継ぎ文書。「現在地」「次にやること」「次に実行するコマンド」だけを置き換える |

`loop.json`（`version: 1`）:

| 項目 | 意味 |
| --- | --- |
| `name` / `repo` / `started_at` / `goal` | 名前・`OWNER/REPO`・開始の時刻（再開で変えない）・`/goal` で始めたか |
| `project_mvv` | 開始時の承認済みプロジェクト MVV（`{version, sha256}`） |
| `budget` / `budget_history` | 効いている予算と上書きの履歴 |
| `same_failure` / `max_issues` / `milestones` | 開始時に設定と引数から写した値 |
| `status` / `stop` / `stops` | `running` / `stopped`・今の止まった理由・過去の止まった理由（追記だけ） |
| `laps[]` | 周回（追記だけ。I1） |
| `waiting[]` | 人の判断待ち。答えは行を消さずに `answer`・`answered_at` を足す。`approved` の行はマージを確かめた時刻 `merged_at` を足す |
| `excluded[]` | `{number, reason, lap}`（`loop exclude`） |
| `sprint_mvv_approvals[]` | `{milestone, sha256, at, by}` |

周回（`laps[]` の 1 行）の主な項目:

| 項目 | 意味 |
| --- | --- |
| `n` / `sprint` / `dir` / `state_path` / `out` / `manifest` | 番号・スプリントの名前（`<ループ名>-<n>`）・周回のディレクトリ・状態ファイル・プランの出力・マニフェスト |
| `milestone` / `rank_digest` / `selection[]` | 選んだマイルストーン・選定に使った `rank` の digest・`{number, milestone, rank, decision: 選んだ\|外した, reason}` |
| `mode` / `versions` | conductor が決めたモードと `{dev, prod}` |
| `sprint_command` / `close_command` / `dones[]` / `prod_plan` / `gate2_plan` / `issues_kept` | 打ったコマンド・done のパス・本番のプラン・承認ゲート 2 で止まった計画・承認ゲート 1 の後に残した課題 |
| `gates[]` | 承認ゲートの判定の写し（`{name, by, verdict, basis, pr, at}`） |
| `retro` / `refine` | 振り返りの置き場（まとめのプランの報告）・棚卸しの結果 `{report, rank_changes, deferred[]}`（upkeep の記録の `candidates.json`・`rank.json` から） |
| `created[]` / `deferred_issues[]` | 周回の中で起票された課題（その周回の選定に入れない）・上限で後へ回した Issue（`lap --deferred-issues`） |
| `cost` / `seconds` | スプリント状態の計画の行の和 |
| `outcome` / `failure` | `進行中` / `完了` / `失敗` / `人へ戻した`・失敗の鍵 |
| `relay_dir` / `started_at` / `ended_at` | 締めたセッションの `NDF_RELAY_DIR`・周回の開始と終わり |

**周回はスプリント状態を `state_path` で参照し、締めるときに費用・秒・承認ゲート・版を写して固定する。** 以後スプリント状態が
変わっても、周回の行は書き換えない。

### 品質の枠（`upkeep.py rank`）

`.ndf/backlog.json` の `quality` 節（`{"label": "quality", "ratio": 0.2, "min": 1}`。キーの既定はこの値）を宣言すると、
`rank` はラベル `label` の品質向上の Issue から、容量の `ratio`（切り捨て。最低 `min` 件。容量が無ければ `min` 件）を
先に取り、切り出しの境界は残りの容量で決める。枠の中の並びは、実害の観測（`observed_harm`）→ 起票の古い順 → 今の順位。
大きい課題の枠の 1 切れ・尺度を超える課題・未完了の依存先が枠に入っていない課題は取らない。枠で容量を使い切ったら
境界は `empty`（通常の候補は 0 件）になる。結果は `quality_slot` と順位の表の「品質の枠」に出る。宣言が無ければ
順位と境界は今と同じである。

ループの中で品質を上げる工程（クロスレビュー・クロスリファクタリング・検査）は今の上限（ラウンド・時間の予算・`timeout`）で
打ち切る。残った指摘は conductor が `out-of-scope` で、`quality.label` のラベルを付けた Issue に起こし、番号を
`loop lap --deferred-issues` で周回へ書く。人を害する・秘密・利用者のデータ・戻せない操作に当たる指摘だけは、上限に達しても
その周回で直すか、レッドラインとして人へ戻す。

## 既知の限界

| 項目 | 内容 |
| --- | --- |
| 周回の `created[]` の書き手 | 選定は `created` を外す理由に読むが、10.21.0 の副命令には `created` を書く経路が無い。周回の中で起票された課題がその周回で選ばれないのは、`rank` が段階の付いた課題だけを並べ、段階は棚卸しが付けるためである |
| 品質の枠の並び | 決定で挙げた「同じ手当てや失敗の繰り返しの回数」は `rank` の入力に無いため数えない（実害の観測 → 起票の古い順 → 今の順位） |
| 予算の既定値 | 標本は 2 件（#2065・#2086 の設計と実装）で、まとめの費用を含まない。リリース後テストの実測で見直す |
| 1 周回の費用の上限 | 予算は周回の境でだけ判定するため、超過は高々 1 周回分になる。1 周回が予算を大きく超えるなら、周回の中の上限を別の課題で考える |
| 持ち越しの読み先 | upkeep の記録の置き場（`$NDF_UPKEEP_STATE_DIR`、既定は一時ディレクトリ）が同じマシンで周回をまたいで残る前提である。マシンをまたぐ置き場は #1640 が持つ |
| `/goal` の評価役 | 止まったときの 1 行を評価役が「条件を満たした」と読んでゴールを終えるかは、評価役の解釈に任される。終わりの条件を再開のコマンドの末尾に書いて読みやすくしている（決定 12） |
| 失敗で締めた周回の後始末 | 失敗で締めた周回のスプリントブランチと開いた Pull Request は、今の conductor の手当てのまま残る |

## 決定の理由

| # | 決定 | 理由 |
| --- | --- | --- |
| 1 | ループの記録を新しい集約（`loop.json`）にし、入口を `sprint-state.py loop` に足す | スプリント状態ファイルに足すと周回ごとに別のファイルができ、一覧に全周回のファイルを探すことになる。新しいスクリプトにすると引数の読み方と結果の出し方が 2 つに分かれる。新しいキューや常駐の処理は作らない |
| 2 | 選定を `rank` の出力と記録だけで決め、1 つのマイルストーンから組む | AI が範囲を広げないため。複数のマイルストーンから混ぜるとスプリント MVV が 2 つ以上になり、着手の順序を飛び越える |
| 3 | モードと版は conductor が決め、対象外は `loop exclude` で選定へ戻す | 判断をスクリプトへ移さない。外した理由が記録に残るため、同じ入力から同じ組になる |
| 4 | 承認ゲート 1 で人へ戻した課題は人の判断待ちとして外して続け、承認ゲート 2 で人へ戻したら止まる（承認ゲート 1 で利用者が選択） | 「レッドライン以外で承認を一切求めない」と「決められないときは人へ戻す」の両方を満たす。ループを止めて待つ案は「判定できない」1 件で全体が止まり、「判定できない」を「従う」と見なす案は共通原則に反する。ゲート 2 で止まるのは、本番を出さずに進むと次の本番に判定を通っていない変更が混ざるため |
| 5 | 予算を周回の数・費用（USD）・経過時間の 3 つにし、既定を 3 周・60 USD・24 時間にする（同上） | 費用と秒はスプリント状態が既に埋めている量で、新しく測る仕組みが要らない。トークン数は周回の単位で集めておらず、費用で同じ目的を果たす。`/goal` の計数は `--continue` / `--resume` でリセットされ、周回ごとにセッションが替わるため使えない。既定は 1 件約 6 USD・L 2〜7 時間の実測から、1 周 3 件で約 20 USD・3 周で 60 USD、24 時間とした |
| 6 | 記録を `.ndf/loop/<名>/` に置き、`laps.md` を描画し、引継ぎ文書は現在地だけを持つ（同上） | 引継ぎ文書と同じく後片付けで消えず、コミットが要らず、引継ぎ文書の 300 行の上限に周回を載せずに済む。課題のコメントは一覧に GitHub の検索が要り（当てにならないことは #1640 で実測）、`docs/` は周回ごとに Pull Request が要る |
| 7 | `--loop` と `loop` 節で切り替え、`loop` 節を `read_pace` から分けて読む（同上） | 今の進め方を壊さないため。`pace: loop` を足す案は、周回の中が `auto` そのものなので承認の扱いを 2 つの節で二重に持つ |
| 8 | 失敗の鍵を「課題の組・計画の種類・止まったところ」にし、既定 2 回で止める（同上） | 理由の文を鍵に含めると文面の揺れで別の鍵になる。2 回は「1 回は立て直してやり直す」を許す回数。Claude Code の安全弁（Tool を使わない応答の連続・Stop hook の block 8 回）はターン単位で、周回をまたぐ失敗を捕まえない |
| 9 | 止まったときは `ndf-next` のブロックを出さず、止まる条件は周回の境でだけ判定する | ブロックを出すとラッパーが次のセッションを起動し、同じ理由で止まり直す。途中で止めるとブランチと Pull Request が半端に残り、再開の起点が境とずれる |
| 10 | `new close --pace auto` でまとめのステージだけを組む | 本番から次の周回までを 1 本のパイプラインで流すため。conductor が `retrospective` と `backlog-refinement` を順に起動する案は、周回の終わりの手順が文面に残る |
| 11 | スプリント MVV を持つマイルストーンは、利用者が承認した sha256 があるときだけ選ぶ（同上） | 周回ごとに承認を求めず、かつスプリントのレッドライン（R）を効かせるため。スプリント MVV を持つマイルストーンを対象から外す案は採らなかった |
| 12 | 止まったときは決まった語の 1 行を出し、再開のコマンドの末尾に終わりの条件を書く（同上で案 B） | `/goal` の評価役はコマンドもファイルも見ず、会話の出力だけで判定する。公式の「条件は出力で示せる形に書く」に合わせた |
| 13 | 品質を上げる工程は今の上限で打ち切り、残った指摘は Issue にして次の周回へ回す（利用者の指定。コスト > スピード > 品質） | 周回の中で上限を延ばすと品質を費用とスピードより先に置く。記録に書くだけでは選定に乗らず埋もれる |
| 14 | 棚卸しの順位付けで品質向上の Issue に容量の枠を取る（承認ゲート 1 で利用者が選択） | 決定 13 だけでは遅延コストの低い品質向上が後ろに積み残る。枠は毎スプリント品質を進めつつ割合で量を抑え、順位の式は変えない。滞留の加点は上がるまで 0 件の周回があり段階の意味を変え、件数の閾値は波が大きい |

### 外部の動向から取り入れたもの

一次情報は 2026-10-24 に本文を開いて確かめた。起票時に挙げた解説記事は根拠に使っていない。

| 考え方 | 出典 | 受けた所 |
| --- | --- | --- |
| 反復には上限を含む止まる条件を必ず置き、人へ戻すのはチェックポイントと行き詰まりに限る。骨格は決まった順の workflow にし、最小から始める | Building effective agents | 止まる条件・承認ゲート 2・同じ失敗。選定・判定・記録は LLM を呼ばないスクリプトで、周回は直列（I2） |
| 1 回に 1 つの塊だけを進め、回の始めに前回の記録を読み、終わりに書いてから次へ進む | Effective harnesses・Huntley | 1 周回 1 スプリント・1 マイルストーン。`loop start` が読み、`loop lap` が書いてから `ndf_next` を返す |
| 完了の判定を書き換えにくい構造化データに置き、作る役と判定する役を分ける。1 回の長い実行は 3〜6 時間・124〜200 USD に達した | Effective harnesses・Harness design・StrongDM | `loop.json` が正本、`laps.md` は描画。承認ゲートの判定は `mvv-gate.py` だけが書く（I6）。予算の上限を必須にする |
| 完了は主張であって証明ではない。人が後から追える記録を残す | Osmani・Huntley | 周回に判定の写し・版・振り返りの置き場を固定する（I1）。`laps.md` と `loop list` |
| 人が持つのは優先順位・受け入れ条件・成果の確認 | OpenAI | 優先順位は MVV と棚卸しの順位、確認は承認ゲート 2 と周回の記録 |
| `/goal` の計数は再開でリセットされ、評価役は出力だけで判定する | Claude Code の `/goal` | 予算の累計は記録が持つ（I8）。止まったときの 1 行（決定 12） |

取り入れなかったもの: 人がコードを書かず・レビューもしない規則と消費の目安（StrongDM。配布するプラグインの検査を外せない）、
止まる条件を持たず収束するまで回す形（StrongDM）、Level 5 を目標にすること（Shapiro）、マージ前の検査を最小にする運用
（OpenAI。記事自身が低スループットの環境では無責任と条件を付けている）、新しい進捗ファイル（Effective harnesses。進捗は
既に Issue・Pull Request・状態ファイルにある）、Stop hook を自作して外側のループを強制すること（`/goal` と二重になり block の
上限に干渉する）、選定にエントロピーの返済の枠を常に入れること（OpenAI。入力が `rank` の出力だけで決まる形が崩れる）、
worker の `claude -p` に `--max-budget-usd` を渡すこと（ステップの途中で止まり半端に残る）。

## テスト観点

止まる条件・選定・記録は、`rank` の出力・記録・設定を入力にして関数を直接呼ぶ単体テストで縛る。`gh`・`claude`・
`upkeep.py rank`・`supervise.py` は `Env` で差し替える（[`tests/test_sprint_loop.py`](../../plugins/ndf/scripts/tests/test_sprint_loop.py)、
`new close --pace auto` は [`tests/test_supervise_close_auto.py`](../../plugins/ndf/scripts/tests/test_supervise_close_auto.py)）。

| 観点 | 壊したら落ちるべきこと |
| --- | --- |
| `--loop` を付けない起動・`--pace` を付けない `new close`・`loop` 以外の副命令・`read_pace` の出力が今のまま | `new close` の既定を `auto` のまとめにする・`read_pace` が `loop` 節を検証する |
| `loop start` が `loop.enabled` 偽・`auto` 節の欠け・プロジェクト MVV の未承認・`loop` 節の形の誤りのそれぞれで 1 を返し、1 スプリントの起動の形を出し、記録を作らない | 確かめを 1 つ外す・断るときに記録を作る |
| 承認ゲートのどの判定でも、ループが承認の記録を書かず、「判定できない」の課題を人の判断待ちへ移す。承認ゲート 1 で全課題が外れたら周回を締めて境の判定へ進み、承認ゲート 2 ではどの判定でも 3 で止まる | `unknown` を通過として扱う・ループが `gate --by mvv` を打つ・ゲート 2 の後に次の周回を始める |
| 同じ `rank` の出力と記録から同じ組と並びが出る。境界まで・`max_issues` まで・1 つのマイルストーンから。品質の枠と大きい課題の枠の 1 切れが候補に入り、品質の枠が `max_issues` の中で先に取られる | 並びを集合で扱う・境界を無視する・2 つのマイルストーンから選ぶ |
| 選定の行が、選んだ行と外した行の両方を理由つきで持つ。周回の中で起票された課題がその周回に入らない | 外した行を書かない・`created` を候補に足す |
| 周回の数・費用・動いていた時間のそれぞれが予算以上で `lap` が 3 を返し、進行中の周回では予算で `stop` を書かない | `>` と `>=` を取り違える・`gate` で予算を判定する |
| 同じ鍵の失敗が `same_failure` 回続くと止まり、完了の周回で数え直す。`same_failure` で止まって再開した後の 1 回目では止まらず、ほかの理由の再開は区切りにしない | 鍵を理由の文で作る・累計で数える・再開より前の周回を数える |
| 候補 0 件で `no_issue`、すべて外れて `outside_mvv` | 2 つを取り違える・0 件で周回を足す |
| `loop stop` のファイルで次の境に止まり、再開でファイルが消える | 周回の途中で止める・再開でファイルを残す |
| 止まった出力の `items` に条件・値・再開のコマンドがあり、`stop_line` の条件が `stop.condition` と一致し、引継ぎ文書の「次に実行するコマンド」が再開のコマンドになる | 再開のコマンドを書かない・条件の語を食い違わせる |
| 再開で周回の数・費用・動いていた時間を引き継ぎ、番号が続く。止まっていた時間は経過時間に入らず、境で止まった時間は引かない | 再開で消費を 0 に戻す・壁時計で数える・境の時間を引いて負にする |
| 周回を締めた後、周回の行が選定・判定の写し・版・振り返りの置き場・棚卸しの結果・費用と秒・後へ回した Issue を持ち、`list` が一覧を出す | どれかを写さない・後へ回した Issue を同じ周回の候補に足す |
| 前の周回の持ち越しが `--carry` で棚卸しの指示に載り、まとめのステージが最後の `--then` につながり、`gate2_returned` からの再開の `pipeline` が止まった計画の続き → 残りのステージ → `--record-pr` 付きのまとめ → `wait` → `lap` になる | 持ち越しを読まない・まとめをつながない・`--record-pr` を固定の値で渡す |
| 人の判断待ちの課題が答えの前は選ばれず、`--approved` の後は設計 PR がマージされた後の周回で `--designed` として選ばれ、`--rejected` の後は選ばれない | 答えを待たずに選ぶ・差し戻しを選び直す・マージ前に選ぶ |
| `quality` 節を宣言した `rank` が同じ入力から同じ境界を返し、枠の分を先に取って印を付ける。宣言が無いと出力が今と同じ | 割合の丸めを違える・宣言が無いのに枠を取る |
| 終わった周回の行を書き換える書き込みが拒まれ、進行中の周回があると `select` が 1、止まったループで `select` が 3 | 締めた周回を上書きする・2 つ目の周回を足す・`start` を経ずに周回を始める |
| 周回の途中の `gate2_returned`・`mvv_changed`・`refused` は周回を進行中のまま残し、`mvv_changed` の再開は解けずに新しい名前での始め直しを示す | 途中の停止で周回を締める・`mvv_changed` の再開で旧周回を続ける |

## 関連リンク

- 課題: #2089（要求と受け入れ条件）、#1640（持ち越しの置き場）、#996（`/goal` に頼らない自走）
- 手順: [`development-workflow` の references/loop.md](../../plugins/ndf/skills/development-workflow/references/loop.md)・[references/pace.md](../../plugins/ndf/skills/development-workflow/references/pace.md)・[references/relay.md](../../plugins/ndf/skills/development-workflow/references/relay.md)
- 関係する確定仕様: [ndf-relay-segment-restart.md](ndf-relay-segment-restart.md)（ラッパー）・[ndf-backlog-refinement-stage.md](ndf-backlog-refinement-stage.md)（スプリントの終わりの棚卸し）・[ndf-workflow-unit-and-gates.md](ndf-workflow-unit-and-gates.md)（承認ゲート）
- 外部の一次情報:
  - Anthropic「Building effective agents」 https://www.anthropic.com/engineering/building-effective-agents
  - Anthropic「Effective harnesses for long-running agents」 https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
  - Anthropic「Harness design for long-running application development」 https://www.anthropic.com/engineering/harness-design-long-running-apps
  - OpenAI「Harness engineering: leveraging Codex in an agent-first world」 https://openai.com/index/harness-engineering/ （日付は検索結果の値で、本文からは確かめていない）
  - Claude Code の `/goal` https://code.claude.com/docs/en/goal 、hook https://code.claude.com/docs/en/hooks-guide
  - 語の原典: Addy Osmani「Loop Engineering」 https://addyosmani.com/blog/loop-engineering/ 、StrongDM「Software Factories And The Agentic Moment」 https://factory.strongdm.ai/ （Story のページだけを確かめた）、Dan Shapiro「The Five Levels」 https://www.danshapiro.com/blog/2026/01/the-five-levels-from-spicy-autocomplete-to-the-software-factory/ 、Geoffrey Huntley「everything is a ralph loop」 https://ghuntley.com/loop/
