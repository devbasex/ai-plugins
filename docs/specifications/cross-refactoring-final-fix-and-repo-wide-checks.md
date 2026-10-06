# cross-refactoring: 最終ゲート修正が静的解析を通らずに push されて push 前の検査で止まり、他の項目の違反で触っていない項目まで取り消されていた → 項目と最終ゲート修正の合否を変えたファイルで push より前に決め、違反は原因の項目へ帰す

## 目的

- **最終ゲート修正のコミットは、push より前に、変えたファイルで静的解析を通る。** 落ちた修正は取り消し、push せずに
  次の最終ゲート修正へ差し戻す。整形・行数の上限の違反が push 前の検査（`.githooks/pre-push`）で拒まれて実行が止まらない
- **他の項目の違反で落ちた項目は取り消さない。** 範囲テストに入ったリポジトリ全体を見るテストが別の項目の違反で落ちても、
  落ちた項目は待たせ、違反を入れた項目（原因の項目）だけを修正へ回す。取り消すときの理由には落ちた suite とファイルが入る

共通の原則は「**項目と最終ゲート修正の合否は、その変更が変えたファイルで判定し、push より前に決める**」である。

例: スプリント m1649 の実行 rf1673 を、今の形で通すと次のようになる。

| 時点 | 振る舞い |
| --- | --- |
| 実装 | 項目 I-014 が `refactor_lib/commands/gate.py` を 509 行にし（上限 500）、I-001 が `scripts/check-script-structure.py` を変える。両方が HEAD に積まれる |
| 検証（I-014） | 変えたファイル `gate.py` へ静的解析の suite `script-structure` を当てて落ちる。I-014 は `failing` になり、`failed_check` に suite とファイルが残る |
| 検証（I-001） | `test_targets` の `scripts/tests/test_check_script_structure.py` が落ちる。JUnit の本文に `gate.py` のパスが現れ、原因は I-014 に決まる。I-001 は `implemented` のまま `blocked_by` を持って待つ |
| 締め切り | I-014 を「静的解析 script-structure が …/gate.py で落ち、修正に使える時間の内に通らなかった」として取り消す。同じ `verify` の中で I-001 を走らせ直し、`verified` になる |
| 最終ゲート修正 | 修正が整形の違反を入れると、`merge-final-fix` は push を呼ばずにコミットを取り消し、`FINAL_FIX=lint_rejected` で終了コード 2 を返す。次の `final-gate` は同じ HEAD の判定を使い回し、依頼に違反の suite とファイルを載せる |

**手順は Skill が正である。** ここに書き写さない。

| 何を | 正本 |
| --- | --- |
| 検証と修正の繰り返し・範囲テストの組み立て・締め切りでの取り消し（同じ範囲テストを共有した項目の取り消し） | [`docs/04-verify-and-report.md`](../../plugins/ndf/skills/cross-refactoring/docs/04-verify-and-report.md) の「検証と修正」 |
| 原因の項目の決め方（手がかり `path` / `isolate` / `undetermined`） | 同上と [cross-refactoring-culprit-and-stop-revert.md](cross-refactoring-culprit-and-stop-revert.md) の「原因の項目の判定」 |
| 最終ゲートの判定・最終ゲート修正の打ち切り・最終ゲート修正の取り込みの手順 | [`docs/04-verify-and-report.md`](../../plugins/ndf/skills/cross-refactoring/docs/04-verify-and-report.md) の「最終ゲート」「最終ゲート修正は項目の修正と別物である」 |
| 最終ゲートへ入る時点の公開と、それより前に push しないこと | 同上の「公開は最終ゲートへ入る時点から」 |
| 修正の CLI が読む落ちた検査と差し戻し | [`prompts/fix.md`](../../plugins/ndf/skills/cross-refactoring/prompts/fix.md)・[`prompts/final-fix.md`](../../plugins/ndf/skills/cross-refactoring/prompts/final-fix.md) |

この文書が扱うのは、Skill に書かない決定の理由、常に成り立つ条件、`prepush_lint` と `scope_verdict` の部品の契約、
`merge-final-fix` と `final-gate` に加わった振る舞い、状態ファイルに足した欄である。

## 用語

定義は [用語集](../glossary.md) が正である（`.ndf/glossary.json` の `document`）。この文書で使う語は
「範囲テスト」「最終ゲート修正」「原因の項目」「巻き込まれた項目」「公開前の静的解析」「原因の手がかり」である。

