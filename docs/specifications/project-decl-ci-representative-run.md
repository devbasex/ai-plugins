# 宣言の解析の代表の run: 重いジョブを飛ばした CI の run を所要に採らず、プランの CI 待ちとテストの上限がテストを走らせた run の所要から出る

## 目的

- **CI の所要は、テストを実際に走らせた run から測る。** 宣言の解析（`project-decl.py measure`）は、ワークフローごとに
  ジョブを飛ばしていない最新の成功の run を代表の run に採り、壁時計（`ci.workflows[].wall_seconds`）とテストの所要
  （`test_duration` の `ci-steps`・`ci-junit`）をそこから測る
- **プランの `merge` が、必須のチェックの完了前に打ち切られない。** `ci_wait_timeout`（3·c）とテストの上限が、
  ジョブを飛ばした数秒〜十数秒の run から出なくなり、conductor が `merge-when-green` を手で打ち直す手当てが要らなくなる

例: スプリント m1693 の時点の ai-plugins を、変更の前と後で解析する。

| 時点 | 振る舞い |
| --- | --- |
| 成功の run の一覧 | `pytest.yml` の最新の成功は release の Pull Request の run 37160943783。ジョブ `pytest (${{ matrix.shard }}/2)` は `conclusion: skipped`・steps 0 個で、run の conclusion は `success`、壁時計は 17 秒 |
| 変更の前 | 最新の 1 件を代表に採り、`pytest.yml` の壁時計は 17 秒・`ci-steps` は 1.0 秒。c は全ワークフローの最大（runtime-plugin-smoke の 42 秒）になり、`ci_wait_timeout` = 126 秒。`merge-when-green --timeout 126` が `pytest (0/2)` の完了前に打ち切られた（PR 1714・1716） |
| 変更の後 | 37160943783 のジョブを取って飛ばした run と判定し、次の候補へ進む。shard を走らせた run（実測 525〜578 秒）が代表になり、c と `ci_wait_timeout` が pytest の所要から出る |
| 他のワークフロー | 飛ばした run が無いので、候補の 1 件目で代表が決まる。値も `gh` の呼び出しの数も変更の前と同じ |

**解析の手順と書き出しの規則は Skill が正である。** ここに書き写さない。

| 何を | 正本 |
| --- | --- |
| 代表の run の選び方（直近 50 件・ワークフローごとに最大 5 件・ジョブの conclusion で判定）と、注記の 2 つの形 | [`project-analysis.md`](../../plugins/ndf/skills/development-workflow/references/project-analysis.md) の「3 手」（`measure` の締め切りの段落の後） |
| 書き出しで `ci` のワークフローの行の壁時計を前の値から残す規則と、出力の行 | 同上の「書き出しの規則」 |
| `measure` / `write` の終了コードと次に行うこと | 同上の「3 手」の表 |
| 解析器の版が変わったときの自動の再解析 | 同上の「再解析」 |

この文書が扱うのは、Skill に書かない決定の理由、常に成り立つ条件、`measure_ci.py` と `merge.py` の部品の契約、
解析器の版、テスト観点である。

## 用語

定義は [用語集](../glossary.md) が正である（`.ndf/glossary.json` の `document`）。この文書で使う語は
「プロジェクトの宣言」「解析」「測った値」「代表の run」「ジョブを飛ばした run」である。

## 対象範囲

| 扱う | 扱わない |
| --- | --- |
| 宣言の解析の CI の測定で、代表の run を選ぶ規則（`wall_seconds`・`ci-steps`・`ci-junit`） | `merge-when-green` と `test-run.py` の CI 待ちの打ち切りの規則 |
| 代表の run が決まらないワークフローの扱い（壁時計を書かない・注記を出す・書き出しで前の値を残す） | `ci_wall_seconds` の「名前の合うワークフローが無ければ最大」の規則と、`3·c` / `3·w` の係数 |
| 候補のジョブの取得が時間切れになったときの扱い | ワークフローの `ci-heavy-skip.py` の判定（飛ばすこと自体は正しい振る舞い） |
| 解析器の版（`ANALYZER`）を 2 へ上げ、版 1 の宣言を自動の再解析にかけること | 宣言をメインディレクトリから読む仕組み（worktree 側の値が効かない件） |
| | 宣言の形（`project.schema.json`）。値の出所の run が変わるだけで、形は変えない |

