# #3955: cross-review の相手役が欠けても、空いた席を claude の読むだけの起動で埋めて 2 者でレビューする

## 関連リンク

- 要求仕様: [issue-3955-requirements.md](./issue-3955-requirements.md)（正は #3955 https://github.com/devbasex/ai-plugins/issues/3955 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-3955-design.md](./issue-3955-design.md)、詳細設計書: [issue-3955-design-detail.md](./issue-3955-design-detail.md)
- 同じ移動を予定している計画: #4076（cross-review の指示に MVV を入れる課題 https://github.com/devbasex/ai-plugins/issues/4076 ）の実装計画 [issue-4076-plan.md](./issue-4076-plan.md) の U2 が、この計画の単位 1 と同じ移動（`drive.py` から `changes.py`・`settings.py` へ）を行う。先に main へ入ったほうの形に、後から入るほうが取り込みで合わせる
- モード: `standard`（要求仕様の「影響」の表で公開インタフェースが「変わる（互換あり）」で、触るファイルが 20 あるため、実装計画を省けない）

## 修正対象

詳細設計書の「地図」と、構成要素ごとの関数の仕様から引き写した。地図に無いファイルは、単位 1 の `changes.py`・`settings.py` の 2 つと、`test_crossreview_fixer_sessions.py`・`tests/route/test_flows.py` の 2 つで、足す理由は Task 1 にある。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/src/ndf/domains/crossreview/changes.py`（新設。地図に無い） | `drive.py` から移す `_Changed`・`_regular_blobs`・`_local_text`・`_committed_texts`・`_changed`・`_sections`・`changed_sections`（中身は変えない） |
| `plugins/ndf/src/ndf/domains/crossreview/settings.py`（新設。地図に無い） | `drive.py` から移す `ReviewSettings`・`_settings_path`・`_inside`・`read_settings`・`_blob_id`・`_proposed_differs`・`outside_checkout`（中身は変えない） |
| `plugins/ndf/contracts/domains/cli-floors.json` | claude の行に `"flag_floors": {"--restricted": "2.1.316"}`、`columns.flag_floors.summary`。`min_version` は変えない |
| `plugins/ndf/src/ndf/adapters/cli_floor.py` | `require(name, argv0, env=None, cwd=None, args=())`。`flag_floors` の引数があればその下限と比べ、版を確かめられなければ `Unverified`（新設。`TooOld` の派生）を投げる |
| `plugins/ndf/src/ndf/adapters/agents.py` | `_guarded_run` が `cli_floor.require` に `args=spec.argv[1:]` を渡す |
| `plugins/ndf/src/ndf/adapters/claude.py` | `ClaudeCall` に読める場所、`permission_args` が `--restricted`・`--add-dir`・`--permission-mode dontAsk` を組む、定数 `DONT_ASK`・`RESTRICTED` |
| `plugins/ndf/src/ndf/core/converge/participants.py` | `Seats`・`review_seats`・`runtime_of`・`free_seat`・`fill_targets`・`refusals`・`read_only_launch`・`answer_text`・`_able`・`READ_TOOLS`・`SEAT_COUNT`（新設）、`unready`・`assign`（中身だけ） |
| `plugins/ndf/src/ndf/domains/crossrefactor/launch.py` | `_read_only`・`_collect_stdout` が participants の関数を呼ぶ。`_answer_text`・`READ_TOOLS` を消す |
| `plugins/ndf/src/ndf/domains/crossreview/scope_decide.py` | `Filled`・`FILLED`・`filled_of`・`stand_ins`・`assignable`（新設）、`Basis`・`read_by_all_head`（変更） |
| `plugins/ndf/src/ndf/domains/crossreview/drive.py` | `RunConfig`・`_Run` のフィールド、`_Unread`・`_Reviewed`・`_input_path`・`_drop_inputs`・`_seat_launch`・`_fill`・`_fill_refused`・`_filled_warning`・`_reused_warnings`（新設）、`_launch_reviewers`・`_review`・`_reviewer_findings`・`_unread_hint`・`_round`・`_reusable`・`drive`（変更）、`_write_diff`・`_remarks_file`・`_prepare_round`（書き先か読む値だけ） |
| `plugins/ndf/src/ndf/domains/crossreview/report_decide.py` | `_seats_cell`（新設）、`_round_rows`（「相手役」の列） |
| `plugins/ndf/src/ndf/domains/crossreview/cli.py` | `Resolved`・`_reviewers` が `review_seats` で席を決める、`_run_config`・`cmd_scope`・`_state_parsers` |
| `plugins/ndf/skills/cross-review/SKILL.md` | 相手役の説明・終わり方の表・`review-scope` が起動の前に分かる席だけで範囲を決めること |
| `plugins/ndf/skills/cross-review/docs/01-state-and-review.md` | ラウンドの記録の `filled`・`reviewers` の意味、資料のディレクトリ `review-input-<接尾辞>/` |
| `docs/specifications/cross-review-participants-and-seats.md` | 席の決め方・埋め方・`claude-2` を今の形に書き直す |

テストは各タスクの対象ファイルに挙げる。

## タスク分解

### Task 1: `drive.py` の範囲の読み取りと設定の読み取りを別のモジュールへ移す（準備のリファクタリング）

- **対象ファイル:** `plugins/ndf/src/ndf/domains/crossreview/drive.py`、`plugins/ndf/src/ndf/domains/crossreview/changes.py`（新設）、`plugins/ndf/src/ndf/domains/crossreview/settings.py`（新設）、`plugins/ndf/src/ndf/domains/crossreview/cli.py`、`plugins/ndf/tests/domains/crossreview/test_crossreview_scope.py`、`plugins/ndf/tests/domains/crossreview/test_crossreview_run.py`、`plugins/ndf/tests/domains/crossreview/test_crossreview_fixer_sessions.py`、`plugins/ndf/tests/route/test_flows.py`
- **変更内容:** 「修正対象」の表の `changes.py`・`settings.py` の行のとおり、中身を変えずに移す。外から読む名前（`changed_sections`・`read_settings`・`ReviewSettings`・`outside_checkout`）は、呼び手とテストを新しいモジュールへ向ける。地図に無いファイルを足す理由: `drive.py` は今 1,000 行で、`ndf check` の構造の規則（`plugins/ndf/src/ndf/checks/rules.json` の `structure.max_lines` 1,000 行）の上限にある。詳細設計書は `drive.py` に新設の関数 7 つとクラス 2 つを置くので、移さずに足すと規則を超える。移す範囲は #4076 の計画の U2 と同じにし、2 つの課題の取り込みで形が食い違わないようにする
- **満たす受け入れ条件:** 無し（振る舞いを変えない。既存のテストが変更なしで通ることで確かめる）
- **進め方:** テスト駆動を適用しない（振る舞いを変えない移動）。移す前と後で `test_crossreview_*` と `test_flows.py` の結果が同じで、テストの差分が import の向きだけであることを確かめる

### Task 2: 今の振る舞いを縛る退行テスト

- **対象ファイル:** `plugins/ndf/tests/core/converge/test_converge.py`、`plugins/ndf/tests/domains/crossrefactor/test_launch_and_phase.py`、`plugins/ndf/tests/domains/crossreview/test_crossreview_run.py`
- **変更内容:** 変えない振る舞いを先にテストで縛る。cross-refactoring の相手役の選び方（`assign` の既定・名指し・`allowed`）と、読むだけの claude の起動の引数と本文の取り出し（受け入れ条件 13）。codex と kiro が両方読めるとき claude を相手役として起動せず、ラウンドの記録に `filled` が無いこと（9）。両方が起動し直しても読めないとき `precondition` で止まり claude を起動しないこと（10）。既にあるテストで縛れているものは足さない
- **満たす受け入れ条件:** 9・10・13（今の振る舞いを壊さない）
- **進め方:** 今のコードで通るテストを書く（機能追加の最初のタスク）

### Task 3: claude の下限を起動の引数ごとに当てる

- **対象ファイル:** `plugins/ndf/contracts/domains/cli-floors.json`、`plugins/ndf/src/ndf/adapters/cli_floor.py`、`plugins/ndf/src/ndf/adapters/agents.py`、`plugins/ndf/tests/adapters/test_cli_floor.py`
- **変更内容:** 詳細設計書の「下限の表と手順書」の節のうち、`cli-floors.json`・`cli_floor.require`・`agents._guarded_run`。`--restricted` を含む起動は 2.1.316 未満か版を確かめられないときに断り（`Unverified`）、`_guarded_run` は `require` の例外を `refused` にする。`--restricted` を含まない起動は今のとおり `min_version` と比べ、版を読めなければ通す
- **満たす受け入れ条件:** 8（読み取りの境界を頼れる版でだけ席の claude を起動する。テスト設計の「claude の下限（決定 2）」の (a)〜(e)）
- **進め方:** 失敗するテスト（2.1.315 で `--restricted` 付きの起動が断られない）→ 通す最小実装 → 整理

### Task 4: claude の読むだけの起動を相手役を決める部品へ移し、読める場所を渡す

- **対象ファイル:** `plugins/ndf/src/ndf/adapters/claude.py`、`plugins/ndf/src/ndf/core/converge/participants.py`（`read_only_launch`・`answer_text`・`READ_TOOLS`）、`plugins/ndf/src/ndf/domains/crossrefactor/launch.py`、`plugins/ndf/tests/adapters/test_claude_args.py`、`plugins/ndf/tests/domains/crossrefactor/test_launch_and_phase.py`
- **変更内容:** 詳細設計書の「claude の読むだけの起動」の節。`ClaudeCall` に読める場所の一覧を足し、空でないときだけ `permission_args` が `--restricted`・`--add-dir <場所>`・`--permission-mode dontAsk` を組み、`acceptEdits` を使わない。`--tools`・`--allowed-tools` は今のとおり `Read,Grep,Glob` の Tool の名前だけ。cross-refactoring の `_read_only`・`_collect_stdout` は移した関数を呼び、読める場所を渡さないので起動の引数は今と同じ
- **満たす受け入れ条件:** 7・8・13
- **進め方:** 失敗するテスト（読める場所を渡した起動に `--restricted` と `dontAsk` が無い）→ 通す最小実装 → 整理

### Task 5: 起動の前に席を決め、claude の席を起動する

- **対象ファイル:** `plugins/ndf/src/ndf/core/converge/participants.py`（`Seats`・`review_seats`・`runtime_of`・`_able`・`SEAT_COUNT`・`assign` の中身）、`plugins/ndf/src/ndf/domains/crossreview/scope_decide.py`（`Filled`・`FILLED`・`filled_of`）、`plugins/ndf/src/ndf/domains/crossreview/cli.py`（`Resolved`・`_reviewers`・`_run_config`・`cmd_scope`・`_state_parsers`）、`plugins/ndf/src/ndf/domains/crossreview/drive.py`（`RunConfig.may_fill`・`prefilled`、`_Run.seats`・`filled`・`input_dir`、`_input_path`・`_drop_inputs`・`_seat_launch`・`_filled_warning`、`_write_diff`・`_remarks_file`・`_launch_reviewers`・`_reviewer_findings`・`_unread_hint`）、`plugins/ndf/tests/core/converge/test_converge.py`、`plugins/ndf/tests/domains/crossreview/test_crossreview_run.py`、`plugins/ndf/tests/domains/crossreview/test_crossreview_scope.py`
- **変更内容:** 詳細設計書の「席を決める」「埋めた席の記録」のうち `Filled`・`filled_of`、「駆動が今の席を持ち、空いた席を埋める」のうち席の起動と資料のディレクトリ、「CLI が席を決める」の節。`allowed` の中で相手役になれるランタイムが 2 つに満たなければ `claude`（と `claude-2`）で席を埋め、`filled` の `reason` に `allowed の相手役 <数>（<名前>）` を書き、注意に「起動の前」に埋めたことを載せる。名指しが 1 者のときは `may_fill` を偽にする（出現回数ではなく異なる名前の数で数える）。席の名前とランタイムの名前を `runtime_of` で分け、出力のファイルの名前は `round<n>-claude-2` にする。資料は実行ごとに新しく作る `review-input-<接尾辞>/` に書き、終わりに消す。`run` と `review-scope` の両方が `review_seats` を使う
- **満たす受け入れ条件:** 5・6・7・8・12・13
- **進め方:** 失敗するテスト（`allowed` が claude だけで `invalid` で止まる）→ 通す最小実装 → 整理

### Task 6: 始める前の確かめで断られた相手役の席を埋める

- **対象ファイル:** `plugins/ndf/src/ndf/core/converge/participants.py`（`refusals`・`unready` の中身）、`plugins/ndf/src/ndf/domains/crossreview/drive.py`（`_fill_refused`・`drive`）、`plugins/ndf/tests/core/converge/test_converge.py`、`plugins/ndf/tests/domains/crossreview/test_crossreview_run.py`
- **変更内容:** 詳細設計書の「断られた担当を確かめる」の節と `drive._fill_refused`。`refusals` で断られた claude 以外の席を claude で埋め（`reason` は `unready: ` で始める）、修正担当の claude が断られたときと、名指し 1 者が断られたときは `precondition` で止める。同じランタイムの確かめは 1 回にし、再利用の判定より後に確かめる
- **満たす受け入れ条件:** 15
- **進め方:** 失敗するテスト（kiro を断ると `precondition` で止まる）→ 通す最小実装 → 整理

### Task 7: ラウンドの中で、起動し直しても読めない相手役の席を埋める

- **対象ファイル:** `plugins/ndf/src/ndf/core/converge/participants.py`（`free_seat`・`fill_targets`）、`plugins/ndf/src/ndf/domains/crossreview/drive.py`（`_Unread`・`_Reviewed`・`_review`・`_fill`・`_round`）、`plugins/ndf/tests/core/converge/test_converge.py`、`plugins/ndf/tests/domains/crossreview/test_crossreview_run.py`
- **変更内容:** 詳細設計書の「駆動が今の席を持ち、空いた席を埋める」の節の分岐の表。claude 以外の結果の無い席だけを 1 度起動し直し、全員が結果を返さなければ `precondition`、claude の席に結果が無ければ埋めずに `incomplete_reviewers`、それ以外は `fill_targets` の席を `free_seat` の名前の claude で埋め、全差分の依頼文で 1 回ずつ起動する。埋めた claude は起動し直さない。結果の有無は `AgentState.DONE` で出力に `findings` 配列があるかで決める
- **満たす受け入れ条件:** 1・2・9・10・11・12
- **進め方:** 失敗するテスト（kiro だけが読めないと `incomplete_reviewers` で止まる）→ 通す最小実装 → 整理

### Task 8: 埋めた席のまま以後のラウンドを続け、同じ head の打ち直しで前回の結果を使う

- **対象ファイル:** `plugins/ndf/src/ndf/domains/crossreview/scope_decide.py`（`stand_ins`・`assignable`・`Basis`・`read_by_all_head`）、`plugins/ndf/src/ndf/domains/crossreview/drive.py`（`_reusable`・`_reused_warnings`・`_prepare_round`・ラウンドの記録）、`plugins/ndf/tests/domains/crossreview/test_crossreview_scope.py`、`plugins/ndf/tests/domains/crossreview/test_crossreview_run.py`
- **変更内容:** 詳細設計書の「埋めた席の記録」の照合と、`drive._reusable`。ラウンドの記録の `reviewers` に実際の席を、`filled` に埋めた席を書き、次のラウンドは今の席（`_Run.seats`）で起動する。前回の席に今回の席を 1 対 1 で割り当てて読み終えたかを判定し、再利用したときは注意に「前回の round <n>」で埋めたことを載せる。ラウンドの範囲は埋めた claude のために差し替えない
- **満たす受け入れ条件:** 2・3・4
- **進め方:** 失敗するテスト（2 ラウンド目で kiro を起動する）→ 通す最小実装 → 整理

### Task 9: 記録のコメントのラウンドの表に「相手役」の列を足す

- **対象ファイル:** `plugins/ndf/src/ndf/domains/crossreview/report_decide.py`、`plugins/ndf/tests/domains/crossreview/test_crossreview_report.py`
- **変更内容:** 詳細設計書の「記録のコメントの「相手役」の列」の節。`_seats_cell` が `codex・claude（kiro の席）` の形を作る
- **満たす受け入れ条件:** 3
- **進め方:** 失敗するテスト（ラウンドの表に「相手役」の列が無い）→ 通す最小実装 → 整理

### Task 10: 手順書と確定仕様

- **対象ファイル:** `plugins/ndf/skills/cross-review/SKILL.md`、`plugins/ndf/skills/cross-review/docs/01-state-and-review.md`、`docs/specifications/cross-review-participants-and-seats.md`
- **変更内容:** 詳細設計書の地図の 3 行のとおり。相手役の説明と終わり方の表を受け入れ条件 1〜12 と 15 の振る舞いに合わせ、状態ファイルの `filled` と資料のディレクトリを書き、確定仕様の席の決め方と埋め方を今の形に書き直す
- **満たす受け入れ条件:** 14
- **進め方:** テスト駆動を適用しない（`.md` の文言を照合するテストは書かない決まり）。`ndf check` が通ることと、単位の PR のレビューで確かめる

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 1 準備のリファクタリング | Task 1 | 無し（振る舞いを変えない） | 約 450 | （実装の後に書き足す） |
| 2 claude の読むだけの起動と下限 | Task 2・3・4 | 3955:7・3955:8・3955:9・3955:10・3955:13 | 約 450 | |
| 3 起動の前に席を決める | Task 5・6 | 3955:5・3955:6・3955:7・3955:8・3955:12・3955:13・3955:15 | 約 650 | |
| 4 ラウンドの中で席を埋めて続ける | Task 7・8・9 | 3955:1・3955:2・3955:3・3955:4・3955:9・3955:10・3955:11・3955:12 | 約 750 | |
| 5 手順書と確定仕様 | Task 10 | 3955:14 | 約 250 | |

- 行数は追加と削除の和で、`git diff --shortstat <ベース>...HEAD -- . ':(exclude)issues/issue-*'` で数える。ロックファイルと再生成のファイルは触らない
- 課題 1 つの見込みが約 2,550 行で 1,000 行を超えるため、タスクの依存の向きで 5 つに分けた。どの単位も単独でテストが通る
- 実行の順: 単位 1 と単位 2 は触るファイルが重ならないので並列にしてよい（単位 1 は `crossreview/` の `drive.py`・`cli.py` と移動先、単位 2 は `adapters/`・`participants.py`・`crossrefactor/launch.py`）。単位 3 は単位 1 と 2 の後（`drive.py` を 1,000 行の下へ戻してから足し、席の claude の起動に `read_only_launch` を使う）。単位 4 は単位 3 の後（`_Run.seats`・`filled`・`Filled` と席の起動を使う）。単位 5 は単位 4 の後

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | `drive.py` の範囲の読み取り（約 95 行）と設定の読み取り（約 100 行）を `changes.py`・`settings.py` へ移す（Task 1・単位 1） | `drive.py` は 1,000 行で構造の規則の上限にあり、10 タスク中 5 タスクが触る。テストは `test_crossreview_run.py` 45 件・`test_crossreview_fixer_sessions.py` 20 件・`test_crossreview_scope.py` 14 件で厚く、移動は既存のテストで確かめられる。課題は起票せず、#4076 の計画の U2 と同じ範囲の移動をこの計画の単位として先に流す |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `drive.py` の席の起動と埋める処理（`_seat_launch`・`_fill`・`_fill_refused`・`_review`）と、`participants.py` の席を決める関数の間の重複 | 単位 3 と単位 4 が同じ `drive.py` と `participants.py` を別々に触り、席の名前からランタイムを引く処理と注意の文を作る処理が両方の単位に現れる |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 単位 1 の後も、単位 3・4 で足す関数で `drive.py` が 1,000 行を超える（移動後の見込みは約 800 行、足すのは約 200 行） | 単位 3 と単位 4 のそれぞれの終わりに `ndf check` を打つ。超えたら、設計書の保存先を変える判断が要るとして実装を止め、conductor に返す |
| #4076 の計画の U2 が同じ移動を別のブランチで行い、取り込みで衝突する | 移す範囲と移動先の名前を #4076 の計画の U2 と同じにする。後から main へ入るほうが、取り込みで先の形に合わせる |
| 席の claude の読み取りの境界（`--restricted`・`--add-dir`・`dontAsk`）が実際の CLI で効かない | 設計書の「読み取りの境界の実機確認」の手順を、単位 3 の PR で手で起動して確かめる。テストは起動の引数と依頼文が指す場所だけを確かめる |

## 切り戻し手順

- 単位ごとに PR を revert すれば戻る。状態ファイルの `filled` は足すキーで、戻した版は読まずに無視するので、データの移行と巻き戻しは無い
- 単位 3 か 4 だけを戻すときは、後の単位から順に戻す（単位 4 は単位 3 の席の型を使う）

## 完了の定義

- [ ] 受け入れ条件 1〜15 をすべて満たし、条件ごとに検証手段（設計書の「テスト設計」の行）と結果が対応している
- [ ] 単位ごとに `uv run --frozen --project . --all-extras pytest <触ったテストのファイル> -q -n 4` と `ndf check` が通る
- [ ] 設計書の「読み取りの境界の実機確認」を単位 3 の PR で行い、結果を PR に残す
- [ ] リリース後テストで、kiro が未ログインの環境の実際の PR で codex と claude の 2 者がレビューし、記録のコメントに kiro の席を claude で埋めたことが載る
