# #3597 の詳細設計: 確定の後の承認ゲートの依頼・同じ問いの答えの引き継ぎ・一時レーンの空き待ち

設計文書 [issue-3597-design.md](issue-3597-design.md) の「構成要素」と「処理の流れ」を、ファイルと関数の粒度へ下ろした文書である。語は設計文書のドメインモデルの「用語」の表と用語集（`docs/glossary.md`）に合わせる。決定の番号（決定 1 など）は [issue-3597-design-decisions.md](issue-3597-design-decisions.md) を指す。

この文書で使う語:

| 語 | 意味 |
| --- | --- |
| 送った結果 | 依頼を台帳へ書いたか・同じキーの行があったか・中身が同じか・答え済みかをまとめた値（`parallel_ledger.Sent`） |
| 同じ問いの結果 | 同じ一時レーンの名前・区分・問いの対象の依頼が台帳にあったときの、その依頼の id と、答え済みか答え待ちか（`parallel_question.Same`） |
| 起動の記録 | 耐久ワークフローの `launches`（起動の目的 → 結果）。設計文書の集約「起動の記録」 |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/limits.json
├── scripts/
│   ├── parallel.py
│   ├── hook_lib/ask_guard.py
│   ├── relay_lib/hub.py
│   ├── lib/
│   │   ├── parallel_answer.py
│   │   ├── parallel_args.py
│   │   ├── parallel_gate2.py *
│   │   ├── parallel_helper.py
│   │   ├── parallel_lane.py
│   │   ├── parallel_launch.py
│   │   ├── parallel_ledger.py
│   │   ├── parallel_question.py
│   │   └── parallel_run.py
│   └── tests/
│       ├── test_ask_guard.py
│       ├── test_parallel_lane.py
│       ├── test_parallel_launch.py
│       ├── test_parallel_ledger.py
│       ├── test_parallel_run.py
│       └── test_relay_hub.py
└── skills/development-workflow/references/
    ├── asking.md
    └── parallel.md
scripts/script-structure-allow/plugins__ndf__scripts__lib__parallel_run.py--lines.json
```

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/scripts/lib/parallel_ledger.py` | 変更 | 送った結果の型と、同じキーの行の中身の判定を持ち、処理済みのリリース承認ゲートの判定を確定の後の依頼に合わせ、行の種類 `reused` を知る | `parallel_plan`・`locks`・`limits` |
| `plugins/ndf/scripts/lib/parallel_question.py` | 変更 | 問いの対象の組み立て・同じ問いの判定・一時レーンの依頼と答えの一覧を、台帳の行から引く | 無し |
| `plugins/ndf/scripts/lib/parallel_lane.py` | 変更 | 台帳へ書いた結果を送り手へ返し、区分つきの問いで同じ問いを先に引く。リリース承認ゲートの依頼の組み立てを外へ移す | `parallel_ledger`・`parallel_question`・`parallel_external` |
| `plugins/ndf/scripts/lib/parallel_gate2.py` | 新設 | 提示と確定の後のリリース承認ゲートの依頼を組んで送り、確定の後の依頼への答えを耐久ワークフローで当てる | `parallel_lane`・`parallel_ledger`・`parallel_helper`・`parallel_release`・`durable`・`gh_call`・`step_read` |
| `plugins/ndf/scripts/lib/parallel_helper.py` | 変更 | 最後の起動の指示を読む関数を切り出し、一時レーンの流すものに確定の後の依頼の 1 行と自分の依頼と答え（`asked`）を足す | `parallel_lane`・`parallel_ledger`・`parallel_question`・`durable` |
| `plugins/ndf/scripts/lib/parallel_launch.py` | 変更 | 一時レーンの同時数の断りを空き待ちとして起動の記録に持ち、ループの 1 回ごとに読み直す | `durable`・`limits`・`clock`・`parallel_release` |
| `plugins/ndf/scripts/lib/parallel_run.py` | 変更 | 確定の後の依頼への答えの部品を継ぎ、観測の後に空き待ちを読み直す | `parallel_gate2`・`parallel_launch` ほか今の依存 |
| `plugins/ndf/scripts/lib/parallel_answer.py` | 変更 | 答えの行が既にあったことを、答えのコマンドの出力へ渡す | `parallel_lane` |
| `plugins/ndf/scripts/lib/parallel_args.py` | 変更 | `request-gate2` に `--confirmed` と `--done` を足す | 無し |
| `plugins/ndf/scripts/parallel.py` | 変更 | 依頼のコマンドが送った結果から終了コードと出力を決める | `parallel_lane`・`parallel_ledger`・`parallel_gate2`・`parallel_helper` |
| `plugins/ndf/scripts/hook_lib/ask_guard.py` | 変更 | `request-ask` が引き継いだ答えを拒否の理由に載せて返す | `parallel.py`（子プロセス） |
| `plugins/ndf/scripts/relay_lib/hub.py` | 変更 | 一時レーンの同時数の断りを結果の値 `helper-busy` と動いている一時レーンの名前で返す | `relay_lib` の今の依存 |
| `plugins/ndf/contracts/limits.json` | 変更 | 空き待ちの上限 `parallel-launch.helper-wait` を持つ | 無し |
| `scripts/script-structure-allow/plugins__ndf__scripts__lib__parallel_run.py--lines.json` | 変更 | `parallel_run.py` の行数の例外を 842 行にし、理由に #3597 を足す | 無し |
| `plugins/ndf/skills/development-workflow/references/parallel.md` | 変更 | 一時レーンの手順 1（`asked` を読む）・確定の後の承認ゲートの答え方・空き待ちを書く | 無し |
| `plugins/ndf/skills/development-workflow/references/asking.md` | 変更 | 一時レーンが問いの `metadata.target` にブランチ名を載せることと、答えを `asked` で読むことを書く | 無し |
| `plugins/ndf/scripts/tests/test_parallel_ledger.py` | 変更 | 送った結果の判定と処理済みのリリース承認ゲートの判定を縛る | `parallel_ledger` |
| `plugins/ndf/scripts/tests/test_parallel_lane.py` | 変更 | 依頼のコマンドの終了コード・同じ問いの引き継ぎ・確定の後の依頼の組み立てを縛る | `parallel.py`・`parallel_lane`・`parallel_gate2` |
| `plugins/ndf/scripts/tests/test_parallel_launch.py` | 変更 | 空き待ち・上限・ほかの断りの今の振る舞いを縛る | `parallel_launch` |
| `plugins/ndf/scripts/tests/test_parallel_run.py` | 変更 | 確定の後の依頼への答え（打ち直す・差し戻す）と途中停止の問いが立たないことを縛る | `parallel_run`・`parallel_gate2` |
| `plugins/ndf/scripts/tests/test_ask_guard.py` | 変更 | 引き継いだ答えと答え待ちの id が hook の拒否の理由に載ることを縛る | `hook_lib/ask_guard` |
| `plugins/ndf/scripts/tests/test_relay_hub.py` | 変更 | `helper-busy` と動いている名前の断りを縛る | `relay_lib/hub` |

**依存の向き。** 下の層から上の層へ一方向に読み、循環は無い。

| 層 | モジュール | 持つもの |
| ---: | --- | --- |
| 1 | `parallel_question` | 台帳の行だけを受け取る純粋な関数 |
| 2 | `parallel_ledger` | 台帳のファイル |
| 3 | `parallel_lane` | 台帳へ書いて耐久ワークフローへ知らせる |
| 4 | `parallel_helper`・`parallel_gate2` | 一時レーンの流すものと確定の後の依頼 |
| 5 | `parallel_launch`・`parallel_run` | 耐久ワークフロー |
| 6 | `parallel.py` | CLI |

- `parallel_gate2` は `parallel_lane`・`parallel_ledger`・`parallel_helper`・`parallel_release`・`durable`・`gh_call` を読む。`parallel_run` だけが `parallel_gate2` を読む
- `parallel_question` は `parallel_ledger` を読まない（行は呼ぶ側が渡す）
- `relay_lib/hub.py` は `lib/` の並列の計画のモジュールを読まない
- `parallel_launch` も `relay_lib` を読まない（断りの結果の欄 `busy` の有無だけで分ける）

**`parallel.py` は `parallel_gate2` を `cmd_request_gate2` の中で読む。** `parallel_gate2` は耐久の部品（DBOS）を読み込むため、問いの hook が打つ `request-ask` の起動に載せない。`parallel_helper` を `cmd_helper` の中で読む今の形と同じである。

