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
    │   ├── claude.py                   call_claude にスロットを待つ上限と組の選び方
    │   ├── launch_slot.py              gate・held にスロットを待つ上限
    │   └── reply_judge.py *            判定の子（hook の閉包の外）
    └── domains/hooks/
        ├── reply_judge.py *            判定のリクエスト（hook の閉包の中・標準ライブラリだけ）
        ├── guards/stop_wait.py         判定の選び方
        ├── context.py                  AskUserQuestion を使ったか
        ├── counter.py                  上限の手前か・モデルの wait の印
        ├── dispatch.py                 GuardServices に締め切り
        ├── main.py                     SIGALRM と同じ締め切りを dispatch へ
        └── cli.py                      ndf hooks judge-cases
```

依存の向き: `guards/stop_wait.py` → `domains/hooks/reply_judge.py`（標準ライブラリと `tables` だけ）→（子プロセスの起動。import しない）→ `adapters/reply_judge.py` → `adapters/claude.py` → `adapters/launch_slot.py`。`domains/hooks/cli.py` は hook の閉包の外にあり（今も `ndf.contracts` を import する）、`adapters/reply_judge.py` を import してよい。循環は無い。hook の閉包に `adapters/reply_judge.py` を入れない（入ると `ndf.adapters.claude` が閉包に入り、import の表の検査が落ちる）。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/domains/hooks/reply_judge.py` | 新設 | 本文から質問に渡す文を作り、送信先が同じかを確かめ、子を起動して上限の秒で待つ。休みのマーカーを読み書きする | `tables`、標準ライブラリ | モジュール | `judge_text`（新設）・`same_endpoint`（新設）・`ask`（新設）・`resting`（新設）・`mark_rest`（新設）・`Judged`（新設の型） |
| `plugins/ndf/src/ndf/adapters/reply_judge.py` | 新設 | 質問の文を持ち、軽いモデルを 1 回打って回答を返す。判定の例の組を周の数だけ確かめる | `adapters.claude`、`contracts.limits` | モジュール | `classify`（新設）・`main`（新設）・`run_cases`（新設） |
| `plugins/ndf/src/ndf/domains/hooks/guards/stop_wait.py` | 変更 | 呼ばない条件を見て判定を選び、変更要求か通過を返す | `reply_judge`、`verdict` | モジュール | `check`（変更） |
| `plugins/ndf/src/ndf/domains/hooks/context.py` | 変更 | 最後の利用者の入力の後に AskUserQuestion を使ったかを読む | 標準ライブラリ | `HookContext` | `asked_since_prompt`（新設）・`HookContext.asked_this_turn`（新設） |
| `plugins/ndf/src/ndf/domains/hooks/counter.py` | 変更 | 変更要求が上限の手前かを数えずに返す。今の入力の中でモデルが `wait` と回答した印を持つ | `tables` | `Counter` | `pushback_left`（新設）・`mark_model_wait`（新設）・`model_waited`（新設） |
| `plugins/ndf/src/ndf/domains/hooks/dispatch.py` | 変更 | guard へ締め切りの時刻を渡す | — | `GuardServices` | `GuardServices`（欄を足す）・`run`（シグネチャの変更） |
| `plugins/ndf/src/ndf/domains/hooks/main.py` | 変更 | SIGALRM に使う締め切りの絶対時刻を `dispatch.run` へ渡す | `dispatch` | モジュール | `_guards`（中身だけ変える） |
| `plugins/ndf/src/ndf/domains/hooks/cli.py` | 変更 | `ndf hooks judge-cases` を足す | `adapters.reply_judge` | モジュール | `_judge_cases`（新設） |
| `plugins/ndf/src/ndf/adapters/claude.py` | 変更 | スロットを待つ上限と、新しいプロセスの組で起動するかを前置きと起動へ渡す | `launch_slot` | モジュール | `call_claude`・`_accounted`・`_prefaced`・`_run_claude`・`_popen`（シグネチャの変更） |
| `plugins/ndf/src/ndf/adapters/launch_slot.py` | 変更 | スロットを待つ上限を呼び手が渡せる | `slot_pool` | モジュール | `gate`・`held`・`_wait_for`（シグネチャの変更） |

