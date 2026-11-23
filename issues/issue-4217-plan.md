# 並列の計画の起動の許可: 2 つの計画が同時に起動を求める場合と排他の待ちを、待たずにテストで確かめる（#4217）

省いた文書: 設計書（課題 1・公開インタフェース 変わらない・触るファイル 3・新設する関数 0・シグネチャを変える関数 0。数える関数はプロダクションのコードのもので、テストの関数は数えない。省いてよい条件は `requirements-design` の `references/document-roles.md` の「省いてよい文書と条件」が決める）
省いた文書: 詳細設計書（新設する関数 0・シグネチャを変える関数 0。下の「修正対象」に畳む）
省いた文書: 実行計画（課題 1・PR 1）

起動の排他の中で数える作り（#3989 の決定）を確かめるテスト 3 本が、本物の時間の待ちと子のプロセスを除くコミット 79b02ac21 で消え、代わりが無い。排他を偽の錠に、`ndf lanes status` の生死の材料を外部のコマンドを呼ばない偽に差し替え、待たず子のプロセスも起動しないテストとして戻す。確定仕様のテスト観点は、戻したテストにそろえる。事実と受け入れ条件は [要求仕様](./issue-4217-requirements.md) にあり、コードは変えない。

## 関連リンク

- 要求仕様: [issues/issue-4217-requirements.md](./issue-4217-requirements.md)（正は課題 #4217 の本文）。受け入れ条件は 1・2。**マシン・ラッパー・レーン・計画・起動の許可・起動の排他の意味は、要求仕様の「用語」を先に読む**（この計画の「計画 A・B・C」は並列の計画のことで、文書の種類の実装計画・実行計画とは別のもの）
- 設計書: 省いた（上の 1 行目）
- モード: `standard`（工程をすべて通すモード。設計 Pull Request と設計承認ゲートがある。進め方は `normal` で、承認は人が行う）

## 修正対象