## 対象範囲

| 扱う | 扱わない |
| --- | --- |
| 最終ゲート修正の取り込み（`merge-final-fix`）で、修正のコミットが変えたファイルへ静的解析の範囲テストを当て、push より前に合否を決めること | push が拒まれたときの記録 |
| 最終ゲートへ入る時点の push が、静的解析の合否が決まっていないコミットを公開しないこと | cross-review の修正コミットの同じ形 |
| 項目の範囲テストが他の項目の違反だけで落ちたとき、その項目を取り消さないこと | 宣言に静的解析の suite を足すこと・範囲テストの組み方（`lint_runs` / `scope_runs`）を変えること |
| 違反を入れた項目を原因として記録し、取り消しの理由に suite とファイルを残すこと | 個別の全体を見るテスト（`scripts/tests/test_check_script_structure.py` など）を項目のファイルへ絞ること |
| | `.githooks/pre-push`・`scripts/check-lint.sh` の変更、宣言をメインディレクトリから読む順序 |

## 背景

最終ゲートは、入る時点で push（`publish.enter_final_gate`）してから静的解析の全体テスト（`gate_lint.lint_gate`）を走らせる。
最終ゲート修正の取り込みは範囲テストを走らせず、取り込みの終わりに push していた。最終ゲート修正が整形の違反を入れると、
取り込みの push か次の `final-gate` の push が push 前の検査で拒まれ、refactor が止まった（2026-09-30 のスプリント m1523・PR 1564）。
宣言に整形の suite を足しても、この経路は塞がらない。

範囲テストには `test_targets` で選んだテストが入る。そこにリポジトリ全体を見るテスト
（`test_repository_matches_its_allow_list`）が入ると、別の項目が変えたファイルの違反で落ちる。実行 rf1673（PR #1673）では
15 件中 9 件が取り消され、記録の残る 5 件はどれも I-014 が 509 行にした `gate.py` だけで落ちていた（5 件とも `gate.py` を触っていない）。
I-014 自身の検証で静的解析が組まれなかったのは、`init` が宣言をメインディレクトリから読み、そこに静的解析の suite が無かったためである（読む順序は扱わない）。

## 決定と理由

- **#1688 は範囲テストの対象を選ぶ側でなく、落ちた失敗を原因の項目へ帰す側で直す。** 全体を見るテストを `test_targets` から外すには、
  走らせる前に名前かパスで見分けるしかない。全体を見るテストは項目をまたいだ本当の壊れも拾うため、外すと最終ゲートまで見えなくなる。
  落ちた後に全体テストと同じ `culprit.determine` を当てれば、テストの名前を読まずに、本文に現れた変えたファイルと外した走らせ直しで原因を決められる
- **原因が他の項目だけに決まった項目は `implemented` のまま待たせる。** `verified` にすると、原因の項目が直らずに取り消されたとき、
  失敗が自分のものに変わっていても検証されないまま公開される。`failing` にすると直せない失敗のために修正担当が起動され、締め切りで
  取り消される。原因が決まらない・自分を含む・JUnit を読めないときは自分の失敗として扱う。
  判定を誤って本当に壊した項目を逃がすより、取り消しへ倒す方が害が小さい
- **原因の判定は、落ちた範囲テストがテストの種別のときだけ行う。** 静的解析の範囲テストは項目が変えたファイルだけから組むため、
  落ちたなら違反は自分のファイルにある。判定の手間（JUnit の読み取り・走らせ直し）を、他の項目の違反が入りうる側にだけ掛ける
- **宣言に静的解析の suite が無いときは、公開前の静的解析も原因の判定も行わない。** 要求がそのプロジェクトでの振る舞いの違いを無しと
  決めたためである。判定元は状態ファイルの `strategy.suites` で、宣言は読み直さない
- **最終ゲート修正が静的解析で落ちたら、同じ試行で直させずに取り消して次の修正へ差し戻す。** 同じ試行で直させると、
  修正の CLI の起動と締め切りの管理を `merge-final-fix` に重ねて持つことになる。差し戻せば、修正の回数と締め切りは既存の最終ゲートの
  判定（`_final_fix_stop`）がそのまま決める。違反の内容は次の依頼に載せ、同じ違反を繰り返させない