## 背景

`measure_ci.py` の `_measure_runs` は、`actions/runs?status=success&per_page=50` の直近 50 件から、ワークフローごとに
最新の 1 件を代表に採っていた。重いジョブを飛ばした run も conclusion は `success` で、run の一覧からは見分けられない。
そのため壁時計とテストの step の合計が飛ばした run の値になり、`lib/test_strategy.py` の `ci_wall_seconds`・`whole_seconds` を
通してプランの上限が縮んだ。3 回再現している。

| いつ | どこで | 症状 |
| --- | --- | --- |
| 2026-10-02 | スプリント m725（PR 1662） | `pytest.yml` の壁時計が 19 秒になり、`ci_wait_timeout` 156 秒で `merge` が打ち切られた |
| 2026-10-03 | スプリント m1649 | `test_duration` が `ci-steps` の 1.0 秒だけになり、範囲テストの上限が 3 秒になった |
| 2026-10-04 | スプリント m1693（PR 1714・1716。#1717 として別に起票） | `ci_wait_timeout` 126 秒で `merge` が `pytest (0/2)` の完了前に打ち切られた |

`gh api` で直近 50 件の各 run のジョブを数えると、`pytest.yml` は 7 件のうち 2 件（どちらも release の Pull Request。
壁時計 14 秒・16 秒）がジョブを飛ばし、残る 5 件は 525〜578 秒だった。他の 8 本のワークフローは飛ばした run が 0 件だった（2026-10-04 の実測）。

## 決定と理由

- **待ちの側ではなく測定の側で直す。** `merge-when-green` に「必須のチェックが動いている間は打ち切らない下限」を足すと、
  壊れた所要は宣言に残ったまま、テストの上限（m1649）は直らない。原因の場所は代表の run の選び方の 1 か所である
- **飛ばした run はジョブの `conclusion == "skipped"` だけで判定する。** run の conclusion は `success` のままで、
  run の一覧の欄では見分けられない。ワークフロー・ジョブの名前や event・branch で判定すると、配布先のリポジトリごとに
  設定が要る。`conclusion` の無いジョブは飛ばしていないものとして扱う
- **代表はジョブを飛ばしていない最新の 1 件にする。** 最新の 1 件は、変更の前と同じ「今のテストの大きさ」を表す。
  複数件の最大や中央値を採るには各 run のジョブを取る必要があり、飛ばした run の無いワークフローでも呼び出しが件数倍になる。
  ばらつき（525〜578 秒）は `ci_wait_timeout` = 3·c の係数が吸収し、係数と余白を重ねない
- **候補はワークフローごとに 5 件までにし、前の頁を取りに行かない。** 上限が無いと、いつもどれかのジョブを飛ばす
  ワークフローの run の数だけ呼び出しが増え、PR の宛先と課題の測定の時間を食う。前の頁（`page=2`）やワークフロー別の一覧を
  取りに行くと、呼び出しの数が履歴の形で変わり、動的なワークフロー（`dynamic/pages/...`）はファイル名で引けない。
  5 件は、実測（7 件のうち飛ばしたのは 2 件で連続しない）に対して Pull Request が 5 本続けて飛ばすのを許す幅である
- **前の壁時計を残す処理は書き出しの側（`merge.py`）に置く。** 前の値を残す規則と、前の値が解析の値か手の値かを指紋で
  見分ける仕組みは、すでに書き出しが持っている。測定の側で前の宣言を読むと、測定の結果のファイルに測っていない値が
  「測った」として載り、手で直した値の扱いが 2 か所に分かれる。測定は宣言を読まず、書き出しは GitHub を読まない
- **候補のジョブの取得が時間切れでも `ci` を不明にしない。** 代表が決まったワークフローの行と必須のチェックを捨てる理由が無い。
  そのため必須のチェックの取得を候補の取得より前へ移した。成功の run の一覧と必須のチェックの取得の時間切れは、変更の前と同じく `ci` を不明にする