数え: 新設する関数 14（`judge_text`・`same_endpoint`・`ask`・`resting`・`mark_rest`・`classify`・`main`・`run_cases`・`asked_since_prompt`・`HookContext.asked_this_turn`・`Counter.pushback_left`・`Counter.mark_model_wait`・`Counter.model_waited`・`_judge_cases`）、シグネチャを変える関数 9（`call_claude`・`_accounted`・`_prefaced`・`_run_claude`・`_popen`・`gate`・`held`・`_wait_for`・`dispatch.run`）。

## 判定のリクエスト（hook の側）

満たす決定: 決定 1・3・6・7・10・11、受け入れ条件 1・5・9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `judge_text` | `domains/hooks/reply_judge.py` | 新設 | `judge_text(text: str, code_lines: list[bool], max_chars: int) -> str` | コードブロックの行と空行を除いた本文を改行でつなぎ、`max_chars` を超えれば末尾の `max_chars` 字を返す |
| `same_endpoint` | `domains/hooks/reply_judge.py` | 新設 | `same_endpoint(runtime: str, env: Mapping[str, str], pid: int, read: Callable[[Path], bytes] = <ファイルの読み>) -> bool` | 送信先がセッション自身の Claude の API と同じと確かめられれば真（設計の決定 11）。次をすべて満たすときだけ真、ほかは偽。(1) `runtime == "claude"`。(2) `pid`（hook のプロセス）から `/proc/<pid>/stat` の親の番号をたどり、`endpoint.ancestors` 代のうちに `/proc/<pid>/cmdline` が `endpoint.parent_argv` に当たる（引数の 1 つの basename か末尾が一致する）プロセスがあり、その引数に `endpoint.flags` のどれも無い。(3) `env` に `CLAUDE_PROJECT_DIR` がある（Stop の入力の `cwd` は使わない）。(4) 設定ファイル（`endpoint.managed` のパス・`$CLAUDE_CONFIG_DIR/settings.json`（無ければ `~/.claude/settings.json`）・`$CLAUDE_PROJECT_DIR/.claude/settings.json`・`$CLAUDE_PROJECT_DIR/.claude/settings.local.json`）のどれにも `endpoint.keys` の最上位のキーが無く、`env` のオブジェクトに名前が `endpoint.env_prefixes` で始まる変数が無い。無い設定ファイルは飛ばし、`/proc` や設定ファイルが読めない（`OSError`）・JSON でないときは偽 |
| `ask` | `domains/hooks/reply_judge.py` | 新設 | `ask(text: str, seconds: float, env: Mapping[str, str], popen: Callable[..., subprocess.Popen] = subprocess.Popen, killpg: Callable[[int, int], None] = os.killpg) -> Judged` | 判定の子を新しいプロセスの組で起動し、標準入力に `{"text", "seconds"}` を渡し、`seconds` で待って回答を読む。どの終わり方でも組へ送って回収する |
| `Judged` | `domains/hooks/reply_judge.py` | 新設 | `NamedTuple(kind: Optional[str], failure: Optional[str], ms: float, calls: int)` | 判定の結果。`kind` は `wait` / `report`、回答が得られなければ None と `failure` |
| `resting` | `domains/hooks/reply_judge.py` | 新設 | `resting(state_dir: Path, now: float) -> bool` | 休みのマーカーを読み、起動の失敗から休む間（設定）が過ぎていなければ真。マーカーが無い・読めないときは偽 |
| `mark_rest` | `domains/hooks/reply_judge.py` | 新設 | `mark_rest(state_dir: Path, failure: str, now: float) -> None` | 起動の失敗（`launch`・`refused`・`usage_limit`・`auth`）のときだけ休みのマーカーを書く。ほかの失敗では何もしない。書けなくても例外を投げない |