- **差し戻しの後の最終ゲートは、同じ HEAD の失敗の判定を使い回す。** 取り消しで HEAD は前回の判定の地点へ戻るため、走らせ直しても
  判定は変わらない。走らせ直すと、最終ゲート修正に取ってある時間（`plan.reserve.final_fix`）を判定だけで使い切る
- **最終ゲートへ入る時点の push には検査を足さず、公開しうるコミットを作る経路で守る。** HEAD へ未検証のコミットを積む経路は
  改善項目・最終ゲート修正・取り消しのコミット・生成物の同期の 4 つで、前の 2 つを検査すれば公開されるのは検査を通ったものになる。
  push の直前に検査しても、落ちたときに戻す先が無い
- **公開前の静的解析と項目の静的解析は同じ関数（`targets.lint_runs_for`）で組む。** `round-only` の扱いも含めて、項目と
  最終ゲート修正で基準が食い違わない
- **2 つの課題を 1 本で直す。** 落ちた検査の記録の形（suite・コマンド・ファイル）、修正の依頼への渡し方（`launch-cli.sh` と
  `prompts/`）、行数の上限の余白が無いため処理を移す `gate.py` と `converge.py` を両方が触る。分けると片方が決めた形へもう片方が合わせ直す

## 仕様

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | 最終ゲート修正のコミットは、公開前の静的解析を通ったものだけが `final_gate.fix_commits` に入り、push される | 通らなければ取り消し、push せずに終了コード 2 で返す |
| I2 | 公開前の静的解析の対象は、修正のコミットが変えたファイルのうち suite の `paths` に当たるものだけである | 修正が触っていないファイルの既存の違反では落ちない（組み方で守る） |
| I3 | 最終ゲートへ入る時点の push が公開するのは、検証を通った項目のコミット・I1 を通った最終ゲート修正・取り消しのコミット・生成物の同期のコミットだけである | 破る経路を足さない |
| I4 | 状態ファイルの `strategy.suites` に `kind: lint` の suite があり、範囲テストのテストの種別が落ち、原因の項目が決まり（`undetermined` でない）、その中に自分が無い項目は `failing` にならず、取り消されない | 原因が決まらない・自分を含む・落ちたテストを読めないときは自分の失敗（`failing`） |
| I5 | 巻き込まれた項目（`implemented` で `blocked_by` を持つ）が検証の 1 回を終えて残るのは、`blocked_by` の項目のどれかが `failing` のときだけである | 原因の項目が `failing` でなければ、同じ `verify` の中で走らせ直す |
| I6 | 範囲テストで落ちて締め切りで取り消した項目の理由には、落ちた検査（suite か落ちたテスト）とファイルが入る | `failed_check` が無い・読めないときは理由を「範囲テストが修正に使える時間の内に通らなかった」にする |
| I7 | `strategy.suites` に `kind: lint` の suite が無いとき、公開前の静的解析は何も走らせず、原因の判定（I4）と走らせ直し（I5）も行わない。`merge-final-fix` と項目の検証の合否・push・テストの実行回数は変更前と同じである | — |
| I8 | 公開前の静的解析と項目の範囲テストの起動の失敗は、最終ゲート修正を取り込みも取り消しもせず、項目の状態を変えずに `launch.stop`（終了コード 4）で止まる | — |
| I9 | 全体を見るテストの見分けにも原因の判定にも、テストの名前・パスを使わない | 別の名前の全体を見るテストでも同じ結果になる |

### 公開前の静的解析（`refactor_lib/prepush_lint.py`）

| 関数 | 契約 |
| --- | --- |
| `check_files(state, files, label)` → `PrepushResult` | `targets.lint_runs_for(state, files)` で組んだ suite を順に走らせる。組めなければ（suite が無い・`round-only`）何も走らせず通す。上限は `timeline.state_test_timeout(state)` を suite 群全体で 1 つ使い、残りが 1 秒を切るか超えたら打ち切りとして落とす。ログは `<tmp_dir>/<label>-<suite>.log` |
| `summary(rejections)` | 差し戻しの 1 行（「静的解析 <suite> が <ファイル> で落ちた」を ` / ` でつなぐ） |
| `fix_request(gate)` | 次の最終ゲート修正の依頼に載せる Markdown。載せるものが無ければ空 |