- **解析器の版を 2 へ上げる。** `ANALYZER` は測る項目を変えたら上げる版で、上げると次の手順 0 の `check` が版 1 の宣言を古いと
  判定して再解析する。上げないと、入力のファイルが変わるまで壊れた値（pytest.yml の 17 秒）が残り、利用者が `check --force` を
  打つことになる。再解析が書き換えるのは解析が書いた値だけで、手で書いた値は残る
- **#1717 は #1664 の実装 1 本に寄せる。** 原因（`_measure_runs` の代表の選び方）も触るファイルも同じで、分けると 2 本の
  Pull Request が同じ関数を触り、片方の受け入れ条件がもう片方の実装でしか満たせない
- **m1649 で `ndf-record` が消えた件はコードを直さない。** `~/.local/state` の作成は 2026-10-03 00:48、m1649 のプランは 00:53、
  NDF の実行の記録の最初の行は 03:57 で、コンテナの作り直しで記録のファイルが無くなった後に解析していた。`ndf_record` は
  ファイルが無ければ `None` を返す正しい振る舞いで、記録があるのに消える経路は見つからなかった。前の宣言の `ndf-record` を
  残す案は採らない。`ci-steps`・`ci-junit` がこの変更で正しい値になり、`whole_seconds` はそちらへ進む

## 仕様

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | ワークフローの行の `wall_seconds` は代表の run から測る。代表の run は、ワークフローごとに新しい順の成功の run を最大 `CANDIDATE_RUNS`（5）件見て、ジョブを飛ばしていない最初の 1 件である。候補より古い run は探さない | テストが落ちる |
| I2 | ジョブを飛ばした run かどうかは、ジョブの `conclusion == "skipped"` だけで決める。ワークフローのファイル名・ジョブ名・run の event と branch は判定に使わない。`conclusion` の無いジョブは飛ばしていない | テストが落ちる |
| I3 | `test_duration` の `ci-steps` と `ci-junit` は代表の run からだけ測る。代表の run の無いワークフローからは測らない | テストが落ちる |
| I4 | 代表の run が決まらないワークフローの行は `wall_seconds` を持たない。注記 `飛ばしていない run が無い: <パス>（候補 <n> 件）` は、候補が 1 件以上あり、見た候補がすべてジョブを飛ばした run のときだけ 1 行出る。候補が 0 件（成功の run が一覧に無い）のワークフローには出さない | テストが落ちる |
| I5 | 候補のジョブを取る `gh` の呼び出しは、ワークフローごとに `CANDIDATE_RUNS` 回までで、どれも解析の締め切りの中で打ち切る。候補のジョブの取得が `GhUnavailable("時間切れ")` になったら `ci` を不明にせず、それまでの行と必須のチェックを残し、そのワークフローから後の候補を持つ行は `wall_seconds` を持たず、注記 `候補のジョブを取れない（時間切れ）: <パス>` を 1 行ずつ出す | テストが落ちる |
| I6 | 書き出しで `ci` の解析の値を新しい値で置き換えるとき、`wall_seconds` を持たない行は、前の値の同じ `path` の行の `wall_seconds` を引き継ぐ。前の値が手で書いた値（指紋が違う）なら引き継がず、食い違いの扱いになる | テストが落ちる |
| I7 | 飛ばした run の無いワークフローは、候補の 1 件目で代表が決まり、2 件目のジョブを取らない。値は変更の前と同じである | テストが落ちる |
| I8 | 解析が `gh` へ渡すのは `--method GET` の読む要求だけで、締め切りを過ぎた後は `gh` を起動しない | テストが落ちる |

### 測定の部品（`plugins/ndf/scripts/project_lib/measure_ci.py`）

