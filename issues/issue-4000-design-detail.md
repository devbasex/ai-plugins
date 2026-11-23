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
    │   └── launch_slot.py              gate・held にスロットを待つ上限
    └── domains/hooks/
        ├── reply_judge.py *            判定のリクエスト（hook の閉包の中・標準ライブラリだけ）
        ├── reply_wait.py *             ルールの前置き（10 系の classify_reply_wait を移し、引用と別の場所の待ちを外す reply_kind。標準ライブラリだけ）
        ├── judge_child.py *            判定の子（hook の閉包の外）
        ├── guards/stop_wait.py         判定の選び方
        ├── context.py                  AskUserQuestion を使ったか
        ├── counter.py                  上限の手前か・モデルの wait のフラグ
        ├── dispatch.py                 GuardServices に締め切り
        ├── main.py                     SIGALRM と同じ締め切りを dispatch へ
        └── cli.py                      ndf hooks judge-cases
```

依存の向き: `guards/stop_wait.py` → `domains/hooks/reply_judge.py`（標準ライブラリと `tables` だけ）→（子プロセスの起動。import しない）→ `domains/hooks/judge_child.py` → `adapters/claude.py` → `adapters/launch_slot.py`。`domains/hooks/cli.py` は hook の閉包の外にあり（今も `ndf.contracts` を import する）、`domains/hooks/judge_child.py` を import してよい。循環は無い。hook の閉包に `domains/hooks/judge_child.py` を入れない（入ると `ndf.adapters.claude` が閉包に入り、import の表の検査が落ちる）。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/domains/hooks/reply_judge.py` | 新設 | 本文から質問に渡す文を作り、親の Claude Code の起動を確かめ、子を起動して上限の秒で待つ。休みのマーカーを読み書きする | `tables`、`relay.claude_options`、`relay.signals`、標準ライブラリ | モジュール | `judge_text`（新設）・`parent_launch`（新設）・`Launch`（新設の型）・`ask`（新設）・`resting`（新設）・`mark_rest`（新設）・`endpoint_resting`（新設）・`endpoint_scope`（新設）・`Judged`（新設の型） |
| `plugins/ndf/src/ndf/domains/hooks/reply_wait.py` | 新設 | 10 系（`ndf--v10.27.77` の `scripts/lib/wait_notice.py`）の本文の判定を移し、本文が回答・承認を待って終えたかをルールだけで返す。語の列は 10 系に 11.0.22 の `classify_reply` が拾う本人の回答待ち（「返答を待ちます」「ご回答をお待ちしています」）を足し、文の分け方はインラインコード・リンク先・URL の中の記号で切らない。guard が使う形は、かぎ括弧の引用の中身を見ず、待ちの文がどれも別の場所の待ちの報告だけ（同じ文の前後の節に本人への問いが無い）の本文を `elsewhere_wait` にする | 標準ライブラリ | モジュール | `classify_reply_wait`（新設）・`prose_lines`（新設。本文の行の抜き出し。1 回の Stop で 1 回だけ打つ）・`prose_kind`（新設。`prose_lines` の空でない返りを受け、`elsewhere_wait` / `prose_wait` / `closing_report` / `report`。行を抜き出し直さず、かぎ括弧の引用は開閉を行をまたいで追って外す（行を改行でつないで当て、引用の中の改行を残して行の位置を保つ）。回答・承認を求めない定型の結び（「引き続きよろしくお願いします。」）だけの文は待ちに数えず、結びの前が完了の報告だけなら `closing_report`）・`reply_kind`（新設。`prose_lines` が空なら `none`、あれば `prose_kind`）・`ELSEWHERE_RE`（新設の定数。`context.py` から移す） |
| `plugins/ndf/src/ndf/domains/hooks/judge_child.py` | 新設 | 質問の文を持ち、実効の設定で送信先を確かめてから軽いモデルを 1 回打って回答を返す。判定の例の組を周の数だけ確かめる | `adapters.claude`、`contracts.limits`、`domains.relay.account_dir`、`domains.relay.claude_args` | モジュール | `endpoint_refusal`（新設）・`classify`（新設）・`main`（新設）・`run_cases`（新設） |
| `plugins/ndf/src/ndf/domains/hooks/guards/stop_wait.py` | 変更 | 呼ばない条件を見て判定を選び、変更要求か通過を返す | `reply_judge`、`verdict` | モジュール | `check`（変更） |
| `plugins/ndf/src/ndf/domains/hooks/context.py` | 変更 | 最後の利用者の入力の後に AskUserQuestion を使ったかを読む。最後の応答の種類をルールの判定から返す | `reply_wait`、標準ライブラリ | `HookContext` | `asked_since_prompt`（新設）・`HookContext.asked_this_turn`（新設）・`HookContext.has_reply_prose`（新設）・`HookContext.last_reply_kind`（変更）・`classify_reply`（削除） |
| `plugins/ndf/src/ndf/domains/hooks/counter.py` | 変更 | 変更要求が上限の手前かを数えずに返す。今の入力の中でモデルが `wait` と回答したフラグを持つ | `tables` | `Counter` | `pushback_left`（新設）・`mark_model_wait`（新設）・`model_waited`（新設） |
| `plugins/ndf/src/ndf/domains/hooks/dispatch.py` | 変更 | guard へ締め切りの時刻を渡す | — | `GuardServices` | `GuardServices`（欄を足す）・`run`（シグネチャの変更） |
| `plugins/ndf/src/ndf/domains/hooks/main.py` | 変更 | SIGALRM に使う締め切りの絶対時刻を `dispatch.run` へ渡す | `dispatch` | モジュール | `_guards`（中身だけ変える） |
| `plugins/ndf/src/ndf/domains/hooks/cli.py` | 変更 | `ndf hooks judge-cases` を足す | `domains.hooks.judge_child` | モジュール | `_judge_cases`（新設） |
| `plugins/ndf/src/ndf/adapters/claude.py` | 変更 | スロットを待つ上限と、新しいプロセスの組で起動するかを前置きと起動へ渡す | `launch_slot` | モジュール | `call_claude`・`_accounted`・`_prefaced`・`_run_claude`・`_popen`（シグネチャの変更） |
| `plugins/ndf/src/ndf/adapters/launch_slot.py` | 変更 | スロットを待つ上限と、学習の設定の記録を確かめ直すかを呼び手が渡せる | `slot_pool` | モジュール | `gate`・`held`・`_wait_for`（シグネチャの変更） |
| `plugins/ndf/src/ndf/slot_pool.py` | 変更 | プールの排他を待つ上限を呼び手が渡せる | `filelock` | モジュール | `take`（シグネチャの変更） |
| `plugins/ndf/src/ndf/domains/relay/claude_options.py` | 新設 | claude の CLI の引数からオプションを読む（値を取るかの表は呼び手が渡す）。`claude_args` から移し、hook の `reply_judge` と共に使う。標準ライブラリだけを読み、hook の閉包の表（`contracts/hooks/imports.json`）に足す | 標準ライブラリ | モジュール | `take`・`options`・`table_of`・`Option`・`TABLE`（`claude_args` から移す） |
| `plugins/ndf/src/ndf/domains/relay/claude_args.py` | 変更 | オプションの読み方を `claude_options` へ移し、表を読んで渡すだけにする（`options` などの外の形は変えない） | `claude_options`、`contracts` | モジュール | `_take`・`options`（中身を委ねる） |
| `plugins/ndf/src/ndf/domains/relay/account_dir.py` | 変更 | 実効の設定の解き方で、指定した最上位のキーを定義する最も強い層を返す | `claude_args`、`key_helper` | `Resolved` | `effective_env`（シグネチャの変更）・`Resolved`（欄を足す） |