- `ask`
  - 事前条件: `seconds > 0`。`text` は `judge_text` の返り
  - 事後条件: どの終わり方でも子の組（判定の子と、同じ組で起動した `claude`）は止まって回収されている。`kind` が None なら `failure` は `timeout` / `launch` / `exit` / `shape` / `no_slot` / `refused` / `usage_limit` / `auth` のどれか
  - 副作用: 子プロセスの起動（`[sys.executable, "-P", "-m", "ndf.adapters.reply_judge"]`、`start_new_session=True`、`PYTHONPATH` にプラグインの `src`、環境に `MAX_THINKING_TOKENS=0`）。`outbox.kick` と同じ起動の形
  - 子へ渡す環境: `env` から、環境変数の表（`contracts/domains/env.json`。hook の側は `tables.load` で `json` のまま読む）の `child` が `drop` の行の変数を外す。`relay/session_env.py` の `tool_env` と同じ行で、`CLAUDECODE`・`CLAUDE_CODE_SESSION_ID` を含み、接続先と認証の変数を含まない（外す名前をコードに書かない）。そのうえで `PYTHONPATH` と `MAX_THINKING_TOKENS` を入れる
  - 組の止め方: 時間切れ・例外のときは組（`proc.pid`）へ SIGTERM、`proc.wait(timeout=0.2)` で終わらなければ SIGKILL を送って `proc.wait()` で回収する。正常に終えたときも、組に残る者があれば止めるため SIGKILL を組へ送る（`ProcessLookupError` は受け流す）。判定の子は `claude` を同じ組で起動する（下の「判定の子」）ので、組への送信が `claude` まで届く

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `ask` | 時間切れ | 子が `seconds` で終わらない（スロット・認証の確かめ・モデルの遅れ） | `communicate(timeout=seconds)` の `TimeoutExpired` | `failure="timeout"` で返す | 無し（計測に残る） | 組へ SIGTERM、0.2 秒で終わらなければ SIGKILL して回収。再実行しない |
| `ask` | 割り込み | エントリポイントの締め切りの SIGALRM が待ちの中で来る | `BaseException` | 組を止めて回収してから例外を投げ直す | エントリポイントの今の締め切りの扱い | 同上 |
| `ask` | 起動できない | python が無い・`OSError` | `Popen` | `failure="launch"` | 無し | 無し |
| `ask` | 子が 0 以外で終わる | 子の中の例外 | 終了コード | `failure="exit"` | 無し | 無し |
| `ask` | 出力の形が違う | 1 行の JSON でない・`kind` が `wait` / `report` でも `failure` でもない | JSON の読み | `failure="shape"` | 無し | 無し |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `hook.stop-wait.judge` | 設定値 | `contracts/limits.json` | `{"summary": …, "model": "haiku", "seconds": 5, "max_chars": 6000, "rest_seconds": 600, "rounds": 3, "auth_markers": ["Not logged in", "Invalid API key"], "endpoint": {"ancestors": 8, "parent_argv": ["claude", "@anthropic-ai/claude-code/cli.js"], "flags": ["--settings", "--setting-sources"], "managed": ["/etc/claude-code/managed-settings.json"], "keys": ["apiKeyHelper", "awsAuthRefresh", "awsCredentialExport", "gcpAuthRefresh"], "env_prefixes": ["ANTHROPIC_", "CLAUDE_CODE_USE_", "CLAUDE_CONFIG_DIR", "AWS_", "GOOGLE_", "CLOUD_ML_", "VERTEX_"]}}`（`rest_seconds` は起動が失敗した後に休む間（決定 10）、`rounds` は `judge-cases` が 1 つの例を打つ周の数（決定 9）、`auth_markers` は認証の失敗と読む本文の文字列（決定 10、MEA8）、`endpoint` は送信先が同じかの確かめに使う値（決定 11。`env_prefixes` は設定の `env` が hook の環境に入ると確かめるまでの安全側の決まりで、確かめが済めば空にする）） |
| `hook.budget.stop` | 設定値 | `contracts/limits.json` | `p95_ms` 2500・`deadline` 7・`margin` 1.5・`timeout` 9（他のキーは今のまま） |
| `CHILD_MODULE` | 定数 | `domains/hooks/reply_judge.py` | `"ndf.adapters.reply_judge"` |
| 休みのマーカー | ファイル | `<状態の保存先>/hooks/judge-rest.json` | 無し（起動が失敗したときだけ `{"at": <失敗した時刻>, "failure": "<種類>"}` を書く。`metrics.prune_state` が消す対象と同じ保存先） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `judge_text` | 質問の後に 9 行の報告が続く本文で、質問の行が返りに入る | ちょうど `max_chars` 字と 1 字多い本文。コードブロックの中の質問が返りに入らない | — |
| `ask` | 偽の `popen`（実プロセスを起動しない。`communicate` が `{"kind": "wait", "calls": 1}` の 1 行と終了コード 0 を返す）で `kind="wait"` と `calls`。`popen` に渡った引数が `[sys.executable, "-P", "-m", "ndf.adapters.reply_judge"]`・`start_new_session=True` で、環境に `MAX_THINKING_TOKENS=0` があり `CLAUDECODE`・`CLAUDE_CODE_SESSION_ID` が無く `ANTHROPIC_BASE_URL` が残る。`communicate` に渡った `timeout` が `seconds` | 正常に終えた後も偽の `killpg` に組の番号と SIGKILL が渡る | 偽の `communicate` が `TimeoutExpired` を上げる → `timeout`、偽の `killpg` に SIGTERM、偽の `wait(timeout=0.2)` が `TimeoutExpired` なら続けて SIGKILL、最後に `wait()` が呼ばれる。偽の `communicate` が `KeyboardInterrupt` を上げる → 同じ順で送って回収し、例外を投げ直す。`popen` が `OSError` → `launch`。終了コード 1 → `exit`。形の違う出力 → `shape`。どれも実時間を待たない |
| `same_endpoint`（偽の `read` で `/proc` と設定ファイルを差し替える。実の `/proc` もファイルも読まない） | ランタイム `claude`・親の引数が `claude -p …`（`--settings` 無し）・`CLAUDE_PROJECT_DIR` あり・設定ファイルにキーも接続の `env` も無い → 真 | 設定ファイルが 1 つも無い → 真。親が `ancestors` 代目にいる → 真、その 1 代上 → 偽。設定の `env` が接続に関わらない名前（`FOO`）だけ → 真 | ランタイム `codex` → 偽。親の引数に `--settings '{"apiKeyHelper":…}'` または `--settings /path/a.json` → 偽。`/proc` の読みが `OSError`（macOS の代わり）→ 偽。`CLAUDE_PROJECT_DIR` が無い → 偽。`cwd` を別の所（プロジェクトの根の下のサブディレクトリ・別の worktree）にしても `$CLAUDE_PROJECT_DIR/.claude/settings.local.json` の `apiKeyHelper` で偽（`cwd` の側の設定は読まれない）。管理者の設定・利用者の設定に `apiKeyHelper` → 偽。設定の `env` に `ANTHROPIC_BASE_URL`・`CLAUDE_CODE_USE_BEDROCK`・`AWS_PROFILE` → 偽。`env_prefixes` を空にした設定では `ANTHROPIC_BASE_URL` があっても真。JSON でない・読めない設定ファイル → 偽 |
| `resting` / `mark_rest` | `launch`・`auth` を書いた直後は真、休む間を過ぎた時刻では偽 | 休む間のちょうどの時刻 | マーカーが無い・壊れたマーカー・書けない保存先で偽（例外を投げない）。`timeout`・`no_slot`・`exit` を渡すとマーカーを書かない |