| 関数・定数 | 契約 |
| --- | --- |
| `CANDIDATE_RUNS` | 5。ワークフローごとに代表を探す成功の run の上限 |
| `_has_skipped_job(jobs)` → `bool` | ジョブの一覧に `conclusion == "skipped"` のものがあるか。純粋な関数で、`gh` を呼ばない |
| `_representative(gh, candidates)` → `(run \| None, jobs, seen)` | 新しい順の候補を最大 `CANDIDATE_RUNS` 件、`actions/runs/<id>/jobs?per_page=100` で見て、ジョブを飛ばしていない最初の run とそのジョブ、見た件数を返す。無ければ `(None, [], seen)`。`GhUnavailable` は受け止めずに呼ぶ側へ返す |
| `_measure_workflow(path, static_jobs, run, jobs)` → `(entry, steps \| None)` | 代表の run とそのジョブから行（`path`・`jobs`・`wall_seconds`）とテストの step の所要を作る。ジョブは自分で取らない。run が無ければ静的なジョブ数だけの行。`jobs` は代表の run のジョブ数と静的な数の大きい方 |
| `_measure_runs(gh, jobs_static, head)` → `(ci, durations, notes)` | 成功の run の一覧 → 必須のチェック → パスの順にワークフローごとの代表、の順に取る。候補の取得の時間切れはワークフローごとに受け止め（I5）、それ以外の `GhUnavailable` は呼ぶ側へ返す。`durations` は `ci-junit`（パスの順で最初に JUnit を持つ代表の run）と `ci-steps`（代表の run の合計のうち最大）。`notes` は I4・I5 の注記 |
| `measure_ci(tree, repo, head, deadline)` | `_measure_runs` の `notes` を出力の `notes` へ足す。`_measure_runs` が `GhUnavailable` を返したら `ci` を不明にする（変更の前と同じ） |

### 書き出しの部品（`plugins/ndf/scripts/project_lib/merge.py`）

| 関数 | 契約 |
| --- | --- |
| `_carry_walls(cur, new)` → `(new', rows)` | 前の値と新しい値がどちらも不明でない `ci` のとき、新しい値の `wall_seconds` の無い行に、前の値の同じ `path` の行の `wall_seconds` を引き継ぐ。引き継いだ行ごとに `{"kind": "unknown", "key": "ci#<パス>", "reason": "新しい解析に壁時計が無い", "kept_previous": true}` を返す。前の値の行に `wall_seconds` が無ければ何もしない。純粋な関数 |
| `merge_item(key, ...)` | 前の値が解析の値（`analysis.written` の指紋が一致）で、新しい値が不明でないときだけ、キーが `ci` なら `_carry_walls` を通す。手の値・新しい値が不明の分岐は変更の前と同じ |

出力の行のキー `ci#<パス>` は、`#` の前で項目（P4）に分類される。

### 宣言と測定の結果の値

宣言の形は変えない。

| 対象 | 値 |
| --- | --- |
| `ci.workflows[].wall_seconds` | 代表の run の壁時計。代表の run が無ければ前の解析の値。前の値も無ければキーを書かない |
| `test_duration.measured[]` の `ci-steps` | 各ワークフローの代表の run のテストの step の合計のうち最大。`detail` の run の番号は代表の run のもの |
| `test_duration.measured[]` の `ci-junit` | パスの順で最初に JUnit を持つ代表の run の合計 |
| `measure` の出力の `notes[]` | I4・I5 の注記 |
| `analysis.analyzer` | 2（`plugins/ndf/scripts/project_lib/__init__.py` の `ANALYZER`）。`check` は版 1 の宣言を古いと判定する |

### 呼び手への影響

| 呼び手 | 振る舞い |
| --- | --- |
| `lib/test_strategy.py` の `ci_wall_seconds`・`whole_seconds` | 変えない。読む値の出所が代表の run になる |
| プランの `merge`（`supervise_lib/verify_steps.py`）と `test-run.py` の CI 待ち | 変えない。`ci_wait_timeout` が代表の run の壁時計から出る |
| 手順 0 の `project-decl.py check` | 版 1 の宣言を古いと判定し、自動の再解析が走る |

## テスト観点

- 同じワークフローに新しい飛ばした run と古い飛ばしていない run があると、`wall_seconds`・`ci-steps`（秒と `detail` の run 番号）・
  `ci-junit` が古い run のものになること。ファイル名とジョブ名の違うワークフロー、`conclusion` の無いジョブでも同じであること
  （`plugins/ndf/scripts/tests/test_project_decl_ci_jobs.py` の `test_representative_is_the_newest_run_without_skipped_jobs`）
