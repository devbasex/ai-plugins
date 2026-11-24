# #3955 の詳細設計: cross-review の空いた席を claude の読むだけの起動で埋める

設計文書 [issue-3955-design.md](./issue-3955-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。新設する関数が 2 つ以上あり、既存の関数のシグネチャも変えるため、別ファイルにした。前提は今の `main` のコードの形である（`participants.assign`・`drive._review`・`drive._launch_reviewers`・`crossrefactor.launch._read_only` など）。

**埋めた席の記録の読み書きは、ラウンドの範囲の判断（`scope_decide.py`）に置く。** 理由は次の 2 つである。

- ラウンドの記録のキー（`base`・`reviewers`・`focus`）を持つのが、今このモジュールである。記録のコメント（`report_decide.py`）と再利用の判定（`drive._reusable`）の両方が、このモジュールを読む
- `participants.py` は学習の設定や起動の部品を読む。そこに置くと、判断のモジュール（`report_decide.py`・`scope_decide.py`）が `participants.py` を読むことになる

## 地図

パッケージ・モジュール構成（触るファイルだけ。新設のファイルは無い）:

```text
plugins/ndf/
├── contracts/domains/cli-floors.json             … claude の行に --restricted の下限 2.1.316（flag_floors）を足す。共通の下限は変えない
├── src/ndf/adapters/cli_floor.py                 … require が起動の引数を受け、flag_floors の引数があればその下限と比べる
├── src/ndf/adapters/agents.py                    … run_agent が起動の引数をすべて require に渡す
├── src/ndf/adapters/claude.py                    … ClaudeCall に読める場所・permission_args が --restricted・--add-dir・dontAsk を組む
├── src/ndf/core/converge/participants.py         … 席を決める・席の名前からランタイムを引く・断られた理由の組・claude の読むだけの起動
├── src/ndf/domains/crossrefactor/launch.py       … claude の読むだけの起動と本文の取り出しを participants の関数で行う
├── src/ndf/domains/crossreview/
│   ├── scope_decide.py                           … 埋めた席の記録（Filled）・前回の席に今回の席を割り当てる照合
│   ├── drive.py                                  … 今の席を持つ・起動し直しの後に埋める・資料を実行ごとの review-input-*/ に書く
│   ├── report_decide.py                          … ラウンドの表に「相手役」の列
│   └── cli.py                                    … review_seats で席を決め、駆動の設定へ渡す
├── skills/cross-review/SKILL.md                  … 相手役の説明と終わり方の表
├── skills/cross-review/docs/01-state-and-review.md … 状態ファイルの filled と資料のディレクトリ
└── tests/
    ├── adapters/test_claude_args.py
    ├── adapters/test_cli_floor.py
    ├── core/converge/test_converge.py
    ├── domains/crossrefactor/test_launch_and_phase.py
    ├── domains/crossreview/test_crossreview_scope.py
    ├── domains/crossreview/test_crossreview_run.py
    └── domains/crossreview/test_crossreview_report.py
docs/specifications/cross-review-participants-and-seats.md … 席の決め方と埋め方を書き直す
```

依存の向き: `crossreview/cli.py` と `crossreview/drive.py` が `participants` と `scope_decide` を読む。`report_decide` は `scope_decide` を読み、`participants` を読まない（判断のモジュールから学習の設定や起動の部品を読まない）。`crossrefactor/launch.py` は `participants` を読む。`participants` は `adapters.claude` を読み、`domains` を読まない。循環は無い。型の関係は設計文書の「構成要素」の図にある。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/core/converge/participants.py` | 変更 | 相手役と席を決め、読むだけの起動を組み立てる | `adapters.claude`・`adapters.agents`・`adapters.training`・`contracts.facts` | `Seats`（新設）、モジュール | `review_seats`・`runtime_of`・`free_seat`・`fill_targets`・`refusals`・`read_only_launch`・`answer_text`・`_able`・定数 `READ_TOOLS`・`SEAT_COUNT`（新設）・`unready`・`assign`（中身だけ変更） |
| `src/ndf/adapters/claude.py` | 変更 | `claude -p` の起動の引数 | — | `ClaudeCall`（変更） | `permission_args`（変更）・`claude_launch`（読める場所を渡すだけ）・定数 `DONT_ASK`・`RESTRICTED`（新設） |
| `src/ndf/domains/crossreview/scope_decide.py` | 変更 | ラウンドの範囲と記録のキー | `store`・`finish_decide` | `Filled`（新設）・`Basis`（変更） | `filled_of`・`stand_ins`・`assignable`・定数 `FILLED`（新設）・`read_by_all_head`（変更） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | cross-review の収束ループの駆動 | `participants`・`scope_decide`・`report_decide` | `RunConfig`・`_Run`（変更）・`_Unread`・`_Reviewed`（新設） | `_input_path`・`_drop_inputs`・`_seat_launch`・`_fill`・`_fill_refused`・`_filled_warning`・`_reused_warnings`（新設）・`_launch_reviewers`・`_review`・`_reviewer_findings`・`_unread_hint`・`_round`・`_reusable`・`drive`（変更）・`_write_diff`・`_remarks_file`・`_prepare_round`（書き先か読む値だけ変える） |
| `src/ndf/domains/crossreview/report_decide.py` | 変更 | 記録のコメントの本文 | `scope_decide` | モジュール | `_seats_cell`（新設）・`_round_rows`（変更） |
| `src/ndf/domains/crossreview/cli.py` | 変更 | `ndf cross-review` のエントリポイント | `participants`・`drive` | `Resolved`（変更） | `_reviewers`（変更）・`_run_config`・`cmd_scope`・`_state_parsers`（読む値か help だけ変える） |
| `src/ndf/domains/crossrefactor/launch.py` | 変更 | cross-refactoring の工程の担当の起動 | `participants` | モジュール | `_read_only`・`_collect_stdout`（呼ぶ先だけ変える）。`_answer_text` と定数 `READ_TOOLS` は消す（participants へ移す） |
| `contracts/domains/cli-floors.json` | 変更 | 外部 CLI の下限の表 | — | — | `claude` の行に `"flag_floors": {"--restricted": "2.1.316"}` を足す。`min_version`（`2.1.268`）は変えない。`columns.flag_floors.summary` に「引数の名前から下限への対応。その引数で NDF が頼る振る舞いの不具合が直った最初のバージョンで、行の `min_version` 以上。行に無ければ `min_version` だけで比べる」と、足した理由（`--restricted` の読み取りの境界、#3955）を書く |
| `src/ndf/adapters/cli_floor.py` | 変更 | 外部 CLI のバージョンを下限と比べる | `contracts.facts` | モジュール | `require(name, argv0, env=None, cwd=None, args: Sequence[str] = ())`（変更）。`args` に行の `flag_floors` の引数があれば、その下限のうち最も高いものと比べる。無ければ今のとおり `min_version` と比べる。`refuse_old`・`version` は変えない |
| `src/ndf/adapters/agents.py` | 変更 | 外部 CLI の起動と監視 | `cli_floor` | モジュール | `_guarded_run`（変更）。`cli_floor.require` に `args=spec.argv[1:]` を渡す |
| `skills/cross-review/SKILL.md` | 変更 | cross-review の手順書 | — | — | 相手役の説明・終わり方の表・`review-scope` が起動の前に分かる席だけで範囲を決めること（受け入れ条件 14） |
| `skills/cross-review/docs/01-state-and-review.md` | 変更 | 状態ファイルとレビューの資料の説明 | — | — | ラウンドの記録の `filled`・`reviewers` の意味・実行ごとに新しく作り、終わりに消す資料のディレクトリ `review-input-<ランダムな接尾辞>/` |
| `docs/specifications/cross-review-participants-and-seats.md` | 変更 | 席の確定仕様 | — | — | 席の決め方・埋め方・席の名前 `claude-2` を今の形に書き直す |

## 席を決める（participants.py）

満たす決定: 決定 1・3・6、受け入れ条件 5・6・12・13

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Seats` | `core/converge/participants.py` | 新設 | `class Seats(NamedTuple): names: tuple[str, ...]; added: tuple[str, ...]; reason: Optional[str]; may_fill: bool` | 起動の前に決めた席・`allowed` のために足した claude の席・足した理由・以後に席を埋めてよいか |
| `_able` | `core/converge/participants.py` | 新設 | `_able(allowed: Optional[Collection[str]]) -> list[str]` | ランタイムの表の行の順に、`review_args` を持ち `allowed` の中にある名前（`assign` と `review_seats` の共通の母集合） |
| `assign` | `core/converge/participants.py` | 変更（中身だけ） | 今のまま | 母集合を `_able` で求める。入出力・例外は変えない（受け入れ条件 13） |
| `review_seats` | `core/converge/participants.py` | 新設 | `review_seats(requested: Sequence[str], allowed: Optional[Collection[str]], fixer: str = FIXER) -> Seats` | cross-review の起動の前の席を決める |
| `runtime_of` | `core/converge/participants.py` | 新設 | `runtime_of(seat: str) -> str` | 席の名前からランタイムの名前を引く（`claude-<数>` は `claude`、ほかはそのまま） |
| `free_seat` | `core/converge/participants.py` | 新設 | `free_seat(taken: Collection[str]) -> str` | `claude`・`claude-2`・`claude-3`… のうち `taken` に無い最初の名前 |
| `fill_targets` | `core/converge/participants.py` | 新設 | `fill_targets(seats: Sequence[str], unread: Collection[str]) -> tuple[str, ...]` | 起動し直しの後に埋める席（設計文書の「1 ラウンドの流れ」の分岐の表の、順 0〜3 の行。下の「処理の手順」の表と同じ）。`seats` の順で返す |

- `review_seats`
  - 事前条件: `requested` は `--reviewer` の値をそのまま並べたもの（重なりを含んでよい）
  - 事後条件: 名指しの有無で次のとおりになる

    | フィールド | 名指しがある | 名指しが無い |
    | --- | --- | --- |
    | `names` | 重なりと修正担当を除いた名指しの並び | 既定の席（`assign` の既定と同じ選び方）に、`SEAT_COUNT` に足りない分の `free_seat` の名前を続けたもの |
    | `added` | 空 | 足した名前 |
    | `reason` | None | 足したときだけ `allowed の相手役 <数>（<名前を ・ で並べる>）`（数が 0 なら括弧を付けない） |
    | `may_fill` | `len(names) != 1` | 真 |
- `fill_targets`
  - 事後条件: 結果の無い席が 0 か、全員か、claude の席（`runtime_of` が `FIXER`）を 1 つでも含むなら空。ほかは結果の無い席を `seats` の順に並べたもの。全員のときの `precondition` と、claude の席を含むときの `incomplete_reviewers` は呼び手が決める

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `review_seats` | 修正担当が `allowed` の外 | `.ndf/runtimes.json` の `allowed` に claude が無い | 関数の頭 | `FixerNotAllowed` を投げる（今と同じ） | CLI が `precondition` で止める | 後始末は無い。`allowed` を直して打ち直す |
| `review_seats` | 名指しが読むだけの起動を持たない・`allowed` の外 | `--reviewer claude` など | `_check_requested` | `UnknownParticipant` を投げる（今と同じ） | CLI が `invalid` で止める | 後始末は無い。名指しを直して打ち直す |
| `free_seat` | — | 名前は `claude-<数>` で尽きない | — | — | — | — |

選べる相手役が 0 でも `review_seats` は投げない（受け入れ条件 6）。`assign` は今のとおり投げる。

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `review_seats` | `allowed` が無い・名指し無し | `names` = `codex`・`kiro`、`added` 空 |
| `review_seats` | `allowed` = claude・codex、名指し無し | `names` = `codex`・`claude`、`added` = `claude`、`reason` = `allowed の相手役 1（codex）` |
| `review_seats` | `allowed` = claude だけ、名指し無し | `names` = `claude`・`claude-2`、`reason` = `allowed の相手役 0` |
| `review_seats` | `--reviewer codex --reviewer codex` | `names` = `codex`、`may_fill` 偽 |
| `review_seats` | `--reviewer codex --reviewer kiro` | `names` = `codex`・`kiro`、`may_fill` 真 |
| `fill_targets` | 席 `codex`・`kiro`、結果無し `kiro` | `kiro` |
| `fill_targets` | 席 `codex`・`claude`、結果無し `claude` | 空（呼び手が `incomplete_reviewers`） |
| `fill_targets` | 席 `codex`・`claude`、結果無し `codex`・`claude` | 空（呼び手が `precondition`） |

名指しが 1 者なら席は 1 つで、結果が無ければ全員に当たるので、`fill_targets` は `may_fill` を受けなくても埋めない。`may_fill` を読むのは、始める前の確かめ（`drive._fill_refused`）と、再利用の判定（`drive._reusable` の `stand_in`）の 2 か所である。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `SEAT_COUNT` | 定数 | `core/converge/participants.py` | `2`（1 ラウンドの席の数。10 系の #1598 の利用者の決定） |
| `READ_TOOLS` | 定数 | `core/converge/participants.py`（`crossrefactor/launch.py` から移す） | `"Read,Grep,Glob"` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `review_seats` | `assign` | `participants.py` を読んだ | 母集合と名指しの確かめ（`_able`・`_check_requested`）は共有する。`assign` そのものは選べる相手役 0 で投げ、埋めないので使えない（決定 1） |
| `_able` | `assign` の中の内包表記 | 同上 | 切り出して `assign` も使う |
| `runtime_of`・`free_seat` | ランタイムの表（`facts.rows`） | `grep -rn "claude-2\|seat" src/ndf` | 表は席の名前を持たない。11 系に席の名前の部品は無い |
| `fill_targets` | `drive._round` の `len(unread) == len(reviewers)` | `drive.py` を読んだ | 全員の判定は残し、埋める席の判定を足す |

### 単体テストの観点

テストのファイルは `tests/core/converge/test_converge.py`。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `review_seats` | 既定・2 者の名指し | `allowed` の相手役が 1・0、同じ名前の名指しの重なり | 修正担当が `allowed` の外で `FixerNotAllowed`、`--reviewer claude` で `UnknownParticipant` |
| `assign` | 既存のテストが通る | — | 選べる相手役 0 で今のとおり `UnknownParticipant` |
| `runtime_of` | `codex` → `codex`、`claude-2` → `claude` | `claude` → `claude` | — |
| `free_seat` | 空 → `claude` | `claude` を使用中 → `claude-2` | — |
| `fill_targets` | 処理の手順の表の各行 | 結果の無い席が 0 | — |

## 断られた担当を確かめる（participants.py）

満たす決定: 決定 6、受け入れ条件 15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `refusals` | `core/converge/participants.py` | 新設 | `refusals(runtimes: Sequence[str]) -> dict[str, str]` | 重なりを除いたランタイムごとに `training.ensure` を 1 回呼び、断られたランタイムから理由への対応を返す |
| `unready` | `core/converge/participants.py` | 変更（中身だけ） | 今のまま | `list(refusals(runtimes).values())` を返す。cross-refactoring の呼び出しと返り値は変えない |

- `refusals`
  - 事前条件: 呼び手は席の名前を `runtime_of` でランタイムの名前へ直して渡す
  - 事後条件: 断られなかったランタイムは含まない。並びは渡した順
  - 副作用: `training.ensure` が学習の設定のバックアップを確かめ直して記録する（今の `unready` と同じ）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `refusals` | 担当が断られる | 学習の設定が確かめられない | `training.ensure` が理由を返す | 理由を対応に入れて呼び手へ返す | 呼び手（`drive._fill_refused`）が決める | 後始末は無い |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `refusals` | `training.ensure` を差し替え、kiro だけ断ると `{"kiro": <理由>}` | `claude`・`claude`・`codex` を渡すと claude を 1 回だけ確かめる | — |
| `unready` | 同じ入力で今と同じ理由の並び | すべて通ると空 | — |

## claude の読むだけの起動（participants.py・adapters/claude.py・crossrefactor/launch.py）

満たす決定: 決定 2、受け入れ条件 7・8・13

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `read_only_launch` | `core/converge/participants.py` | 新設（`launch._read_only` の claude の分岐から移す） | `read_only_launch(system: str, workdir: Path, prompt: str, stem: Path, timeout: float, readable: Sequence[Path] = ()) -> agents.Launch` | `READ_TOOLS` だけを許した最小構成の `claude -p` の 1 回の起動。`agent` は `FIXER` |
| `answer_text` | `core/converge/participants.py` | 新設（`launch._answer_text` から移す） | `answer_text(runtime: str, out: Path) -> str` | claude なら出力の外側の JSON の `result`、ほかは `agents.tail(out)` |
| `ClaudeCall` | `adapters/claude.py` | 変更 | フィールド `readable: tuple[Path, ...] = ()` を足す | 読める場所の一覧。空でなければ権限 `TOOLS` のときだけ受ける |
| `permission_args` | `adapters/claude.py` | 変更 | `permission_args(permission: Permission, tools: Optional[str] = None, readable: Sequence[Path] = ()) -> list[str]` | `readable` が空なら今と同じ。空でなければ `--tools`・`--allowed-tools` の後に `--restricted`・`--add-dir <場所>`（場所ごと）・`--permission-mode dontAsk` を並べ、`acceptEdits` を置かない |
| `claude_launch` | `adapters/claude.py` | 変更（渡す値だけ） | 今のまま | `permission_args` に `call.readable` を渡す |
| `_read_only` | `crossrefactor/launch.py` | 変更（呼ぶ先だけ） | 今のまま | claude の分岐で `participants.read_only_launch` を呼ぶ。`readable` を渡さない |
| `_collect_stdout` | `crossrefactor/launch.py` | 変更（呼ぶ先だけ） | 今のまま | `participants.answer_text(req.runtime, out)` を呼ぶ |

- `permission_args`
  - 事後条件: `readable` が空でないとき、引数は次の順である

    ```text
    ["--tools", t, "--allowed-tools", t, "--restricted", "--add-dir", p1, …, "--permission-mode", "dontAsk"]
    ```

    `--allowed-tools` と `--add-dir` は複数の値を取る引数である。そこで、値を 1 つだけ取る `--permission-mode` を最後に置く。後ろに続く依頼文（`Launch` の最後の引数）が、`--add-dir` の値として読まれないようにするためである
- `read_only_launch`
  - 副作用: 無い（起動の形を作るだけ。起動は `agents.run_agent`）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `ClaudeCall` | 読める場所を権限 `TOOLS` 以外で渡す | 呼び手の誤り | `__post_init__` | `ValueError` を投げる | テストで落ちる（利用者には出ない） | — |
| `answer_text` | claude の出力の外側が JSON でない | 途中で切れた出力 | `json.loads` | 出力をそのまま返す（今と同じ） | 呼び手が `findings` を探し、無ければ結果が無いとして扱う | — |
| `answer_text` | 出力のファイルが無い | 起動の失敗 | `read_text` の `OSError` | 呼び手へ返す（今と同じ。cross-refactoring は `is_file` で先に確かめる。cross-review は `_reviewer_findings` が受ける） | — | — |
| 席の claude の起動 | claude が `--restricted` の下限より古い | 2.1.316 より前 | `agents.run_agent` の `cli_floor.require`（起動の引数の `--restricted` で `flag_floors` の下限を引く） | 起動せずに `refused` の状態を返す | 駆動がその席を結果が無いとして扱う。修正担当とほかの席の起動は今の下限で確かめるので断られない | claude を更新して打ち直す |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `read_only_launch` | `crossrefactor/launch.py` の `_read_only` | `grep -rn "Permission.TOOLS" src/ndf` | 移して共有する（決定 2） |
| `answer_text` | `crossrefactor/launch.py` の `_answer_text` | `grep -rn "_answer_text" src/ndf` | 移して共有する |
| `permission_args` の読める場所 | `adapters/claude.py` の `ACCEPT_EDITS` | `adapters/claude.py` を読んだ | 権限の引数は今のとおり `permission_args` の 1 か所で組む |

### 単体テストの観点

テストのファイルは次の 3 つである。

- `tests/adapters/test_claude_args.py`
- `tests/domains/crossrefactor/test_launch_and_phase.py`
- `tests/core/converge/test_converge.py`

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `permission_args` | 読める場所 1 つで、`--restricted`・`--add-dir <場所>`・`--permission-mode dontAsk` があり `acceptEdits` が無い。`--tools` と `--allowed-tools` の値が Tool の名前だけ | 読める場所が空なら今と同じ引数。最後の 2 つが `--permission-mode dontAsk` | — |
| `ClaudeCall` | — | — | 権限 `JUDGE` で読める場所を渡すと `ValueError` |
| `read_only_launch` | `agent` が `claude`、会話の指定が無く `--no-session-persistence` がある | 読める場所を渡さないと cross-refactoring の今の引数と同じ | — |
| `answer_text` | claude の外側の JSON の `result` | codex は `agents.tail` | 外側が JSON でなければそのまま |
| `_read_only`・`_collect_stdout` | 既存の cross-refactoring のテストが通る | — | — |

## 埋めた席の記録（scope_decide.py）

満たす決定: 決定 5、受け入れ条件 3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Filled` | `domains/crossreview/scope_decide.py` | 新設 | `class Filled(NamedTuple): seat: str; absent: Optional[str]; reason: str` と `record() -> dict[str, Any]` | 埋めた席 1 件。`record()` は `{"seat", "for", "reason"}` の形（`absent` を `for` に書く） |
| `filled_of` | `domains/crossreview/scope_decide.py` | 新設 | `filled_of(record: Mapping[str, Any]) -> list[Filled]` | ラウンドの記録の `filled` を読む。形の違う要素は飛ばす |
| `stand_ins` | `domains/crossreview/scope_decide.py` | 新設 | `stand_ins(record: Mapping[str, Any]) -> list[frozenset[str]]` | ラウンドの `reviewers` の席ごとに、割り当ててよい名前の候補（`filled` に載り `for` があれば席の名前とその相手役の 2 つ、ほかは席の名前だけ） |
| `assignable` | `domains/crossreview/scope_decide.py` | 新設 | `assignable(seats: Collection[str], options: Sequence[Collection[str]]) -> bool` | `seats` の全員を、名前を候補に含む `options` の別々の要素へ割り当てられるか（2 部グラフの最大マッチング。増加路を探す数行の処理） |
| `Basis` | `domains/crossreview/scope_decide.py` | 変更 | フィールド `stand_in: bool = False` を足す | 真なら、前のラウンドの席を `stand_ins` の候補で今回の席と照合する |
| `read_by_all_head` | `domains/crossreview/scope_decide.py` | 変更 | 今のまま | `basis.stand_in` が真なら `assignable(basis.reviewers, stand_ins(r))`、偽なら今のとおり `set(reviewers)` で包含を見る |

- `stand_ins`
  - 事後条件: 返す並びの長さは `reviewers` と同じである。要素は 1 つか 2 つの名前を持つ
- `assignable`
  - 事後条件: 真なら、`options` の 1 つの要素に今回の席を 2 つ以上割り当てていない（設計文書の「前回の 1 つの席で今回の 2 つの席を満たさない」）。`seats` が `options` の数より多ければ偽

`stand_in` を真にするのは `drive._reusable` だけで、`may_fill` が真のときである。差分の起点を決める `round_scope.previous_head` と CLI の `review-scope` は `stand_in` を渡さず、今のとおり席の名前で比べる。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `FILLED` | 定数（ラウンドの記録のキー） | `domains/crossreview/scope_decide.py` | `"filled"` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `Filled`・`filled_of`・`stand_ins` | `scope_decide` の `REVIEWERS`・`store.list_of` | `scope_decide.py` を読んだ | 記録のキーを同じモジュールに置き、配列の読み出しに `store.list_of` を使う |
| `assignable` | 標準ライブラリと依存のライブラリのマッチング | `pyproject.toml` の依存を読んだ | 依存にグラフのライブラリは無い。席は 2 つほどで、増加路の探索を数行で書く。マッチングのためだけに依存を足さない |

### 単体テストの観点

テストのファイルは `tests/domains/crossreview/test_crossreview_scope.py`。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `Filled.record`・`filled_of` | 往復で同じ値 | `for` が null | `filled` が配列でない・要素が辞書でない・`seat` が無い要素を飛ばす |
| `stand_ins` | `codex` と `{"seat": "claude", "for": "kiro"}` → `{codex}`・`{claude, kiro}` | `for` が null の席はその席の名前だけ | — |
| `assignable` | 今回 `codex`・`kiro`、候補 `{codex}`・`{claude, kiro}` で真。今回 `codex`・`claude`、同じ候補で真 | 今回の席が候補の数より多いと偽 | 今回 `codex`・`claude`、候補 `{kiro}`・`{claude, codex}` で偽（1 つの候補を 2 度使わない） |
| `read_by_all_head` | `stand_in` 真で、前回 `codex`・`claude（kiro の代わり）` を、今回 `codex`・`kiro` も今回 `codex`・`claude` も読んだとみなす | `stand_in` 偽なら今と同じ結果 | 前回 `kiro`・`claude（codex の代わり）`、今回 `codex`・`claude` で None |

## 駆動が今の席を持ち、空いた席を埋める（drive.py）

満たす決定: 決定 2・3・4・5・6、受け入れ条件 1・2・3・4・9・10・11・15、不変条件 I1〜I5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `RunConfig` | `domains/crossreview/drive.py` | 変更 | フィールド `may_fill: bool = True`・`prefilled: tuple[scope_decide.Filled, ...] = ()` を足す | `may_fill` は、名指しが 1 者でないか。`prefilled` は、`allowed` のために起動の前に埋めた席。`reviewers` は起動の前に決めた席（`Seats.names`）で、書き換えない |
| `_Run` | `domains/crossreview/drive.py` | 変更 | フィールド `seats: list[str]`・`filled: list[scope_decide.Filled]`・`input_dir: Optional[Path]` を足す | 今の席と、この実行で埋めた席と、この実行の資料のディレクトリ。`_start` が `cfg.reviewers`・`cfg.prefilled` で始め、`input_dir` は None で始める |
| `_Unread` | `domains/crossreview/drive.py` | 新設 | `class _Unread(NamedTuple): hint: str; cause: str` | 結果の無い席 1 つの直し方（`precondition` の詳細の行）と理由（`filled` の `reason`） |
| `_Reviewed` | `domains/crossreview/drive.py` | 新設 | `class _Reviewed(NamedTuple): raised: list[dict[str, Any]]; unread: dict[str, _Unread]; silent: bool` | 1 ラウンドのレビューの結果。`silent` は全員が結果を返さなかったか |
| `_input_path` | `domains/crossreview/drive.py` | 新設 | `_input_path(run: _Run, round_no: int, suffix: str) -> Path` | 相手役へ渡す資料のパス（`run.input_dir / f"round{round_no}{suffix}"`）。`run.input_dir` が None なら、`tempfile.mkdtemp(prefix=INPUT_PREFIX, dir=run.cfg.state_dir)` で新しいディレクトリを作って持つ。既存のディレクトリを探さず、再利用しない |
| `_drop_inputs` | `domains/crossreview/drive.py` | 新設 | `_drop_inputs(run: _Run) -> None` | `run.input_dir` があれば消す（`shutil.rmtree(…, ignore_errors=True)`）。`drive` の終わりに、止まったときも含めて呼ぶ |
| `_write_diff`・`_remarks_file` | `domains/crossreview/drive.py` | 変更（書き先だけ） | 今のまま | `_round_stem` でなく `_input_path` に書く |
| `_seat_launch` | `domains/crossreview/drive.py` | 新設 | `_seat_launch(run: _Run, seat: str, prompt: str, stem: Path) -> agents.Launch` | 席の起動。claude の席は `participants.read_only_launch`（読める場所は `run.input_dir` だけ、上限は `REVIEW_TIMEOUT`）、ほかは `participants.review_launch(runtime_of(seat), …)` |
| `_reviewer_findings` | `domains/crossreview/drive.py` | 変更 | `_reviewer_findings(status: agents.AgentStatus, out: Path, runtime: str) -> Optional[list[dict[str, Any]]]` | claude の席は `participants.answer_text` の本文から、ほかは今のとおり出力の全文から `findings` を取り出す |
| `_unread_hint` | `domains/crossreview/drive.py` | 変更（責務） | 今のまま（1 つ目の引数を席の名前として受ける） | 表の行を `runtime_of(seat)` で引き、行の頭には席の名前を書く |
| `_launch_reviewers` | `domains/crossreview/drive.py` | 変更 | `_launch_reviewers(run: _Run, round_no: int, prompts: Mapping[str, str], launch: Launcher, suffix: str = "") -> tuple[list[dict[str, Any]], dict[str, _Unread]]` | 席ごとの依頼文で並べて起動する。出力の名前・指摘の担当（`by`）・注意・過程には席の名前を使う |
| `_review` | `domains/crossreview/drive.py` | 変更 | `_review(run: _Run, round_no: int, prep: _Prepared, remarks: Optional[Path], launch: Launcher) -> _Reviewed` | 今の席を起動し、claude 以外の結果の無い席だけを 1 度起動し直し、`fill_targets` が返す席を `_fill` で埋める |
| `_fill` | `domains/crossreview/drive.py` | 新設 | `_fill(run: _Run, round_no: int, prep: _Prepared, remarks: Optional[Path], targets: Mapping[str, _Unread], launch: Launcher) -> tuple[list[dict[str, Any]], dict[str, _Unread]]` | 席ごとに `free_seat` の名前で今の席を置き換え、`filled` と注意を足し、全差分の依頼文で claude を 1 回ずつ並べて起動する |
| `_fill_refused` | `domains/crossreview/drive.py` | 新設 | `_fill_refused(run: _Run) -> None` | 始める前の確かめ。`refusals` で断られた claude 以外の席を claude で埋めるか、`precondition` で止める（`drive` の `unready` の呼び出しを置き換える） |
| `_filled_warning` | `domains/crossreview/drive.py` | 新設 | `_filled_warning(f: scope_decide.Filled, when: str) -> str` | 席を埋めた注意の文。`when` は `round <n>`・`起動の前`・`前回の round <n>` |
| `_reused_warnings` | `domains/crossreview/drive.py` | 新設 | `_reused_warnings(run: _Run) -> None` | 前回の結果を返すときに、最後のラウンドの `filled` から注意を作り直す |
| `_prepare_round` | `domains/crossreview/drive.py` | 変更（読む値だけ） | 今のまま | `Basis` に `cfg.reviewers` でなく `run.seats` を渡す |
| `_round` | `domains/crossreview/drive.py` | 変更 | 今のまま | `_review` の `silent` で `precondition`、`unread` が残れば `incomplete_reviewers`。記録の `reviewers` に `run.seats`、`run.filled` が空でなければ `filled` を書く |
| `_reusable` | `domains/crossreview/drive.py` | 変更 | 今のまま | `Basis(…, run.seats, focus, stand_in=cfg.may_fill)` で比べる |
| `drive` | `domains/crossreview/drive.py` | 変更 | 今のまま | 再利用なら `_reused_warnings` の後に返す。返さないなら `cfg.prefilled` の注意を足し、`_fill_refused` の後にラウンドへ進む。終わるとき（止まったときを含む）に `_drop_inputs` を呼ぶ |

- `_review`
  - 事後条件: 起動し直したのは claude 以外の席だけである。1 つの席の起動は 1 ラウンドに起動し直しを含めて 2 回まで、claude の席は 1 回である。`silent` が真なら席を置き換えていない
- `_fill`
  - 事前条件: `targets` は `fill_targets` が返した席と、その起動し直しの理由
  - 事後条件: `run.seats` の欠けた席の位置に埋めた名前が入り、`run.filled` に `Filled(席, 欠けた相手役, 理由)` が足されている。ラウンドの範囲（`prep.scope`）は変えていない
  - 副作用: 前の head からの差分のラウンドなら、`_input_path(run, round_no, "-full.diff")` に `--base` からの全差分を書く。claude を起動する
  - 依頼文: `_review_prompt` に次の `prep` を渡して組み立てる。置き換えた `prep` はこの依頼文にだけ使い、記録には使わない。

    ```python
    prep._replace(scope=Scope(FULL, None, prep.scope.unreviewed), diff=<全差分>, previous=None)
    ```

    埋めた claude はどれもこの実行で初めて加わる担当なので、いつもこの形にする
- `_fill_refused`
  - 事後条件: 断られた担当が無ければ何もしない。修正担当が断られた、または `may_fill` が偽で相手役が断られたなら、どの担当も起動せずに `DriveStopped("precondition", "担当を起動できない: …")`（今と同じ文）。ほかは断られた席ごとに `Filled(席, 断られた相手役, "unready: " + 理由)` を足して席を置き換える
  - 副作用: `participants.refusals` が学習の設定を確かめる

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_round` | 全員が結果を返さない | 全員の起動の失敗か出力の形の誤り | `_Reviewed.silent` | `DriveStopped("precondition")`。そのラウンドを状態ファイルに書かない（I3） | 結果 JSON が `precondition`、詳細に席ごとの直し方 | 直して打ち直せば同じ状態ファイルから続く |
| `_round` | 結果の無い席が残る | claude の席が結果を返さない・埋めた claude が結果を返さない | `_Reviewed.unread` が空でない | 判定を `incomplete_reviewers` にして止める（I2・前提 2） | 結果 JSON が `incomplete_reviewers`、注意に席と理由 | 打ち直せば同じ状態ファイルから続く。読めた席の指摘はジャーナルに残る |
| `_fill_refused` | 修正担当が断られる | claude の学習の設定 | `refusals` の結果に `cfg.fixer` | `DriveStopped("precondition")` | 今と同じ | 設定を直して打ち直す |
| `_fill_refused` | 名指し 1 者が断られる | その相手役の学習の設定 | `may_fill` が偽 | `DriveStopped("precondition")`（決定 6） | 今と同じ | 同上 |
| `_reviewer_findings` | 出力を読めない | 出力のファイルが無い | `OSError` | None を返す（今と同じ） | 結果が無い席として注意に載る | — |
| `_input_path` | 資料のディレクトリを作れない | 保存先に書けない | `mkdtemp` の `OSError` | 呼び出し元へ返す（今の `_write_diff` の書き込みの失敗と同じ扱い） | トレースバック（今の差分のファイルの書き込みの失敗と同じ） | 保存先を直して打ち直す |
| `_drop_inputs` | 資料のディレクトリを消せない | 保存先の権限の変化など | `rmtree` の `ignore_errors` | 何もしない。実行の結果は変えない | 無し | 消し残しは次の実行の `--add-dir` に渡らない（次の実行は新しいディレクトリを作る） |

### 処理の手順

`_review` の分岐は設計文書の「1 ラウンドの流れ」の表のとおりで、関数への割り当ては次のとおりである。

| 起動し直した後の状態 | 決める関数 | 結果 |
| --- | --- | --- |
| 結果の無い席が 0 | `fill_targets`（空） | `_Reviewed(raised, {}, False)` |
| 全員が結果を返さない | `_review`（`len(unread) == len(run.seats)`） | `_Reviewed(raised, unread, True)` |
| 結果の無い席に claude の席がある | `fill_targets`（空） | `_Reviewed(raised, unread, False)` |
| 結果の無い席がどれも claude 以外 | `fill_targets` → `_fill` | `_fill` の後の結果の無い席（埋めた claude のうち結果を返さないもの）を `unread` にする |

始める前の確かめは `drive` の中で次の順に行う。順は今のままで、再利用の判定を先にする（受け入れ条件 15 の (f)）。

1. `_start` が `run.seats = list(cfg.reviewers)`、`run.filled = list(cfg.prefilled)` で始める
2. `_reusable` が真なら `_reused_warnings` で注意を作り直して返す
3. `cfg.prefilled` ごとに `_filled_warning(f, "起動の前")` を注意へ足す
4. `_fill_refused` を呼ぶ
5. ラウンドを回す
6. 終わるとき（止まったときを含む）に `_drop_inputs` で資料のディレクトリを消す

- 繰り返しの終わる条件: `_fill` は `targets` の席ごとに 1 回だけ起動し、結果を返さなくても繰り返さない
- 計算量の注意: 1 ラウンドの起動は、席の数 + 起動し直し（claude 以外の結果の無い席）+ 埋めた席の数で、席が 2 つなら最大 4 回である

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `INPUT_PREFIX` | 定数 | `domains/crossreview/drive.py` | `"review-input-"`（実行ごとの資料のディレクトリの名前の頭。後ろは `mkdtemp` が付けるランダムな文字列） |
| 席の claude の system の文 | 定数 `SEAT_SYSTEM` | `domains/crossreview/drive.py` | `"cross-review の相手役（読むだけ）"` |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じラウンドに並べて起動した席 | `run.input_dir` の資料（読むだけ）と、席ごとの出力のファイル | 出力のファイルの名前を席の名前で分ける（`round<n>-<席>`・`round<n>-<席>-retry`）。`claude` と `claude-2` が重ならないのは決定 3 による | 埋めた後に止まっても、状態ファイルに書くのはラウンドの記録を足すときだけなので、打ち直せば席を決め直す |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_fill`・`_fill_refused`・`drive` | 席を埋めた注意（`_filled_warning`）。欠けた相手役・理由・埋めた席 | 結果 JSON の `warnings` | 差分や依頼文の本文 |
| `_round` | ラウンドの記録の `reviewers`（今の席）・`filled`（`Filled.record()` の並び） | 状態ファイル | — |
| `_launch_reviewers` | 席ごとの過程の行（`round <n> <席>`） | 結果 JSON の過程 | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_input_path` | `_round_stem`・`tempfile.mkdtemp` | `grep -n "_round_stem" drive.py`・Python の `tempfile` の説明 | 名前の作り方は同じにし、ディレクトリだけ分ける。ディレクトリは標準ライブラリの `mkdtemp` で作り、名前の重なりの扱いを自前で書かない。出力・修正担当の結果・ログは今のとおり `_round_stem`（保存先の直下）に置く |
| `_seat_launch` | `participants.review_launch`・`read_only_launch` | `participants.py` を読んだ | 2 つを席の種類で呼び分けるだけにする |
| `_fill` | `_launch_reviewers`・`_review_prompt`・`_write_diff` | `drive.py` を読んだ | 起動・依頼文・全差分の書き出しはどれも使う |
| `_fill_refused` | `participants.unready` | 同上 | どのランタイムが断られたかが分からないので `refusals` を使う |
| `_filled_warning`・`_reused_warnings` | `_note` | 同上 | `_note` は過程の行も足すので、注意だけを足す `run.warnings.append` を使う |
| `_Unread`・`_Reviewed` | 今の `dict[str, str]` の返り値 | 同上 | 理由（`cause`）が直し方の文に埋もれて取り出せないので型を足す |

### 単体テストの観点

テストのファイルは `tests/domains/crossreview/test_crossreview_run.py`。相手役の起動は偽物にし、席ごとに回答の有無を決める（設計文書の「テスト設計」）。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_review`・`_fill` | kiro が起動し直しても結果を返さず、claude で 1 回埋め、codex と claude の指摘で判定する | round 2 で初めて埋めた claude に全差分と、前回の情報を含まない依頼文が渡る。記録の `scope` は `since` のまま | 埋めた claude も結果を返さず `incomplete_reviewers`、claude の起動は 1 回 |
| `_round` | 記録の `reviewers` が `codex`・`claude`、`filled` が 1 件 | 全員が結果を返すと `filled` が無い | 全員が結果を返さず `precondition`、claude の席だけが結果を返さず `incomplete_reviewers` |
| `_fill_refused` | kiro を断ると席が `codex`・`claude`、`reason` が `unready: ` で始まる | 両方断ると `claude`・`claude-2` | 修正担当を断ると `precondition`、`--reviewer kiro` で kiro を断ると `precondition` |
| `_reusable`・`_reused_warnings` | 埋めた席で収束した同じ head の打ち直しで前回の結果を返し、注意が載る。前回 `codex`・`claude（kiro の代わり）` で、`allowed` を claude と codex に絞った打ち直しでも前回の結果を返す | 前回 `kiro`・`claude（codex の代わり）` で、`allowed` を claude と codex に変えた打ち直しで前回の結果を返さない | 名指し 1 者の打ち直しで代わりに数えない |
| `_seat_launch` | `claude-2` の起動の `agent` が `claude`、出力の名前が `round<n>-claude-2` | 依頼文が指すファイルがすべて `run.input_dir` の下 | — |
| `_input_path`・`_drop_inputs` | 1 回の実行の資料が 1 つのディレクトリにそろい、`--add-dir` がそのディレクトリを指す | 同じ保存先に前の実行の `review-input-*` のディレクトリ（別の PR の差分のファイル入り）と、`review-input-` で始まるシンボリックリンクを置いてから実行すると、`--add-dir` はどちらとも違う新しいディレクトリを指し、そこに前の実行のファイルが無い | 実行が `precondition` で止まっても、資料のディレクトリが消えている |
| `_reviewer_findings` | `{"findings": []}` は成功 | — | 終了コード 0 でも `findings` が無ければ結果が無い |

## 記録のコメントの「相手役」の列（report_decide.py）

満たす決定: 決定 7、受け入れ条件 3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_seats_cell` | `domains/crossreview/report_decide.py` | 新設 | `_seats_cell(r: Mapping[str, Any]) -> str` | ラウンドの `reviewers` を `・` で並べ、`filled` に `for` がある席は `（<相手役> の席）` を添える。`reviewers` が無い記録は `—` |
| `_round_rows` | `domains/crossreview/report_decide.py` | 変更（責務） | 今のまま | 「ラウンド」の次に「相手役」の列を足す |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_seats_cell` | `scope_decide.filled_of`・`_cell` | `report_decide.py` を読んだ | `filled_of` で読み、表のセルの逃がしは `_cell` を使う |

### 単体テストの観点

テストのファイルは `tests/domains/crossreview/test_crossreview_report.py`。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_seats_cell` | `codex・claude（kiro の席）` | `for` が null の席は名前だけ | `reviewers` の無い古い記録で `—` |
| `_round_rows` | 列の数が見出しと行でそろう | — | — |

## CLI が席を決める（cli.py）

満たす決定: 決定 1・6、受け入れ条件 5・6・12

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_reviewers` | `domains/crossreview/cli.py` | 変更 | `_reviewers(requested: Optional[list[str]], policy: Policy) -> participants.Seats` | `participants.review_seats` で席を決める（`run` と `review-scope` が共有する） |
| `Resolved` | `domains/crossreview/cli.py` | 変更 | フィールド `reviewers` を `seats: participants.Seats` に替える | 実行者の設定から決まる席 |
| `_run_config` | `domains/crossreview/cli.py` | 変更（渡す値だけ） | 今のまま | `reviewers=seats.names`・`may_fill=seats.may_fill`・`prefilled=tuple(Filled(s, None, seats.reason) for s in seats.added)` を渡す |
| `cmd_scope` | `domains/crossreview/cli.py` | 変更（読む値だけ） | 今のまま | `Basis` に `resolved.seats.names` を渡す |
| `_state_parsers` | `domains/crossreview/cli.py` | 変更（help だけ） | 今のまま | `review-scope` の help に「範囲は起動の前に分かる席で決め、始める前の確かめやラウンドの中で埋める席は含まない」を書く |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_reviewers` | 名指しの誤り | `UnknownParticipant`（`ValueError`） | `review_seats` | 今のとおり `cmd_scope`・`cmd_run` が受ける | `invalid` | 名指しを直して打ち直す |
| `_reviewers` | 修正担当が `allowed` の外 | `FixerNotAllowed` | `review_seats` | 同上 | `precondition` | `allowed` を直して打ち直す |

### 単体テストの観点

テストのファイルは `tests/domains/crossreview/test_crossreview_run.py`。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_run_config`・`cmd_scope` | `allowed` が claude だけで、`run` と `review-scope` がどちらも `claude`・`claude-2` で範囲を決める | `allowed` が claude と codex で `prefilled` の `reason` が `allowed の相手役 1（codex）` | `--reviewer claude` で `invalid` |

## 下限の表と手順書（cli-floors.json・cli_floor.py・agents.py・SKILL.md・docs）

満たす決定: 決定 2、受け入れ条件 14

変える関数は `cli_floor.require` と `agents._guarded_run` の 2 つで、中身は地図の表の行にある。

- `cli-floors.json` に `flag_floors` を足した後は、`agents.run_agent` の `cli_floor.require` が、`--restricted` を付けた 2.1.315 以前の claude の起動だけを起動せずに `refused` で返す。`--restricted` を付けない起動は、今のとおり `min_version`（2.1.268）と比べる
- テストは 2 か所に置く。どちらもバージョンの検査を差し替え、子のプロセスを起動しない（設計文書のテスト設計の「claude の下限」）
  - `tests/adapters/test_cli_floor.py`: 2.1.315 のとき、`args` に `--restricted` があれば `TooOld`、無ければ通る。2.1.316 ならどちらも通る。2.1.267 ならどちらも `TooOld`
  - `tests/domains/crossreview/test_crossreview_run.py`: 2.1.315 で、席を埋めない実行（codex と kiro が両方結果を返す）がレビューと修正担当の起動まで今と同じに進む。kiro の席を埋める実行では、席の claude だけが断られて結果の無い席になる
- 手順書と確定仕様の文言は `.md` を照合するテストで縛らない（AGENTS.md の DON'T）。設計 PR と実装の PR のレビューで確かめる