**`parallel_run.py` の行数。** 今は行数の例外の 840 行ちょうどである。確定の後の依頼への答えの部品は `parallel_gate2.py` に置く。`parallel_run.py` に足すのは、import の 1 行と、観測の後に空き待ちを読み直す 1 行だけである。`apply_own` の呼び出しは、今の `apply_end` の条件の行へ `or` で足し、行を増やさない。842 行になるため、例外の行数を同じ変更で 842 にする。

**`parallel_lane.py` の行数。** 499 行から、`request_gate2` を移した分（18 行）が減り、送った結果を返す分と同じ問いを引く分（10 行前後）が増える。500 行を超えないよう、問いの対象の組み立ては `parallel_question.target_of` に置く。`parallel_lane` には、先端を求める git の 1 関数だけを残す。

置き換えの形（戻り値）が変わって落ちる既存のテストは、表のテストのファイルの中で形を合わせる。

## クラスと関数の構造

型の関係は設計文書の「構造」の節を指し、ここでは描き直さない（設計文書は構成要素の図と処理の流れの図を持つ）。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/parallel_ledger.py` | モジュール | `Sent`（新設の型）・`resent`（新設）・`settled_gates`（変更）・`KINDS`（`reused` を足す） |
| `plugins/ndf/scripts/lib/parallel_question.py` | モジュール | `Same`（新設の型）・`target_of`（新設）・`same_question`（新設）・`asked_of`（新設）。`cause_of` は変えない |
| `plugins/ndf/scripts/lib/parallel_lane.py` | モジュール | `Asked`（新設の型）・`deliver_message`（変更）・`send_inbox`（変更。`_send_inbox` を改名）・`send_release_request`（変更）・`send_answer`（変更）・`request_ask`（変更）・`_rev`（変更。`_head_sha` を改名）・`resume_plan`（戻り値だけ変える）。`request_gate2` は `parallel_gate2.py` へ移す |
| `plugins/ndf/scripts/lib/parallel_gate2.py` | モジュール | `NotConfirmed`（新設の例外）・`request_gate2`（移す）・`request_confirmed`（新設）・`gate_detail`（新設）・`step_close_release_pr`（新設の耐久ステップ） |
| `plugins/ndf/scripts/lib/parallel_gate2.py` | `ConfirmedGate`（新設。`_Run` が継ぐ） | `apply_confirmed`（新設） |
| `plugins/ndf/scripts/lib/parallel_helper.py` | モジュール | `last_launch`（新設）・`helper_task`（変更） |
| `plugins/ndf/scripts/lib/parallel_launch.py` | モジュール | `helper_wait_seconds`（新設）・`_hit_once`（変更）・`step_launch`（変更） |
| `plugins/ndf/scripts/lib/parallel_launch.py` | `LaunchRouting` | `launch_once`（変更）・`launch_waiting`（新設）・`_ask_launch`（新設。`launch_once` の問いの組み立てを切り出す） |
| `plugins/ndf/scripts/lib/parallel_run.py` | `_Run` | 継ぐ部品に `ConfirmedGate` を足す。`apply_own` は `apply_end` の条件の行で `apply_confirmed` も呼ぶ（責務と失敗の扱いは変えない） |
| `plugins/ndf/scripts/lib/parallel_run.py` | モジュール | `_observe_run` は最後に `run.launch_waiting(rows)` を呼ぶ（責務と失敗の扱いは変えない） |
| `plugins/ndf/scripts/lib/parallel_answer.py` | モジュール | `answer_with_merge`（変更） |
| `plugins/ndf/scripts/lib/parallel_args.py` | モジュール | `add_release_parsers` は `request-gate2` の `--report` を任意にし、`--confirmed`・`--done` を足す（責務は変えない） |
| `plugins/ndf/scripts/parallel.py` | モジュール | `_resend`（新設）・`cmd_request`・`cmd_request_ask`・`cmd_request_gate2`・`cmd_helper`・`cmd_answer`・`cmd_confirm`・`cmd_settle`・`cmd_stop`（変更） |
| `plugins/ndf/scripts/hook_lib/ask_guard.py` | モジュール | `reused_text`（新設）・`forward_to_organizer`（変更） |
| `plugins/ndf/scripts/relay_lib/hub.py` | モジュール | `HELPER_BUSY`（新設の定数）・`Live`（欄を足す）・`Refusal`（欄を足す）・`live`（変更）・`hub_result`（変更） |
| `plugins/ndf/scripts/relay_lib/hub.py` | `Hub` | `judge`（変更）・`refuse`（変更） |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `resent` | `plugins/ndf/scripts/lib/parallel_ledger.py` | 新設 | `resent(main: Path, name: str, key: str, msg: Optional[dict]) -> Sent` | 同じキーの行が既にあったときに、その行と送ろうとした中身が同じか、答え済みかを判定する | `append_ledger_row` が同じ `key` で `False` を返した直後である | `written` は `False`。`same` は `at` を除いた `msg`（伏せ字を当てた後）が一致すれば `True`。`answered` は同じ id の `answer` の行があるか `settled_gates` に入れば `True`。`existing` は同じキーの行の id | ファイル（台帳を読む） |
| `settled_gates` | `plugins/ndf/scripts/lib/parallel_ledger.py` | 変更 | `settled_gates(rows: list[dict], earlier: list[dict] = ()) -> set[str]` | 処理済みのリリース承認ゲートの id を返す | `rows` は `_pos` を持つ | `msg.after` が `confirmed` の `gate2` は、同じ id の `answer` の行か、それより後の同じ連番の締め（`phase: settle`）の行があるときだけ入る。確定（`phase: confirm`）の行では入らない。提示の `gate2` は今と同じ | 無し |
| `target_of` | `plugins/ndf/scripts/lib/parallel_question.py` | 新設 | `target_of(raw: object, resolve: Callable[[str], str]) -> str` | 問いの `metadata.target` から問いの対象の文字列を組む | `resolve` はブランチ名から先端のコミットの 40 桁を返し、求められなければ空文字を返す | `#<数字>` はそのまま返す。ほかの空でない文字列は `resolve` が 40 桁を返したときだけ `<ブランチ名>@<40 桁>` を返す。それ以外は空文字 | 無し |
| `same_question` | `plugins/ndf/scripts/lib/parallel_question.py` | 新設 | `same_question(rows: list[dict], lane: str, source: str, target: str) -> Optional[Same]` | 同じ一時レーンの名前・区分・問いの対象の最新の依頼と、その答えを台帳の行から引く | `rows` は最初の `opened` の行より後の行である。`target` は空でない | 一致する `ask` の行が無ければ `None`。あれば `Same(id, state, answer)` で、`state` は同じ id の `answer` の行があれば `answered`（`answer` に `result`・`choice`）、無ければ `pending` | 無し |
| `asked_of` | `plugins/ndf/scripts/lib/parallel_question.py` | 新設 | `asked_of(rows: list[dict], lane: str) -> list[dict]` | 一時レーンが送った依頼と、その答えの一覧を作る | `rows` は最初の `opened` の行より後の行である | 送り手が `lane` の `gate2`・`ask` の行ごとに `id`・`kind`・`source`・`target`・`at` と、答えの行があれば `answer`（`result`・`choice`）を、台帳の順で返す | 無し |
| `deliver_message` | `plugins/ndf/scripts/lib/parallel_lane.py` | 変更 | `deliver_message(main: Path, name: str, msg: dict, topic: str, key: str) -> plg.Sent` | 台帳へ書いた結果を返し、書いたか中身が同じときだけ耐久ワークフローへ知らせる | 今と同じ | 書いたときは `Sent(written=True, same=True)`。同じキーの行があれば `resent` の結果を返す。`same` が `False` なら知らせない | ファイル（台帳）・耐久の記録（知らせ） |
| `send_inbox` | `plugins/ndf/scripts/lib/parallel_lane.py` | 変更 | `send_inbox(main: Path, name: str, msg: dict, key: str) -> plg.Sent` | 話題 `inbox` へ送り、送った結果を返す（`_send_inbox` を改名し、`parallel_gate2` から呼べるようにする） | 今と同じ | `deliver_message` の結果を返す。外のオーガナイザーのレーンは今と同じく `ValueError` | ファイル（台帳）・耐久の記録（知らせ） |
| `send_release_request` | `plugins/ndf/scripts/lib/parallel_lane.py` | 変更 | `send_release_request(main, name, lane, prs, issues, state, verify_plan=None) -> plg.Sent` | リリースの依頼を送り、送った結果を返す | 今と同じ | `Sent.id` は依頼の id（今の戻り値） | ファイル（依頼の id のファイル・台帳）・耐久の記録（知らせ） |
| `send_answer` | `plugins/ndf/scripts/lib/parallel_lane.py` | 変更 | `send_answer(main, name, rid, result, note=None, choice=None, stamp=None, merge=None, by="user") -> plg.Sent` | 答えを送り、送った結果を返す | 今と同じ | 戻り値のほかは今と同じ | ファイル（台帳）・耐久の記録（知らせ） |
| `request_ask` | `plugins/ndf/scripts/lib/parallel_lane.py` | 変更 | `request_ask(main: Path, name: str, lane: str, tool_input: dict, cwd: str, ledger_id: str) -> Asked` | 区分つきの問いで同じ問いを先に引き、無ければ問いの対象を載せた依頼を送る | 今と同じ | 問いの対象が組めて同じ問いがあれば、依頼の行を足さずに `reused` の行を 1 行足し、`Asked(id=引き継いだ id, sent=None, same=Same)` を返す。無ければ `target` の欄（組めたときだけ）を載せた依頼を送り、`Asked(id, sent, None)` を返す | ファイル（台帳）・耐久の記録（知らせ）・外部コマンド（git） |
| `_rev` | `plugins/ndf/scripts/lib/parallel_lane.py` | 変更 | `_rev(cwd: str, ref: str = "HEAD") -> str` | 作業ディレクトリで `ref` のコミットの 40 桁を求める（`_head_sha` を `ref` を取る形へ広げる） | 無し | `git -C <cwd> rev-parse --verify --quiet <ref>^{commit}` が 0 で 40 桁を返せばそれを、ほかは空文字を返す | 外部コマンド（git） |
| `request_gate2` | `plugins/ndf/scripts/lib/parallel_gate2.py` | 変更 | `request_gate2(main: Path, name: str, n: int, k: int, report: str) -> plg.Sent` | 提示のリリース承認ゲートの依頼を送る（`parallel_lane.py` から移し、送った結果を返す） | 今と同じ | id と中身は今と同じ（`<計画名>-p<連番>-<提示>`） | ファイル（`presented.json` を読む・台帳）・耐久の記録（知らせ） |
| `request_confirmed` | `plugins/ndf/scripts/lib/parallel_gate2.py` | 新設 | `request_confirmed(main: Path, name: str, n: int, k: int, done: str) -> plg.Sent` | 確定の後の承認ゲートの依頼を組んで送る | 一時レーンが確定の流すものの本番のプランを終えた | 最後の起動が同じ連番・提示の `confirmed`（`carried` を持たない）なら、id `<計画名>-p<連番>-<提示>-c<起動の seq>`、`after: confirmed`・`reason`・`pr`・`options: ["打ち直す", "差し戻す"]`・`question`・`report`・`stamp`（承認したコミット）の依頼を送った結果を返す。違えば `NotConfirmed` | ファイル（done・本番のプランの状態・`presented.json` を読む・台帳）・耐久の記録（イベントを読む・知らせ） |
| `gate_detail` | `plugins/ndf/scripts/lib/parallel_gate2.py` | 新設 | `gate_detail(done: str) -> tuple[str, Optional[int], str]` | 本番のプランの done から、承認ゲートの理由・本番の PR の番号・承認資料のパスを読む | 無し | done の結果が `関門` の項目の `report` の置き場の `progress.jsonl` で最後の終了コード 10 のステップの出力を読み、`metrics.reason`（無ければ `summary`）と `metrics.main_pr` を返す。項目のほかが読めなければ理由は「承認ゲートの理由を読めない（<パス>）」、番号は `None` | ファイル（done・`progress.jsonl`・ステップの出力を読む） |
| `step_close_release_pr` | `plugins/ndf/scripts/lib/parallel_gate2.py` | 新設 | `step_close_release_pr(main: str, pr: int, comment: str) -> dict`（耐久ステップ `parallel.close-release-pr`） | 差し戻した本番の PR を閉じ、理由と依頼の id をコメントに残す | `pr` は正の整数 | `gh pr close <pr> --comment <comment>` を 1 回打ち、`{"closed": 終了コードが 0, "reason": 失敗の標準エラーの 1 行目か None}` を返す。例外にしない | 外部コマンド（gh）・ネットワーク（GitHub） |
| `ConfirmedGate.apply_confirmed` | `plugins/ndf/scripts/lib/parallel_gate2.py` | 新設 | `apply_confirmed(self, q: dict, choice: list[str]) -> bool` | 確定の後の依頼への答え「打ち直す」「差し戻す」を当てる | `q` は答えた問い。`self` は `_Run` | `q` が確定の後の `gate2` でなければ `False`（ほかの当て方へ進む）。そうなら `True` を返し、「打ち直す」は新しい目的の確定の起動を流し、「差し戻す」は本番の PR を閉じる耐久ステップを打ってから `settle(rel, {"result": "rejected", "reason": ...})` を呼ぶ | 耐久の記録（イベント `pause`）・起動（`launch_helper`）・外部コマンド（`step_close_release_pr`） |
| `last_launch` | `plugins/ndf/scripts/lib/parallel_helper.py` | 新設 | `last_launch(main: Path, name: str) -> dict` | 最後の起動の指示（イベント `pause` か、それに添えた `release`）を読む | 計画の耐久の記録がある | `action` が `release` か `confirmed` のイベント、それ以外のイベントに添えた `release`、無ければ空の dict を返す | 耐久の記録（イベントを読む） |
| `helper_task` | `plugins/ndf/scripts/lib/parallel_helper.py` | 変更 | `helper_task(main: Path, name: str, n: int, k: int) -> dict` | 一時レーンの流すものに、確定の後の依頼の 1 行と自分の依頼と答えを足す | 今と同じ | `phase: confirmed` の `gate2` は `request-gate2 <計画名> <連番> <提示> --confirmed --done <release-done.json>`。両方の `phase` に `asked`（`asked_of` の結果） | 耐久の記録（イベントを読む）・ファイル（台帳を読む） |
| `_resend` | `plugins/ndf/scripts/parallel.py` | 新設 | `_resend(s: plg.Sent) -> dict` | 送った結果から、出力に足す値を作り、中身の違う同じキーの依頼を終了コード 1 で断る | 依頼のコマンドの中で呼ぶ | `same` が `False` なら `reply("stopped", ..., code=1, existing=..., reason=...)` で終える。書いたなら空の dict、同じなら `{"already": True, "answered": s.answered}` | 標準出力（統一 JSON レポート） |
| `cmd_request` | `plugins/ndf/scripts/parallel.py` | 変更 | `cmd_request(a) -> None` | リリースの依頼を送り、送った結果で出力を決める | 今と同じ | 書いたときは今と同じ。同じなら終了コード 10 のまま要約を「送り済み」にし `already`・`answered` を足す。中身が違えば 1 で、依頼の記録のファイルを書かない | ファイル（台帳・依頼の記録）・耐久の記録（知らせ）・標準出力 |
| `cmd_request_ask` | `plugins/ndf/scripts/parallel.py` | 変更 | `cmd_request_ask(a) -> None` | 問いの依頼を送り、引き継いだときは引き継いだ答えか id を返す | 今と同じ | 引き継いだら 0 で `reused`・`id`（と答え済みなら `answer`）。送ったら今と同じ（同じなら `already`）。中身が違えば 1 | ファイル（台帳）・耐久の記録（知らせ）・外部コマンド（git）・標準出力 |
| `cmd_request_gate2` | `plugins/ndf/scripts/parallel.py` | 変更 | `cmd_request_gate2(a) -> None` | 提示か確定の後のリリース承認ゲートの依頼を送る | `--report` か `--confirmed --done` のどちらかがある | 送ったら 0（同じなら `already`）。中身が違えば 1。確定の起動が合わなければ 4。引数の組が合わなければ 1 | ファイル（台帳ほか `request_confirmed` が読むもの）・耐久の記録・標準出力 |
| `cmd_helper` | `plugins/ndf/scripts/parallel.py` | 変更 | `cmd_helper(a) -> None` | 一時レーンの流すものを返す。台帳を読めなければ止める | 今と同じ | 今の出力に `asked` が載る。台帳を読めなければ終了コード 2 | 耐久の記録（イベントを読む）・ファイル（台帳を読む）・標準出力 |
| `cmd_answer` | `plugins/ndf/scripts/parallel.py` | 変更 | `cmd_answer(a) -> None` | 答えの行が既にあれば要約を「送り済み」にする | 今と同じ | 終了コードは今と同じ。`already: true` を足す | ファイル（台帳）・耐久の記録（知らせ）・標準出力 |
| `cmd_confirm` | `plugins/ndf/scripts/parallel.py` | 変更 | `cmd_confirm(a) -> None` | 同上（確定の行） | 今と同じ | 同上 | ファイル（台帳）・耐久の記録（知らせ）・標準出力 |
| `cmd_settle` | `plugins/ndf/scripts/parallel.py` | 変更 | `cmd_settle(a) -> None` | 同上（締めの行） | 今と同じ | 同上 | ファイル（台帳・done を読む）・耐久の記録（知らせ）・標準出力 |
| `cmd_stop` | `plugins/ndf/scripts/parallel.py` | 変更 | `cmd_stop(a) -> None` | 同上（止める指示の行） | 今と同じ | 同上 | ファイル（台帳）・耐久の記録（知らせ）・外部コマンド（relay.py）・標準出力 |
| `answer_with_merge` | `plugins/ndf/scripts/lib/parallel_answer.py` | 変更 | `answer_with_merge(main, name, q, result, items, **kw) -> tuple[str, dict]` | 答えを送った結果のうち、行が既にあったことを出力の値へ渡す | 今と同じ | 戻り値の dict に `already`（`send_answer` の結果の `written` の否定）を足す | ファイル（台帳）・耐久の記録（知らせ）・外部コマンド（設計 PR のマージ。今と同じ） |
| `reused_text` | `plugins/ndf/scripts/hook_lib/ask_guard.py` | 新設 | `reused_text(rid: str, answer: dict, plan: str) -> str` | 引き継いだ答えを、一時レーンが読む拒否の理由の文にする | `answer` は `result`・`choice` を持つ | 依頼の id・答えの `choice`（無ければ `result`）・「この答えで進め。同じ問いを出し直さない」・正本の手順書のパスを含む文を返す | 無し |
| `forward_to_organizer` | `plugins/ndf/scripts/hook_lib/ask_guard.py` | 変更 | `forward_to_organizer(ev, row_id: str) -> dict \| None` | `request-ask` の結果の `reused` に応じて拒否の理由を選ぶ | 今と同じ | `reused: answered` なら `deny(reused_text(...))`、`reused: pending` と送ったときは `deny(hold_text(id, plan))`。0 以外と id の無い結果は今と同じく `None`（通す） | 外部コマンド（`parallel.py request-ask`） |
| `helper_wait_seconds` | `plugins/ndf/scripts/lib/parallel_launch.py` | 新設 | `helper_wait_seconds() -> float` | 空き待ちの上限の秒を返す | 無し | `NDF_PARALLEL_HELPER_WAIT_SECONDS` が数なら `max(0, 値)`、ほかは上限の宣言の `parallel-launch.helper-wait` の秒 | 無し |
| `_hit_once` | `plugins/ndf/scripts/lib/parallel_launch.py` | 変更 | `_hit_once(main, where, argv, lane, fresh) -> tuple[bool, float, str, str]` | 1 回打ち、断りの結果に `busy` があればその名前を返す | 今と同じ | 4 つ目の値は断りの項目の `busy`（無ければ空文字）。`busy` のある断りは立ち上がりを待たない | 外部コマンド（relay.py）・ファイル（一覧を読む） |
| `step_launch` | `plugins/ndf/scripts/lib/parallel_launch.py` | 変更 | `step_launch(main, argv, lane, cwd=None, fresh=False) -> dict` | `busy` のある断りで 2 回目を打たずに空き待ちを返す | 今と同じ | `busy` のある断りなら `{"result": "waiting", "attempts": 打った回数, "at", "reason", "busy", "limit": helper_wait_seconds()}`。ほかは今と同じ | 外部コマンド（relay.py）・ファイル（一覧を読む） |
| `LaunchRouting.launch_once` | `plugins/ndf/scripts/lib/parallel_launch.py` | 変更 | `launch_once(self, purpose, lane, argv, cwd=None, fresh=False, **extra) -> dict` | 空き待ちの結果を問いにせず起動の記録に残す | 今と同じ | `waiting` なら記録に `since`（`at`）・`busy`・`limit`・`launch`（`argv`・`cwd`・`fresh`）・`extra` を残して返し、問いを立てない。`failed` は今と同じく区分 `spawn` の問い | 外部コマンド（`step_launch`）・耐久の記録（問いのイベント） |
| `LaunchRouting.launch_waiting` | `plugins/ndf/scripts/lib/parallel_launch.py` | 新設 | `launch_waiting(self, rows: dict) -> None` | 空き待ちの起動の記録をループの 1 回の観測ごとに読み直し、打ち直すか上限で問う | `rows` は一覧が成功した観測の行。`self.seen_at` はその観測の時刻 | 待つ相手が一覧で動いて観えなければ `step_launch` を打ち直し、`up` なら記録を `up` と `waited` にする。動いていて上限を過ぎていれば記録を `failed` にして区分 `spawn` の問いを立てる。どちらでもなければ記録を変えない。古い記録の打ち直し（`launching` が偽）では何もしない | 外部コマンド（`step_launch`）・耐久の記録（問いのイベント） |
| `LaunchRouting._ask_launch` | `plugins/ndf/scripts/lib/parallel_launch.py` | 新設 | `_ask_launch(self, purpose: str, lane: str, text: str, spec: dict, extra: dict) -> None` | 起動できない一時レーンの区分 `spawn` の問いを立てる（`launch_once` の問いの組み立てを切り出す） | 無し | id `<計画名>-launch-<目的>`、選択肢「起こし直す」「止める」、`purpose`・`launch`・`extra` を持つ問いが 1 回だけ立つ | 耐久の記録（問いのイベント） |
| `live` | `plugins/ndf/scripts/relay_lib/hub.py` | 変更 | `live(ws, registry, sessions) -> Live` | 動いている一時レーンの名前も数える | 今と同じ | `Live.helper_names` に役割 `helper` の名前を並べる。ほかの欄は今と同じ | 無し |
| `Hub.judge` | `plugins/ndf/scripts/relay_lib/hub.py` | 変更 | `judge(name, now: Live, role=None, by=None) -> Refusal \| None` | 一時レーンの同時数の断りを `helper-busy` で返す | 今と同じ | 役割 `helper` で動いている一時レーンがあれば `Refusal(HELPER_BUSY, <文に動いている名前>, 3, busy=<先頭の名前>)`。全体の上限は `limit` のまま | 無し |
| `Hub.refuse` | `plugins/ndf/scripts/relay_lib/hub.py` | 変更 | `refuse(kind, name, r: Refusal) -> dict` | 断りに `busy` があれば結果の項目へ載せる | 今と同じ | 結果の `items[0].busy` に名前が載る。リレーハブの記録の理由は `helper-busy` | ファイル（リレーハブの記録） |
| `hub_result` | `plugins/ndf/scripts/relay_lib/hub.py` | 変更 | `hub_result(kind, status, summary, name, result, code, busy: str \| None = None) -> dict` | 結果の項目に `busy` を足せるようにする | 無し | `busy` が空でなければ `items[0].busy` を置く。無ければ今と同じ形 | 無し |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `resent` | 台帳を読めない | ファイルの権限・ディスクの誤り | `read_ledger_rows` の `OSError` | 呼び出し元へ返す | 依頼のコマンドが「計画の台帳へ書けない」と終了コード 2（`sent` が受ける） | 台帳は変えていない。再実行してよい |
| `resent` | 同じキーの行が見つからない | 最初の `opened` より前の行だけが同じキーを持つ（`append_ledger_row` は受けるので起きない） | 行を探した結果が無い | 既定値で続ける（`same=False`・`existing=""`） | 終了コード 1（理由「同じキーの行を読めない」） | 書いていない。原因を直して再実行する |
| `deliver_message` | 台帳を書けない | ロックの上限・ディスク | `append_ledger_row` の `OSError`・`LockTimeout` | 呼び出し元へ返す（今と同じ） | 終了コード 2 | 知らせを送らない。再実行してよい |
| `deliver_message` | 中身の違う同じキーの行 | 別の依頼が同じ id を使った | `resent` の `same` が `False` | 呼び出し元へ返す（`Sent` で） | 依頼のコマンドが終了コード 1 | 台帳を変えず、知らせない。送り手が次の手を変える |
| `deliver_message` | 耐久ワークフローへ知らせられない | 記録を開けない | `durable.DurableError` | 既定値で続ける（今と同じ。標準エラーへ 1 行） | 送ったと返る。耐久ワークフローは次の観測で台帳から読む | 再実行は不要 |
| `send_inbox` | 外のオーガナイザーのレーン | 計画が外の計画 | 計画名の判定 | 呼び出し元へ返す（`ValueError`。今と同じ） | 今と同じ | 今と同じ |
| `send_release_request` | 依頼の id のファイルを書けない・台帳を書けない | ディスク | `OSError` | 呼び出し元へ返す（今と同じ） | 終了コード 2 | 再実行してよい（id はファイルから読み直す） |
| `send_answer` | 台帳を書けない | ディスク・ロック | `OSError` | 呼び出し元へ返す（今と同じ） | 終了コード 2 | 再実行してよい |
| `request_ask` | ブランチ名の先端を求められない | 無いブランチ名・作業ディレクトリが git でない | `_rev` が空文字 | 既定値で続ける（`target` の欄を置かずに新しい依頼） | 今と同じく問いがオーガナイザーへ届く | 前の答えを当てない（安全な側） |
| `request_ask` | 台帳を読めない・書けない | ディスク・ロック | `OSError` | 呼び出し元へ返す | 終了コード 2。hook は問いを通す（今と同じ） | 再実行してよい |
| `request_ask` | `reused` の行が既にある | 同じ判定の記録の id で打ち直した | `append_ledger_row` が `False` | 既定値で続ける（同じ問いの結果をそのまま返す） | 0 で `reused` | 行は増えない |
| `_rev` | git が無い・失敗する | PATH・リポジトリでない | 終了コードが 0 でない・`OSError` | 既定値で続ける（空文字） | 出ない | 無し |
| `request_gate2` | `presented.json` を読めない | 提示のプランが書いていない | `OSError`・`ValueError` | 既定値で続ける（`stamp` を空にする。今と同じ） | 今と同じ | 今と同じ |
| `request_gate2` | 台帳を書けない | ディスク・ロック | `OSError` | 呼び出し元へ返す | 終了コード 2 | 再実行してよい |
| `request_confirmed` | 最後の起動が同じ連番・提示の確定でない | 確定の前に打った・別の提示の確定 | `last_launch` の結果を比べる | 止める（`NotConfirmed`） | 終了コード 4「最後の起動が確定でない（送らない）」 | 台帳を変えない。一時レーンは止まり、途中停止の問いになる |
| `request_confirmed` | 耐久の記録を読めない | 記録のロック・壊れ | `durable.DurableError` | 呼び出し元へ返す | 終了コード 2 | 台帳を変えない。再実行してよい |
| `request_confirmed` | done を読めない | 本番のプランが done を書いていない | `gate_detail` の `OSError`・`ValueError` | 呼び出し元へ返す | 終了コード 2「done を読めない」 | 台帳を変えない。一時レーンは止まり、途中停止の問いになる |
| `request_confirmed` | done に結果が `関門` の項目が無い | 本番のプランが承認ゲートで終わっていない | `gate_detail` の結果 | 止める（`NotConfirmed`） | 終了コード 4「承認ゲートで止まっていない（settle を打つ）」 | 台帳を変えない。一時レーンは `settle` を打つ |
| `gate_detail` | 本番のプランの状態か出力を読めない | 状態のディレクトリが消えた・出力が JSON でない | `OSError`・`ValueError`・`last_json` が `None` | 既定値で続ける（理由は「承認ゲートの理由を読めない（<パス>）」、番号は `None`） | 依頼の問いに理由の代わりにパスが出る。PR の番号が無ければ「差し戻す」で PR を閉じない | 無し |
| `gate_detail` | done を読めない | ファイルが無い・JSON でない | `OSError`・`ValueError` | 呼び出し元へ返す | `request_confirmed` の行のとおり終了コード 2 | 無し |
| `step_close_release_pr` | PR を閉じられない | 認証切れ・閉じ済み・ネットワーク | `gh` の終了コードが 0 でない | 既定値で続ける（`closed: false` と標準エラーの 1 行目） | 締めの理由に「本番の PR を閉じられない（<理由>）」が足される | 耐久ステップは 1 回だけ記録され、打ち直しで 2 回打たない。閉じるのは人が PR の画面で行う |
| `ConfirmedGate.apply_confirmed` | 流れているリリースが無い・連番が違う | 先に締めた・別のリリースの答え | `self.current` と `q["n"]` を比べる | 既定値で続ける（`True` を返して何もしない） | 出ない | 無し |
| `ConfirmedGate.apply_confirmed` | 選択肢が「打ち直す」「差し戻す」のどちらでもない | オーガナイザーが別の語を送った | `choice` を比べる | 既定値で続ける（`True` を返して何もしない） | 一時レーンは止まったままで、次の観測で区分 `helper` の途中停止の問いが立つ（問いの一覧から確定の後の依頼が外れたため） | 人が途中停止の問いへ答え直す |
| `ConfirmedGate.apply_confirmed` | 最後の起動の記録（`launch`）が無い | 古い記録の打ち直し | `self.launch` が空 | 既定値で続ける（「打ち直す」を流さない） | 途中停止の問いが立つ | 人が途中停止の問いへ答え直す |
| `ConfirmedGate.apply_confirmed` | 一時レーンを起動できない | リレーハブの断り | `launch_once` の結果 | 呼び出し元へ返す（今の起動の扱いのまま。`failed` は区分 `spawn` の問い、`helper-busy` は空き待ち） | 区分 `spawn` の問い | 人が答える |
| `last_launch` | 耐久の記録を読めない | 記録のロック・壊れ | `durable.DurableError` | 呼び出し元へ返す | 呼び出し元の行のとおり | 無し |
| `helper_task` | 台帳を読めない | ディスク | `OSError` | 呼び出し元へ返す | `cmd_helper` が終了コード 2 | 再実行してよい |
| `helper_task` | 耐久の記録を読めない | 記録のロック・壊れ | `durable.DurableError` | 呼び出し元へ返す（今と同じ） | 今と同じ | 今と同じ |
| `_resend` | 中身の違う同じキーの依頼 | 別の依頼が同じ id を使った | `s.same` が `False` | 止める | 終了コード 1、`status: stopped`、`metrics.existing`・`metrics.reason` | 送り手が次の手を変える |
| `cmd_request` | 中身の違う同じキーの依頼 | 同上 | `_resend` | 止める | 終了コード 1。依頼の記録のファイル（`request.txt`）を書かない | 同上 |
| `cmd_request` | 台帳を書けない | ディスク・ロック | `sent` の `OSError` | 止める（今と同じ） | 終了コード 2 | 再実行してよい |
| `cmd_request_ask` | 中身の違う同じキーの依頼 | 同じ判定の記録の id で中身の違う問い | `_resend` | 止める | 終了コード 1。hook は問いを通す | 人へ問いが届く |
| `cmd_request_ask` | 台帳を読めない・書けない | ディスク・ロック | `sent` の `OSError` | 止める（今と同じ） | 終了コード 2。hook は問いを通す | 再実行してよい |
| `cmd_request_gate2` | 引数の組が合わない | `--report` と `--confirmed` が両方か両方とも無い・`--confirmed` に `--done` が無い | 引数を比べる | 止める | 終了コード 1 | 引数を直して再実行する |
| `cmd_request_gate2` | 確定の起動が合わない・承認ゲートで止まっていない | `NotConfirmed` | 例外を受ける | 止める | 終了コード 4 | 台帳を変えない |
| `cmd_request_gate2` | 耐久の記録・done・台帳を読めない・書けない | `OSError`・`ValueError`・`durable.DurableError` | 例外を受ける | 止める | 終了コード 2 | 再実行してよい |
| `cmd_request_gate2` | 中身の違う同じキーの依頼 | 同じ確定の起動で本番のプランの done が変わった | `_resend` | 止める | 終了コード 1 | 一時レーンは止まり、途中停止の問いになる |
| `cmd_helper` | 台帳を読めない | ディスク | `OSError` | 止める | 終了コード 2「計画の台帳を読めない」。一時レーンは問いを送らずに止まる | 耐久ワークフローの途中停止の問いになる |
| `cmd_answer` | 台帳を書けない | ディスク・ロック | `sent` の `OSError` | 止める（今と同じ） | 終了コード 2 | 再実行してよい |
| `cmd_confirm` | 台帳を書けない | 同上 | 同上 | 止める（今と同じ） | 終了コード 2 | 再実行してよい |
| `cmd_settle` | 台帳を書けない・done を読めない | 同上 | 同上 | 止める（今と同じ） | 終了コード 2。承認ゲートの done は今と同じく 4 | 再実行してよい |
| `cmd_stop` | 台帳を書けない・止まらないレーン | 同上 | 同上 | 止める（今と同じ） | 今と同じ | 今と同じ |
| `answer_with_merge` | 台帳を書けない | ディスク・ロック | `send_answer` の `OSError` | 呼び出し元へ返す（今と同じ） | 終了コード 2 | 再実行してよい |
| `forward_to_organizer` | `request-ask` が上限の秒を超える・0 以外で終わる | 台帳・計画の状態 | `subprocess.TimeoutExpired`・終了コード | 既定値で続ける（今と同じく通す） | 問いが人へそのまま出る | 無し |
| `forward_to_organizer` | `reused: answered` の結果に `answer` が無い | 古い版の `parallel.py` | 結果の欄を見る | 既定値で続ける（`hold_text` で保留の手順を返す） | 保留の手順 | 無し |
| `_hit_once` | 打てない・一覧を読めない | relay.py の失敗 | 終了コード・一覧の結果 | 呼び出し元へ返す（今と同じ） | 出ない | 今と同じ |
| `step_launch` | 2 回とも立ち上がらない（`busy` の無い断り） | `limit`・名前の重なり・tmux の失敗 | `_hit_once` の結果 | 呼び出し元へ返す（今と同じく `failed`） | 区分 `spawn` の問い | 今と同じ |
| `LaunchRouting.launch_once` | 起動が `failed` | 同上 | 結果の値 | 止める（今と同じく問いを立てる） | 区分 `spawn` の問い | 人が答える |
| `LaunchRouting.launch_waiting` | 上限を過ぎても待つ相手が動いている | 前の一時レーンが長く動く | `self.seen_at - since >= limit` | 止める（記録を `failed` にして問いを立てる） | 区分 `spawn` の問い。本文に「一時レーンは同時に 1 本まで」・待った秒・待つ相手の名前 | 人が「起こし直す」で新しい目的の起動を流す |
| `LaunchRouting.launch_waiting` | 打ち直しがまた `busy` のある断り | 別の一時レーンが先に立ち上がった | `step_launch` の結果 | 既定値で続ける（`since` を変えずに `busy` を新しい名前にする） | 出ない | 次の観測で読み直す |
| `LaunchRouting.launch_waiting` | 打ち直しが `failed` | `busy` の無い断り | `step_launch` の結果 | 止める（`_ask_launch`） | 区分 `spawn` の問い | 人が答える |
| `LaunchRouting._ask_launch` | 同じ id の問いが既にある | 打ち直し | `ask` の判定 | 既定値で続ける（今と同じく 1 回だけ立つ） | 出ない | 無し |
| `Hub.refuse` | リレーハブの記録を書けない | ディスク | `hub_log` の中 | 既定値で続ける（今と同じ） | 断りの結果は返る | 今と同じ |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `resent` | 同じキーの行があり、`at` を除いた中身が同じ・答えの行も処理済みも無い | `same=True`・`answered=False`（答え待ちの再送） |
| `resent` | 同じキーの行があり、中身が同じ・答えの行か処理済みがある | `same=True`・`answered=True` |
| `resent` | 同じキーの行があり、中身が違う | `same=False`・`existing=<その行の id>` |
| `_resend` | 書いた | 空の dict（出力は今と同じ） |
| `_resend` | 書かず、中身が同じ | `already: true`・`answered`、要約は「送り済み」 |
| `_resend` | 書かず、中身が違う | 終了コード 1 |
| `request_ask` | `metadata.target` が無い | 新しい依頼（`target` の欄なし） |
| `request_ask` | `#<数字>` | 対象はその文字列。同じ問いを引く |
| `request_ask` | ブランチ名で、先端を求められる | 対象は `<ブランチ名>@<40 桁>`。同じ問いを引く |
| `request_ask` | ブランチ名で、先端を求められない・`#` の後が数字でない | 新しい依頼（`target` の欄なし） |
| `request_ask` | 対象があり、同じ名前・区分・対象の依頼が答え済み | `reused` の行（`result: answered`）を足し、依頼を書かない |
| `request_ask` | 対象があり、同じ依頼が答え待ち | `reused` の行（`result: pending`）を足し、依頼を書かない |
| `request_ask` | 対象があり、同じ依頼が無い（名前か区分か先端が違う） | 新しい依頼（`target` の欄あり） |
| `request_confirmed` | 最後の起動が `confirmed`・同じ `n` と `k`・`carried` 無し、done に `関門` がある | 依頼を送る（id に起動の `seq`） |
| `request_confirmed` | 最後の起動が `confirmed` でない、`n`・`k` が違う、`carried` がある | `NotConfirmed`（終了コード 4） |
| `request_confirmed` | 起動は合うが done に `関門` が無い | `NotConfirmed`（終了コード 4） |
| `ConfirmedGate.apply_confirmed` | `kind` が `gate2` でない、`after` が `confirmed` でない | `False`（ほかの当て方へ） |
| `ConfirmedGate.apply_confirmed` | 確定の後・流れているリリースの連番と同じ・「打ち直す」・`launch` がある | `pause("confirmed", 今の起動の欄, purpose=<今の目的>-a<問いの id>)` の後に `launch_helper()`。`helper_asked` を空にする |
| `ConfirmedGate.apply_confirmed` | 同上で「差し戻す」・`pr` がある | `step_close_release_pr` を打ってから `settle(rel, {"result": "rejected", "reason": ...})` |
| `ConfirmedGate.apply_confirmed` | 同上で「差し戻す」・`pr` が無い | PR を閉じずに `settle`。理由に「本番の PR の番号を読めないため閉じていない」を足す |
| `ConfirmedGate.apply_confirmed` | ほかの選択肢・連番が違う・流れているリリースが無い | `True` を返して何もしない |
| `step_launch` | 打つ前の一覧で動いている（`fresh` でない） | `up`・`attempts: 0`（今と同じ） |
| `step_launch` | 1 回目が `busy` のある断り | `waiting`・`attempts: 1`（2 回目を打たない） |
| `step_launch` | 1 回目が `busy` の無い断りで、2 回目が `busy` のある断り | `waiting`・`attempts: 2` |
| `step_launch` | 2 回とも `busy` の無い失敗 | `failed`（今と同じ） |
| `LaunchRouting.launch_waiting` | 待つ相手が一覧に無いか、`running`・`awaiting_approval` でない | `step_launch` を打ち直す |
| `LaunchRouting.launch_waiting` | 待つ相手が動いていて、`seen_at - since` が `limit` 未満 | 何もしない |
| `LaunchRouting.launch_waiting` | 待つ相手が動いていて、`seen_at - since` が `limit` 以上 | `failed` にして区分 `spawn` の問い |
| `settled_gates` | 確定の後の `gate2` の後に同じ連番の `confirm` の行だけがある | 処理済みにしない |
| `settled_gates` | 確定の後の `gate2` に同じ id の `answer` の行がある、または後に同じ連番の `settle` の行がある | 処理済み |
| `settled_gates` | チェックポイントの問いにある確定の後の `gate2` で、`rows` に同じ id の答えか同じ連番の締めがある | 処理済み |