**判定は値を返すだけで止めない。** 取り消すのも `launch.stop` で止めるのも呼ぶ側（`commands/final_fix.py`）である。

| 値 | 欄 |
| --- | --- |
| `PrepushResult` | `passed`・`rejections: list[LintRejection]`・`seconds`・`launch_failure: Optional[LaunchFailure]` |
| `LintRejection` | `suite`・`command`・`files`（suite に渡したファイルのうちログに現れたもの。1 つも現れなければ渡したファイル）・`reason`（打ち切りなら「<秒> 秒の上限で打ち切った」）・`log`。`as_dict` で状態ファイルへ書く |
| `LaunchFailure` | `command`・`outcome`・`log`（`launch.stop` へ渡す材料。工程名は `prepush`） |

`fix_request` が並べるもの:

1. 直前の最終ゲートで変更起因だった静的解析（`final_gate.lint` の `verdict: caused`）の suite・コマンド・理由
2. 直前の最終ゲートで変更起因だったテスト（`final_gate.triage.caused` の先頭 10 件と走らせ直しのコマンド）
3. `lint_rejections` の最後の 1 件が直前のラウンド（`round >= fix_rounds - 1`）のものなら、その suite・ファイル・コマンド・理由・ログ

### `refactor.py merge-final-fix <id>`

本体は `refactor_lib/commands/final_fix.py`（`gate.py` から移した）。順序は「範囲の確定 → 手順の検証（未申告・トレーラー・範囲） →
公開前の静的解析 → 取り込みか取り消し → push」で、手順の検証で取り消したときは静的解析を走らせない。

| 結末 | 終了コード | 出力 | push |
| --- | --- | --- | --- |
| 公開前の静的解析を通って取り込んだ | 0 | — | する |
| 手順の検証で取り消した | 0 | — | する（取り消した HEAD を公開する） |
| 公開前の静的解析で落ちて取り消した | 2 | `FINAL_FIX=lint_rejected` | **しない**（HEAD は公開済みの `fix_base_sha` へ戻る） |
| 同じ修正ラウンドを差し戻し済み（打ち直し） | 2 | `FINAL_FIX=lint_rejected` | しない（結果ファイルを読み直さない） |
| 範囲を確定できない・担当が結果を残さなかった | 2 | — | 取り消したときだけ |
| 公開前の静的解析を起動できなかった | 4 | — | しない |

テストの合否は見ない（次の `final-gate` が採った側で 1 度だけ見る）。`drive.py` は `merge-final-fix` を `ok=(0, 2)` で呼び、
どちらでも `final-gate` へ戻る。止まった駆動を打ち直したときに途中の試行を取り込む呼び出しも同じである。

### `refactor.py final-gate <id>` の判定の使い回し

引数と終了コードは変わらない。落ちたら `final_gate.last_failing` に HEAD・記録の 1 行・取り込んだ修正の数を残し、通ったら消す。
次のすべてが成り立つときは、全体テストも静的解析も走らせずに前回の判定を使い、`checks` に `mode: reused` の行を足して打ち切りの判定へ進む。

- `final_gate.lint_rejections` がある（差し戻しの後である）
- HEAD が `last_failing.head` と同じ
- 取り込んだ修正の数（`fix_commits` の件数）が `last_failing.fix_commits` と同じ

落ちて修正へ回すとき（`_gate_failing`）は、ラウンドを進めてから `prepush_lint.fix_request` を `final_gate.fix_request` に書く（空なら消す）。
`launch-cli.sh` は `final-fix` の手順でこれを `RF_FINAL_FIX_REQUEST` に渡す（無ければ「（無し）」）。

### 項目の範囲テストの判定（`refactor_lib/scope_verdict.py`）

`converge.py` の `verify` が呼ぶ。範囲テストを走らせる処理は `converge.py` の `_run_limited` から移した。

| 関数 | 契約 |
| --- | --- |
| `judge_items(path, state, items)` | 項目ごとに `targets.verify_runs`（テストの種別と静的解析）を走らせ、`verified` / `failing` / 待ち（`implemented` と `blocked_by`）に決める。同じコマンドの並びは 1 回だけ走らせて結果を共有する。終わったら `waiting` の項目を走らせ直し、項目の数 + 1 回の内で終わる |
| `waiting(state)` | `implemented` で `blocked_by` を持ち、`blocked_by.items` のどれも `failing` でない項目（I5） |
| `judges_culprits(state)` | `strategy.suites` に `kind: lint` の suite があるか（I4・I7） |
| `reason(item)` | 締め切りで取り消すときの理由（I6）。下の表 |
| `verify_log(state, item_id)` | `<tmp_dir>/verify-<項目>.log` |