数え: 新設する関数 17（`judge_text`・`parent_launch`・`ask`・`resting`・`endpoint_resting`・`mark_rest`・`endpoint_scope`・`endpoint_refusal`・`classify`・`main`・`run_cases`・`asked_since_prompt`・`HookContext.asked_this_turn`・`Counter.pushback_left`・`Counter.mark_model_wait`・`Counter.model_waited`・`_judge_cases`）、シグネチャを変える関数 11（`call_claude`・`_accounted`・`_prefaced`・`_run_claude`・`_popen`・`gate`・`held`・`_wait_for`・`slot_pool.take`・`dispatch.run`・`account_dir.effective_env`）。

## 判定のリクエスト（hook の側）

満たす決定: 決定 1・3・6・7・10・11、受け入れ条件 1・5・9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `judge_text` | `domains/hooks/reply_judge.py` | 新設 | `judge_text(text: str, code_lines: list[bool], max_chars: int) -> str` | 本文を `markdown.code_lines` と同じ数え方（改行は `\r\n`・`\r`・`\n` だけ。U+2028 などは改行にしない）で行に分け、コードブロックの行と空行を除いて改行でつなぎ、`max_chars` を超えれば末尾の `max_chars` 字を返す。`code_lines` の数が行の数と合わなければ空の文を返す（どの行がコードか分からない本文を送らない） |
| `parent_launch` | `domains/hooks/reply_judge.py` | 新設 | `parent_launch(env: Mapping[str, str], pid: int, read: Callable[[Path], bytes] = <ファイルの読み>, readlink: Callable[[Path], str] = <リンクの読み>, stat: Callable[[Path], os.stat_result] = os.stat) -> Optional[Launch]` | 送信先の確かめの hook の側（設計の決定 11）。次をすべて満たせば `Launch(argv=<親の起動引数>, project_dir=<CLAUDE_PROJECT_DIR>, exe=<親の実行ファイルの実パス>, exe_id=<親が読み込んだ実行ファイルの [st_dev, st_ino]>)`、ほかは None。(1) `env["CLAUDE_CODE_ENTRYPOINT"]` が `endpoint.entrypoints` のどれか。(2) `CLAUDE_PROJECT_DIR` が絶対パス。(3) `CLAUDE_CONFIG_DIR` が無いか絶対パス（相対は確かめられないので None）。(4) `pid`（hook のプロセス）の親から `/proc/<pid>/stat` の親の番号をたどり、`endpoint.ancestors` 代のうちで**最も近い Claude Code の実プロセス 1 つ**を見つける。Claude Code かは `readlink(/proc/<pid>/exe)` の実パスが `endpoint.cli_exe` の glob に当たるかで決め、引数の中身（シェルの文字列・位置引数）では決めない。それより前に `readlink(/proc/<pid>/exe)` の名前が `endpoint.script_hosts` の glob に当たる祖先に出会ったら、`/proc/<pid>/cmdline` を見ずにそこで探すのを止めて None（npm の導入の Claude Code は node の上で動き、`claude` のリンク名で起動すると起動引数は `node /usr/local/bin/claude` になるので、起動引数から Claude Code でないと確証できない。親が起動時に読み込んだスクリプトの実体も特定できないので、node の Claude Code を親としても認めない）。見つけたらそこで止め、それより上は見ない。そのプロセス（実行ファイルの形）の `/proc/<pid>/cmdline` を `argv`、`readlink(/proc/<pid>/exe)` を `exe`、`stat(/proc/<pid>/exe)`（`stat` はリンクの先のパスでなく親が読み込んだ実体を返す）の `[st_dev, st_ino]` を `exe_id` にする。`readlink` の返りが ` (deleted)` で終わる（親の起動後に消えたか置き換わった）なら None。見つからない・`OSError`・`/proc` が無いときは None。ここでは引数の中身を検査しない（`--settings` などの検査は判定の子の `endpoint_refusal` が `claude_args` で行う） |
| `Launch` | `domains/hooks/reply_judge.py` | 新設 | `NamedTuple(argv: list[str], project_dir: str, exe: str, exe_id: list[int])` | 親の Claude Code の起動引数・プロジェクトの根・親の実行ファイルの実パス・親が読み込んだ実行ファイルの `[st_dev, st_ino]`。`ask` が子の標準入力へ渡す |
| `ask` | `domains/hooks/reply_judge.py` | 新設 | `ask(text: str, seconds: float, env: Mapping[str, str], launch: Launch, popen: Callable[..., subprocess.Popen] = subprocess.Popen, killpg: Callable[[int, int], None] = os.killpg) -> Judged` | 判定の子を新しいプロセスの組で起動し、標準入力に `{"text", "seconds", "parent_argv", "project_dir", "parent_exe", "parent_exe_id"}` を渡し、`seconds` で待って回答を読む。どの終わり方でも組へ送ってから回収する |
| `Judged` | `domains/hooks/reply_judge.py` | 新設 | `NamedTuple(kind: Optional[str], failure: Optional[str], ms: float, calls: int)` | 判定の結果。`kind` は `wait` / `report`、回答が得られなければ None と `failure` |
| `resting` | `domains/hooks/reply_judge.py` | 新設 | `resting(state_dir: Path, now: float) -> bool` | 休みのマーカーを読み、起動の失敗から休む間（設定）が過ぎていなければ真。`parent_launch` より前に見る。マーカーが無い・読めないときは偽 |
| `endpoint_resting` | `domains/hooks/reply_judge.py` | 新設 | `endpoint_resting(state_dir: Path, now: float, env: Mapping[str, str], launch: Launch) -> bool` | 休みのマーカーを読み、この送信先の確かめの対象（`endpoint_scope(env, launch)`）の送信先の不一致から休む間が過ぎていなければ真。`parent_launch` の後に見る。マーカーが無い・読めないときは偽 |
| `mark_rest` | `domains/hooks/reply_judge.py` | 新設 | `mark_rest(state_dir: Path, failure: str, now: float, env: Mapping[str, str], launch: Launch) -> None` | 起動の失敗（`launch`・`refused`・`usage_limit`・`auth`）はどの対象も休む時刻を、送信先の不一致（`endpoint`）は `endpoint_scope(env, launch)` の対象だけが休む時刻を書く。休む間を過ぎた対象は書くときに外す。読んで足して書く間は錠のファイル（`<状態の保存先>/hooks/.judge-rest.lock`）の `flock` で別のセッションの Stop と直列にし（`signals.held`。待つのは設定の `lock_seconds` まで、取れなければ書かない）、読み手へは一時ファイルからの置き換え（`signals.replace_text(path, <JSON の文>, mode=0o600)`。一時ファイルを 0600 で作り、書く前に権限を 0600 にしてから名前を変える。権限を指定できない `signals.replace_json` は使わない）で所有者だけが読めるファイルとして渡す。ほかの失敗では何もしない。書けなくても例外を投げない |
| `endpoint_scope` | `domains/hooks/reply_judge.py` | 新設 | `endpoint_scope(env: Mapping[str, str], launch: Launch) -> str` | 送信先の確かめの対象の名前。判定の子の `endpoint_refusal` の結果を変える材料だけの SHA-256 の先頭 16 桁。材料は、`claude_options.options(<表>, launch.argv[1:])`（子の `claude_args.options` と同じ読み方）のうち `endpoint.flags` に当たるオプションの名前（子は名前があれば値によらず拒むので値は入れない）・プロジェクトの根（`launch.project_dir`）・`CLAUDE_CONFIG_DIR` の値・`ANTHROPIC_CONFIG_DIR` の値（子の `account_dir.effective_env` がプロファイルを読む保存先で、読めなければ `unresolved` になる。相対パスなら子は理由を返すので、値のまま入れる。`endpoint.flags` に当たるオプションがあれば子はプロファイルを読む前に拒むので入れない）・名前が `endpoint.env_names` に前方一致する環境変数の名前（値は入れない）・親の実行ファイルの実パス（`launch.exe`）と `[st_dev, st_ino]`（`launch.exe_id`。子は同一性が合わなければ `endpoint` を返すので、実行ファイルの差し替えを検出した旧版のセッションの休みが、同じプロジェクトと設定で起動した新版のセッションの判定を止めない。`endpoint.flags` に当たるオプションがあれば子は同一性によらず拒むので入れず、更新の後に同じ拒否のための子を起動し直さない）。プロンプトなどの位置引数・ほかのオプション・API キーなどの値は入れない。同じリポジトリでも `--settings` 付きの起動と通常の起動は別の対象で、プロンプトだけが違う `claude -p` は同じ対象 |