- 繰り返しの終わる条件: `launch_waiting` はループの 1 回で、`waiting` の記録ごとに `step_launch` を高々 1 回打つ。待ちは上限（既定 3,600 秒）を過ぎた最初の観測で `failed` になって終わる。`gate_detail` は `progress.jsonl` を末尾から 1 回読むだけで繰り返さない
- 計算量の注意: `resent`・`same_question`・`asked_of`・`helper_task` は台帳を先頭から 1 回読む（台帳の行数に比例）。リリース 17 の計画の台帳は数百行で、1 回の読み出しは問いの hook の上限の秒に収まる。`launch_waiting` の `waiting` の記録は一時レーンの同時数の上限から高々 1 件である

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `Sent` | 型（`NamedTuple`: `id: str`・`written: bool`・`same: bool`・`answered: bool`・`existing: str`） | `plugins/ndf/scripts/lib/parallel_ledger.py` | 無し |
| `Same` | 型（`NamedTuple`: `id: str`・`state: str`（`answered` / `pending`）・`answer: Optional[dict]`） | `plugins/ndf/scripts/lib/parallel_question.py` | 無し |
| `Asked` | 型（`NamedTuple`: `id: str`・`sent: Optional[Sent]`・`same: Optional[Same]`） | `plugins/ndf/scripts/lib/parallel_lane.py` | 無し |
| `NotConfirmed` | 例外（`理由の文` を持つ） | `plugins/ndf/scripts/lib/parallel_gate2.py` | 無し |
| `KINDS` に足す `reused` | 定数（行の種類。`TAKE`・`QUESTIONS` には入れない） | `plugins/ndf/scripts/lib/parallel_ledger.py` | 無し |
| `CONFIRMED_OPTIONS` | 定数（`("打ち直す", "差し戻す")`。依頼の `options` と答えの当て方の両方がここを読む） | `plugins/ndf/scripts/lib/parallel_gate2.py` | `("打ち直す", "差し戻す")` |
| `HELPER_BUSY` | 定数（リレーハブの断りの結果の値） | `plugins/ndf/scripts/relay_lib/hub.py` | `"helper-busy"` |
| `parallel-launch.helper-wait` | 設定値（空き待ちの上限の秒） | `plugins/ndf/contracts/limits.json` | 3,600 秒（`NDF_PARALLEL_HELPER_WAIT_SECONDS` で変える） |
| 起動の記録の `waiting` の値 | 内部の型（`lane`・`result: "waiting"`・`attempts`・`at`・`reason`・`since`・`busy`・`limit`・`launch`・`extra`） | `plugins/ndf/scripts/lib/parallel_launch.py`（台帳のチェックポイントの `launches` に保存） | 無し |
| 起動の記録の `waited` | 内部の型（`{"busy": <名前>, "seconds": <秒>}`。`up` の記録に足す） | `plugins/ndf/scripts/lib/parallel_launch.py` | 無し |