時刻はどれも引数（`now`）か偽の時計で渡し、実時間の経過と実プロセスの起動をテストの仕様にしない（tdd-cycle の「テストに書かないもの」）。

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
  - スロットと組: `call_claude(..., slot_wait=0, new_group=False)`。`claude` を判定の子と同じプロセスの組で起動し、hook の側の組への送信が `claude` まで届くようにする（今の `_popen` は `start_new_session=True` で別の組に置く）。判定の子は中継の確かめ（`claude.register` の `relay_checks`）を登録しないので、起動は `_prefaced` → `_run_claude` の経路を通る
  - 打つ秒の内訳: `claude --version`（`cli_floor` の下限。版の記録はプロセスの中だけなので、子ごとに 1 回打つ。0.007〜0.009 秒）を含めて `seconds − 経過` に収める
  - 失敗の写し: `ClaudeReply.failure` の `launch` / `timeout` / `refused` / `usage_limit` / `no_slot` をそのまま `failure` へ。`ok` が偽で `failure` が None のとき、`ClaudeReply.raw`（結果の行の本文か標準エラー）が設定の `auth_markers` のどれかを含めば `auth`、含まなければ `exit`（認証の失敗は今の `call_claude` では `failure` が None になる。設計の MEA8）
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
| `classify` | 偽の `call` が `report` → `kind="report"`・`calls=1`。`wait` → `kind="wait"`・`calls=1`（同じ Stop で 2 回打たない）。偽の `call` に `slot_wait=0`・`new_group=False` が渡る | 残りが 0.5 秒を切る（偽の時計）→ 打たずに `timeout` | `failure` を返す `call` → その種類。`failure` が None で本文が「Not logged in · Please run /login」・「Invalid API key · Fix external API key」→ `auth`、ほかの本文 → `exit`。`Wait.`・`wait\n理由…` → `shape` |
| `classify` の引数 | 起動の引数に `--setting-sources ""`・`--strict-mcp-config`・`--tools ""`・`--model <設定の値>` が入る | 設定の model を替えると引数が替わる | — |
| `run_cases` | 偽の `call` で全件当たり → 外れ 0、打った回数は 件数 × `rounds` | `rounds=1` で 1 周だけ打つ | 3 周のうち 1 周だけ外す偽の `call` → その例が外れとして出る |