- `ask`
  - 事前条件: `seconds > 0`。`text` は `judge_text` の空でない返り（空なら呼び手の `check` が呼ばない）
  - 事後条件: どの終わり方でも子の組（判定の子と、同じ組で起動した `claude`）は止まって回収されている。`kind` が None なら `failure` は `timeout` / `launch` / `exit` / `shape` / `no_slot` / `refused` / `usage_limit` / `auth` / `endpoint` のどれか
  - 副作用: 子プロセスの起動（`[sys.executable, "-P", "-m", "ndf.domains.hooks.judge_child"]`、`start_new_session=True`、`PYTHONPATH` にプラグインの `src`、環境に `MAX_THINKING_TOKENS=0`）。`outbox.kick` と同じ起動の形
  - 子へ渡す環境: `env` から、環境変数の表（`contracts/domains/env.json`。hook の側は `tables.load` で `json` のまま読む）の `child` が `drop` の行の変数を外す。`relay/session_env.py` の `tool_env` と同じ行で、`CLAUDECODE`・`CLAUDE_CODE_SESSION_ID` を含み、接続先と認証の変数を含まない（外す名前をコードに書かない）。そのうえで `PYTHONPATH` と `MAX_THINKING_TOKENS` を入れる
  - 回答の読み: `communicate` は判定の子（組のリーダー）を回収してしまうので使わない。標準入力（書き込み）と標準出力（読み込み）を同じ `selectors` のループで扱い、書き込みも読み込みも起動から `seconds` の同じ期限の中で進める（書ける状態のときだけパイプの `PIPE_BUF` 以下を書くので、判定の子が入力を読まずに止まってもパイプの容量を超える書き込みで止まらない）。書き終えたら標準入力を閉じ、標準出力が終わりまで来たら回答とする。期限までに終わらなければ時間切れ。リーダーを回収するまでは組の番号が再利用されないので、組への送信は必ず回収の前に済ませる
  - 組の止め方: 時間切れ・例外のときは組（`proc.pid`）へ SIGTERM を送って 0.2 秒おき、判定の子が先に終わっていても組に `claude` が残りうるので続けて SIGKILL を送り、それから `proc.wait()` で回収する。0.2 秒の待ちが締め切りの SIGALRM などの例外で破られても、SIGKILL を送ってから回収し、例外はその後に投げ直す。正常に終えたときも、組に残る者があれば止めるため SIGKILL を組へ送ってから回収する（`ProcessLookupError` は受け流す）。回答を読めたら終了コードは見ない（読んだ後の SIGKILL が終わりかけの子の終了コードになりうる）。判定の子は `claude` を同じ組で起動する（下の「判定の子」）ので、組への送信が `claude` まで届く

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `ask` | 時間切れ | 子が `seconds` で終わらない（スロット・認証の確かめ・モデルの遅れ） | `seconds` までに標準出力が終わらない | `failure="timeout"` で返す | 無し（計測に残る） | 組へ SIGTERM、0.2 秒おいて SIGKILL を送ってから回収。再実行しない |
| `ask` | 割り込み | エントリポイントの締め切りの SIGALRM が待ちの中で来る | `BaseException` | 組を止めて回収してから例外を投げ直す | エントリポイントの今の締め切りの扱い | 同上 |
| `ask` | 起動できない | python が無い・`OSError` | `Popen` | `failure="launch"` | 無し | 無し |
| `ask` | 子が 0 以外で終わる | 子の中の例外 | 回答を読めず、終了コードが 0 以外 | `failure="exit"` | 無し | 無し |
| `ask` | 出力の形が違う | 1 行の JSON でない・`kind` が `wait` / `report` でも `failure` でもない | JSON の読み | `failure="shape"` | 無し | 無し |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `hook.stop-wait.judge` | 設定値 | `contracts/limits.json` | `{"summary": …, "model": "haiku", "runtimes": ["claude"], "seconds": 8, "max_chars": 1600, "rest_seconds": 600, "lock_seconds": 0.2, "rounds": 3, "auth_markers": ["Not logged in", "Invalid API key"], "endpoint": {"entrypoints": ["cli", "sdk-cli"], "ancestors": 8, "cli_exe": ["*/claude/versions/*"], "script_hosts": ["node*"], "flags": ["--settings", "--setting-sources"], "keys": ["awsAuthRefresh", "awsCredentialExport", "gcpAuthRefresh"], "env_names": ["ANTHROPIC_", "CLAUDE_CODE_USE_", "CLAUDE_CODE_SKIP_", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_SCOPES", "AWS_", "GOOGLE_", "CLOUD_ML_", "VERTEX_", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "all_proxy", "no_proxy", "NODE_EXTRA_CA_CERTS"]}}`（`runtimes` はモデルを呼ぶランタイムで、guard の `skip:runtime` と、`seconds` を締め切り − 余白と比べる予算の検査（`budget._component_error`）がここを読む、`rest_seconds` は起動が失敗した後に休む間（決定 10）、`lock_seconds` は休みのマーカーの更新の錠を待つ上限、`rounds` は `judge-cases` が 1 つの例を打つ周の数（決定 9）、`auth_markers` は認証の失敗と読む本文の文字列（決定 10、MEA8）、`endpoint` は送信先が同じかの確かめに使う値（決定 11。`env_names` は前方一致で、設定の層が出所の変数だけに当てる。`CLAUDE_CODE_SKIP_`（`CLAUDE_CODE_SKIP_BEDROCK_AUTH` など）は接続の認証の省略なので含める。`apiKeyHelper` は `account_dir` の `helper` が見るので `keys` に入れない）） |
| `hook.budget.stop` | 設定値 | `contracts/limits.json` | `p95_ms` 5000・`deadline` 10・`margin` 1.5・`timeout` 12（他のキーは今のまま。設計の決定 2 の値を変えた経緯）。Claude Code の Stop だけが読む |
| `hook.budget.stop.codex` | 設定値 | `contracts/limits.json` | 今の `hook.budget.stop` の値（`p95_ms` 300・`deadline` 3.5・`margin` 1.5・`timeout` 6・`inject_chars` 2000）。Codex の Stop はモデルを呼ばないので判定の秒を足さない |
| `CHILD_MODULE` | 定数 | `domains/hooks/reply_judge.py` | `"ndf.domains.hooks.judge_child"` |
| 休みのマーカー | ファイル | `<状態の保存先>/hooks/judge-rest.json` | 無し（起動の失敗と送信先の不一致のときだけ `{"launch": {"at": <失敗した時刻>, "failure": "<種類>"}, "endpoint": {"<endpoint_scope>": <失敗した時刻>}}` を書く。`launch` はどの対象も休み、`endpoint` は対象ごとに休む。1 つの起動の形（リポジトリ・`--settings`・接続先の変数）の送信先の不一致で、別の起動の形のモデルの判定を止めない。更新は `.judge-rest.lock` の錠の中で行い、一時ファイルからの置き換え（`signals.replace_text(..., mode=0o600)`）で所有者だけが読めるファイルとして公開する。`metrics.prune_state` が消す対象と同じ保存先） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `judge_text` | 質問の後に 9 行の報告が続く本文で、質問の行が返りに入る | ちょうど `max_chars` 字と 1 字多い本文。コードブロックの中の質問が返りに入らない。U+2028 を含む行を 1 行と数える | `code_lines` の数が行の数と合わない → 空の文 |
| `ask` | 偽の `popen`（実プロセスを起動しない。標準入出力は同じプロセスの中のパイプで、標準出力に `{"kind": "wait", "calls": 1}` の 1 行、終了コード 0）で `kind="wait"` と `calls`。標準入力の JSON に `launch` の `parent_argv`・`project_dir`・`parent_exe`・`parent_exe_id` が入る。`popen` に渡った引数が `[sys.executable, "-P", "-m", "ndf.domains.hooks.judge_child"]`・`start_new_session=True` で、環境に `MAX_THINKING_TOKENS=0` があり `CLAUDECODE`・`CLAUDE_CODE_SESSION_ID` が無く `ANTHROPIC_BASE_URL` が残る | 正常に終えた後も偽の `killpg` に組の番号と SIGKILL が渡り、それが `wait()` より前。SIGKILL で終わった終了コードでも読めた回答を使う | 標準出力が閉じないまま `seconds` が 0 → `timeout`、偽の `wait` がすぐ返る（判定の子だけが先に終わった）ときも SIGTERM・猶予の待ち（差し替え）・SIGKILL・`wait()` の順。読みの中で `KeyboardInterrupt` → 同じ順で送って回収し、例外を投げ直す。`popen` が `OSError` → `launch`。終了コード 1 → `exit`。形の違う出力 → `shape`。どれも実時間を待たない |
| `parent_launch`（偽の `read`・`readlink` で `/proc` を差し替える。実の `/proc` は読まない） | エントリポイント `cli`・`CLAUDE_PROJECT_DIR` が絶対パス・親が実行ファイル `…/claude/versions/2.1.330` → その引数と根と `exe=…/claude/versions/2.1.330` と、偽の `stat` が返す `exe_id` を返す | 親が `ancestors` 代目 → 返す、その 1 代上 → None。近い順に `/bin/bash -c 'claude --settings x.json'`（実行ファイルが bash）・`claude`（`--settings=gw.json` 付き）・`claude`（フラグ無し）と並べると、2 つ目の引数を返す（シェルの文字列を親とせず、3 つ目の外側を見ない）。`CLAUDE_CONFIG_DIR` が絶対パス → 返す | エントリポイント `sdk-ts` → None。`CLAUDE_PROJECT_DIR` が無い・相対 → None。`CLAUDE_CONFIG_DIR` が相対（`./cfg`）→ None。`/proc` の読みが `OSError`（macOS の代わり）→ None。node の起動（実行ファイル `node`・2 つ目の引数が `…/@anthropic-ai/claude-code/cli.js`・相対の `node_modules/@anthropic-ai/claude-code/cli.js`・npm のリンク名 `/usr/local/bin/claude` のどれか）→ None（その上のネイティブの Claude Code も見ない）。近い順に `node /usr/local/bin/claude`（内側）・フラグの無いネイティブ版 `…/claude/versions/2.1.330`（外側）と並べる → None（外側の引数を返さない）。`readlink(/proc/<pid>/exe)` が `… (deleted)` → None。引数の位置に `claude` を含むだけの python のプロセス → 親としない |
| `resting` / `endpoint_resting` / `mark_rest` | `launch`・`auth` を書いた直後は `resting` が真、`endpoint` を書いた直後は同じ対象の `endpoint_resting` が真、休む間を過ぎた時刻では偽。`endpoint` は `resting` を真にせず、同じリポジトリの `--settings` の無い起動・別のプロジェクトの根・別の接続先の変数・別の実行ファイルのパスか `exe_id` では偽（`--settings` 付きの起動では実行ファイルのパスと `exe_id` が違っても真）。先に書いた休みを後の書き込みが消さない。API キーの値と引数の中の値がファイルに入らない。プロンプトだけが違う起動は同じ対象。ファイルの権限が 0600 | 休む間のちょうどの時刻 | マーカーが無い・壊れたマーカー・書けない保存先で偽（例外を投げない）。`timeout`・`no_slot`・`exit` を渡すとマーカーを書かない。錠を別のセッションが持っていれば書かない |