- 候補がすべて飛ばした run のワークフローは `wall_seconds` を持たず、所要の候補に出ず、注記が 1 行出ること。
  成功の run の無いワークフローには注記が出ないこと（`test_workflow_with_only_skipped_runs_has_no_wall_and_a_note`）
- ワークフローごとのジョブの取得が 5 回で止まり、6 件目の run は探さないこと（`test_candidates_stop_at_the_limit`）
- 候補のジョブの取得が時間切れになると、先の行の壁時計と必須のチェックが残り、後の行は壁時計を持たず時間切れの注記が出て、
  それ以上 `gh` を呼ばないこと（`test_timeout_while_fetching_candidates_keeps_what_was_measured`）
- 締め切りを過ぎた `Gh` は `gh` を起動せずに時間切れを返すこと（`test_runs_list_after_the_deadline_is_a_timeout_and_starts_no_gh`）
- 飛ばした run が無ければ値と形が変更の前と同じで、2 件目の run のジョブを取らないこと（`test_measure_runs_keeps_the_current_shape`）
- 飛ばした run しか無い再解析で、前の解析の壁時計が宣言に残り、注記と「前の値を残した」の出力の行が出ること
  （`plugins/ndf/scripts/tests/test_project_decl.py` の `test_workflow_wall_is_kept_when_the_new_analysis_has_only_skipped_runs`）
- 引き継ぐのは同じパスの前の値だけで、前の値の無い行に値を作らず、手で書いた値は食い違いの扱いになること
  （`test_carry_walls_only_fills_the_same_path_of_an_analysis_value`）
- NDF の実行の記録に `whole_test.init` があれば `test_duration` に `ndf-record` が出ること（`test_ndf_record_is_listed_as_a_source`）
- 記録の版が `ANALYZER` より小さい宣言で `check` が古いと判定すること（`test_older_analyzer_makes_it_stale`）。`ANALYZER` の値そのものはテストで縛らない
- 解析がリポジトリへ書かず、`gh` へ読む要求だけを渡すこと（`test_measure_writes_nothing_to_the_repo_and_only_gets_from_gh`）
- このリポジトリで再解析した `pytest.yml` の `wall_seconds` と `ci-steps` の run 番号が、`gh run view <run> --json jobs` で
  `pytest (0/2)`・`pytest (1/2)` が success の run と一致すること。自動のテストでは縛らず、リリース後テストで確かめる

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 条件付きのジョブを持つ他のリポジトリ | `if:` で特定のブランチだけ動くジョブ（デプロイなど）を持つワークフローでは、そのジョブが動いた run だけが代表になり、壁時計がテストの所要より長く出る。いつも飛ばすジョブがあれば代表が決まらず、前の値か注記になる |
| 候補の上限 5 件の幅 | Pull Request が 5 本続けて pytest を飛ばすと代表が決まらず、前の値が残る。初めての解析で前の値が無いと、c は他のワークフローの最大になり、元の症状が出る。起きた回数は振り返りで注記の行を数えて確かめる |
| 記録の置き場 | NDF の実行の記録（`~/.local/state/ndf/metrics`）はコンテナの外に無く、作り直すと `ndf-record` が消える |

## 関連リンク

- [`project-analysis.md`](../../plugins/ndf/skills/development-workflow/references/project-analysis.md)（宣言の解析の手順と書き出しの規則）
- [`plugins/ndf/scripts/project_lib/measure_ci.py`](../../plugins/ndf/scripts/project_lib/measure_ci.py)
- [`plugins/ndf/scripts/project_lib/merge.py`](../../plugins/ndf/scripts/project_lib/merge.py)
- [`plugins/ndf/scripts/lib/test_strategy.py`](../../plugins/ndf/scripts/lib/test_strategy.py)（`ci_wall_seconds`・`whole_seconds`）
- 課題: #1664（取り込んだ課題 #1717）・Pull Request #1730