## guard `stop-wait`

満たす決定: 決定 1・4・5・6・10・11、受け入れ条件 1・4・5・6

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
| `svc.counter.pushback_left()` が偽 | 文字列の判定で決める（`judge=skip:limit`）。ただし文字列の判定が `report` で `svc.counter.model_waited()` が真なら変更要求を返す（dispatch が上限で通し、待ち通知を送る。設計の決定 5） |
| `reply_judge.same_endpoint(ctx.runtime, os.environ, os.getpid())` が偽（Codex のセッション・起動の引数や設定ファイルで接続を選ぶ・確かめられない） | 文字列の判定で決める（`judge=skip:endpoint`） |
| `reply_judge.resting(svc.state_dir, 今)` が真（前の起動の失敗から休む間のうち） | 文字列の判定で決める（`judge=skip:rest`） |
| 上のどれでもなく、残りの秒（`svc.deadline − 余白 − 今` と設定の `seconds` の小さい方）が 0.5 秒を切る | 文字列の判定で決める（`judge=skip:time`） |
| 上のどれでもない | `reply_judge.ask(judge_text(...), 残りの秒, os.environ)`。`kind="wait"` なら `svc.counter.mark_model_wait()` を呼んで変更要求（今の文面）、`report` なら通す。`kind` が None なら `mark_rest` を呼んでから文字列の判定で決める（`judge=fallback:<failure>`） |