時刻はどれも引数（`now`）か偽の時計で渡し、実時間の経過と実プロセスの起動をテストの仕様にしない（tdd-cycle の「テストに書かないもの」）。

## 判定の子

満たす決定: 決定 1・4・8・9・10・11、受け入れ条件 3・9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `endpoint_refusal` | `domains/hooks/judge_child.py` | 新設 | `endpoint_refusal(env: Mapping[str, str], parent_argv: Sequence[str], project_dir: Path, resolve: Callable[..., account_dir.Resolved] = account_dir.effective_env) -> Optional[str]` | 送信先の確かめの子の側（設計の決定 11）。呼んではいけない理由の項目名（値を含まない）か None を返す。(1) `claude_args.options(parent_argv[1:])` の名前に `endpoint.flags` のどれかがあれば理由（`--settings <値>` も `--settings=<値>` も同じ名前に読める。`account_dir._flag` と同じ解き方）。(2) `env` の `CLAUDE_CONFIG_DIR`・`ANTHROPIC_CONFIG_DIR` のどちらかが相対パスなら理由（その変数の名前）。判定用の `claude` はセッションと違う作業ディレクトリ（プラグインの根）で動くので、相対パスが同じディレクトリに解ける保証が無い。(3) `resolve(env, <設定ディレクトリ>, project_dir, parent_argv, extra=account_dir.Extra(keys=endpoint.keys))` を打つ（`account_dir.effective_env` の署名どおり、キーは `Extra` で渡す）。設定ディレクトリは `env` の `CLAUDE_CONFIG_DIR`（絶対パス）か `~/.claude`。返りの `unresolved` が空でない・`helper` がある・`key_origin` が空でない・`origin` が `子の環境` でない変数のうち名前が `endpoint.env_names` のどれかで始まるものがある、のどれかで理由 |
| `classify` | `domains/hooks/judge_child.py` | 新設 | `classify(text: str, seconds: float, exe: Optional[str], call: Caller = claude.call_claude) -> dict` | 質問の文で 1 回だけ打ち、`{"kind", "calls"}` か `{"failure", "calls", "error"}` を返す。`error` は `ClaudeReply.error`（拒否・起動の失敗の理由の文。無ければ None）で、呼び手の中だけで使う。`exe` は起動する実行ファイル（hook から来て `main` が同一性を確かめた `parent_exe`）で、None なら PATH の `claude`（`ndf hooks judge-cases`） |
| `main` | `domains/hooks/judge_child.py` | 新設 | `main(argv: Optional[list[str]] = None, stat: Callable[[str], os.stat_result] = os.stat, call: Caller = claude.call_claude) -> int` | 標準入力の JSON（`text`・`seconds`・`parent_argv`・`project_dir`・`parent_exe`・`parent_exe_id`。どれも必須）を読み、先に `endpoint_refusal` を打つ。続けて `stat(parent_exe)` の `[st_dev, st_ino]` が `parent_exe_id` と同じかを比べ、違う・`stat` が `OSError` なら（親の起動後に差し替わった）`claude` を起動せずに `{"failure": "endpoint", "calls": 0}`。理由があれば `claude` を起動せずに `{"failure": "endpoint", "calls": 0}`（理由の項目名は計測へ渡さない）、無ければ `classify(text, seconds, parent_exe)` の返りから `error` を除いて 1 行の JSON で標準出力へ書く（hook の側は `failure` だけで戻り方と休みを決めるので、拒否の理由は hook の出力にも計測にも載せない）。0 で終わる |
| `run_cases` | `domains/hooks/judge_child.py` | 新設 | `run_cases(path: Path, rounds: int, call: Caller = claude.call_claude) -> dict` | 判定の例の組を 1 件ずつ `rounds` 周判定し（`classify(text, <設定の seconds>, None, call)`。親が無いので `exe` は None）、1 周でも外れた例・件数・秒を返す。`rounds` が 1 以上の整数でなければ打たずに `ValueError`（`ndf hooks judge-cases` は `--rounds` と設定の `rounds` を同じ規則で確かめ、外れれば `invalid`）。打つ間は環境変数の表の `child` が `drop` の行（`CLAUDECODE`・`CLAUDE_CODE_SESSION_ID` を含む）をこのプロセスの環境から外し、終われば戻す（Stop の経路で hook の側が外すのと同じ行。Claude Code のセッションの中で打っても入れ子の起動として断られない）。`classify` の返りの `failure` が `refused` なら、残りの例を打たずに `JudgeRefused(<返りの error>)`（このモジュールの `RuntimeError` の子。`reason` に理由の文を持つ）を投げる |