**`HELPER_BUSY` の値を `parallel_launch` に写さない。** 起動の部品は、断りの項目に `busy` の欄があるかどうかで空き待ちを決め、結果の値の文字列を比べない。リレーハブだけが値を持つ。

**比べる中身から外す欄は `at` だけである。** `stamp`（問いを出した作業ディレクトリの先頭）・`reason`・`pr` は同じ依頼の再送で変わらないため比べる。変われば別の依頼であり、終了コード 1 になる。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 依頼のコマンド（レーン・一時レーン・オーガナイザー）どうし | 計画の台帳 `ledger.jsonl` | 行を足すのは今と同じく `append_ledger_row` の排他（`locks.exclusive`）の中だけ。`resent` は排他を取らずに読む。台帳の行は追記だけで書き換わらない（I7）ため、同じキーの行は読んだ後に変わらない | 同じキーの再送は行を増やさず、同じ結果を返す。中断した送り手は同じコマンドを打ち直してよい |
| 問いの hook（`request-ask`）の同じ問いの判定と書き込み | 計画の台帳 | 判定と `reused` の行の書き込みの間は排他を取らない。1 つの一時レーンの問いは 1 つのセッションから 1 つずつ出るため、同じ名前の判定が重ならない。別の名前の一時レーンは判定の対象が重ならない | `reused` のキーは `reused:<引き継いだ id>:<判定の記録の id>` で、hook の打ち直しは行を増やさない |
| 耐久ワークフローのループの 1 回と依頼のコマンド | 計画の台帳・耐久の記録 | 耐久ワークフローは計画ごとに 1 本（今の排他）。依頼のコマンドは台帳へ書いてから知らせる（今と同じ） | 確定の後の依頼への答えは `answered` に入り、2 回目の答えは `reply` が捨てる（今と同じ） |
| 耐久ワークフローの空き待ちの読み直しと、別の一時レーンの終わり | 起動の記録・リレーハブの登録簿 | 起動の記録を書き換えるのは `LaunchRouting` のメソッドだけ（設計文書の集約「起動の記録」）。空き待ちの判定は耐久ステップ（観測・`step_launch`）の記録した結果だけを使う | 上限の秒は `step_launch` の結果に記録し、打ち直しで環境変数を読み直さない。打ち直しは同じ道を通る |
| 「差し戻す」の本番の PR を閉じる操作 | GitHub の PR | 耐久ステップ `parallel.close-release-pr` の記録 | 耐久ステップは 1 回だけ打たれ、打ち直しでは記録した結果を返す |
| リレーハブの判定と起動 | 動いているラッパーの一覧 | 今と同じ（リレーハブは 1 本。`hub.lock`） | 断りは何も起動しない |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_resend` | 送り済み・既にある id・断った理由 | 統一 JSON レポート（`metrics.already`・`answered`・`existing`・`reason`） | 依頼の中身の全文 |
| `cmd_request_ask` | 引き継いだ id・答え（`choice`・`result`） | 統一 JSON レポート（`metrics.reused`・`id`・`answer`） | 問いの文と選択肢の全文 |
| `request_ask` | 引き継いだ依頼の id・送り手・区分・問いの対象・今の判定の記録の id・答え済みか | 計画の台帳の `reused` の行 | 秘密（台帳へ書く前に今と同じく伏せる） |
| `request_confirmed` | 止まった理由・本番の PR の番号・承認したコミット | 計画の台帳の `gate2` の行と、問いの一覧 | done の中身の全文 |
| `ConfirmedGate.apply_confirmed` | 差し戻しの理由と依頼の id、PR を閉じられなかった理由 | PR のコメント・リリースの締めの理由（範囲のレーンへの答え） | 認証情報（`gh` の標準エラーは 1 行目だけ） |
| `LaunchRouting.launch_waiting` | 待つ相手の名前・最初に断られた時刻・待った秒 | 起動の記録（台帳のチェックポイント）と区分 `spawn` の問いの本文 | 無し |
| `Hub.refuse` | 断りの理由 `helper-busy` | リレーハブの記録（`hub_log`） | 無し |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `resent` | `append_ledger_row`・`read_ledger_rows`・`settled_gates`・`_redact` | `grep -n "^def " plugins/ndf/scripts/lib/parallel_ledger.py` | 行の読み出しと伏せ字と処理済みの判定は使う。`append_ledger_row` は戻り値を `bool` のまま保つ（呼び出し元 7 か所が `bool` で読む）ため、判定は別の関数にする |
| `target_of` | `cause_of`（同じ原因の問いの鍵） | `cat plugins/ndf/scripts/lib/parallel_question.py` | 使えない。`cause_of` は人にしかできない操作の問いをまとめる鍵で、取り消せない操作の問いには空を返す（対象を区別するため） |
| `same_question` | `receive_question` の同じ id の判定・`pending_questions` | `grep -n "def receive_question\|def pending_questions" plugins/ndf/scripts/lib/*.py` | 使えない。どちらも同じ id だけを見て、名前・区分・対象では引かない |
| `asked_of` | `requests_for`・`answers_of`・`pending_questions` | `grep -n "def requests_for\|def answers_of" plugins/ndf/scripts/lib/parallel_lane.py` | 使えない。`requests_for` はリリースの依頼のファイル、`answers_of` は耐久の記録を読み、一時レーンの問いの依頼を台帳から並べない |
| `request_confirmed` | `request_gate2`（提示）・`helper_task` の最後の起動の読み方 | `sed -n 446,461p plugins/ndf/scripts/lib/parallel_lane.py`・`sed -n 86,109p plugins/ndf/scripts/lib/parallel_helper.py` | 使う。承認したコミットの読み方は `request_gate2` と同じ `presented.json`、最後の起動は `last_launch` に切り出して共有する |
| `gate_detail` | `_release_result`（`parallel.py`）・`step_read.last_json` | `grep -n "def _release_result" plugins/ndf/scripts/parallel.py`・`grep -n "def last_json" plugins/ndf/scripts/lib/step_read.py` | `last_json` は使う。`_release_result` は承認ゲートを終了コード 4 で終える CLI の関数で、理由と PR の番号を返さないため使えない |
| `step_close_release_pr` | `gh_call.gh`・`review_range` の `gh pr close` | `grep -rn '"pr", "close"' plugins/ndf/scripts --include=*.py` | `gh_call.gh` を使う（`check-trigger.py` が同じ形で PR を閉じる） |
| `ConfirmedGate.apply_confirmed` | `_Run.settle`・`confirm_k`・`LaunchRouting.relaunch` | `sed -n 562,584p plugins/ndf/scripts/lib/parallel_run.py`・`sed -n 127,147p plugins/ndf/scripts/lib/parallel_launch.py` | `settle` と `launch_helper` は使う。`relaunch` は区分 `spawn`・`helper` の問いの `launch` の欄を読む形で、確定の後の依頼は流れているリリースの最後の起動を引き継ぐため使えない（目的の名前の付け方 `-a<問いの id>` は揃える） |
| `last_launch` | `helper_task` の中の読み方 | `sed -n 86,96p plugins/ndf/scripts/lib/parallel_helper.py` | 使う。中の 2 行を切り出し、`helper_task` と `request_confirmed` が呼ぶ |
| `_resend` | `sent`（`parallel.py`） | `sed -n 152,158p plugins/ndf/scripts/parallel.py` | 使う。`sent` は台帳を書けないときの終了コード 2 を持ち、`_resend` はその戻り値を読む |
| `reused_text` | `hold_text`・`ask_stop_text` | `grep -n "def hold_text\|def ask_stop_text" plugins/ndf/scripts/hook_lib/ask_guard.py` | 使えない。どちらも答えを持たない文で、答えを載せる文は無い。正本の手順書のパス（`ASKING_MD`）は同じ定数を使う |
| `LaunchRouting.launch_waiting` | `launch_wakes`・`launch_once`・DBOS の待ち・tenacity | `grep -n "def launch_wakes\|def launch_once" plugins/ndf/scripts/lib/parallel_launch.py` と設計文書の LIB1・LIB2 | `launch_once` の結果の作り方と `step_launch` は使う。`launch_wakes` は止まったレーンの再起動の対象（`wake`）を読む形で、起動の目的ごとの待ちを持たない。DBOS と tenacity は決定 10 のとおり使えない |
| `LaunchRouting._ask_launch` | `launch_once` の中の問いの組み立て | `sed -n 102,119p plugins/ndf/scripts/lib/parallel_launch.py` | 使う。中の組み立てを切り出し、`launch_once` と `launch_waiting` が呼ぶ |
| `helper_wait_seconds` | `launch_seconds`・`parallel_repo.stall_seconds` | `grep -n "def launch_seconds\|def stall_seconds" plugins/ndf/scripts/lib/*.py` | 同じ形（環境変数を読み、読めなければ上限の宣言の値）に揃える。`launch_seconds` は別の上限の値を返すため流用しない |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `resent` | 中身が同じ再送で `same`・`answered` が正しい | `at` だけが違う再送は同じ。最初の `opened` より前の同じキーの行は数えない | 中身が違えば `same=False` と既にある id。台帳を読めなければ `OSError` |
| `settled_gates` | 提示の `gate2` は今と同じ | 確定の後の `gate2` は確定の行で処理済みにならず、答えか後の締めで処理済みになる。チェックポイントの問いでも同じ | 無し（入力の形は台帳の読み出しが保証する） |
| `target_of` | `#3593` はそのまま。ブランチ名は `<名>@<40 桁>` | 空文字・`#` の後が数字でない・`resolve` が空文字 | 無し |
| `same_question` | 答え済み・答え待ちの同じ問いを引く | 名前・区分・対象のどれか 1 つだけが違えば `None` | 無し |
| `asked_of` | 自分の名前の `gate2`・`ask` と答えを並べる | 別の名前の行は入らない。答えの無い依頼は `answer` を持たない | 無し |
| `deliver_message` | 書いたら `written` | 中身の同じ再送は知らせを送る | 中身が違えば知らせを送らない。台帳を書けなければ例外 |
| `request_ask` | 同じ問いで `reused` の行が 1 行増え、依頼の行が増えない | 先端の動いた同じブランチ名は新しい依頼。先端を求められない名前は `target` の無い依頼 | 台帳を書けなければ終了コード 2 で、hook は通す |
| `request_confirmed` | id が `-c<seq>` で、理由・PR の番号・選択肢が載る | 同じ起動の 2 回目は行が増えず送り済み。打ち直した後の起動は別の id | 起動が合わない・`関門` が無いと 4。done を読めないと 2 |
| `gate_detail` | リリース 17 の形の出力から理由と `main_pr` を読む | ステップの出力が無いと理由はパス、番号は `None` | done を読めないと例外 |
| `step_close_release_pr` | 閉じたら `closed: true` | 無し | `gh` が失敗したら `closed: false` と理由（例外にしない） |
| `ConfirmedGate.apply_confirmed` | 「打ち直す」で新しい `seq` の確定の起動が流れる。「差し戻す」で範囲のレーンへ `rejected` と理由が返り、PR を閉じる耐久ステップが 1 回呼ばれる | 確定の後でない `gate2` は `False`。PR の番号が無いと閉じずに締める | PR を閉じられなくても締める。ほかの選択肢では何もせず、次の観測で途中停止の問いが立つ |
| `last_launch` | `confirmed` のイベントを返す | ほかのイベントに添えた `release` を返す。無ければ空 | 耐久の記録を読めなければ例外 |
| `helper_task` | `phase: confirmed` の `gate2` が `--confirmed --done` の 1 行になる。`asked` が載る | 提示では `gate2` が今と同じ | 台帳を読めなければ `cmd_helper` が 2 |
| `_resend` | 書いたら空、同じなら `already` | 答え済みの同じ再送も `already`（1 にしない） | 中身が違えば 1 と `existing`・`reason` |
| `cmd_request` | 書いたら今と同じ 10 | 答えのある依頼の打ち直しは 10・`already`・`answered`、続く `await` が答えを読む | 中身が違えば 1 で `request.txt` を書かない。台帳を書けなければ 2 |
| `cmd_request_ask` | 引き継ぎで 0・`reused` | 答え待ちの同じ問いは `reused: pending` と答え待ちの id | 中身が違えば 1。台帳を書けなければ 2 |
| `cmd_request_gate2` | 提示も確定の後も 0 | 同じ依頼の再送は送り済み | 引数の組が合わないと 1、起動が合わないと 4、読めないと 2、中身が違うと 5 |
| `cmd_helper` | `asked` が載る | 依頼の無い一時レーンは空の一覧 | 台帳を読めなければ 2 |
| `cmd_answer` | 今と同じ出力 | 2 回目の答えに `already: true` | 台帳を書けなければ 2 |
| `cmd_confirm` | 今と同じ出力 | 2 回目に `already: true` | 台帳を書けなければ 2 |
| `cmd_settle` | 今と同じ出力 | 2 回目に `already: true` | 台帳を書けなければ 2。承認ゲートの done は 4 |
| `cmd_stop` | 今と同じ出力 | 2 回目に `already: true` | 今と同じ |
| `answer_with_merge` | `already: false` | 2 回目に `already: true` | 台帳を書けなければ例外 |
| `reused_text` | 依頼の id と `choice` を含む | `choice` が空なら `result` を載せる | 無し |
| `forward_to_organizer` | `reused: answered` で答えを載せた拒否 | `reused: pending` で答え待ちの id の保留の手順 | 0 以外は通す。`answer` の無い `answered` は保留の手順 |
| `helper_wait_seconds` | 環境変数の値 | 0・負の数は 0 | 数でなければ上限の宣言の値 |
| `_hit_once` | `busy` のある断りで立ち上がりを待たずに名前を返す | `busy` の無い断り・名前の重なりは今と同じ | 打てなければ今と同じ |
| `step_launch` | `busy` のある断りで `waiting` と `limit` | 1 回目が別の断りで 2 回目が `busy` のある断り | 2 回とも `busy` の無い失敗は `failed`（受け入れ条件 11） |
| `LaunchRouting.launch_once` | `waiting` で問いを立てず記録に残す | 記録のある目的は打ち直さない（今と同じ） | `failed` は今と同じく問い |
| `LaunchRouting.launch_waiting` | 待つ相手が止まった観測で打ち直して `up`・`waited` | 上限ちょうどで問い。上限の前は何もしない。ループの 1 回の中で眠らない（I6） | 打ち直しが `failed` なら問い。また `busy` なら待ちを続ける。古い記録の打ち直しでは何もしない |
| `LaunchRouting._ask_launch` | 問いが 1 回立つ | 同じ id は 2 回立たない | 無し |
| `live` | `helper_names` に一時レーンの名前 | 一時レーンが無ければ空 | 無し |
| `Hub.judge` | 動いている一時レーンがあれば `helper-busy` と名前 | 全体の上限は `limit` のまま。人の起動は今と同じ | 無し |
| `Hub.refuse` | 結果の `items[0].busy` に名前 | `busy` の無い断りは今と同じ形 | 記録を書けなくても結果は返る |
| `hub_result` | `busy` を置く | `busy` が空なら欄を置かない | 無し |