- 文字列の判定は今の `ctx.last_reply_kind`（`classify_reply`）をそのまま使う
- 変更要求の文面・上限・上限の後の待ちの知らせは変えない（dispatch と main が持つ）。上限の後に変更要求を返すのは、待ちの知らせへ届けるためだけである
- `svc.state_dir` は `GuardServices` が今も持つ状態の保存先である
- 残りの秒の余白は、ここで 1 回だけ引く。`svc.deadline` は余白を引いていない絶対時刻（SIGALRM と同じ。下の「hook の文脈・数え手・締め切り」）である

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `check` | 判定の notes: `reply`（`prose_wait` / `report` / `elsewhere_wait` / `none`）・`judge`（`model` / `fallback:<failure>` / `skip:<理由>`）・`judge_ms`・`judge_calls` | hook の計測の行（`<状態の保存先>/hooks/<日付>.jsonl`） | 本文 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 偽の `ask` が `wait` → pushback と `model_waited()` が真、`report` → 通過。#4000 の 2 回目の形の本文で `ask` に質問が渡る | 残りの秒が 0.5 秒を切る → `ask` を呼ばない。`svc.deadline` が今 ＋ 7 秒・余白 1.5 秒・設定 5 秒のとき `ask` に渡る秒が 5（余白を 2 回引かない） | 呼ばない条件 4 つと送信先の条件でそれぞれ `ask` が呼ばれない。`ask` の失敗の種類ごとに文字列の判定と同じ判定・`judge=fallback:<failure>` |
| `check` の上限の後 | `model_waited()` が真の数え手で上限に達し、本文が文字列の判定で `report` → pushback（`ask` は呼ばない）。dispatch を通すと `limited` が真になる | `model_waited()` が偽 → 通す | — |
| `check` の休みのマーカー | `ask` が `launch` を返した次の `check` で `ask` が呼ばれない（`judge=skip:rest`） | 休む間を過ぎた時刻の `check` で呼ばれる | `ask` が `timeout` を返した次の `check` では呼ばれる（マーカーを書かない） |

## hook の文脈・数え手・締め切り

満たす決定: 決定 5、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `asked_since_prompt` | `context.py` | 新設 | `asked_since_prompt(tail: str) -> bool` | 会話の記録の末尾を後ろから読み、利用者の入力（`type` が `user` で `content` が文字列か text の要素を持つ行）に当たる前に、`name` が `AskUserQuestion` の `tool_use` があれば真 |
| `HookContext.asked_this_turn` | `context.py` | 新設 | `cached_property -> bool` | `asked_since_prompt(self.transcript_tail)` |
| `Counter.pushback_left` | `counter.py` | 新設 | `pushback_left(self) -> bool` | 数えずに、今の入力の後の変更要求が上限の手前かを返す。数え手が記録の排他を取れず数えられない状態（`usable` が偽）のときは偽 |
| `Counter.mark_model_wait` | `counter.py` | 新設 | `mark_model_wait(self) -> None` | 今の入力の記録に `model_wait: true` を置く（入力が変わると記録ごと初期化される）。`usable` が偽なら何もしない |
| `Counter.model_waited` | `counter.py` | 新設 | `model_waited(self) -> bool` | 今の入力の記録の `model_wait`。無ければ偽 |
| `GuardServices` | `dispatch.py` | 変更 | 欄 `deadline: float`（`time.monotonic()` の基準の締め切りの絶対時刻。余白を引かない）を足す | guard が残りの秒を求める |
| `run` | `dispatch.py` | 変更 | 引数 `deadline: float` を足す（`clock` の前） | 受け取った `deadline` をそのまま `GuardServices` に渡す。今の guard を打ち切る判定（`clock() - start > budget.usable`）は変えない |
| `_guards` | `main.py` | 変更（中身だけ） | 変えない | SIGALRM に使う `run.started + found.deadline` を `dispatch.run` の `deadline` に渡す（SIGALRM と guard が同じ起点の時刻を使う） |

- `asked_since_prompt` の境: tool_result だけを持つ `user` の行（Tool の結果）は利用者の入力に数えない。末尾に利用者の入力が無ければ、読めた範囲で探す

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `asked_since_prompt` | 入力 → AskUserQuestion → 結果 → 本文 で真。入力 → Bash → 本文 で偽 | 前の入力の前にだけ AskUserQuestion がある → 偽。Tool の結果の `user` の行を入力と読まない | 壊れた行が混じっても読める行で決める |
| `pushback_left` | 0 回・上限−1 回で真 | 上限ちょうどで偽 | 数えない数え手で偽。呼んでも回数が増えない |
| `mark_model_wait` / `model_waited` | 置いた後に真 | 新しい入力で開き直した数え手で偽 | 数えない数え手で置いても偽 |
| `run` | 渡した `deadline` が guard の `svc.deadline` に届く | — | — |