- `classify`
  - 起動の引数: `exe` があれば `claude.claude_args(ClaudeCall(system=SYSTEM, permission=Permission.JUDGE, model=<設定の model>), base=(exe,))`、None なら `base` を省く（既定の `claude` を PATH で引く）。どちらも最小構成・Tool なし・会話を残さない。hook からの起動では `exe` は `main` が渡す `parent_exe`（親が読み込んだ実体と同じと `main` が確かめた実パス）で、PATH で引かない。回答は `ClaudeReply.raw` の本文を空白を除いて読み、`wait` か `report` に完全に一致しなければ `shape`
  - 打つ秒: `seconds − 経過`。残りが 0.5 秒を切れば打たずに `failure="timeout"`
  - スロットと組と下限と学習の設定: `exe` があれば（hook からの起動）`call_claude(..., options=claude.LaunchOptions(slot_wait=0, new_group=False, cli_floor=False, ensure_training=False))`。`exe` が None（`ndf hooks judge-cases`）なら `options=`LaunchOptions(ensure_training=False, switch_account=False)``（スロットを設定の上限まで待ち、新しい組で起動し、PATH の `claude` の下限を `claude --version` で確かめる。親と同じ実行ファイルと確かめられないので下限を飛ばさない。学習の設定の記録が無いか期限切れなら確かめ直さずに `refused`（`training.ensure` のアカウントの設定の読み・書き換え・読み直しをせず、記録と設定の変更は `ndf training check` に委ねる）。中継の起動先を継いだ環境でも、利用上限で替え先のアカウントへ打ち直さない（1 回の判定で 1 回だけ起動し、打った回数をそのまま数える））。以下は `exe` があるときの説明。`claude` を判定の子と同じプロセスの組で起動し、hook の側の組への送信が `claude` まで届くようにする（今の `_popen` は `start_new_session=True` で別の組に置く）。判定の子は中継の確かめ（`claude.register` の `relay_checks`）を登録しないので、起動は `_prefaced` → `_run_claude` の経路を通る。CLI の下限の確かめ（`claude --version`）は打たない（利用者の決定、2026-11-23。判定の対象は今動いている Claude Code 自身で、その親のセッションが動いている時点で下限は満たしている）。下限を飛ばせるのは、起動する実行ファイルが親の読み込んだ実体と同じ（`parent_exe` の `[st_dev, st_ino]` が `parent_exe_id` と同じ）ときだけで、PATH 上の別の `claude` や差し替わったファイルを下限の確かめなしに起動しない。学習に使わない設定の記録が無いか期限切れなら、その場で確かめ直さず（`training.ensure` のアカウントの設定の API の呼び出しと設定の書き換えをしない）`refused` で判定を見送る（休みのマーカーが書かれ、記録は `ndf` のほかの起動か `ndf training check` が作る）
  - 打つ秒の内訳: `claude -p` の 1 回だけを `seconds − 経過` に収める（下限の確かめを打たないので、起動するプロセスは判定の子の python と `claude -p` の 2 つである）
  - 失敗の対応付け: `ClaudeReply.failure` の `launch` / `timeout` / `refused` / `usage_limit` / `no_slot` をそのまま `failure` へ。`ok` が偽で `failure` が None のとき、`ClaudeReply.raw`（結果の行の本文か標準エラー）が設定の `auth_markers` のどれかを含めば `auth`、含まなければ `exit`（認証の失敗は今の `call_claude` では `failure` が None になる。設計の MEA8）
- 質問の文（`SYSTEM` と `QUESTION`）はこのモジュールの定数に置く。`QUESTION` は設計の外部の調査の MEA6 の形にする。応答を書いた本人が人に判断・選択・承認を求め、その回答を待っていれば `wait`、質問が途中にあり後に報告が続いても `wait`、それ以外は `report` とし、`report` の例として「他人の質問を引用して経緯を説明している」「ほかのセッションやレーンの承認待ちを伝えている」「承認を求める資料を用意したことや作業の結果を報告している」の 3 つを並べ、1 語だけを返させる。本文は `<reply>` と `</reply>` で囲む
- 判定の例の組の形: `{"version": 1, "cases": [{"id": "miss-1", "text": "…", "expect": "wait", "source": "#4000 の 1 回目"}, …]}`。要求の受け入れ条件 2 の文を持つ

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `main` | `endpoint_refusal` が例外 | `account_dir` の読みの想定外の失敗 | `Exception` | `{"failure": "endpoint", "calls": 0}`（確かめられないので呼ばない） | 0 で終わる | 無し |
| `main` | 入力が JSON でない・`text` が無い・`parent_exe` が無いか絶対パスでない・`parent_exe_id` が整数 2 つでない | 呼び手の誤り | `json.loads` | `{"failure": "shape", "calls": 0}` を書く | 0 で終わる | 無し |
| `run_cases` | 組のファイルが読めない | ファイルが無い・形が違う | 読み込み | 呼び手（CLI）へ例外 | `ndf hooks judge-cases` が `result.stopped(TOOL, "invalid", <読めない理由>)`（`status` は `stopped`・`cause.kind` は `invalid`・終了コード 2。読めない・形の誤りは入力の誤りで、不変条件による拒否の `refused` とは分ける） | 直して打ち直す |
| `run_cases` | 判定が `refused` で返る | 学習の設定の記録が無いか期限切れ（`ensure_training=False`）・下限より古い `claude`・中継の確かめ（`checks.begin`。`switch_account=False` でも打つ）が断った（認証を上書きする設定など） | `classify` の `failure` | 残りの例を打たずに `JudgeRefused(<classify の返りの error>)` を投げる（`error` が None なら理由は `refused`） | `ndf hooks judge-cases`（`_judge_cases`）が `JudgeRefused` を受け、`result.stopped(TOOL, "refused", <reason の文>)` を `result.emit` に渡す（`status` は `stopped`・`cause.kind` は `refused`・`cause.detail` が理由の文。終了コードは停止理由の表の `refused` の値）。`_judge_cases` は案内を足さない。対処は理由の文が出どころごとに持つ（学習の設定は `training_cache` の `ndf training check --runtime claude`、古い CLI は `cli_floor.TooOld` の「下限以上へ上げる」、中継は `AccountRefused.detail` の対処）ので、中継が断ったときに学習の設定や CLI の更新を案内しない | 理由の文が示す対処の後に打ち直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `classify` | 偽の `call` が `report` → `kind="report"`・`calls=1`。`wait` → `kind="wait"`・`calls=1`（同じ Stop で 2 回打たない）。`exe` を渡すと偽の `call` の `options` が `LaunchOptions(slot_wait=0, new_group=False, cli_floor=False, ensure_training=False)` で、起動の引数の先頭が `exe`。`exe` が None なら `options` が `LaunchOptions(ensure_training=False, switch_account=False)` で、起動の引数の先頭が既定の `claude` | 残りが 0.5 秒を切る（偽の時計）→ 打たずに `timeout` | `failure` を返す `call` → その種類。`failure` が None で本文が「Not logged in · Please run /login」・「Invalid API key · Fix external API key」→ `auth`、ほかの本文 → `exit`。`Wait.`・`wait\n理由…` → `shape`。`failure="refused"`・`error="…"` の `call` → 返りの `error` がその文 |
| `endpoint_refusal`（偽の `resolve` か一時ディレクトリの設定ファイル。実の `/etc` と利用者の設定は読まない） | 引数に接続の引数が無く、偽の `resolve` の返りが空 → None。子の環境の `ANTHROPIC_BASE_URL`（出所 `子の環境`）→ None | 設定の層の `env` に `FOO` だけ → None | `claude --settings x.json`・`claude --settings=x.json`・`claude --setting-sources=user`・`claude --setting-sources user` → 理由。偽の `resolve`（署名は `account_dir.effective_env` と同じ）の `extra.keys` に設定の `keys` が渡り、相対パスの `ANTHROPIC_CONFIG_DIR` → 理由、返りの `unresolved` がある・`helper` がある・`key_origin` に `awsCredentialExport` がある・出所が `利用者の設定` の `ANTHROPIC_BASE_URL`・`HTTPS_PROXY`・`https_proxy`・`NO_PROXY` がある → 理由。`main` は理由があると偽の `call` を呼ばずに `endpoint` を書く。`parent_exe` の無い入力・相対パスの入力では偽の `call` を呼ばずに `shape` を書く。一時ディレクトリのファイルを `parent_exe` にし、`parent_exe_id` をその `[st_dev, st_ino]` と違う値にする（同じパスへ別のファイルを置き換えた形）と、偽の `call` を呼ばずに `endpoint` を書く |
| `classify` の引数 | 起動の引数に `--setting-sources ""`・`--strict-mcp-config`・`--tools ""`・`--model <設定の値>` が入る | 設定の model を替えると引数が替わる | — |
| `run_cases` | 偽の `call` で全件当たり → 外れ 0、打った回数は 件数 × `rounds`。偽の `call` に渡る起動の引数の先頭が既定の `claude` で、`options` が `LaunchOptions(ensure_training=False, switch_account=False)` | `rounds=1` で 1 周だけ打つ | 3 周のうち 1 周だけ外す偽の `call` → その例が外れとして出る。2 件目で `failure="refused"`・`error="学習の設定の記録が期限切れ"` を返す偽の `call` → 3 件目を打たずに `JudgeRefused` が上がり、`reason` がその文 |
| `_judge_cases` | 偽の `run_cases` が外れ 0 → `status` が `ok` | — | 偽の `run_cases` が `JudgeRefused("…中継の理由…")` を投げる → `status` が `stopped`・`cause.kind` が `refused`・`cause.detail` がその文のまま（`ndf training check` などの案内を足さない） |