1 つの項目の決め方:

1. 範囲テストが通れば `verified`。`failed_check` と `blocked_by` を消す
2. 落ちたら `failed_check` を書く。落ちた suite がテストの種別なら、suite の `junit` から落ちた ID と本文を読む（読めなければ `note` に理由）
3. 落ちたのがテストの種別で、落ちた ID を読め、`judges_culprits` が真なら `culprit.determine(state, 落ちた ID, 本文, culprit.fix_deadline(state))` で原因を決める
4. 原因が決まり自分を含まなければ `implemented` のまま `blocked_by` を書く。そうでなければ `failing`

**原因の項目への印は、検証の 1 回を終えてから付ける。** 後の項目の通過で上書きしないためである。印を付ける項目が
`verified` か `implemented` なら `failed_check`（`by_item` つき）を書き換えて `blocked_by` を消し、`failing` にする。
検証を通った項目が `failing` へ戻るのはこの経路だけである。

`converge.py` の `_settle_scope` は、最初に直さなかった項目を取り消し（`_drop_unfixed`）、判定を待つ項目
（`scope_verdict.pending`）を範囲テストで判定する（`judge_items`）。続けて、締め切り（`_give_up`）で `failing` の
項目を取り消したら `pending` を判定し直すことを、取り消しが無くなるまで繰り返す（項目の数 + 1 回の内）。
`pending` には `waiting` の項目に加えて、取り消しで HEAD が変わったため `verified` から `implemented` へ戻した
確かめ直しの項目も入る（規則は [cross-refactoring-failed-item-rules.md](cross-refactoring-failed-item-rules.md) の
R3・決定 2・決定 3）。巻き込まれた項目は修正へ回さない（`fix` の手順は `failing` の項目だけを渡す）。

`reason(item)` が作る理由（末尾はどれも「修正に使える時間の内に通らなかった」）:

| `failed_check` | 理由 |
| --- | --- |
| `by_item` がある | 範囲テスト <suite> で <by_item> のテスト <先頭 3 件> を落とし（<ファイル>）、… |
| `kind: lint` | 静的解析 <suite> が <ファイル> で落ち、… |
| `kind: test` | 範囲テスト <suite> の <先頭 3 件> が <ファイル> で落ち、… |
| 無い・suite が空 | 範囲テストが修正に使える時間の内に通らなかった |

### 状態ファイルに足した欄

既存の欄は消さず、意味も変えない。

| 置き場 | 欄 | 形 | 書く者 | 読む者 |
| --- | --- | --- | --- | --- |
| `items[]` | `failed_check` | 下の表 | `scope_verdict` | `scope_verdict.reason`（取り消しの理由）・`launch-cli.sh`（`fix` の `ITEMS_JSON[].failed_check`） |
| `items[]` | `blocked_by` | `{items: [ID], tests: [ID], basis: "path" \| "isolate"}` | `scope_verdict` | `scope_verdict.waiting` |
| `final_gate` | `lint_rejections` | `[{round, at, commits: [sha], rejections: [LintRejection]}]` | `final_fix._reject_by_lint` | `gate._reusable_failure`・`prepush_lint.fix_request`・`final_fix._already_rejected` |
| `final_gate` | `fix_request` | 文字列（Markdown） | `gate._gate_failing` | `launch-cli.sh`（`RF_FINAL_FIX_REQUEST`） |
| `final_gate` | `last_failing` | `{head, detail, fix_commits: 件数}` | `gate._remember_failure` | `gate._reusable_failure` |

`failed_check` は検証のたびに書き直し、通ったら消す。`blocked_by` も自分の失敗か通過に決まったら消す。

