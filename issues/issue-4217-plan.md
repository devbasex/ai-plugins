# 並列の計画の起動の許可: 2 つの計画が同時に起動を求める場合と排他の待ちを、待たずにテストで確かめる（#4217）

省いた文書: 設計書（課題 1・公開インタフェース 変わらない・触るファイル 3・新設する関数 0・シグネチャを変える関数 0。数える関数はプロダクションのコードのもので、テストの関数は数えない。省いてよい条件は `requirements-design` の `references/document-roles.md` の「省いてよい文書と条件」が決める）
省いた文書: 詳細設計書（新設する関数 0・シグネチャを変える関数 0。下の「修正対象」に畳む）
省いた文書: 実行計画（課題 1・PR 1）

起動の排他の中で数える作り（#3989 の決定）を確かめるテスト 3 本が、本物の時間の待ちと子のプロセスを除くコミット 79b02ac21 で消え、代わりが無い。排他を偽の錠に、`ndf lanes status` の生死の材料を偽の口に差し替え、待たず子のプロセスも起動しないテストとして戻す。確定仕様のテスト観点は、戻したテストにそろえる。事実と受け入れ条件は [要求仕様](./issue-4217-requirements.md) にあり、コードは変えない。

## 関連リンク

- 要求仕様: [issues/issue-4217-requirements.md](./issue-4217-requirements.md)（正は課題 #4217 の本文）。受け入れ条件は 1・2。**マシン・ラッパー・レーン・計画・起動の許可・起動の排他の意味は、要求仕様の「用語」を先に読む**（この計画の「計画 A・B・C」は並列の計画のことで、文書の種類の実装計画・実行計画とは別のもの）
- 設計書: 省いた（上の 1 行目）
- モード: `standard`（工程をすべて通すモード。設計 Pull Request と設計承認ゲートがある。進め方は `normal` で、承認は人が行う）

## 修正対象