## guard `stop-wait`

満たす決定: 決定 1・4・5・6・10・11、受け入れ条件 0・1・4・5・6・6a

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `guards/stop_wait.py` | 変更 | `check(ctx, svc) -> Verdict`（変えない） | 本文の空と AskUserQuestion を見た後にルールの判定を当て、待ちならモデルを呼ばずに変更要求を返す。待ちでなければ呼ばない条件を見て、軽いモデルの判定かルールの判定の結果かを選ぶ |

### 処理の手順

| 条件の組み合わせ | 結果 |
| --- | --- |
| `ctx.has_reply_prose`（`ctx.reply_prose_lines` が空でないか）が偽（本文が空。文の判定はせず、`ctx.last_reply_kind` はまだ評価しない） | 通す（`reply=none`・`judge=skip:empty`） |
| `ctx.asked_this_turn` が真 | 通す（`judge=skip:asked`）。ルールを当てない（`ctx.last_reply_kind` を評価しない）。AskUserQuestion で既に問うたターンの本文は提示した選択肢の案内（「選択肢から選んでください。」）を含みやすく、ルールはそれを待ちと読むため（設計の決定 5） |
| ここで初めて `ctx.last_reply_kind`（`reply_wait.prose_kind` の本文の判定。1 行目で持った `ctx.reply_prose_lines` を使い、本文の行を抜き出し直さない）を評価し、ルールの判定が `elsewhere_wait`（待ちの文がどれも別の場所の待ちの報告だけ） | 通す（`judge=skip:elsewhere`） |
| ルールの判定が `closing_report`（待ちの文が無く、定型の結び（「引き続きよろしくお願いします。」）を末尾から外した残りの文がどれも待ちの形でない丁寧の過去の言い切り（「ました」「でした」）。結びの前に質問・承認の依頼・言い切らない文があれば `prose_wait` か `report`） | 通す（`judge=skip:closing`）。軽いモデルを呼ばない |
| ルールの判定が `prose_wait` | 変更要求（今の文面。`judge=rule`）。軽いモデルを呼ばない。上限に達していても返す（dispatch が上限で通し、待ち通知を送る） |
| 上のどれでもなく、`judge_text(...)`（質問へ渡す本文）が空（コードブロックの行と空行だけの応答・`code_lines` の数が行の数と合わない応答。U+2028 を改行と数えるルールの判定は `report` などを返しうる） | 通す（`judge=skip:no_prose`。空の本文で子と `claude` を起動しない）。ただし `svc.counter.pushback_left()` が偽で `svc.counter.model_waited()` が真なら変更要求を返す（上限の後の待ちの知らせ） |
| `ctx.background_tasks` が空でない | 通す（`judge=skip:background`。ルールの判定は `report`） |
| `ctx.runtime` が `claude` でない（Codex のセッション） | 通す（`judge=skip:runtime`） |
| `svc.counter.pushback_left()` が偽 | 通す（`judge=skip:limit`）。ただし `svc.counter.model_waited()` が真なら変更要求を返す（dispatch が上限で通し、待ち通知を送る。設計の決定 5） |
| `svc.decided` が真（表の前の guard（`unattended` など）が変更要求か拒否を既に返した。再開は決まっている） | 通す（`judge=skip:decided`）。モデルを呼ばない（`dispatch.run` が guard ごとに `GuardServices.decided` を入れる） |
| `reply_judge.resting(svc.state_dir, 今)` が真（前の起動の失敗から休む間のうち） | 通す（`judge=skip:rest`） |
| 上のどれでもなく、残りの秒（`svc.deadline − 余白 − 今` と設定の `seconds` の小さい方）が 0.5 秒を切る | 通す（`judge=skip:time`） |
| 上のどれでもなく、`reply_judge.parent_launch(os.environ, os.getpid())` が None | 通す（`judge=skip:endpoint`） |
| 上のどれでもなく、`reply_judge.endpoint_resting(svc.state_dir, 今, os.environ, <parent_launch の返り>)` が真（この起動の形の送信先の不一致から休む間のうち） | 通す（`judge=skip:rest`） |
| 上のどれでもない | `reply_judge.ask(<judge_text の空でない返り>, 残りの秒, os.environ, <parent_launch の返り>)`。`kind="wait"` なら `svc.counter.mark_model_wait()` を呼んで変更要求（今の文面）、`report` なら通す。`kind` が None なら `mark_rest(svc.state_dir, failure, 今, os.environ, <parent_launch の返り>)` を呼んでから通す（`judge=fallback:<failure>`） |