| `failed_check` の欄 | 中身 |
| --- | --- |
| `kind` | `lint` / `test` |
| `suite` | 落ちた範囲テストの suite の名前 |
| `command` | 落ちたコマンド |
| `files` | ログと JUnit の本文に現れた、その項目が変えたファイル（`failure_paths.mentioned`）。無ければ、静的解析は suite が覆う変えたファイル、テストは `test_targets` のファイル。原因の印では `Verdict.evidence` のパス |
| `tests` | テストの種別のとき、JUnit から読んだ落ちた ID |
| `by_item` | 他の項目の範囲テストで原因に決まったときだけ。その項目の ID |
| `note` | 打ち切った・落ちたテストを読めなかったときだけ。その理由 |

## テスト観点

- 静的解析の suite を持つ一時リポジトリで、最終ゲート修正が行数の上限を超えるファイルを作ると、`merge-final-fix` が終了コード 2 と
  `FINAL_FIX=lint_rejected` で終わり、push が 1 回も呼ばれず、HEAD が起点へ戻り、`lint_rejections` に suite とファイルが残ること。
  戦略 `local-full`・`local-scoped-ci-whole`・`--ci-check` と単独の起動のどれでも同じであること
- 静的解析を通る最終ゲート修正は `fix_commits` に入って push され、`lint_rejections` を作らないこと（同じ 4 通り）
- 修正が触っていないファイルに既存の違反があっても、修正は取り込まれること
- 宣言に静的解析の suite が無ければ、違反を入れた修正も取り込まれて push されること
- 差し戻しの後の `final-gate` は全体テストも静的解析も走らせず、何も公開せず、`checks` の最後が `mode: reused` で、
  `fix_request` に suite とファイルが入ること。HEAD が進んでいれば走らせること
  （以上 `plugins/ndf/skills/cross-refactoring/tests/test_prepush_lint_git.py`）
- 項目 X が上限ちょうどのファイルへ行を足し、項目 Y がリポジトリ全体の行数を見るテストを `test_targets` に持つと、X は静的解析で
  `failing`（`failed_check` に suite とファイル）、Y は `implemented` で `blocked_by.items` が X、手がかりが `path` になり、修正へ回るのは X だけであること
- 締め切りを過ぎていれば、X だけが suite とファイルを含む理由で取り消され、Y は同じ `verify` で走らせ直されて `verified` になること
- 全体を見るテストを別の名前とパスのテストに替えても、上の 2 つが同じ結果になること
- Y が自分の変えたファイルで全体を見るテストを落とすと、Y は `blocked_by` を持たずに `failing` になること
- 宣言に静的解析の suite が無ければ、Y は `blocked_by` を持たずに `failing` になり、範囲テストの実行回数が 1 回であること
  （以上 `plugins/ndf/skills/cross-refactoring/tests/test_scope_verdict_git.py`）
- 既存の cross-refactoring のテスト（`plugins/ndf/skills/cross-refactoring/tests/`）がすべて通ること

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 取り消しと同期のコミット | `_give_up`・`_revert_shared` の取り消しのコミットと、`publish.push_head` が push の直前に作る生成物の同期のコミットは静的解析を通さずに公開される |
| 手がかり `path` の誤り | 落ちたテストの本文に原因でない項目のファイルのパスが現れると、その項目を原因に決める。無実の項目が `failing` になりうる（`failed_check.by_item` で確かめる） |
| 宣言の読み込み | メインディレクトリの宣言に静的解析の suite が無い実行では原因の判定も行われず、他の項目の違反で項目が取り消されうる |
| 原因の判定の時間 | 範囲テストの失敗ごとに手がかり `isolate` の走らせ直しが増える。上限は修正の締め切りで、実際の秒数は `culprit` の `seconds` で確かめる |

## 関連リンク

- [`cross-refactoring` の SKILL.md](../../plugins/ndf/skills/cross-refactoring/SKILL.md)
- [`docs/04-verify-and-report.md`](../../plugins/ndf/skills/cross-refactoring/docs/04-verify-and-report.md)
- [cross-refactoring-verify-and-final-gate.md](cross-refactoring-verify-and-final-gate.md)（検証と最終ゲート）
- [cross-refactoring-culprit-and-stop-revert.md](cross-refactoring-culprit-and-stop-revert.md)（原因の項目の判定）
- [cross-refactoring-lint-suite-and-ci-coverage.md](cross-refactoring-lint-suite-and-ci-coverage.md)（静的解析の suite と範囲テスト）
- 課題: #1693（取り込んだ課題 #1688。関連 #464・#1648・#1507・#1709）・Pull Request #1713