直す場所はテストと確定仕様だけで、`plugins/ndf/src/` は触らない。根本原因は、79b02ac21 が本物の錠・スレッド・子のプロセスを使うテストを消したとき、同じ条件を確かめる代わりのテストを置かなかったことである。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/tests/progress/test_lane_machine.py` | テストを 2 本足す（下の Task 1・2）。`machine.admit_lock` を偽の錠に差し替える補助（出入りを呼び出しの記録へ積む）を、テストのファイルの中に置く |
| `plugins/ndf/tests/progress/test_plan.py` | テストを 1 本足す（Task 3）。生死の材料を読む `plan_run.probes` を、外部のコマンドを呼ばない偽に差し替える |
| `docs/specifications/ndf-parallel-machine-plan-limit.md` | 「テスト観点」の 4 項目に、確かめるテストのファイルと、範囲外にした項目の課題の番号を書く（Task 4。項目は消さない） |

テストが呼ぶ既存の関数の振る舞い（変えない。テストが縛る前提）:

| 関数 | 入力 | 返り・失敗の扱い |
| --- | --- | --- |
| `machine.admitted(own, lane_wrapper, listed, go, where)`（確定仕様の語では「起動の許可」） | 自分の計画のレーン・起動するラッパーの名前・ラッパーの一覧を読む関数・起動の手・計画の所在 | `admit_lock` の中で `cap()`・`listed()`・`count`（数え上げ）・`over_cap`（上限の判定）・`index_plan`・`go` の順に打つ（`count`・`over_cap`・`index_plan` はモジュールの名前で呼ぶので `monkeypatch.setattr(machine, …)` の差し替えが効く）。収まらなければ `waiting(RULE か OBSERVE, …)`。`admit_lock` が `LockBusy` を上げれば、どれも打たずに `waiting(LOCK, None, None)`（`{"ok": False, "waiting": {"reason": "lock", "machine_lanes": None, "cap": None, "unreadable": []}, "detail": …}`） |
| `plan_cli.main(argv)` | `["limits", …]`・`["set", …, "--plans", …]` | 引数の誤りとして `causes.exit_code("invalid")` を返す（例外を上げない） |
| `plan_run.probes(repo, plan)` | `status` が呼ぶ、生死の材料を読む関数の組 | 既定は `ndf relay ls` と `ndf progress wait` を子のプロセスで打つ。テストは差し替える |
| `command.store_root_of(cwd)`・`command.repo_id(cwd, root)` | 作業ディレクトリ（と保存先） | チェックアウトの保存先のパスとリポジトリの識別。どちらも中で Git を子のプロセスで打つ（`git rev-parse`・`git remote get-url`）。Task 3 は両方を差し替える |

## タスク分解

### Task 1: 2 つの計画が同時に起動を求めても上限を超えないことを確かめる

- **対象ファイル:** `plugins/ndf/tests/progress/test_lane_machine.py`
- **変更内容:** 偽の錠を足す。偽の錠は持ち主がいるかを持ち、持ち主がいる間に入ろうとすると `machine.LockBusy` を上げる（待たない）。入った時刻・出た時刻の代わりに、出入りを呼び出しの記録へ `lock-in`・`lock-out` として積む。`monkeypatch.setattr(machine, "admit_lock", <偽>)` で差し替える。
  - **共有のラッパーの一覧:** テストの中に辞書を 1 つ持ち（名前 → 行。最初は動いている `o-a` の 1 本）、`listed` の偽はこの辞書の写しを返す。`go` の偽は記録へ `go` を積み、**起動したラッパー（`w-a` など）を動いている行として辞書へ足してから、足した後の本数が上限 3 以下であることを確かめ**（起動前の本数では確かめない。3 本の状態で起動を許す誤りは足した後の 4 本で落ちる）、`{"ok": True}` を返す。A の `go` だけは、この確かめの後に初回の B を打つ。これで B の打ち直しと C は、A・B を足した後の一覧を読む
  - **実処理へ委ねる薄いラッパー:** `machine.count`・`machine.over_cap`・`machine.index_plan` を、記録へ `count`・`over_cap`・`index` を積んでから元の関数を呼ぶ関数に差し替える。元の関数は `monkeypatch.setattr` の前に変数へ取っておく。記録専用の偽にはしない。数え上げと上限の判定は本物が行い、索引は本物が保存先（`identity.load().state_dir` の下。既存テストと同じ分離された場所）へ書く
  - 上限 3（`named_cap.write`）・動いている `o-a` 1 本の状態で次を順に打つ
  1. 計画 A（`plan:w-a`）の起動の許可を打つ。A の `go` の中（A が排他を持つ間）で、計画 B（`plan:w-b`）の起動の許可を打つ
  2. B の結果が `reason: lock` の待ちで、B の `listed`・`count`・`over_cap`・`index`・`go` が 1 度も呼ばれていないことを確かめる
  3. A の結果が `ok` のあと、B を打ち直し、続けて計画 C（`plan:w-c`）を打つ
  4. **試行ごとに、起動の許可の結果と、その試行を終えた直後の一覧（辞書の名前の集合）を確かめる。** 初回の B を含む 4 試行すべてで、一覧の本数は上限 3 以下である

     | 試行 | 結果 | 試行の直後の一覧 |
     | --- | --- | --- |
     | 初回の B（A の `go` の中） | `reason: lock` の待ち（`machine_lanes`・`cap` は None） | `o-a`・`w-a`（変わらない） |
     | A | `ok` | `o-a`・`w-a` |
     | B の打ち直し | `ok` | `o-a`・`w-a`・`w-b` |
     | C | `reason: machine-lanes`・`machine_lanes: 3`・`cap: 3` の待ち | `o-a`・`w-a`・`w-b`（変わらない） |

     あわせて、索引（`machine.indexed()`）の計画 ID が A と B の 2 つだけである（C は書かれない）ことを確かめる
  5. **計画ごとの呼び出しの記録を確かめる。** A と B の打ち直しは `["lock-in", "listed", "count", "over_cap", "index", "go", "lock-out"]`、C は `["lock-in", "listed", "count", "over_cap", "lock-out"]`。数え上げ・上限の判定・索引・起動の手が 1 つでも排他の外へ出れば、記録の順が変わって落ちる
  - `time.sleep` を、呼ばれたら失敗する偽に差し替えておく（待たないことを確かめる）
  - **索引の計画を読む処理も差し替える。** A を索引に書いた後、B の打ち直しと C の `machine_plans` は索引の計画を `machine.read_lanes` で読みに行き、その中の `command.store_root_of` が `git rev-parse` を子のプロセスで打つ。そこで `machine.read_lanes` を、`skip` に入った計画は None を返し、それ以外はレーンの無い読める `PlanLanes` を返す偽にする（本数は共有の一覧のラッパーで数える）。あわせて `subprocess.Popen` を呼ばれたら必ず失敗する偽にし、差し替えの漏れで子のプロセスが起動すれば落ちるようにする
- **満たす受け入れ条件:** 1（期待する結果の 1）
- **進め方:** テストを書いて通す。**このテストが退行を捕まえることを、プロダクションのコードを 1 度だけ壊して確かめる。** 壊し方は、**排他を 2 つに割り、数え上げと上限の判定だけをその間（排他の外）へ出す**。1 つ目の `with machine.admit_lock(...)` の中に `cap()`・`listed()`・`machine_plans` を残す。それを抜けた後で `count`・`over_cap` を打ち、収まれば 2 つ目の `with machine.admit_lock(...)` の中で `index_plan`・`go` を打つ。`go` が排他の中に残るので、A の `go` の中の初回の B は排他を取れず、手順 2 と手順 4 の初回の B の確かめは通ったままになる。このとき記録は `["lock-in", "listed", "lock-out", "count", "over_cap", "lock-in", "index", "go", "lock-out"]` になる。落ちるのが**手順 5 の記録の確かめ**であることを、pytest の出力の落ちた `assert` の行で個別に確かめる。別の確かめで落ちた場合は、この退行の確かめとして採らない。見てから、コードを元へ戻す（戻した状態をコミットする）。一覧の取得の位置を変えずに数え上げと判定だけを外へ出す退行を、手順 5 が捕まえることの確かめである。確かめた手順と出力は Pull Request の本文へ書く

### Task 2: 排他を取れないとき数えずに lock の待ちを返すことを確かめる

- **対象ファイル:** `plugins/ndf/tests/progress/test_lane_machine.py`
- **変更内容:** 入ると必ず `machine.LockBusy` を上げる偽の錠に差し替え、`machine.cap`・`listed`・`machine.count`・`machine.over_cap`・`machine.index_plan`・`go` を呼ばれた回数を数える偽にする。`admitted` の結果が `{"ok": False, "waiting": {"reason": "lock", "machine_lanes": None, "cap": None, "unreadable": []}}`（`detail` は除いて比べる）で、6 つがどれも 0 回であることを確かめる
- **満たす受け入れ条件:** 1（期待する結果の 2）
- **進め方:** テストを書いて通す。退行を捕まえることは、`admitted` の `except LockBusy` を一時的に `pass` へ変えるとテストが落ちることで 1 度だけ確かめ、コードを元へ戻す

### Task 3: 消したサブコマンドと引数が断られ、status に 3 つの値が無いことを確かめる

- **対象ファイル:** `plugins/ndf/tests/progress/test_plan.py`
- **変更内容:** 79b02ac21 で消えた `test_cli_has_no_limits_subcommand_and_no_plans_option` と同じ 2 つの引数の並びで、パラメータ化したテストを足す。受け入れ条件 1 は子のプロセスを起動しないことを求めるので、**`git init` を打つ既存の `repo` の fixture は使わず**、Git の子のプロセスを打たない fixture をこのファイルに足す。CLI は `plan_cli.main` で同じプロセスから呼ぶ。`start` の切り離しの起動は既存の `detached` の fixture が偽にする
  - **Git を呼ばない fixture:** `tmp_path` の下に作業ディレクトリを作って `monkeypatch.chdir` で移る（`git init` はしない）。`start`・`status` が Git を呼ぶのは 2 か所である。保存先を求める `command.store_root_of`（`durable.store_root` の `git rev-parse`）と、リポジトリの識別を求める `command.repo_id`（`Git(cwd).remote_url()`）である。前者を `tmp_path` の下の固定の保存先を返す偽に、後者を固定の識別の文字列を返す偽に差し替える。後片付けは、偽が返すのと同じ保存先のパスを fixture が変数に持ち、`durable.release_store(<そのパス>)` で放す（`git rev-parse` を打たない）。`plan/run.py` の `record_dir` も Git を呼ぶが、呼び手は `detach` だけで、`detached` が偽にするので通らない
  - `plan_run.probes` を、ラッパーの一覧が空・スプリントの結果が None の `liveness.Probes` を返す偽にする（`ndf relay ls` の子のプロセスを打たせない）
  - `subprocess.Popen` を、**呼ばれたら必ず失敗する偽**にする（`git` も通さない）。`subprocess.run` は中で `Popen` を使うので、上の 2 つの差し替えに漏れがあって Git が呼ばれても、ここで落ちて分かる
  - `time.sleep` を、呼ばれたら失敗する偽にする
  - `start` → 消した引数の並びで `plan_cli.main` が `causes.exit_code("invalid")` を返す → `status` の `metrics` に `max_lanes`・`max_plans` が無く、レーンの行に `plans` が無いことを確かめる
- **満たす受け入れ条件:** 1（期待する結果の 3）
- **進め方:** テストを書いて通す。差し替えの漏れで子のプロセス（Git を含む）が起動すれば `Popen` の偽が落とす。**退行を捕まえることは、消したインタフェースを 1 つずつ一時的に戻して確かめる**（`start`・引数の解析・`status` は `admitted` を呼ばないので、Task 1 の排他を割る壊し方では落ちない。その手順は使わない）。次の 3 つを 1 つずつ入れ、そのたびに落ちたのが対応する確かめの `assert` であることを pytest の出力の落ちた行で見てから、元へ戻す（戻した状態をコミットする）
  1. `plugins/ndf/src/ndf/progress/plan/cli.py` のパーサーに `limits` のサブコマンドを足す → `limits` の並びのケースで、終了コードが `causes.exit_code("invalid")` でなくなって落ちる
  2. 同じファイルの `set` のパーサー（`sub.add_parser("set")`）に `--plans` の引数を足す → `set … --plans …` の並びのケースで同じく落ちる
  3. `plugins/ndf/src/ndf/progress/plan/__init__.py` の `status_of`（`status` の本体）が組む `metrics` に `max_lanes` を足す → `metrics` に `max_lanes` が無いことの確かめで落ちる

  確かめた手順と出力は Pull Request の本文へ書く

### Task 4: 確定仕様のテスト観点をテストにそろえる

- **対象ファイル:** `docs/specifications/ndf-parallel-machine-plan-limit.md`
- **変更内容:** 「テスト観点」の 18 項目は、設計の工程で今あるテストと 1 つずつ照らし済みで、**直す項目は下の 4 つに決まっている**。照らした全文は設計 Pull Request の本文にある（判定は `ある` 13・`一部` 3・`無い` 2）。**項目は 1 つも消さない。**

  | 項目 | 今の判定 | 直し方 |
  | --- | --- | --- |
  | 1 項目目（3 つの値・`ndf lanes limits`・`ndf lanes set --plans`・I20・I2） | 一部（消した引数の分だけ無い） | Task 3 のテストで満たす。名指すファイルは `tests/progress/test_plan.py` のままで、I20 を確かめているのは `tests/progress/test_lane.py` なのでそちらも括弧に足す |
  | 12 項目目（別の 2 つの計画の起動の許可を同時に走らせても上限を超えない） | 一部（前半が無い） | Task 1 のテストで満たす。括弧に `tests/progress/test_lane_machine.py` を書く |
  | 13 項目目（`machine_wait` の `lock`） | ある（`status` の側だけ） | 起動の許可の側の `lock` を Task 2 が確かめるので、その 1 文を足して同じファイルを指す |
  | 9 項目目（上限を下げた後の続きの起動）・17 項目目（テストのスロットが変わらない）・18 項目目（時間で区切らない） | 一部・無い（この課題の範囲外） | 項目は消さず、行の末尾に別の課題の番号（#4221・#4222）を括弧で添える |

  末尾の「テストのパスは …」の行が挙げる 2 本（`tests/progress/test_plan_run.py`・`tests/support/plan_decls.py`）は、どちらも実在するので変えない。
- **満たす受け入れ条件:** 2
- **進め方:** テスト駆動を適用しない（文書だけ）。直した後、項目が名指すファイルがすべて実在することを `ls` で確かめ、結果を Pull Request の本文へ書く

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| #4217 | Task 1〜4 | 4217:1・4217:2 | 約 150（消えたテスト 3 本が 46 行で、呼び出しの順の記録と差し替えの偽で 2 倍ほどになる見込み。確定仕様の直しが 6 行） | （実装の後に書き足す） |

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | コードを触らない。テストのファイル 2 つ（`test_lane_machine.py` 約 420 行・`test_plan.py` 約 500 行）にテストを足すだけ |
| 実装の中 | 各タスクで行う | テスト駆動の手順（`tdd-cycle`）のリファクタリングのステップ。偽の錠は Task 1・2 で 1 つにする |
| スプリントの終わり（cross-refactoring） | しない | 単位が 1 つで、単位をまたぐ重複が無い |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 偽の錠は本物の `flock` の排他そのものを確かめない | 確かめる対象は「一覧の取得・数え上げ・上限の判定・索引・起動の手が排他の中で起き、排他を取れなければどれも起きない」という作りで、Task 1 の手順 5 の呼び出しの順の記録がこれを縛る。`flock` が本当に排他するかは確定仕様の既知の限界（ネットワークのファイルシステム）に残す。本物の錠を待たないのは受け入れ条件 1 の求めである |
| 偽物を使うテストは、偽物を壊しても落ちるので、退行を捕まえる証明にならない | Task 1・2 の進め方で、プロダクションのコードを 1 度だけ壊してテストが落ちることを確かめ、確かめた手順と出力を Pull Request の本文へ書く |
| `status` が `probes` の外で子のプロセスを打つ経路が残る | Task 3 で `subprocess.Popen` を呼ばれたら必ず失敗する偽にするので、残れば落ちて分かる |

## 完了の定義

- [ ] 受け入れ条件 1・2 をすべて満たし、条件ごとに検証手段と結果が対応している（PR の CI の結果。このリポジトリはテストを手元で全体に走らせない）
- [ ] 差分のファイルが上の「修正対象」の 3 つ（`issues/issue-4217-*` を除く）に収まり、`plugins/ndf/src/` に差分が無い