直す場所はテストと確定仕様だけで、`plugins/ndf/src/` は触らない。根本原因は、79b02ac21 が本物の錠・スレッド・子のプロセスを使うテストを消したとき、同じ条件を確かめる代わりのテストを置かなかったことである。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/tests/progress/test_lane_machine.py` | テストを 2 本足す（下の Task 1・2）。`machine.admit_lock` を偽の錠に差し替える補助を、テストのファイルの中に置く |
| `plugins/ndf/tests/progress/test_plan.py` | テストを 1 本足す（Task 3）。`plan_run.probes` を偽の口に差し替える |
| `docs/specifications/ndf-parallel-machine-plan-limit.md` | 「テスト観点」の記述を、実際にあるテストのファイルと名前にそろえる（Task 4） |

テストが呼ぶ既存の関数の振る舞い（変えない。テストが縛る前提）:

| 関数 | 入力 | 返り・失敗の扱い |
| --- | --- | --- |
| `machine.admitted(own, lane_wrapper, listed, go, where)`（確定仕様の語では「起動の許可」。課題の本文の「起動の判定」と同じもの） | 自分の計画のレーン・起動するラッパーの名前・ラッパーの一覧を読む口・起動の手・計画の所在 | `admit_lock` の中で `cap()`・`listed()`・数える・`index_plan`・`go` の順に打つ。収まらなければ `waiting(RULE か OBSERVE, …)`。`admit_lock` が `LockBusy` を上げれば、どれも打たずに `waiting(LOCK, None, None)`（`{"ok": False, "waiting": {"reason": "lock", "machine_lanes": None, "cap": None, "unreadable": []}, "detail": …}`） |
| `plan_cli.main(argv)` | `["limits", …]`・`["set", …, "--plans", …]` | 引数の誤りとして `causes.exit_code("invalid")` を返す（例外を上げない） |
| `plan_run.probes(repo, plan)` | `status` が呼ぶ生死の材料の口 | 既定は `ndf relay ls` と `ndf progress wait` を子のプロセスで打つ。テストは差し替える |

## タスク分解

### Task 1: 2 つの計画が同時に起動を求めても上限を超えないことを確かめる

- **対象ファイル:** `plugins/ndf/tests/progress/test_lane_machine.py`
- **変更内容:** 偽の錠を足す。偽の錠は持ち主がいるかを持ち、持ち主がいる間に入ろうとすると `machine.LockBusy` を上げる（待たない）。入った時刻・出た時刻の代わりに、出入りを呼び出しの記録へ `lock-in`・`lock-out` として積む。`monkeypatch.setattr(machine, "admit_lock", <偽>)` で差し替える。上限 3、ほかの動いているラッパー 1 本（`o-a`）の状態で次を順に打つ
  1. 計画 A（`plan:w-a`）の起動の許可を打つ。A の `go` の中（A が排他を持つ間）で、計画 B（`plan:w-b`）の起動の許可を打つ
  2. B の結果が `reason: lock` の待ちで、B の数える処理（`listed`）と起動の手（`go`）が 1 度も呼ばれていないことを確かめる
  3. A の結果が `ok` のあと、B を打ち直すと `ok`、続けて計画 C（`plan:w-c`）を打つと `reason: machine-lanes`・`machine_lanes: 3`・`cap: 3` の待ちになる
  4. 起動の手が呼ばれるたびに、その時点の一覧の本数が上限 3 以下であること、索引（`machine.indexed()`）が A と B だけであることを確かめる
  5. **呼び出しの記録が `["lock-in", "listed", "index", "go", "lock-out"]` の順であることを確かめる**（`listed`・`machine.index_plan`・`go` の偽が、呼ばれるたびに記録へ名前を積む）。数える処理・索引・起動の手が 1 つでも排他の外へ出れば、記録の順が変わって落ちる
  - `time.sleep` を、呼ばれたら失敗する偽に差し替えておく（待たないことを確かめる）
- **満たす受け入れ条件:** 1（期待する結果の 1）
- **進め方:** テストを書いて通す。**このテストが退行を捕まえることを、プロダクションのコードを 1 度だけ壊して確かめる。** `admitted` の数える処理（`listed()` から `over_cap` まで）を `with machine.admit_lock(...)` の前へ出し、手順 5 の記録の順が `["listed", "lock-in", …]` になってテストが落ちることを見てから、コードを元へ戻す（戻した状態をコミットする）。確かめた手順と出力は Pull Request の本文へ書く

### Task 2: 排他を取れないとき数えずに lock の待ちを返すことを確かめる

- **対象ファイル:** `plugins/ndf/tests/progress/test_lane_machine.py`
- **変更内容:** 入ると必ず `machine.LockBusy` を上げる偽の錠に差し替え、`machine.cap`・`listed`・`machine.index_plan`・`go` を呼ばれた回数を数える偽にする。`admitted` の結果が `{"ok": False, "waiting": {"reason": "lock", "machine_lanes": None, "cap": None, "unreadable": []}}`（`detail` は除いて比べる）で、4 つがどれも 0 回であることを確かめる
- **満たす受け入れ条件:** 1（期待する結果の 2）
- **進め方:** テストを書いて通す。退行を捕まえることは、`admitted` の `except LockBusy` を一時的に `pass` へ変えるとテストが落ちることで 1 度だけ確かめ、コードを元へ戻す

### Task 3: 消したサブコマンドと引数が断られ、status に 3 つの値が無いことを確かめる

- **対象ファイル:** `plugins/ndf/tests/progress/test_plan.py`
- **変更内容:** 79b02ac21 で消えた `test_cli_has_no_limits_subcommand_and_no_plans_option` と同じ 2 つの引数の並びで、パラメータ化したテストを足す。`repo` の fixture の後に、次を差し替える。`git init` は基盤のツールなので本物でよく、`start` の切り離しの起動は `detached` の fixture が偽にする
  - `plan_run.probes` を、ラッパーの一覧が空・スプリントの結果が None の `liveness.Probes` を返す偽にする（`ndf relay ls` の子のプロセスを打たせない）
  - `subprocess.Popen` と `time.sleep` を、呼ばれたら失敗する偽にする
  - `start` → 消した引数の並びで `plan_cli.main` が `causes.exit_code("invalid")` を返す → `status` の `metrics` に `max_lanes`・`max_plans` が無く、レーンの行に `plans` が無いことを確かめる
- **満たす受け入れ条件:** 1（期待する結果の 3）
- **進め方:** Task 1 と同じ。差し替えの漏れで子のプロセスが起動すれば `Popen` の偽が落とす

### Task 4: 確定仕様のテスト観点をテストにそろえる

- **対象ファイル:** `docs/specifications/ndf-parallel-machine-plan-limit.md`
- **変更内容:** 「テスト観点」の項目を、今あるテストと 1 つずつ照らす。照らす項目は 2 種類ある。
  - 79b02ac21 がテストを消したファイル（`tests/progress/test_plan.py`・`test_lane_machine.py`・`test_plan_run.py`・`test_run_lanes.py`）を指す項目
  - Task 1〜3 が確かめる項目（1 項目目・「別の 2 つの計画の起動の許可を同時に…」・「排他が取られているときは `lock`」）

  照らした後の直し方:
  - 1 項目目の「検査の誤りに I20・I2 が出ない」は、確かめているテストのファイル（`tests/progress/test_lane.py` など）を括弧に足す。どこにも無ければその句を消す
  - 同時に起動を求める場合と `lock` の待ちの項目は、Task 1・2 のテストのファイル（`tests/progress/test_lane_machine.py`）を括弧に書く
  - 照らして今あるテストが無い項目が残れば、その項目を消し、テストを戻す課題を `out-of-scope` で起票して番号を PR に書く（この課題の範囲外）
  - 末尾の「ほかに `tests/progress/test_plan_run.py`… がある」の行は、今あるファイルだけを残す
- **満たす受け入れ条件:** 2
- **進め方:** テスト駆動を適用しない（文書だけ）。照らした結果（項目ごとのテストの名前）を PR 本文の確かめの欄に書く

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| #4217 | Task 1〜4 | 4217:1・4217:2 | 約 130 | （実装の後に書き足す） |

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | コードを触らない。テストのファイル 2 つ（`test_lane_machine.py` 約 420 行・`test_plan.py` 約 500 行）にテストを足すだけ |
| 実装の中 | 各タスクで行う | テスト駆動の手順（`tdd-cycle`）のリファクタリングのステップ。偽の錠は Task 1・2 で 1 つにする |
| スプリントの終わり（cross-refactoring） | しない | 単位が 1 つで、単位をまたぐ重複が無い |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 偽の錠は本物の `flock` の排他そのものを確かめない | 確かめる対象は「数える処理・索引・起動の手が排他の中で起き、排他を取れなければどれも起きない」という作りで、Task 1 の手順 5 の呼び出しの順の記録がこれを縛る。`flock` が本当に排他するかは確定仕様の既知の限界（ネットワークのファイルシステム）に残す。本物の錠を待たないのは受け入れ条件 1 の求めである |
| 偽物を使うテストは、偽物を壊しても落ちるので、退行を捕まえる証明にならない | Task 1・2 の進め方で、プロダクションのコードを 1 度だけ壊してテストが落ちることを確かめ、確かめた手順と出力を Pull Request の本文へ書く |
| `status` が `probes` の外で子のプロセスを打つ経路が残る | Task 3 で `subprocess.Popen` を失敗する偽にするので、残れば落ちて分かる |

## 完了の定義

- [ ] 受け入れ条件 1・2 をすべて満たし、条件ごとに検証手段と結果が対応している（PR の CI の結果。このリポジトリはテストを手元で全体に走らせない）
- [ ] 差分のファイルが上の「修正対象」の 3 つ（`issues/issue-4217-*` を除く）に収まり、`plugins/ndf/src/` に差分が無い
