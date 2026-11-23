# #4000 の詳細設計: stop-wait の軽いモデルの判定

設計文書 [issue-4000-design.md](./issue-4000-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── limits.json                     判定の行を足し、Stop の予算を上げる
│   └── hooks/
│       ├── guards.json                 stop-wait の limits と summary
│       └── reply-judge-cases.json *    判定の例の組
├── hooks/
│   ├── claude.json                     ndf hooks render で作り直す（Stop の timeout）
│   └── codex.json                      同上
└── src/ndf/
    ├── adapters/
    │   ├── claude.py                   call_claude にスロットを待つ上限
    │   ├── launch_slot.py              gate・held にスロットを待つ上限
    │   └── reply_judge.py *            判定の子（hook の閉包の外）
    └── domains/hooks/
        ├── reply_judge.py *            判定のリクエスト（hook の閉包の中・標準ライブラリだけ）
        ├── guards/stop_wait.py         判定の選び方
        ├── context.py                  AskUserQuestion を使ったか
        ├── counter.py                  上限の手前かを数えずに読む
        ├── dispatch.py                 GuardServices に締め切り
        └── cli.py                      ndf hooks judge-cases
```

依存の向き: `guards/stop_wait.py` → `domains/hooks/reply_judge.py`（標準ライブラリと `tables` だけ）→（子プロセスの起動。import しない）→ `adapters/reply_judge.py` → `adapters/claude.py` → `adapters/launch_slot.py`。`domains/hooks/cli.py` は hook の閉包の外にあり（今も `ndf.contracts` を import する）、`adapters/reply_judge.py` を import してよい。循環は無い。hook の閉包に `adapters/reply_judge.py` を入れない（入ると `ndf.adapters.claude` が閉包に入り、import の表の検査が落ちる）。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/domains/hooks/reply_judge.py` | 新設 | 本文から質問に渡す文を作り、子を起動して上限の秒で待つ。休みのマーカーを読み書きする | `tables`、標準ライブラリ | モジュール | `judge_text`（新設）・`ask`（新設）・`resting`（新設）・`mark_rest`（新設）・`Judged`（新設の型） |
| `plugins/ndf/src/ndf/adapters/reply_judge.py` | 新設 | 質問の文を持ち、軽いモデルを 1 回打って回答を返す。判定の例の組を周の数だけ確かめる | `adapters.claude`、`contracts.limits` | モジュール | `classify`（新設）・`main`（新設）・`run_cases`（新設） |
| `plugins/ndf/src/ndf/domains/hooks/guards/stop_wait.py` | 変更 | 呼ばない条件を見て判定を選び、変更要求か通過を返す | `reply_judge`、`verdict` | モジュール | `check`（変更） |
| `plugins/ndf/src/ndf/domains/hooks/context.py` | 変更 | 最後の利用者の入力の後に AskUserQuestion を使ったかを読む | 標準ライブラリ | `HookContext` | `asked_since_prompt`（新設）・`HookContext.asked_this_turn`（新設） |
| `plugins/ndf/src/ndf/domains/hooks/counter.py` | 変更 | 変更要求が上限の手前かを数えずに返す | `tables` | `Counter` | `pushback_left`（新設） |
| `plugins/ndf/src/ndf/domains/hooks/dispatch.py` | 変更 | guard へ締め切りの時刻を渡す | — | `GuardServices` | `GuardServices`（欄を足す）・`run`（変更） |
| `plugins/ndf/src/ndf/domains/hooks/cli.py` | 変更 | `ndf hooks judge-cases` を足す | `adapters.reply_judge` | モジュール | `_judge_cases`（新設） |
| `plugins/ndf/src/ndf/adapters/claude.py` | 変更 | スロットを待つ上限を前置きへ渡す | `launch_slot` | モジュール | `call_claude`・`_accounted`・`_prefaced`（シグネチャの変更） |
| `plugins/ndf/src/ndf/adapters/launch_slot.py` | 変更 | スロットを待つ上限を呼び手が渡せる | `slot_pool` | モジュール | `gate`・`held`・`_wait_for`（シグネチャの変更） |

数え: 新設する関数 11（`judge_text`・`ask`・`resting`・`mark_rest`・`classify`・`main`・`run_cases`・`asked_since_prompt`・`HookContext.asked_this_turn`・`Counter.pushback_left`・`_judge_cases`）、シグネチャを変える関数 6（`call_claude`・`_accounted`・`_prefaced`・`gate`・`held`・`_wait_for`）。

## 判定のリクエスト（hook の側）

満たす決定: 決定 1・3・6・7・10、受け入れ条件 1・5・9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `judge_text` | `domains/hooks/reply_judge.py` | 新設 | `judge_text(text: str, code_lines: list[bool], max_chars: int) -> str` | コードブロックの行と空行を除いた本文を改行でつなぎ、`max_chars` を超えれば末尾の `max_chars` 字を返す |
| `ask` | `domains/hooks/reply_judge.py` | 新設 | `ask(text: str, seconds: float, env: Mapping[str, str]) -> Judged` | 判定の子を新しいプロセスの組で起動し、標準入力に `{"text", "seconds"}` を渡し、`seconds` で待って回答を読む |
| `Judged` | `domains/hooks/reply_judge.py` | 新設 | `NamedTuple(kind: Optional[str], failure: Optional[str], ms: float, calls: int)` | 判定の結果。`kind` は `wait` / `report`、回答が得られなければ None と `failure` |
| `resting` | `domains/hooks/reply_judge.py` | 新設 | `resting(state_dir: Path, now: float) -> bool` | 休みのマーカーを読み、起動の失敗から休む間（設定）が過ぎていなければ真。マーカーが無い・読めないときは偽 |
| `mark_rest` | `domains/hooks/reply_judge.py` | 新設 | `mark_rest(state_dir: Path, failure: str, now: float) -> None` | 起動の失敗（`launch`・`refused`・`usage_limit`）のときだけ休みのマーカーを書く。ほかの失敗では何もしない。書けなくても例外を投げない |

- `ask`
  - 事前条件: `seconds > 0`。`text` は `judge_text` の返り
  - 事後条件: どの終わり方でも子の組は止まって回収されている。`kind` が None なら `failure` は `timeout` / `launch` / `exit` / `shape` / `no_slot` / `refused` / `usage_limit` のどれか
  - 副作用: 子プロセスの起動（`[sys.executable, "-P", "-m", "ndf.adapters.reply_judge"]`、`start_new_session=True`、`PYTHONPATH` にプラグインの `src`、環境に `MAX_THINKING_TOKENS=0`）。`outbox.kick` と同じ起動の形

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `ask` | 時間切れ | 子が `seconds` で終わらない（スロット・認証の確かめ・モデルの遅れ） | `communicate(timeout=)` の `TimeoutExpired` | `failure="timeout"` で返す | 無し（計測に残る） | 組へ SIGTERM、0.2 秒で終わらなければ SIGKILL して回収。再実行しない |
| `ask` | 割り込み | エントリポイントの締め切りの SIGALRM が待ちの中で来る | `BaseException` | 組を止めて回収してから例外を投げ直す | エントリポイントの今の締め切りの扱い | 同上 |
| `ask` | 起動できない | python が無い・`OSError` | `Popen` | `failure="launch"` | 無し | 無し |
| `ask` | 子が 0 以外で終わる | 子の中の例外 | 終了コード | `failure="exit"` | 無し | 無し |
| `ask` | 出力の形が違う | 1 行の JSON でない・`kind` が `wait` / `report` でも `failure` でもない | JSON の読み | `failure="shape"` | 無し | 無し |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `hook.stop-wait.judge` | 設定値 | `contracts/limits.json` | `{"summary": …, "model": "haiku", "seconds": 5, "max_chars": 6000, "rest_seconds": 600, "rounds": 3}`（`rest_seconds` は起動が失敗した後に休む間（決定 10）、`rounds` は `judge-cases` が 1 つの例を打つ周の数（決定 9）） |
| `hook.budget.stop` | 設定値 | `contracts/limits.json` | `p95_ms` 2500・`deadline` 7・`margin` 1.5・`timeout` 9（他のキーは今のまま） |
| `CHILD_MODULE` | 定数 | `domains/hooks/reply_judge.py` | `"ndf.adapters.reply_judge"` |
| 休みのマーカー | ファイル | `<状態の保存先>/hooks/judge-rest.json` | 無し（起動が失敗したときだけ `{"at": <失敗した時刻>, "failure": "<種類>"}` を書く。`metrics.prune_state` が消す対象と同じ保存先） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `judge_text` | 質問の後に 9 行の報告が続く本文で、質問の行が返りに入る | ちょうど `max_chars` 字と 1 字多い本文。コードブロックの中の質問が返りに入らない | — |
| `ask` | 偽の子（`wait` を出す python のスクリプト）で `kind="wait"` と `calls` | `seconds` ちょうどで終わる子 | 眠り続ける偽の子で `timeout`、組の子孫が残らない。0 以外で終わる子で `exit`。形の違う出力で `shape`。待ちの中の割り込みで組が止まる |
| `resting` / `mark_rest` | `launch` を書いた直後は真、休む間を過ぎた時刻では偽 | 休む間のちょうどの時刻 | マーカーが無い・壊れたマーカー・書けない保存先で偽（例外を投げない）。`timeout`・`no_slot` を渡すとマーカーを書かない |

## 判定の子

満たす決定: 決定 1・4・8・9・10、受け入れ条件 3・9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `classify` | `adapters/reply_judge.py` | 新設 | `classify(text: str, seconds: float, call: Caller = claude.call_claude) -> dict` | 質問の文で 1 回だけ打ち、`{"kind", "calls"}` か `{"failure", "calls"}` を返す |
| `main` | `adapters/reply_judge.py` | 新設 | `main(argv: Optional[list[str]] = None) -> int` | 標準入力の JSON を読み、`classify` の返りを 1 行の JSON で標準出力へ書く。0 で終わる |
| `run_cases` | `adapters/reply_judge.py` | 新設 | `run_cases(path: Path, rounds: int, call: Caller = claude.call_claude) -> dict` | 判定の例の組を 1 件ずつ `rounds` 周判定し、1 周でも外れた例・件数・秒を返す |

- `classify`
  - 起動の引数: `claude.claude_args(ClaudeCall(system=SYSTEM, permission=Permission.JUDGE, model=<設定の model>))`（最小構成・Tool なし・会話を残さない）。回答は `ClaudeReply.raw` の本文を空白を除いて読み、`wait` か `report` に完全に一致しなければ `shape`
  - 打つ秒: `seconds − 経過`。残りが 0.5 秒を切れば打たずに `failure="timeout"`
  - スロット: `call_claude(..., slot_wait=0)`
  - 失敗の写し: `ClaudeReply.failure` の `launch` / `timeout` / `refused` / `usage_limit` / `no_slot` をそのまま `failure` へ。`ok` が偽で `failure` が None なら `exit`
- 質問の文（`SYSTEM` と `QUESTION`）はこのモジュールの定数に置く。`QUESTION` は設計の外部の調査の MEA6 の形にする。書き手自身が人に判断・選択・承認を求め、その回答を待っていれば `wait`、質問が途中にあり後に報告が続いても `wait`、それ以外は `report` とし、`report` の例として「他人の質問を引用して経緯を説明している」「ほかのセッションやレーンの承認待ちを伝えている」「承認を求める資料を用意したことや作業の結果を報告している」の 3 つを並べ、1 語だけを返させる。本文は `<reply>` と `</reply>` で囲む
- 判定の例の組の形: `{"version": 1, "cases": [{"id": "miss-1", "text": "…", "expect": "wait", "source": "#4000 の 1 回目"}, …]}`。要求の受け入れ条件 2 の文を持つ

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `main` | 入力が JSON でない・`text` が無い | 呼び手の誤り | `json.loads` | `{"failure": "shape", "calls": 0}` を書く | 0 で終わる | 無し |
| `run_cases` | 組のファイルが読めない | ファイルが無い・形が違う | 読み込み | 呼び手（CLI）へ例外 | `ndf hooks judge-cases` が `refused` の結果 JSON と終了コード 1 | 直して打ち直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `classify` | 偽の `call` が `report` → `kind="report"`・`calls=1`。`wait` → `kind="wait"`・`calls=1`（同じ Stop で 2 回打たない） | 残りが 0.5 秒を切る → 打たずに `timeout` | `failure` を返す `call` → その種類。`Wait.`・`wait\n理由…` → `shape` |
| `classify` の引数 | 起動の引数に `--setting-sources ""`・`--strict-mcp-config`・`--tools ""`・`--model <設定の値>` が入る | 設定の model を替えると引数が替わる | — |
| `run_cases` | 偽の `call` で全件当たり → 外れ 0、打った回数は 件数 × `rounds` | `rounds=1` で 1 周だけ打つ | 3 周のうち 1 周だけ外す偽の `call` → その例が外れとして出る |

## guard `stop-wait`

満たす決定: 決定 1・4・5・6・10、受け入れ条件 1・4・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `guards/stop_wait.py` | 変更 | `check(ctx, svc) -> Verdict`（変えない） | 呼ばない条件を見て、軽いモデルの判定か文字列の判定かを選び、変更要求か通過を返す |

### 処理の手順

| 条件の組み合わせ | 結果 |
| --- | --- |
| 文字列の判定が `none`（本文が空） | 通す（`reply=none`・`judge=skip:empty`） |
| `ctx.asked_this_turn` が真 | 文字列の判定で決める（`judge=skip:asked`） |
| `ctx.background_tasks` が空でない | 文字列の判定で決める（`judge=skip:background`） |
| `svc.counter.pushback_left()` が偽 | 文字列の判定で決める（`judge=skip:limit`） |
| `reply_judge.resting(svc.state_dir, 今)` が真（前の起動の失敗から休む間のうち） | 文字列の判定で決める（`judge=skip:rest`） |
| 上のどれでもなく、残りの秒（`svc.deadline − 今 − 余白` と設定の `seconds` の小さい方）が 0.5 秒を切る | 文字列の判定で決める（`judge=skip:time`） |
| 上のどれでもない | `reply_judge.ask(judge_text(...), 残りの秒, os.environ)`。`kind="wait"` なら変更要求（今の文面）、`report` なら通す。`kind` が None なら `mark_rest` を呼んでから文字列の判定で決める（`judge=fallback:<failure>`） |

- 文字列の判定は今の `ctx.last_reply_kind`（`classify_reply`）をそのまま使う
- 変更要求の文面・上限・上限の後の待ちの知らせは変えない（dispatch と main が持つ）
- `svc.state_dir` は `GuardServices` が今も持つ状態の保存先である

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `check` | 判定の notes: `reply`（`prose_wait` / `report` / `elsewhere_wait` / `none`）・`judge`（`model` / `fallback:<failure>` / `skip:<理由>`）・`judge_ms`・`judge_calls` | hook の計測の行（`<状態の保存先>/hooks/<日付>.jsonl`） | 本文 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 偽の `ask` が `wait` → pushback、`report` → 通過。#4000 の 2 回目の形の本文で `ask` に質問が渡る | 残りの秒が 0.5 秒を切る → `ask` を呼ばない | 呼ばない条件 4 つでそれぞれ `ask` が呼ばれない。`ask` の失敗の種類ごとに文字列の判定と同じ判定・`judge=fallback:<failure>` |
| `check` の休みのマーカー | `ask` が `launch` を返した次の `check` で `ask` が呼ばれない（`judge=skip:rest`） | 休む間を過ぎた時刻の `check` で呼ばれる | `ask` が `timeout` を返した次の `check` では呼ばれる（マーカーを書かない） |

## hook の文脈・数え手・締め切り

満たす決定: 決定 5、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `asked_since_prompt` | `context.py` | 新設 | `asked_since_prompt(tail: str) -> bool` | 会話の記録の末尾を後ろから読み、利用者の入力（`type` が `user` で `content` が文字列か text の要素を持つ行）に当たる前に、`name` が `AskUserQuestion` の `tool_use` があれば真 |
| `HookContext.asked_this_turn` | `context.py` | 新設 | `cached_property -> bool` | `asked_since_prompt(self.transcript_tail)` |
| `Counter.pushback_left` | `counter.py` | 新設 | `pushback_left(self) -> bool` | 数えずに、今の入力の後の変更要求が上限の手前かを返す。数え手が記録の排他を取れず数えられない状態（`usable` が偽）のときは偽 |
| `GuardServices` | `dispatch.py` | 変更 | 欄 `deadline: float`（`time.monotonic()` の基準の締め切りの時刻）を足す | guard が残りの秒を求める |
| `run` | `dispatch.py` | 変更 | シグネチャは変えない | `GuardServices` に `start + budget.usable` を渡す |

- `asked_since_prompt` の境: tool_result だけを持つ `user` の行（Tool の結果）は利用者の入力に数えない。末尾に利用者の入力が無ければ、読めた範囲で探す

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `asked_since_prompt` | 入力 → AskUserQuestion → 結果 → 本文 で真。入力 → Bash → 本文 で偽 | 前の入力の前にだけ AskUserQuestion がある → 偽。Tool の結果の `user` の行を入力と読まない | 壊れた行が混じっても読める行で決める |
| `pushback_left` | 0 回・上限−1 回で真 | 上限ちょうどで偽 | 数えない数え手で偽。呼んでも回数が増えない |

## 起動の前置きのスロットを待つ上限

満たす決定: 決定 8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `call_claude` | `adapters/claude.py` | 変更 | `call_claude(argv, prompt, timeout, cwd=None, slot_wait: Optional[float] = None) -> ClaudeReply` | `slot_wait` を前置きへ渡す。None なら今の上限（`launch.wait.llm-cli`） |
| `_accounted` | `adapters/claude.py` | 変更 | 末尾に `slot_wait: Optional[float]` | 同上を `_prefaced` へ渡す |
| `_prefaced` | `adapters/claude.py` | 変更 | 末尾に `slot_wait: Optional[float]` | `launch_slot.gate(..., wait=slot_wait)` |
| `gate` | `adapters/launch_slot.py` | 変更 | `gate(agent, *, cwd=None, env=None, wait: Optional[float] = None)` | `held` へ渡す |
| `held` | `adapters/launch_slot.py` | 変更 | `held(agent, *, cwd=None, environ=None, read_memory=None, wait: Optional[float] = None)` | `_wait_for` へ渡す。親のスロットを受け継ぐときは今のとおり待たない |
| `_wait_for` | `adapters/launch_slot.py` | 変更 | `_wait_for(agent, directory, lim, read_memory, limit: Optional[float] = None)` | 待つ上限を `limit`（None なら設定）にする。0 なら 1 度だけ取りに行き、取れなければ `NoSlot` |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_wait_for` | `limit` を省くと今の上限で待つ | `limit=0` で空きが無ければ眠らずに `NoSlot` | — |
| `call_claude` | `slot_wait=0` で空きが無ければ `Failure.NO_SLOT` を返す（起動しない） | — | — |

## 設定と登録

満たす決定: 決定 2、受け入れ条件 7・8

- `contracts/limits.json`: 上の「内部のデータと定数」の 2 行。`hook.stop-wait.judge` の `seconds`（5）≤ `hook.budget.stop` の締め切り − 余白（7 − 1.5 = 5.5）。`rest_seconds`（600）と `rounds`（3）は秒の順序の検査に出てこない値で、`limits` の行には `hook.stop-wait.judge` だけを挙げる
- `contracts/hooks/guards.json`: `stop-wait` の `limits` を `["hook.stop-wait.judge"]` にし、`summary` に軽いモデルの判定と戻り方を足す
- `hooks/claude.json`・`hooks/codex.json`: `ndf hooks render` で作り直す（Stop の timeout 6 → 9）。手で書かない
- `ndf hooks judge-cases [--cases <パス>] [--rounds <周の数>]`: 既定のパスは `contracts/hooks/reply-judge-cases.json`、既定の周の数は設定の `rounds`（3）。各例を周の数だけ打ち、1 周でも外れた例を結果 JSON の `items` に `id`・`expect`・外れた周の `got` で出す。`metrics` に件数・外れた例の件数・打った回数・秒の最小と最大。外れがあれば `status` は `stopped`（終了コード 1）