- ルールの判定は `ctx.last_reply_kind` で、評価するのは 3 行目（空と AskUserQuestion の確認の後）が最初である。AskUserQuestion 済みの Stop と本文が空の Stop では本文の判定を走らせない。`HookContext.last_reply_kind` は 1 行目と同じ `self.reply_prose_lines` を読み、空なら `none`、あれば `reply_wait.prose_kind(self.reply_prose_lines)` を返す（下の「hook の文脈・数え手・締め切り」）。本文の行の抜き出し（`prose_lines`）は 1 回の Stop で 1 回だけ打つ（`reply_wait.reply_kind` を呼ぶと `prose_lines` をもう一度打つので、guard の経路では呼ばない）。11.0.22 の `classify_reply`（言い回しの列と末尾 4 行）は使わない
- 「ルールの判定で決める」は、`prose_wait` を 4 行目で既に変更要求にしているので、5 行目より下ではいつも通過になる（`judge=skip:limit` で `model_waited()` が真のときだけ変更要求）。モデルを呼べない Stop・モデルが回答しない Stop の結果は通過である（要求仕様の受け入れ条件 5・6a）
- 条件は表の上から順に見て、最初に当たった 1 つを `judge` に書く（複数が当たっても 1 つ）。`/proc` を読む `parent_launch` は起動の失敗の休み（`resting`）と残りの秒の後に置き、送信先の不一致の休み（`endpoint_resting`）は親の起動引数が要るので `parent_launch` の後に置く。`/proc` はモデルを呼ばないと分かっている Stop では読まない。子が `endpoint` を返したら `judge=fallback:endpoint` で、休みのマーカーが次の Stop から休む間だけ、同じ送信先の確かめの対象（`endpoint_scope`）の子の起動を止める
- 変更要求の文面・上限・上限の後の待ちの知らせは変えない（dispatch と main が持つ）。上限の後に変更要求を返すのは、待ちの知らせへ渡すためだけである
- `svc.state_dir` は `GuardServices` が今も持つ状態の保存先である
- 残りの秒の余白は、ここで 1 回だけ引く。`svc.deadline` は余白を引いていない絶対時刻（SIGALRM と同じ。下の「hook の文脈・数え手・締め切り」）である

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `check` | 判定の notes: `reply`（ルールの判定。`prose_wait` / `report` / `closing_report` / `elsewhere_wait` / `none`）・`judge`（`rule` / `model` / `fallback:<failure>` / `skip:<理由>`）・`judge_ms`・`judge_calls` | hook の計測の行（`<状態の保存先>/hooks/<日付>.jsonl`） | 本文 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | #4000 の表の 1〜5 の本文 → pushback・`judge=rule`・`ask` を呼ばない。偽の `ask` が `wait` → pushback と `model_waited()` が真、`report` → 通過。ルールが `report` を返す長文（「どちらにするかの指示をください。」の後に箇条書きでない報告の行が 9 行）で `ask` に質問の行が渡る（表の 3 回目の箇条書き 9 行はルールで止まるので、受け渡しの確かめに使わない） | 残りの秒が 0.5 秒を切る → `ask` を呼ばない。`svc.deadline` が今 ＋ 7 秒・余白 1.5 秒・設定 5 秒のとき `ask` に渡る秒が 5（余白を 2 回引かない） | 呼ばない条件 4 つと送信先の条件でそれぞれ `ask` が呼ばれない。`ask` の失敗の種類ごとに通過・`judge=fallback:<failure>` |
| `check` の送信先と順序 | エントリポイント `cli` で親が見つかる → `ask` に `parent_launch` の返りが渡る | 起動の失敗の休み中（`resting` が真）・残りの秒が足りない Stop で偽の `parent_launch` が呼ばれない（`judge=skip:rest`・`skip:time`）。送信先の不一致の休み中は `parent_launch` が呼ばれ、その返りで `endpoint_resting` が真なら `ask` が呼ばれない（`judge=skip:rest`）。Codex でも休み中でも、記録される `judge` は表で最初に当たった 1 つ | Codex で `skip:runtime`、`parent_launch` が None で `skip:endpoint`（どちらも `ask` を呼ばない）。`ask` が `endpoint` を返す → `judge=fallback:endpoint`・休みのマーカーが書かれる |
| `check` の上限の後 | `model_waited()` が真の数え手で上限に達し、本文がルールの判定で `report` → pushback（`ask` は呼ばない）。上限に達した数え手でルールの判定が `prose_wait` → pushback（`judge=rule`）。dispatch を通すと `limited` が真になる | `model_waited()` が偽 → 通す | — |
| `check` の通す順序 | 引用の誤検出の例（「このレーンは「どちらにするかの指示をください」と書いた後に…こちらでは何も待っていません。」）→ ルールが `report`、偽の `ask` の `report` で通過。「別のレーンは承認を待っています。」→ 通過・`judge=skip:elsewhere`・`ask` を呼ばない | AskUserQuestion を使ったターン（`asked_this_turn` が真）の「選択肢から選んでください。」（ルールは `prose_wait`）→ 通過・`judge=skip:asked`・`ask` を呼ばない | 「別のレーンは承認を待っています。こちらは #4217 を載せてよいですか。」→ pushback・`judge=rule` |
| `check` の休みのマーカー | `ask` が `launch` を返した次の `check` で `parent_launch` も `ask` も呼ばれない（`judge=skip:rest`）。`ask` が `endpoint` を返した次の、同じ起動の形の `check` で `ask` が呼ばれない（`judge=skip:rest`）が、`--settings` の無い起動の `check` では呼ばれる | 休む間を過ぎた時刻の `check` で呼ばれる | `ask` が `timeout` を返した次の `check` では呼ばれる（マーカーを書かない） |

## hook の文脈・数え手・締め切り

満たす決定: 決定 1・5、受け入れ条件 0・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `asked_since_prompt` | `context.py` | 新設 | `asked_since_prompt(tail: str) -> bool` | 会話の記録の末尾を後ろから読み、利用者の入力（`type` が `user` で `isMeta` が真でなく、`content` が文字列か text の要素を持つ行）に当たる前に、`name` が `AskUserQuestion` の `tool_use` があれば真 |
| `HookContext.asked_this_turn` | `context.py` | 新設 | `cached_property -> bool` | `asked_since_prompt(self.transcript_tail)` |
| `HookContext.reply_prose_lines` | `context.py` | 新設 | `cached_property -> list[tuple[str, bool]]` | `reply_wait.prose_lines(self.last_reply)`。`has_reply_prose` と `last_reply_kind` が同じ返りを読む |
| `HookContext.has_reply_prose` | `context.py` | 新設 | `cached_property -> bool` | `bool(self.reply_prose_lines)`。guard `stop-wait` の 1 行目 |
| `HookContext.last_reply_kind` | `context.py` | 変更 | `cached_property -> str`（変えない） | `self.has_reply_prose` が偽なら `none`、真なら `reply_wait.prose_kind(self.reply_prose_lines)`（`reply_wait.reply_kind` は呼ばない。本文の行を 2 回抜き出さない）。11.0.22 の `classify_reply`・`_WAIT_RE`・`_TAIL_LINES` は消す（別の場所の待ちの正規表現は `reply_wait.ELSEWHERE_RE` の 1 つだけ） |
| `Counter.pushback_left` | `counter.py` | 新設 | `pushback_left(self) -> bool` | 数えずに、今の入力の後の変更要求が上限の手前かを返す。数え手が記録の排他を取れず数えられない状態（`usable` が偽）のときは偽 |
| `Counter.mark_model_wait` | `counter.py` | 新設 | `mark_model_wait(self) -> None` | 今の入力の記録に `model_wait: true` を置く（入力が変わると記録ごと初期化される）。`usable` が偽なら何もしない |
| `Counter.model_waited` | `counter.py` | 新設 | `model_waited(self) -> bool` | 今の入力の記録の `model_wait`。無ければ偽 |
| `GuardServices` | `dispatch.py` | 変更 | 欄 `deadline: float`（`time.monotonic()` の基準の締め切りの絶対時刻。余白を引かない）を足し、`params` を既定が空の最後の欄にする | guard が残りの秒を求める。呼び手は `params` 無しで作り、`run` が guard ごとに表の行の値を入れる |
| `run` | `dispatch.py` | 変更 | 引数 `counter`・`state_dir` を締め切りと合わせて 1 つの `svc: GuardServices` にまとめる（`clock` の前）。`run(ctx, specs, budget, svc, clock)` | 受け取った `svc` に guard ごとの `params` を入れた写しを渡す（引数の数の上限 6 を超えないため、締め切りを引数で足さずに `svc` へまとめる）。今の guard を打ち切る判定（`clock() - start > budget.usable`）は変えない |
| `_guards` | `main.py` | 変更（中身だけ） | 変えない | SIGALRM に使う `run.started + found.deadline` を `GuardServices` の `deadline` に入れて `dispatch.run` へ渡す（SIGALRM と guard が同じ起点の時刻を使う） |

- `asked_since_prompt` の境: tool_result だけを持つ `user` の行（Tool の結果）と、`isMeta` が真の `user` の行（Claude Code が書くメタの行。`context_records._user_text` と同じ条件）は利用者の入力に数えない。末尾に利用者の入力が無ければ、読めた範囲で探す

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `asked_since_prompt` | 入力 → AskUserQuestion → 結果 → 本文 で真。入力 → Bash → 本文 で偽 | 前の入力の前にだけ AskUserQuestion がある → 偽。Tool の結果の `user` の行と、AskUserQuestion の後に挟まるメタの行（`isMeta` が真）を入力と読まない | 壊れた行が混じっても読める行で決める |
| `pushback_left` | 0 回・上限−1 回で真 | 上限ちょうどで偽 | 数えない数え手で偽。呼んでも回数が増えない |
| `mark_model_wait` / `model_waited` | 置いた後に真 | 新しい入力で開き直した数え手で偽 | 数えない数え手で置いても偽 |
| `run` | 渡した `svc` の `deadline` が guard の `svc.deadline` に届く | — | — |