## 起動の前置きのスロットを待つ上限

満たす決定: 決定 8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `call_claude` | `adapters/claude.py` | 変更 | `call_claude(argv, prompt, timeout, cwd=None, slot_wait: Optional[float] = None, new_group: bool = True) -> ClaudeReply` | `slot_wait` と `new_group` を前置きへ渡す。`slot_wait` が None なら今の上限（`launch.wait.llm-cli`）。`new_group` が真なら今のとおり `claude` を新しい組で起動する |
| `_accounted` | `adapters/claude.py` | 変更 | 末尾に `slot_wait: Optional[float]`・`new_group: bool` | 同上を `_prefaced` へ渡す |
| `_prefaced` | `adapters/claude.py` | 変更 | 末尾に `slot_wait: Optional[float]`・`new_group: bool` | `launch_slot.gate(..., wait=slot_wait)`。`new_group` を `_run_claude` へ渡す（関所の起動 `_gated` は中継の経路だけで、今のまま） |
| `_run_claude` | `adapters/claude.py` | 変更 | 末尾に `new_group: bool = True` | `_popen` へ渡す |
| `_popen` | `adapters/claude.py` | 変更 | 末尾に `new_group: bool = True` | `start_new_session=new_group`。偽なら呼び手の組に入り、`_talk` の時間切れの `_stop` は `process_group.stop_group` の「組が自分と同じなら `proc` へだけ送る」に従う |
| `gate` | `adapters/launch_slot.py` | 変更 | `gate(agent, *, cwd=None, env=None, wait: Optional[float] = None)` | `held` へ渡す |
| `held` | `adapters/launch_slot.py` | 変更 | `held(agent, *, cwd=None, environ=None, read_memory=None, wait: Optional[float] = None)` | `_wait_for` へ渡す。親のスロットを受け継ぐときは今のとおり待たない |
| `_wait_for` | `adapters/launch_slot.py` | 変更 | `_wait_for(agent, directory, lim, read_memory, limit: Optional[float] = None)` | 待つ上限を `limit`（None なら設定）にする。0 なら 1 度だけ取りに行き、取れなければ `NoSlot` |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_wait_for` | `limit` を省くと今の上限で待つ | `limit=0` で空きが無ければ眠らずに `NoSlot` | — |
| `call_claude` | `slot_wait=0` で空きが無ければ `Failure.NO_SLOT` を返す（起動しない） | — | — |
| `_popen` | 偽の `Popen` に `start_new_session=True` が渡る（既定） | `new_group=False` で `start_new_session=False` が渡る | — |

## 設定と登録

満たす決定: 決定 2、受け入れ条件 7・8

- `contracts/limits.json`: 上の「内部のデータと定数」の 2 行。`hook.stop-wait.judge` の `seconds`（5）≤ `hook.budget.stop` の締め切り − 余白（7 − 1.5 = 5.5）。`rest_seconds`（600）・`rounds`（3）・`auth_markers`・`endpoint` は秒の順序の検査に出てこない値で、`limits` の行には `hook.stop-wait.judge` だけを挙げる
- `contracts/hooks/guards.json`: `stop-wait` の `limits` を `["hook.stop-wait.judge"]` にし、`summary` に軽いモデルの判定と戻り方を足す
- `hooks/claude.json`・`hooks/codex.json`: `ndf hooks render` で作り直す（Stop の timeout 6 → 9）。手で書かない
- `ndf hooks judge-cases [--cases <パス>] [--rounds <周の数>]`: 既定のパスは `contracts/hooks/reply-judge-cases.json`、既定の周の数は設定の `rounds`（3）。各例を周の数だけ打ち、1 周でも外れた例を結果 JSON の `items` に `id`・`expect`・外れた周の `got` で出す。`metrics` に件数・外れた例の件数・打った回数・秒の最小と最大。外れがあれば `status` は `stopped`（終了コード 1）
