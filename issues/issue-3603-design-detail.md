# #3603 の詳細設計: 1 スプリントのレーンの設計承認の依頼と、一時レーンの同じ問い

設計文書 [issue-3603-design.md](issue-3603-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。
語（問いの対象・対象の行・同じ問い・答えの引き継ぎ・設計承認の依頼）は設計文書の「用語」の表の意味で使う。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/forms/parallel-ledger.json
├── scripts/
│   ├── parallel.py
│   ├── sprint-state.py
│   ├── hook_lib/ask_guard.py
│   ├── supervise_lib/design_merge.py
│   ├── lib/
│   │   ├── README.md
│   │   ├── parallel_args.py
│   │   ├── parallel_design_gate.py *
│   │   ├── parallel_question.py
│   │   ├── parallel_lane.py
│   │   ├── parallel_ledger.py
│   │   ├── gate1_requests.py
│   │   └── sprint_loop_gate.py
│   └── tests/
│       ├── test_parallel_design_gate.py *
│       ├── test_parallel_question.py *
│       ├── test_parallel_lane.py
│       ├── test_ask_guard.py
│       ├── test_parallel_answer.py
│       ├── test_parallel_ledger.py
│       ├── test_gate1_requests.py
│       ├── test_sprint_state.py
│       └── test_supervise_sprint_chain.py
└── skills/development-workflow/references/
    ├── approval-request.md
    ├── asking.md
    └── parallel.md
docs/
├── glossary/glossary.json
└── glossary.md
```

依存の向きは一方向である。エントリポイント（`parallel.py`・`hook_lib/ask_guard.py`）が `lib/` の部品を呼び、`lib/` の部品はエントリポイントを読まない。
新設の `parallel_design_gate.py` が読む部品と、それを読む側は次のとおりである。`parallel_question.py` は純粋な関数だけを持ち、ほかの `lib/` を読まない。

- `parallel_design_gate.py` が読む部品: `parallel_lane.py`・`parallel_ledger.py`・`gate1_requests.py`・`parallel_hold_sprint.py`・`run_state.py`
- `parallel_design_gate.py` を読む側: `parallel.py` だけ（上の部品のどれからも読まれない）

`report_pr`・`last_verdict`・`pr_numbers` は `sprint_loop_gate.py` から `gate1_requests.py` へ移す。
移した後は `sprint_loop_gate.py` が `gate1_requests.py` を読む（今も `carried_over` などを読む向きと同じ）。
`gate1_requests.py` は `sprint_loop_gate.py` を読まないため、輪はできない。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/scripts/parallel.py` | 変更 | `request-ask` の送り分けと、新しい副命令 `design-answer` のエントリポイント | `lib/parallel_design_gate.py`・`lib/parallel_question.py`・`lib/parallel_lane.py`・`lib/parallel_ledger.py` |
| `plugins/ndf/scripts/lib/parallel_args.py` | 変更 | `design-answer` の引数を足す | 無し |
| `plugins/ndf/scripts/lib/parallel_design_gate.py` | 新設 | 1 スプリントのレーンの設計承認の依頼の中身を組み、その答えをスプリント状態へ書く | `lib/parallel_lane.py`・`lib/parallel_ledger.py`・`lib/gate1_requests.py`・`lib/parallel_hold_sprint.py`・`lib/run_state.py`・`sprint-state.py`（子プロセス） |
| `plugins/ndf/scripts/lib/parallel_question.py` | 変更 | 対象の行の読み取り・同じ問いの前の依頼の引き当て・判定の記録の id からの結果の復元（純粋な関数） | 無し |
| `plugins/ndf/scripts/lib/parallel_lane.py` | 変更 | 送り手の種類・問いの対象の先端の解決・`ask` の行の `target`・`reused` の行の書き込み | `lib/parallel_ledger.py`・`lib/parallel_hold.py`・`lib/parallel_question.py` |
| `plugins/ndf/scripts/lib/parallel_ledger.py` | 変更 | 台帳の行の種類に `reused` を足す | 無し |
| `plugins/ndf/scripts/lib/gate1_requests.py` | 変更 | 設計のプランの報告の PR の番号と MVV 判定の記録の読み取りを持つ | `lib/project_mvv.py` |
| `plugins/ndf/scripts/lib/sprint_loop_gate.py` | 変更 | 移した 3 関数を `gate1_requests.py` から読む | `lib/gate1_requests.py` |
| `plugins/ndf/scripts/hook_lib/ask_guard.py` | 変更 | `request-ask` の結果の種類ごとに拒否の理由を組み、上限の秒を過ぎたら台帳から結果を復元する | `lib/parallel_ledger.py`・`lib/parallel_question.py` |
| `plugins/ndf/scripts/supervise_lib/design_merge.py` | 変更 | マージ済みで MVV 判定の承認の記録がある設計 PR の承認の確かめを通す | `lib/gate_answer.py` |
| `plugins/ndf/scripts/sprint-state.py` | 変更 | `--by mvv` の記録にも `--pr` と `--outcome` を残す | 無し |
| `plugins/ndf/scripts/lib/README.md` | 変更 | 新設の 1 本と、`gate1_requests.py` の行の役割を直す | 無し |
| `plugins/ndf/contracts/forms/parallel-ledger.json` | 変更 | `reused` の行・`gate1` の行の `ledger_id`・`ask` の行の `target` の形 | 無し |
| `plugins/ndf/skills/development-workflow/references/approval-request.md` | 変更 | 「止まる手段」で、1 スプリントのレーンの設計承認の問い（`ndf-gate:1`）に承認の行を置く書き方。設計 PR を参照するだけの問いには置かない | 無し |
| `plugins/ndf/skills/development-workflow/references/asking.md` | 変更 | 対象の行と承認の行の書き方と、前の答えが返ったときの扱い | 無し |
| `plugins/ndf/skills/development-workflow/references/parallel.md` | 変更 | 1 スプリントのレーンの設計承認の問いの書き方と `design-answer` の打ち方 | 無し |
| `docs/glossary/glossary.json` | 変更 | 設計文書の「用語」の表の 5 語の追加と意味の変更 | 無し |
| `docs/glossary.md` | 変更 | `glossary.py render` で作り直す | `docs/glossary/glossary.json` |
| `plugins/ndf/scripts/tests/test_parallel_design_gate.py` | 新設 | 設計承認の依頼の組み立てと `design-answer` の単体テスト | `lib/parallel_design_gate.py` |
| `plugins/ndf/scripts/tests/test_parallel_question.py` | 新設 | 対象の行・同じ問い・結果の復元の単体テスト | `lib/parallel_question.py` |
| `plugins/ndf/scripts/tests/test_parallel_lane.py` | 変更 | 送り手の種類・先端の解決・`reused` の行のテスト | `lib/parallel_lane.py` |
| `plugins/ndf/scripts/tests/test_ask_guard.py` | 変更 | 結果の種類ごとの拒否の理由と、上限の秒を過ぎたときの復元のテスト | `hook_lib/ask_guard.py` |
| `plugins/ndf/scripts/tests/test_parallel_answer.py` | 変更 | 1 スプリントのレーンの `gate1` の行の MVV 判定の印とマージのテスト | `lib/parallel_answer.py` |
| `plugins/ndf/scripts/tests/test_parallel_ledger.py` | 変更 | `reused` の行が耐久ワークフローの状態へ反映されないこと | `lib/parallel_ledger.py` |
| `plugins/ndf/scripts/tests/test_gate1_requests.py` | 変更 | 移した 3 関数の入出力が今と同じこと | `lib/gate1_requests.py` |
| `plugins/ndf/scripts/tests/test_sprint_state.py` | 変更 | `--by mvv` の記録が `pr`・`outcome` を持つこと | `sprint-state.py` |
| `plugins/ndf/scripts/tests/test_supervise_sprint_chain.py` | 変更 | `design-approved` の I9 の通し方 | `supervise_lib/design_merge.py` |

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/parallel.py` | モジュール | `cmd_request_ask`（変更）・`cmd_design_answer`（新設）・`_expired`（新設） |
| `plugins/ndf/scripts/lib/parallel_args.py` | モジュール | `add_release_parsers`（変更。`design-answer` の副命令を足すだけで、責務とシグネチャは変えない） |
| `plugins/ndf/scripts/lib/parallel_design_gate.py` | `DesignGate`（データクラス）とモジュール | `approval_prs`・`gated_design_prs`・`next_gate1_id`・`design_gate`・`answer_design`（すべて新設） |
| `plugins/ndf/scripts/lib/parallel_question.py` | `AskTarget`・`Prior`（データクラス）とモジュール | `target_line`・`prior_of`・`outcome_of`（新設）・`asked_of`（変更） |
| `plugins/ndf/scripts/lib/parallel_lane.py` | モジュール | `sender_kind`・`ask_target`・`record_reused`（新設）・`request_ask`（変更） |
| `plugins/ndf/scripts/lib/parallel_ledger.py` | モジュール | 定数 `RECORD` に `reused` を足す（関数は変えない） |
| `plugins/ndf/scripts/lib/gate1_requests.py` | モジュール | `pr_numbers`・`report_pr`・`last_verdict`（`sprint_loop_gate.py` から移す。シグネチャ・責務・失敗の扱いは変えない） |
| `plugins/ndf/scripts/lib/sprint_loop_gate.py` | モジュール | 移した 3 関数の定義を消し、`gate1_requests` から読む |
| `plugins/ndf/scripts/hook_lib/ask_guard.py` | モジュール | `forward_to_organizer`（変更）・`held_text`・`design_hold_text`・`reused_text`・`held_after_timeout`（新設） |
| `plugins/ndf/scripts/supervise_lib/design_merge.py` | モジュール | `cmd_design_approved`（変更）・`mvv_approval`（新設） |
| `plugins/ndf/scripts/sprint-state.py` | モジュール | `gate_locked`（変更） |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `cmd_request_ask` | `plugins/ndf/scripts/parallel.py` | 変更 | `cmd_request_ask(a: argparse.Namespace) -> None` | 送り手を確かめ、設計承認の依頼・同じ問い・`ask` の依頼の順に最初に当たった分岐へ送り、統一 JSON レポートで返す | 標準入力が `tool_input`・`cwd`・`ledger_id`・`deadline_at`（任意）を持つ JSON | 台帳に足した行は高々 1 行で、`metrics.kind`・`metrics.id` が足した行か引き当てた行を指す。`deadline_at` を過ぎていれば台帳は変わらない | 台帳のファイルの読み書き・`gh pr view`・`git rev-parse` |
| `_expired` | `plugins/ndf/scripts/parallel.py` | 新設 | `_expired(deadline_at: float \| None, now: Callable[[], float] = time.time) -> bool` | 台帳へ書く直前に、hook の上限の秒が尽きたかを返す | 無し | `deadline_at` が無いか数でなければ偽。`now()` が `deadline_at` 以上なら真 | 無し |
| `cmd_design_answer` | `plugins/ndf/scripts/parallel.py` | 新設 | `cmd_design_answer(a: argparse.Namespace) -> None` | `answer_design` の結果を統一 JSON レポートで返す | `a.name`・`a.id`・`a.state` がある | 終了コードが「入出力の契約」の `design-answer` の表のとおり | スプリント状態・指摘ファイルの書き込み（`answer_design` を通す） |
| `approval_prs` | `plugins/ndf/scripts/lib/parallel_design_gate.py` | 新設 | `approval_prs(tool_input: dict) -> list[int]` | 最初の問いの文の承認の行（`承認: #<番号>`）の番号を、行ごとに出現順で返す（同じ番号の行が 2 行あっても 2 つ返す。問いの文のほかの場所と選択肢の PR の URL・`#<番号>` は読まない） | 無し | 返す番号はすべて正の整数。承認の行が無ければ空 | 無し |
| `gated_design_prs` | `plugins/ndf/scripts/lib/parallel_design_gate.py` | 新設 | `gated_design_prs(sprint: Path) -> dict[int, dict]` | スプリントの `sprint.json` の設計のステージのプランのうち、報告の結果が承認ゲート（`関門`）のものの設計 PR の番号から `{"report": <報告のパス>, "issue": <課題>}` を引く表を返す | `sprint` がスプリントのディレクトリ | 報告を読めないプラン・PR の番号の無いプランは表に入らない | ファイルを読む（`sprint.json`・各プランの `report.md`） |
| `next_gate1_id` | `plugins/ndf/scripts/lib/parallel_design_gate.py` | 新設 | `next_gate1_id(rows: list[dict], base: str) -> tuple[str, bool, str]` | 同じ先頭の `gate1` の行の並びから、今の問いに当てる id・印 `returned` の要否・マージの結果の理由を返す | `rows` は最初の `opened` より後の台帳の行。`base` は `<レーン>-g<PR>-<先頭の 12 桁>` | `base` か `base-r<n>` の最後の行に `by: mvv` の承認の答えがあれば `(base-r<n+1>, True, <答えの merge.reason>)`。ほかは最後の行の id（無ければ `base`）と `False` と空 | 無し |
| `design_gate` | `plugins/ndf/scripts/lib/parallel_design_gate.py` | 新設 | `design_gate(main: Path, name: str, lane: str, tool_input: dict, rows: list[dict], view: Callable[[Path, int], dict \| None] = run_state.view_pr) -> DesignGate \| None` | I1 を満たす問いなら設計承認の依頼の中身を返し、満たさなければ `None` を返す | 送り手が計画 `name` の 1 スプリントのレーン `lane` で、区分が `ndf-gate:1` | 返す `DesignGate` の `pr` は、`approval_prs` がちょうど 1 つの番号を返し、その番号が承認ゲートで終わった設計 PR のときの番号で、`state` が `OPEN`、`stamp` が 40 桁 | ファイルを読む（計画の宣言・スプリント・MVV 判定の記録）・`gh pr view` を 1 回 |
| `answer_design` | `plugins/ndf/scripts/lib/parallel_design_gate.py` | 新設 | `answer_design(main: Path, name: str, rid: str, state: Path, run: Callable[..., tuple[int, str, str]] = design_pr_merge.run_cmd) -> tuple[str, str, int, dict]` | 設計承認の依頼の答えを読み、答えの種類ごとの記録を書き、(状態, 要約, 終了コード, metrics) を返す | `rid` は台帳の `gate1` の行の id | 承認ゲートの記録は同じ `pr`・`by`・`outcome` で高々 1 件。`metrics` が `result`・`by`・`merge`・`pr`・`next` を持つ | 台帳を読む・スプリント状態の書き込み（`sprint-state.py gate` を子プロセスで）・指摘ファイルの書き込み |
| `target_line` | `plugins/ndf/scripts/lib/parallel_question.py` | 新設 | `target_line(tool_input: dict) -> str` | 最初の問いの文の最後の対象の行の値を、前後の空白を除いて返す | 無し | 対象の行が無ければ空文字 | 無し |
| `prior_of` | `plugins/ndf/scripts/lib/parallel_question.py` | 新設 | `prior_of(rows: list[dict], lane: str, source: str, target: str) -> Prior \| None` | 同じ一時レーンの名前・区分・問いの対象の `ask` の行のうち最も新しいものと、その答えを返す | `rows` は最初の `opened` より後の台帳の行 | `target` が空なら `None`。返す `Prior.answer` は `{"result", "choice"}` か `None` | 無し |
| `outcome_of` | `plugins/ndf/scripts/lib/parallel_question.py` | 新設 | `outcome_of(rows: list[dict], ledger_id: str) -> dict \| None` | 判定の記録の id を持つ `gate1`・`ask`・`reused` の最後の行から、`request-ask` の `metrics` と同じ形（`kind`・`id`・`answer`・`target`・`sprint_state`）を復元する | `rows` は最初の `opened` より後の台帳の行 | `ledger_id` が空か行が無ければ `None` | 無し |
| `asked_of` | `plugins/ndf/scripts/lib/parallel_question.py` | 変更 | `asked_of(rows: list[dict], lane: str, settled: set = frozenset()) -> list[dict]`（変えない） | 一時レーンの依頼と答えの一覧を返し、`target` を持つ `ask` には答えの中身を載せる | 今と同じ | `target` を持つ答え済みの `ask` の項目は `answer: {result, choice}` を持つ。`target` の無い `ask` は今どおり `answered: true` だけ | 無し |
| `sender_kind` | `plugins/ndf/scripts/lib/parallel_lane.py` | 新設 | `sender_kind(main: Path, name: str, lane: str) -> str` | 送り手が計画の宣言の 1 スプリントのレーン・開発ループのレーン・一時レーンのどれかを `sprint`・`loop`・`helper` で返す | `sender_of` が計画 `name` の送り手と返した | 計画の宣言に `lane` が無ければ `helper` | ファイルを読む（計画の `plan.json`） |
| `ask_target` | `plugins/ndf/scripts/lib/parallel_lane.py` | 新設 | `ask_target(tool_input: dict, cwd: str) -> AskTarget` | 対象の行の値から問いの対象を作る（ブランチ名なら先端の 40 桁を足し、`#<番号>` はそのまま） | 無し | `target` は `<名>@<40 桁>`・`#<番号>`・空のどれか。先端を求められなければ空 | `git check-ref-format --branch`・`git rev-parse --verify`（`cwd` で） |
| `request_ask` | `plugins/ndf/scripts/lib/parallel_lane.py` | 変更 | `request_ask(main: Path, name: str, lane: str, tool_input: dict, cwd: str, ledger_id: str, target: str \| None = None) -> plg.Sent` | `ask` の依頼を送り、行の `target` に問いの対象を残す | 今と同じ | 行の `msg.target` が引数の `target`（空なら `null`） | 台帳の書き込み・耐久ワークフローへの知らせ |
| `record_reused` | `plugins/ndf/scripts/lib/parallel_lane.py` | 新設 | `record_reused(main: Path, name: str, lane: str, prior: Prior, source: str, target: str, ledger_id: str) -> bool` | 答えの引き継ぎの `reused` の行を 1 行書く（耐久ワークフローへは知らせない） | `prior` は `prior_of` の結果 | 鍵 `reused:<ledger_id>` の行が台帳に 1 行ある。同じ鍵の行が既にあれば書かずに真 | 台帳の書き込み |
| `forward_to_organizer` | `plugins/ndf/scripts/hook_lib/ask_guard.py` | 変更 | `forward_to_organizer(ev, row_id: str) -> dict \| None`（変えない） | `request-ask` に `deadline_at` を渡し、結果の種類ごとの `deny` を返す。上限の秒を過ぎたら台帳から結果を復元する | 今と同じ | 台帳に判定の記録の id の行があれば `deny` を返し、無ければ `None` | 子プロセス（`parallel.py request-ask`）・台帳を読む |
| `held_text` | `plugins/ndf/scripts/hook_lib/ask_guard.py` | 新設 | `held_text(out: dict, plan: str) -> str` | `request-ask` の `metrics`（か `outcome_of` の結果）の種類ごとに拒否の理由を選ぶ | `out.kind` と `out.id` がある | `gate1` は `design_hold_text`、答えのある `reused` は `reused_text`、ほかは `hold_text` の結果を返す | 無し |
| `design_hold_text` | `plugins/ndf/scripts/hook_lib/ask_guard.py` | 新設 | `design_hold_text(rid: str, plan: str, state: str) -> str` | 設計承認の依頼の保留の手順（引継ぎ文書へ `design-answer` の 1 行と依頼の id を書いて止まる）を返す | 無し | 返す文が `parallel.py design-answer <計画> <id> --state <状態>` の 1 行と自分のラッパーを止める 1 行を持つ | 無し |
| `reused_text` | `plugins/ndf/scripts/hook_lib/ask_guard.py` | 新設 | `reused_text(rid: str, answer: dict) -> str` | 前の依頼の id と答え（`choice` があれば選択肢、無ければ `result`）を載せ、その答えで操作を進めるよう示す文を返す | `answer` は `{result, choice}` | 返す文が前の依頼の id と答えを持つ | 無し |
| `held_after_timeout` | `plugins/ndf/scripts/hook_lib/ask_guard.py` | 新設 | `held_after_timeout(home: Path, plan: str, ledger_id: str) -> dict \| None` | 上限の秒を過ぎた後に台帳を 1 回読み、判定の記録の id の行があればその種類の `deny` を返す | 無し | 行が無い・読めなければ `None` | 台帳を読む |
| `cmd_design_approved` | `plugins/ndf/scripts/supervise_lib/design_merge.py` | 変更 | `cmd_design_approved(pr: int, state: str \| None, gh: Callable[[list[str]], tuple[int, str]] = _gh_out) -> tuple[dict, int]`（変えない） | 今の 2 つの証跡が無いとき、PR がマージ済みで `mvv_approval` があれば 0 で通す | 今と同じ | マージ済みかつ MVV 判定の承認の記録ありで 0（ラベルは付けない）。開いている PR は今どおり 10 | `gh pr view`（ラベルと状態）・`gh pr edit` |
| `mvv_approval` | `plugins/ndf/scripts/supervise_lib/design_merge.py` | 新設 | `mvv_approval(state: str \| None, pr: int) -> dict \| None` | スプリント状態から、設計承認ゲートの `pr` の `by: mvv`・`outcome: approved` の最後の記録を返す | 無し | 状態が無い・読めない・記録が無ければ `None` | ファイルを読む（スプリント状態） |
| `gate_locked` | `plugins/ndf/scripts/sprint-state.py` | 変更 | `gate_locked(a: argparse.Namespace) -> dict`（変えない） | `--by mvv` の記録にも、渡された `--pr` と `--outcome approved` を残す | 今と同じ | `--by mvv --pr P --outcome approved` の記録が `pr: P`・`outcome: approved` を持ち、同じ承認ゲートの名と `pr` の記録を置き換える。`--pr` を渡さない呼び出しは今と同じ形 | スプリント状態の書き込み |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `cmd_request_ask` | 標準入力を読めない | JSON でない | `json.loads` | 止める | `stopped`・2（hook は問いを通す） | 後始末なし。問いは人へ届く |
| `cmd_request_ask` | 送り手でない | レーンでも一時レーンでもない | `sender_of` | 止める | `stopped`・4（今と同じ） | 後始末なし |
| `cmd_request_ask` | 台帳を読めない | ファイルの誤り | `plg.read_ledger_rows` の `OSError` | 止める | `stopped`・2（hook は問いを通す） | 何も書いていない。次の問いで再実行できる |
| `cmd_request_ask` | 上限の秒が尽きた | 読む処理に時間が掛かった | 書く直前の `_expired` | 止める | `stopped`・2（hook は問いを通す） | 何も書いていない |
| `cmd_request_ask` | 台帳へ書けない | ロックの待ちの超過・ファイルの誤り | `sent` の `OSError` | 止める | `stopped`・2（今と同じ） | 行は書かれていない |
| `cmd_request_ask` | 同じ id で中身の違う `ask` の行がある | 判定の記録の id の衝突 | `_request_resent_fields` | 止める | `stopped`・1（今と同じ） | 後始末なし |
| `cmd_design_answer` | `answer_design` の失敗 | 下の `answer_design` の行 | `answer_design` の戻り値 | 呼び出し元へ返す | `answer_design` の終了コードのまま | `answer_design` の行のとおり |
| `gated_design_prs` | `sprint.json` を読めない | ファイルが無い・JSON でない | `json.loads` の `OSError`・`ValueError` | 既定値で続ける（空の表） | 無し（`design_gate` が `None` を返し、`ask` で送る） | 後始末なし |
| `gated_design_prs` | プランの報告を読めない | 報告が無い | `report_pr` が `None` | 既定値で続ける（そのプランを外す） | 無し | 後始末なし |
| `design_gate` | レーンのスプリントが見つからない | スプリントのディレクトリが無い・入力の課題と合わない | `find_sprint` が `None` | 既定値で続ける（`None`） | 無し（`ask` で送る） | 後始末なし |
| `design_gate` | PR の状態と先頭を読めない | `gh` の失敗・認証切れ | `view` が `None`・`headRefOid` が 40 桁でない | 既定値で続ける（`None`） | 無し（`ask` で送る。決定 3） | 後始末なし |
| `design_gate` | 承認の行の PR が開いていない | マージ済み・閉じた | `state` が `OPEN` でない | 既定値で続ける（`None`） | 無し（`ask` で送る） | 後始末なし |
| `design_gate` | MVV 判定の記録を読めない | ログが壊れている | `last_verdict` が空 | 既定値で続ける（`verdict` を `unknown`、`boundary` を空にする） | 無し | 後始末なし |
| `answer_design` | 依頼が `gate1` でない | 別の種類の id | 台帳の行の `kind` | 止める | `stopped`・1 | 書かない。正しい id で打ち直せる |
| `answer_design` | 答えがまだ無い | オーガナイザーが答えていない | 台帳に `answer` の行が無い | 止める | `stopped`・3 | 書かない。答えの後に打ち直せる |
| `answer_design` | 答えの値が承認・直し・差し戻しのどれでもない | `answered` などの答え | 答えの `result` | 止める | `stopped`・1 | 書かない |
| `answer_design` | 台帳・スプリント状態を読めない | ファイルの誤り | `OSError`・`jsonio.JsonReadError` | 止める | `stopped`・2 | 書かない。再実行できる |
| `answer_design` | `sprint-state.py gate` が失敗した | 取り消し済みの MVV 判定・ロックの待ちの超過 | 子プロセスの終了コードが 0 でない | 止める | `stopped`・2。要約に子の `summary` を載せる | 記録は書かれていない。原因を直して再実行できる |
| `answer_design` | 指摘ファイルを書けない | ファイルの誤り | `OSError` | 止める | `stopped`・2 | 書きかけを残さない（一時ファイルから置き換える）。再実行できる |
| `ask_target` | ブランチ名の形に合わない | `check-ref-format` が 0 でない | 子プロセスの終了コード | 既定値で続ける（空の対象） | 無し（同じ問いを比べずに新しい依頼。I6） | 後始末なし |
| `ask_target` | 先端を求められない | ブランチが無い・`git` が無い | `rev-parse` が 0 でないか `OSError` | 既定値で続ける（空の対象） | 無し | 後始末なし |
| `request_ask` | 台帳へ書けない | ロックの待ちの超過・ファイルの誤り | `deliver_message` の `OSError` | 呼び出し元へ返す | `cmd_request_ask` の行のとおり | 今と同じ |
| `record_reused` | 台帳へ書けない | ロックの待ちの超過・ファイルの誤り | `plg.append_ledger_row` の `OSError` | 呼び出し元へ返す（偽） | 無し（`cmd_request_ask` が `ask` の新しい依頼へ倒す。I8） | 行は書かれていない |
| `sender_kind` | 計画の宣言を読めない | ファイルの誤り | `lane_input` が `None` | 既定値で続ける（`helper`） | 無し | 後始末なし。`helper` は分岐 1 に入らず、`sender_of` が一時レーンと認めたときだけ分岐 2 に入る |
| `forward_to_organizer` | 子プロセスが上限の秒を過ぎた | 読む処理か書き込みの遅れ | `subprocess.TimeoutExpired` | 既定値で続ける（`held_after_timeout` の結果） | 台帳に行があれば `deny`、無ければ問いを通す | 子プロセスは `subprocess.run` が止める |
| `forward_to_organizer` | 子プロセスの結果が 0 でない | 送れない | 終了コード | 既定値で続ける（今どおり次の候補か `None`） | 問いを通す（4 は次の候補へ） | 今と同じ |
| `held_after_timeout` | 台帳を読めない | ファイルの誤り | `OSError` | 既定値で続ける（`None`） | 問いを通す | 後始末なし |
| `cmd_design_approved` | PR の状態を読めない | `gh` の失敗 | `gh` の終了コード | 止める | `stopped`・2（今のラベルを読めないときと同じ） | 後始末なし。`queue --resume` で再実行できる |
| `mvv_approval` | スプリント状態を読めない | ファイルが無い・壊れている | `gate_answer.gates_of` が空 | 既定値で続ける（`None`） | 無し（`cmd_design_approved` は今どおり 10） | 後始末なし |
| `gate_locked` | `--by mvv` の判定を読めない | `--verdict` が無い・`--reasons` が JSON でない | `mvv_judgement` | 止める（今と同じ） | `stopped`・2 | 書かない |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `cmd_request_ask` | 送り手が `sprint`・区分が `ndf-gate:1`・`design_gate` が中身を返す・その id の `gate1` の行が無い | 書く直前に `_expired` を見て、偽なら `request_gate1` で 1 行書く。`metrics.kind: gate1` |
| `cmd_request_ask` | 送り手が `sprint`・区分が `ndf-gate:1`・`design_gate` が中身を返す・その id の `gate1` の行が既にある | 書かない。既にある id を `metrics.kind: gate1`・`already: true` で返す（I2） |
| `cmd_request_ask` | 送り手が `helper`・`ask_target` が空でない・`prior_of` が前の依頼を返す | 書く直前に `_expired` を見て、`record_reused` が真なら `metrics.kind: reused` と `answer`（答えがあれば）を返す。偽なら次の行 |
| `cmd_request_ask` | ほか（開発ループのレーン・区分が `ndf-gate:1` でない・`design_gate` が `None`・対象が空・前の依頼が無い・`record_reused` が偽） | 書く直前に `_expired` を見て、`request_ask` で `ask` の行を 1 行書く（`target` は `ask_target` の値か `null`）。`metrics.kind: ask` |
| `answer_design` | 答えが `approved` | 承認ゲートの記録が無ければ `sprint-state.py gate <状態> 設計承認ゲート --what <要約> --pr P --by <答えの by> --outcome approved` を打つ（`by: mvv` なら `--verdict`・`--reasons`・`--log` も渡す）。`ok`・0。`next` は `sprint.json` の設計 PR のマージのステージの `resume` |
| `answer_design` | 答えが `revise` | 報告のあるプランの状態ディレクトリの `work/design-revise-<P>.md` へ答えの `note` を書く。`gate`・10 |
| `answer_design` | 答えが `rejected` | 記録が無ければ `--by <答えの by> --outcome rejected` を打つ。`gate`・10 |
| `held_text` | `kind: gate1` | `design_hold_text(id, plan, sprint_state)` |
| `held_text` | `kind: reused` で `answer` がある | `reused_text(id, answer)` |
| `held_text` | `kind: reused` で `answer` が無い・`kind: ask` | `hold_text(id, plan)`（今の保留の手順） |
| `cmd_design_approved` | 承認ラベルがある | 今どおり 0 |
| `cmd_design_approved` | ラベルが無く、利用者の承認の記録がある | 今どおりラベルを付けて 0 |
| `cmd_design_approved` | どちらも無く、PR が `MERGED` で `mvv_approval` がある | 0（ラベルは付けない） |
| `cmd_design_approved` | どちらも無く、PR が `MERGED` でないか `mvv_approval` が無い | 今どおり 10 |

- 繰り返しの終わる条件: `forward_to_organizer` の候補の繰り返しは今と同じ（候補が尽きるか全体の上限の秒）。`held_after_timeout` は台帳を 1 回だけ読み、繰り返さない
- 計算量の注意: `cmd_request_ask` は台帳を 1 回だけ読む。読んだ行は `next_gate1_id`・`prior_of`・既存の行の確かめに使い回す。台帳の行数に比例する。`gh pr view` は承認の行の設計 PR が 1 本に決まった後の 1 回だけ

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `DesignGate` | 型（データクラス。欄は設計文書の「構造」のクラス図。`state` はスプリント状態のパス） | `plugins/ndf/scripts/lib/parallel_design_gate.py` | 無し |
| `AskTarget` | 型（データクラス。`raw`・`target`） | `plugins/ndf/scripts/lib/parallel_question.py` | 無し |
| `Prior` | 型（データクラス。`id`・`answer`） | `plugins/ndf/scripts/lib/parallel_question.py` | 無し |
| `TARGET_PREFIX` | 定数（対象の行の頭） | `plugins/ndf/scripts/lib/parallel_question.py` | `"対象: "` |
| `GATE1_SOURCE` | 定数（設計承認の区分） | `plugins/ndf/scripts/lib/parallel_design_gate.py` | `"ndf-gate:1"` |
| `RETURNED_SUFFIX` | 定数（問い直しの接尾辞の頭） | `plugins/ndf/scripts/lib/parallel_design_gate.py` | `"-r"` |
| `REVISE_NOTE` | 定数（直しの指摘ファイルの名前の形） | `plugins/ndf/scripts/lib/parallel_design_gate.py` | `"design-revise-{pr}.md"` |
| `RECORD` | 定数（耐久ワークフローの状態へ反映しない行の種類） | `plugins/ndf/scripts/lib/parallel_ledger.py` | `("opened", "closed", "rebuilt", "role", "reused")` |
| 依頼の id の SHA の桁 | 定数 | `plugins/ndf/scripts/lib/gate1_requests.py` の `HEAD_LEN`（今の値を使い、新しく持たない） | `12` |
| hook の上限の秒 | 設定値 | `plugins/ndf/contracts/limits.json` の `hook.ask-forward`（今の値を使い、新しく持たない） | 8 |
| 設計 PR のマージのステージの名前 | 定数 | `plugins/ndf/scripts/supervise_lib/sprint.py` の `MERGE_STAGE`（読むだけ） | `"設計 PR のマージ"` |
| 承認ゲートで終わったプランの結果 | 定数 | `plugins/ndf/scripts/supervise_lib/queue_fill.py` の `FINISHED` の `関門`（読むだけ） | `"関門"` |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 複数のレーンと一時レーンの `request-ask`、オーガナイザーの `answer`、耐久ワークフローの記録 | 計画の台帳 | 今の `append_ledger_row` の排他（台帳のロック）。読む処理はロックの外で行い、書く 1 手だけがロックを取る | 鍵（`gate1` は依頼の id、`reused` は `reused:<ledger_id>`、`ask` は今の id）が同じ行は書かれない。書く前の中断は何も残さず、問いは人へ通る |
| hook の上限の秒の超過と、子プロセスの書き込み | 計画の台帳 | 子プロセスは書く直前に `deadline_at` を見る。hook は超過の後に 1 回だけ読む | 書いた後の超過は `held_after_timeout` が行を見つけて保留の側へ倒す。同じ先頭の打ち直しで既存の `gate1` の行を返す場合は何も書かないため、その後の超過では hook が問いを通す（既存の依頼は前の問いの時点から台帳にあり、この問いで新しく食い違いは増えない） |
| `design-answer` の 2 回の打ち直し、同じスプリントの設計のプランと `mvv-gate.py` | スプリント状態 | `sprint-state.py gate` の今のロック（`locks.exclusive(a.sprint)`） | `answer_design` は書く前に同じ `pr`・`by`・`outcome` の記録を探し、あれば書かない。`gate_locked` は同じ名と `pr` の記録を置き換える |
| `design-answer` の `revise` の打ち直し | 指摘ファイル | 一時ファイルへ書いてから置き換える | 同じ中身で上書きされるだけで、何度打っても同じ |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `cmd_request_ask` | 送り分けの結果（`kind`・`id`・`answer`・`target`・`already`） | 統一 JSON レポート（標準出力） | 問いの文の全文（台帳の行が持つ） |
| `cmd_request_ask` | 足した行 | 計画の台帳（`append_ledger_row` が秘密を伏せる） | 認証情報（今の `_redact`） |
| `record_reused` | 前の依頼の id・区分・問いの対象・判定の記録の id・答えたか | 計画の台帳の `reused` の行 | 答えの中身（答えの行が持ち、写さない） |
| `cmd_design_answer` | `result`・`by`・`merge`・`pr`・`next` | 統一 JSON レポート | 指摘の本文（ファイルのパスだけを載せる） |
| `answer_design` | 承認ゲートの記録 | スプリント状態（`sprint-state.py gate`） | 無し |
| `forward_to_organizer` | 保留の手順・前の答え | hook の `deny` の理由 | 台帳のパス以外の内部の値 |
| `cmd_design_approved` | MVV 判定の承認の記録で通したこと（`items` に `{"kind": "mvv", "result": "kept"}`） | 統一 JSON レポート | 無し |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_expired` | `parallel_lane.read_answer` の期限の扱い | `grep -n "deadline" plugins/ndf/scripts/lib/parallel_lane.py` | 使えない理由: `time.monotonic` の期限で、プロセスをまたいで渡せない。壁時計の時刻を比べる 1 行を持つ |
| `cmd_design_answer` | `parallel.py` の `cmd_await` | `plugins/ndf/scripts/parallel.py` の `cmd_await` を読んだ | 使えない理由: リリースの依頼の記録のファイルと答えの値を前提にする（決定 4） |
| `approval_prs` | `sprint_loop_gate.pr_numbers` | `grep -n "def pr_numbers" plugins/ndf/scripts/lib/*.py` | 使う（`gate1_requests.py` へ移した `pr_numbers` へ、承認の行ごとにその値だけを渡す。問いの文のほかの行と選択肢は渡さない） |
| `gated_design_prs` | `parallel_hold_sprint.next_plans`・`supervise_lib.paths.report_result`・`report_pr` | `grep -n "def next_plans\|def report_result\|def report_pr" -r plugins/ndf/scripts` | 使う（`report_result`・`state_dir_of`・`report_pr`）。`next_plans` は未着手のプランを返す関数で、承認ゲートで終わったプランを返さないため使わない |
| `next_gate1_id` | `gate1_requests.prior_id`・`sprint_loop_gate` の問い直しの接尾辞 | `grep -n "suffix" plugins/ndf/scripts/lib/sprint_loop_gate.py plugins/ndf/scripts/lib/gate1_requests.py` | 使えない理由: 接尾辞はループの記録の行の数から作り、台帳の行から作る部品が無い。id の形は `request_gate1` に任せる |
| `design_gate` | `parallel_hold_sprint.find_sprint`・`parallel_hold.lane_input`・`parallel_answer.lane_repo`・`run_state.view_pr`・`last_verdict` | `grep -n "def find_sprint\|def lane_input\|def lane_repo\|def view_pr\|def last_verdict" -r plugins/ndf/scripts/lib` | 使う（すべて読むだけの部品） |
| `answer_design` | `gate_answer.gates_of`・`mvv-gate.py` の `record_gate`・`last_verdict` | `grep -n "def gates_of\|def record_gate" -r plugins/ndf/scripts` | 使う（`gates_of` で同じ記録を探し、`record_gate` と同じく `sprint-state.py gate` を子プロセスで打つ。判定と理由は `last_verdict` の行、無ければ `gate1` の行の `verdict`・`reasons`） |
| `target_line` | `parallel_question.cause_of` | `plugins/ndf/scripts/lib/parallel_question.py` を読んだ | 使えない理由: 問いの文を伏せて鍵にする関数で、1 行を取り出さない |
| `prior_of` | `parallel_question.asked_of` | 同上 | 使えない理由: 一時レーンの全依頼の一覧を返し、区分と対象で絞らない。`asked_of` は `prior_of` と同じ答えの引き方（`answer` の行）に揃える |
| `outcome_of` | `parallel_ledger.resent` | `grep -n "def resent" plugins/ndf/scripts/lib/parallel_ledger.py` | 使えない理由: 鍵で 1 行を引き、判定の記録の id では引かない |
| `sender_kind` | `parallel_hold.lane_input`・`parallel_files.helper_plan_of` | `grep -n "def lane_input\|def helper_plan_of" plugins/ndf/scripts/lib/*.py` | 使う（`lane_input` の `kind`。無ければ `helper`） |
| `ask_target` | `parallel_lane._head_sha` | `grep -n "_head_sha" plugins/ndf/scripts/lib/parallel_lane.py` | 使えない理由: `HEAD` の先頭だけを読み、ブランチ名の形を確かめない。`git` の起動の形は揃える |
| `record_reused` | `parallel_ledger.append_ledger_row`・`parallel_lane.deliver_message` | `grep -n "def append_ledger_row\|def deliver_message" plugins/ndf/scripts/lib/*.py` | 使う（`append_ledger_row`）。`deliver_message` は耐久ワークフローへ知らせるため使わない |
| `held_text` | `ask_guard.hold_text` | `plugins/ndf/scripts/hook_lib/ask_guard.py` を読んだ | 使う（`ask` と答え待ちの `reused` の理由） |
| `design_hold_text` | `ask_guard.hold_text`・`parallel_files.stop_self` | 同上 | 使う（`stop_self` で自分のラッパーを止める 1 行を作る）。`hold_text` の文は `design-answer` を持たないため写さずに別に組む |
| `reused_text` | `ask_guard.hold_text` | 同上 | 使えない理由: 保留の手順で、答えを返す文ではない |
| `held_after_timeout` | `parallel_ledger.read_ledger_rows`・`rows_since_opened` | `grep -n "def read_ledger_rows\|def rows_since_opened" plugins/ndf/scripts/lib/parallel_ledger.py` | 使う |
| `mvv_approval` | `gate_answer.approved`・`gate_answer.gates_of`・`canonical` | `grep -n "def approved\|def gates_of\|def canonical" plugins/ndf/scripts/lib/gate_answer.py` | 使う（`gates_of`・`canonical`）。`approved` は利用者の承認だけを返すため使わない（決定 5） |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `cmd_request_ask` | 1 スプリントのレーンの承認の行つきの問いで `gate1` の行が 1 行、一時レーンの同じ問いで `reused` の行が 1 行、ほかで `ask` の行が 1 行 | 同じ先頭の打ち直しで行が増えない。`deadline_at` ちょうどで書かない | 標準入力が壊れて 2・送り手でなく 4・台帳を読めず 2・時刻を過ぎて 2 で台帳が変わらない・書けず 2・同じ id で中身が違い 1 |
| `_expired` | 未来の時刻で偽 | 等しい時刻で真。`None`・文字列で偽 | 無し（副作用なし） |
| `cmd_design_answer` | 承認で 0 と記録 1 件 | 2 回打って記録 1 件 | `answer_design` の終了コードがそのまま返る |
| `approval_prs` | 承認の行の番号を拾う | 問いの文と選択肢に PR の URL・`#<番号>` だけがあって空・承認の行が 2 行で 2 つ返す・前後の空白 | 無し（副作用なし） |
| `gated_design_prs` | `関門` の設計のプラン 2 本で 2 件 | `完了` のプランは入らない | `sprint.json` が無くて空・報告に PR の行が無いプランを外す |
| `next_gate1_id` | 行が無くて `base` | `-r1` の後に `by: mvv` の承認があり `-r2` | 無し（副作用なし） |
| `design_gate` | 承認の行が 1 行で開いていて中身を返す | 承認の行が無く問いの文で PR を書いただけで `None`・承認の行が 2 行で `None`・承認ゲートで終わった PR が 2 本で承認の行が 1 行なら中身 | スプリントが無い・`view` が `None`・`MERGED`・MVV 判定の記録が壊れて `verdict: unknown` |
| `answer_design` | 承認・直し・差し戻しのそれぞれ | `by: mvv` の承認で `--verdict` などを渡す | `gate1` でなく 1・答えが無く 3・答えが `answered` で 1・状態を読めず 2・子が失敗して 2・指摘ファイルを書けず 2 |
| `target_line` | 対象の行の値 | 対象の行が 2 行で最後・前後の空白 | 無し（副作用なし） |
| `prior_of` | 同じ名前・区分・対象の答え済みの依頼 | 同じ鍵が 2 件で新しい方。対象が空で `None` | 無し（副作用なし） |
| `outcome_of` | `gate1`・`ask`・`reused` のそれぞれを復元 | 同じ判定の記録の id の行が無くて `None` | 無し（副作用なし） |
| `asked_of` | 対象つきの答え済みの `ask` に答えの中身 | 対象の無い `ask` は `answered: true` だけ | 無し（副作用なし） |
| `sender_kind` | `sprint`・`loop` を返す | 宣言に無い名前で `helper` | 宣言を読めず `helper` |
| `ask_target` | 実在のブランチで `<名>@<40 桁>`、`#12` はそのまま | 先端の動いた同じ名前で別の値 | 形に合わない名前・無いブランチで空 |
| `request_ask` | 行の `target` に値が入る | `target` が空で `null` | 台帳へ書けず例外が呼び出し元へ届く |
| `record_reused` | `reused` の行が 1 行で、耐久ワークフローの状態が変わらない | 同じ鍵の打ち直しで行が増えない | 台帳へ書けず偽 |
| `forward_to_organizer` | `gate1`・`reused`・`ask` のそれぞれの `deny` | 子プロセスが上限ちょうどで止まる | 上限を過ぎて行があり `deny`・上限を過ぎて行が無く `None`・終了コードが 0 でなく `None` |
| `held_text` | 種類ごとに選ぶ文 | 答えの無い `reused` で今の保留の手順 | 無し（副作用なし） |
| `design_hold_text` | `design-answer` の 1 行を持つ | ラッパーの外で止める 1 行の代わりの文 | 無し（副作用なし） |
| `reused_text` | 選択肢の答えを載せる | `choice` が空で `result` を載せる | 無し（副作用なし） |
| `held_after_timeout` | 行があって `deny` | `opened` より前の行は数えない | 台帳を読めず `None` |
| `cmd_design_approved` | マージ済みと MVV 判定の承認の記録で 0 | 開いている PR と MVV 判定の記録で 10・マージ済みで記録が無く 10 | PR の状態を読めず 2 |
| `mvv_approval` | `pr` の一致する記録を返す | 別の `pr` の記録・`outcome` の無い記録は返さない | 状態を読めず `None` |
| `gate_locked` | `--by mvv --pr P --outcome approved` の記録が `pr`・`outcome` を持つ | `--pr` を渡さない `--by mvv` は今と同じ形 | `--verdict` が無く 2 で書かない |