## 起動の前置きのスロットを待つ上限

満たす決定: 決定 8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `LaunchOptions` | `adapters/claude.py` | 新設 | `@dataclass(frozen=True)` の欄 `slot_wait: Optional[float] = None`・`new_group: bool = True`・`cli_floor: bool = True`・`ensure_training: bool = True`・`switch_account: bool = True`（省いたときの値 `DEFAULT_LAUNCH`） | 前置きの選び方を 1 つにまとめる。`ensure_training` が偽なら学習の設定の記録が無いか期限切れのとき確かめ直さず `Failure.REFUSED`。`switch_account` が偽なら中継の起動先を継いだ起動が利用上限に当たっても替え先で打ち直さず `Failure.USAGE_LIMIT` を返す。3 つを別々の引数で配ると、通る関数が引数の数の上限（6）を越える |
| `call_claude` | `adapters/claude.py` | 変更 | `call_claude(argv, prompt, timeout, cwd=None, options: LaunchOptions = DEFAULT_LAUNCH) -> ClaudeReply` | `options` を前置きへ渡す。`slot_wait` が None なら今の上限（`launch.wait.llm-cli`）。`new_group` が真なら今のとおり `claude` を新しい組で起動する。`cli_floor` が偽なら CLI の下限の確かめ（`claude --version`）を打たない（既定の真は今のとおり確かめる） |
| `_accounted` | `adapters/claude.py` | 変更 | `_accounted(checks, base: AccountRun, prompt, timeout, cwd, options: LaunchOptions)` | 確かめへ渡す起動を `base`（このプロセスの環境と起動引数）1 つで受け、`options` を `_prefaced` へ渡す。`options.switch_account` が偽なら `checks.retry` を打たない |
| `_prefaced` | `adapters/claude.py` | 変更 | 末尾に `options: LaunchOptions` | `launch_slot.gate(..., wait=options.slot_wait, ensure=options.ensure_training)`。下限の確かめは `require_claude` が持つ。`options.new_group` を `_run_claude` へ渡す（関所の起動 `_gated` は中継の経路だけで、今のまま） |
| `require_claude` | `adapters/claude.py` | 新設 | `require_claude(run: AccountRun, cwd, check: bool) -> Optional[ClaudeReply]` | CLI の下限を確かめ、古ければ起動しない回答（`Failure.REFUSED`）。`check` が偽なら確かめずに None |
| `_run_claude` | `adapters/claude.py` | 変更 | 末尾に `new_group: bool = True` | `_popen` へ渡す |
| `_popen` | `adapters/claude.py` | 変更 | 末尾に `new_group: bool = True` | `start_new_session=new_group`。偽なら呼び手の組に入り、`_talk` の時間切れの `_stop` は `process_group.stop_group` の「組が自分と同じなら `proc` へだけ送る」に従う |
| `gate` | `adapters/launch_slot.py` | 変更 | `gate(agent, *, cwd=None, env=None, wait: Optional[float] = None, ensure: bool = True)` | `wait` を `held` へ渡す。`ensure` が偽なら記録が無いか期限切れでも `training.ensure` を呼ばず `NotLaunched` |
| `held` | `adapters/launch_slot.py` | 変更 | `held(agent, *, cwd=None, environ=None, read_memory=None, wait: Optional[float] = None)` | `_wait_for` へ渡す。親のスロットを受け継ぐときは今のとおり待たない |
| `_wait_for` | `adapters/launch_slot.py` | 変更 | `_wait_for(agent, directory, lim, read_memory, limit: Optional[float] = None)` | 待つ上限を `limit`（None なら設定）にする。0 なら 1 度だけ取りに行き、取れなければ `NoSlot`。`limit` を渡したときは、プールの排他を待つ秒（`slot_pool.take` の `lock_seconds`）と眠る秒も残りの秒に収める（0 なら排他も待たない） |
| `take` | `slot_pool.py` | 変更 | 末尾に `lock_seconds: float = POOL_LOCK_SECONDS` | プールの排他を待つ上限の秒（省けば今の 10 秒） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_wait_for` | `limit` を省くと今の上限で待つ | `limit=0` で空きが無ければ眠らずに `NoSlot`。`limit=0` で偽の `take` に `lock_seconds=0` が渡り、正の `limit` では渡る秒と眠る秒の合計が `limit` を越えない（偽の時計） | — |
| `call_claude` | `slot_wait=0` で空きが無ければ `Failure.NO_SLOT` を返す（起動しない）。`cli_floor=False` で下限の確かめ（`claude --version`）を打たず、省いた既定では今のとおり打つ。`ensure_training=False` で記録が無ければ偽の `training.ensure` を呼ばずに `Failure.REFUSED`（起動しない）。偽の中継の確かめで利用上限を返すと、既定では替え先で 1 回打ち直し、`switch_account=False` では打ち直さずに `Failure.USAGE_LIMIT` | — | — |
| `_popen` | 偽の `Popen` に `start_new_session=True` が渡る（既定） | `new_group=False` で `start_new_session=False` が渡る | — |

## 実効の設定で指定のキーを定義する層

満たす決定: 決定 11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `effective_env` | `domains/relay/account_dir.py` | 変更 | 末尾に `keys: Sequence[str] = ()` | `keys` のそれぞれについて、定義する最も強い層の出所を `Resolved.key_origin` に入れる。強さは今の `helper` の決め方と同じ（サーバーから取ったコピーに項目があればそれ、無ければ管理者の設定のファイル（`managed-settings.json` と `managed-settings.d/*.json`）、無ければ `--settings`・ローカル・プロジェクト・利用者の順）。省けば今のまま |
| `Resolved` | `domains/relay/account_dir.py` | 変更 | 欄 `key_origin: dict[str, str]`（既定は空）を足す | キーの名前 → 出所の名札。値は持たない（I5） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `effective_env` の `keys` | 利用者の設定の `awsCredentialExport` → `key_origin` に `利用者の設定` | `keys` を省くと `key_origin` が空。`managed-settings.d/a.json` と `remote-settings.json` に同じキー → 出所はコピーの方 | 壊れた設定ファイルは今のとおり `unresolved` に入る |

## 設定と登録

満たす決定: 決定 2、受け入れ条件 7・8

- `contracts/limits.json`: 上の「内部のデータと定数」の 3 行。`hook.stop-wait.judge` の `seconds`（5）≤ `hook.budget.stop` の締め切り − 余白（7 − 1.5 = 5.5）。この比較は `runtimes` のランタイム（Claude Code）の予算とだけ行い、`hook.budget.stop.codex` とは比べない。`rest_seconds`（600）・`rounds`（3）・`auth_markers`・`endpoint` は秒の順序の検査に出てこない値で、`limits` の行には `hook.stop-wait.judge` だけを挙げる
- `contracts/hooks/guards.json`: `stop-wait` の `limits` を `["hook.stop-wait.judge"]` にし、`summary` に軽いモデルの判定と戻り方を足す
- `hooks/claude.json`・`hooks/codex.json`: `ndf hooks render` で作り直す（Claude Code の Stop の timeout 6 → 12。Codex の Stop は 6 のまま）。手で書かない
- `ndf hooks judge-cases [--cases <パス>] [--rounds <周の数>]`: 既定のパスは `contracts/hooks/reply-judge-cases.json`、既定の周の数は設定の `rounds`（3）。各例を周の数だけ打ち、1 周でも外れた例を結果 JSON の `items` に `id`・`expect`・外れた周の `got` で出す。`metrics` に件数・外れた例の件数・打った回数・秒の最小と最大。外れがあれば `status` は `stopped`（終了コード 1）
