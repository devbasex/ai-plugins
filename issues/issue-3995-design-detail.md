# #3995 の詳細設計: 文脈の切り替えの 2 つの値を 1 か所に置き、guard が知らせて止める

設計文書 [issue-3995-design.md](./issue-3995-design.md) の決定 1〜7 を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く（`docs/glossary/` だけはリポジトリの根から）。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設、`-` は消す）:

```text
plugins/ndf/
├── src/ndf/
│   ├── checks/children.json              S1e の packages から context_policy を外す
│   └── domains/
│       ├── hooks/
│       │   ├── context.py                ShellScript にリダイレクトとヒアドキュメントの列を足す
│       │   ├── dispatch.py               deny_key が none の拒否を数えず、案内へ下げない
│       │   ├── guards/context_limit.py   PostToolUse で知らせ、PreToolUse で止め続け、背景の作業が残る Stop で引継ぎを促す。サブエージェントを外す
│       │   └── guards/relay_mark.py      中継の下で背景の作業の開始（PreToolUse）と Stop の残りの数を書く
│       ├── relay/
│       │   ├── context_thresholds.py *   2 つの値を読む 1 つの関数と、判定の記録の語
│       │   ├── context_policy.py -       再起動の方針（方針名・余白・.ndf/context.json）
│       │   ├── cli.py                    wrapper_settings が thresholds の運用上限を使う
│       │   ├── cli_context.py            resolve の引数と結果、record の行、report の metrics
│       │   ├── context_records.py        record_row が Thresholds を受ける。語を context_thresholds から引く
│       │   ├── context_report.py         no_policy_rows を消す。語を context_thresholds から引く
│       │   ├── observe.py                会話の記録（本体とサブエージェント）の最後の更新からの秒
│       │   ├── signals.py                背景の作業の数のシグナルファイル（busy.json）の読み書き
│       │   ├── switch_decide.py          HANDOVER_PROMPT の文。背景の作業が残るか未確認の間は引継ぎを求めず、背景の作業中の止まりを会話の記録の更新で見る
│       │   └── wrapper.py                要るときだけ背景の作業の数と会話の記録の更新を読んで見回りの状態に渡す
│       └── statusline/statusline.sh      試行方式の色の分岐を消す
├── contracts/
│   ├── limits.json                       context.decide-from・context.limit を足し、hook.context-limit・relay.context を消す
│   ├── hooks/guards.json                 context-limit の events・deny_key・summary・params
│   ├── hooks/runtimes.json               claude の PreToolUse・PostToolUse の optional に agent_id
│   ├── domains/env.json                  NDF_CONTEXT_LIMIT の説明、NDF_CONTEXT_POLICY を消す
│   ├── domains/ndf-files.json            context の行を消す
│   ├── schemas/ndf-settings/context.schema.json -
│   └── schemas/io/relay.{args,result}.schema.json
├── docs/contracts/{env,files,ndf-settings,commands}.md   ndf check --write-spec で再生成
├── README.md                             hook の表の context-limit の行
├── skills/development-workflow/{SKILL.md,references/context-window.md,parallel.md,relay.md,glossary.md,work-vessels.md}
├── skills/statusline/SKILL.md
└── tests/domains/
    ├── hooks/test_guards.py・test_dispatch.py・test_context.py
    └── relay/test_context_thresholds.py *・test_context_policy.py -・test_relay_decide.py・test_relay_cli_parse.py・test_context_report.py・test_relay_parts.py・test_relay_wrapper.py
docs/glossary/glossary.json・docs/glossary.md
```

依存の向き: `relay/context_thresholds.py` は標準ライブラリだけに依存し、上限の設定の引き方を引数で受ける。持ち主は M3b（`ndf.domains.relay` の既定の持ち主）で、S1e の個別の列挙には載せない。guard（M3a の hook の経路）は `hooks/tables.policy_value` を、`relay/cli.py`・`cli_context.py` は `ndf.contracts.limits.value` を渡す。`context_records.py`・`context_report.py` は `context_thresholds` の語と型だけを引く。hook の経路から `ndf.contracts` へは依存しない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/relay/context_thresholds.py` | 新設 | 判断開始の値と運用上限を上限の設定と環境変数から 1 つの規則で解く | 標準ライブラリ | モジュール・`Thresholds` | `thresholds`（新設）。`CONTINUE`・`DENY`・`RESTART`・`REASONS`・`MIN_SAMPLES` を `context_policy` から移す |
| `src/ndf/domains/relay/context_policy.py` | 削除 | — | — | — | `resolve`・`read_setting`・`parse_setting`・`setting_id`・`is_positive_int`・`Resolved` ほかすべて |
| `src/ndf/domains/hooks/guards/context_limit.py` | 変更 | 文脈の量で知らせ、止める | `tables`・`context`・`context_thresholds`・`signals`・`strict` | モジュール | `check`（変更）・`_passes`（`_next_step` を置き換える）・`_in_handoff`・`_ndf_args_pass`・`_notice`・`_reason`（新設） |
| `src/ndf/domains/hooks/context.py` | 変更 | Bash の構文木を読む | — | `ShellScript` | `ShellScript` に `redirects`・`heredocs` を足す。`_walk` が集める（変更） |
| `src/ndf/domains/hooks/guards/relay_mark.py` | 変更 | 中継のマーカーと背景の作業の開始・残りの数を書く | `signals` | モジュール | `check`（変更）・`_starts_background`（新設） |
| `src/ndf/domains/relay/signals.py` | 変更 | 中継のシグナルファイル | — | モジュール | `write_busy`・`mark_busy_start`・`read_busy`（新設）、`BUSY_FILE`（定数） |
| `src/ndf/domains/relay/observe.py` | 変更 | 子の画面と会話の記録を読む | — | モジュール | `activity_age`（新設） |
| `src/ndf/domains/relay/wrapper.py` | 変更 | ラッパーの見回り | `signals`・`observe` | `Wrapper` | `_state`（変更） |
| `src/ndf/domains/hooks/dispatch.py` | 変更 | guard の判定をまとめる | — | モジュール | `run`（変更） |
| `src/ndf/domains/relay/cli.py` | 変更 | ラッパーの設定を組む | `context_thresholds`・`limits` | モジュール | `wrapper_settings`（変更） |
| `src/ndf/domains/relay/cli_context.py` | 変更 | `ndf relay context` のサブコマンド | `context_thresholds`・`limits` | モジュール | `_context_resolve`・`_context_record`・`_context_report`・引数の定義（変更） |
| `src/ndf/domains/relay/context_records.py` | 変更 | 判定の記録の行と較正 | `context_thresholds` | モジュール | `record_row`（シグネチャを変える） |
| `src/ndf/domains/relay/context_report.py` | 変更 | 作業ごとの集計 | `context_thresholds` | モジュール | 集計の `no_policy_rows` を消す（変更） |
| `src/ndf/domains/relay/switch_decide.py` | 変更 | ラッパーの判断 | — | `SectionState` | `HANDOVER_PROMPT`（文を変える）。`SectionState` に `background`・`activity_seconds` を足し、`_alive` が背景の作業が残るか未確認の間は `handover` を返さず、背景の作業が確かめられる間の止まりを会話の記録の更新で見る（変更）。`needs_background`（新設。見回りが背景の作業の数を読むかの判定） |
| `src/ndf/domains/statusline/statusline.sh` | 変更 | statusline の表示 | — | — | `applied.json` を読む試行方式の分岐を消し、500k（窓 200K 以下は 150k）の赤だけを残す |

## 2 つの値を解く関数（`context_thresholds`）

満たす決定: 決定 1・5、受け入れ条件 1・2・3・9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `thresholds` | `src/ndf/domains/relay/context_thresholds.py` | 新設 | `thresholds(environ: Mapping[str, str], value: Callable[[str, str], Any]) -> Thresholds` | 上限の設定の 2 つの値に `NDF_CONTEXT_LIMIT` を重ね、判断開始の値 ≤ 運用上限にそろえて返す |

- `thresholds`
  - 事前条件: `value(name, key)` は上限の設定の方針の値を返し、行か値が無ければ例外を上げる（`tables.policy_value` は `TableError`、`limits.value` は `LimitsError`）
  - 事後条件: `decide_from ≤ limit` で、どちらも正の整数。`sources` は `{"decide_from": "default" | "env", "limit": "default" | "env"}` で、環境変数を無視したときだけ `"ignored": "NDF_CONTEXT_LIMIT=<値>"` を持つ
  - 副作用: 無い

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `thresholds` | `NDF_CONTEXT_LIMIT` が正の整数でない（`0`・`-1`・`abc`・`1.5`・`²`） | 利用者の入力 | `thresholds`（`re.fullmatch("[0-9]+")` で見てから `int`） | 既定値で続ける（`ignored` を出所に書く） | `resolve` の `items[0].sources.ignored` に値が出る。guard とラッパーは既定で動く | 無い |
| `thresholds` | 上限の設定の 2 つの値が逆（判断開始の値 > 運用上限） | 契約の書き誤り | `thresholds` | 判断開始の値を運用上限へ下げて続ける（出所は運用上限の出所） | 何も出ない | 契約を直す |
| `thresholds` | 上限の設定に行が無い | 契約の壊れ | `value` | 呼び出し元へ返す（例外） | guard は dispatch が例外を `pass` にする（`fail_closed` は偽）。`resolve` は今の CLI の例外の扱いのまま | 契約を直して打ち直す |

### 処理の手順

| 条件 | 結果 |
| --- | --- |
| 環境変数が無いか空 | `decide_from` = `context.decide-from` の `count`、`limit` = `context.limit` の `count`、出所はどちらも `default` |
| `NDF_CONTEXT_LIMIT` が ASCII の数字だけで 0 より大きい | `limit` をその値、`sources.limit` を `env` |
| 正の整数でない | 既定のまま、`ignored` を書く |
| 上の後で `decide_from > limit` | `decide_from` を `limit` へ下げ、`sources.decide_from` を `sources.limit` と同じにする（前提 3） |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Thresholds` | 型（`NamedTuple`: `decide_from: int`・`limit: int`・`sources: dict[str, str]`。`as_dict()` を持つ） | `context_thresholds.py` | — |
| `DECIDE_FROM`・`LIMIT` | 定数（上限の設定の方針の名前 `context.decide-from`・`context.limit`） | `context_thresholds.py` | — |
| `ENV` | 定数（`NDF_CONTEXT_LIMIT`） | `context_thresholds.py` | — |
| `context.decide-from` | 設定値（`count`） | `contracts/limits.json` | 250000 |
| `context.limit` | 設定値（`count`） | `contracts/limits.json` | 500000 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `thresholds` | `ndf.contracts.limits.from_env` | `src/ndf/contracts/limits.py` を読む | 使えない。浮動小数点数で受けて誤りを `EnvError` で上げる（guard とラッパーを落とさない規則と合わない）うえ、`ndf.contracts` を hook の経路で import できない |
| `thresholds` | `context_policy._thresholds` | `src/ndf/domains/relay/context_policy.py` を読む | 下げる規則を移す。`str.isdigit()` は `²` を真にして `int()` が `ValueError` を上げるので、ASCII の数字の照合に替える。方針と設定の分岐は捨てる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `thresholds` | 環境変数なしで 250,000 / 500,000・出所 `default`。差し替えた `value` の値をそのまま返す | `NDF_CONTEXT_LIMIT=250000` で 250,000 / 250,000、`=249999` で 249,999 / 249,999、`=300000` で 250,000 / 300,000 | `0`・`-1`・`abc`・`1.5`・`²` で既定と `ignored`。`value` が 600,000 / 500,000 を返せば 500,000 / 500,000 |

## 文脈の量の guard（`context_limit`）と拒否の数え（`dispatch`）

満たす決定: 決定 2・3・4・6・7、受け入れ条件 4・5・6・8・11・15

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `src/ndf/domains/hooks/guards/context_limit.py` | 変更 | `check(ctx, svc) -> Verdict \| None`（変えない） | サブエージェントと読めない量を外し、PostToolUse で判断開始の値を知らせ、PreToolUse で運用上限を止め、背景の作業が残る Stop で引継ぎを促す |
| `_passes` | 同上 | 新設（`_next_step` を置き換える） | `_passes(ctx, params: dict) -> bool` | 運用上限以上でも通す呼び出し（引継ぎ文書の Read・Write・Edit、`pass_tools`、`pass_ndf` だけの Bash）か |
| `_in_handoff` | 同上 | 新設 | `_in_handoff(path: str, cwd: str, handoff_dir: str) -> bool` | 実際の読み書きの先が、`cwd` を含むリポジトリのメインディレクトリの引継ぎディレクトリの直下か |
| `_ndf_args_pass` | 同上 | 新設 | `_ndf_args_pass(words: list[str], cwd: str, params: dict) -> bool` | `ndf` に続く語が通す 2 つのコマンドの形で、`handoff check` が読み書きする本体と履歴（CLI と同じく `--cwd` の作業ツリーの根から解いた先）がメインディレクトリの引継ぎディレクトリの中か |
| `_notice` | 同上 | 新設 | `_notice(tokens: int, t: Thresholds) -> str` | 判断開始の値を超えたことと役ごとの切りどころを書いた案内の文 |
| `_reason` | 同上 | 新設 | `_reason(t: Thresholds, under_relay: bool, handoff_dir: str) -> str` | 運用上限の拒否の文（背景の作業の扱い・引継ぎ文書・終え方） |
| `run` | `src/ndf/domains/hooks/dispatch.py` | 変更 | 変えない | `spec.deny_key == "none"` の行の拒否は `counter.allow_deny` を呼ばず、案内へ下げない |

- `check`
  - 事前条件: dispatch が PreToolUse・PostToolUse・Stop で呼ぶ（`NDF_HOOK_OFF` に `context-limit` か `all` があれば呼ばれない）
  - 事後条件: `ctx.payload` に `agent_id` があれば None。Stop は、`ctx.background_tasks` が空か、数え手が使えないか、`counts` の `STOP_KEY` が 1 以上なら会話の記録を読まずに None。`tokens ≥ limit` かつ `svc.counter.bump(STOP_KEY) == 1` のときだけ `pushback(_reason(...))`（応答を終えさせず、同じ会話に監視の停止と引継ぎを指示する。子を止めない）、ほかは `passing`。PostToolUse で数え手が使えない（`svc.counter.usable` が偽）か、`svc.counter.data["counts"]` の `NOTICE_KEY` が 1 以上なら、会話の記録を読まずに None（`ctx.transcript_tail` に触れない）。文脈の量が None なら None。PreToolUse は `tokens ≥ limit` かつ `_passes` が偽のときだけ `deny(_reason(...))`、ほかは `passing`。PostToolUse は `decide_from ≤ tokens < limit` かつ `svc.counter.bump(NOTICE_KEY) == 1` のときだけ `advise(_notice(...))`、ほかは `passing`
  - 副作用: 数え手の `counts` を 1 つ増やす（PostToolUse の知らせの帯と、運用上限以上で背景の作業が残る Stop のときだけ）。会話の記録の末尾を読むのは、上の早い終わりに当たらないときだけ
- `_in_handoff`
  - 事前条件: `path` は入力の `file_path`、`cwd` は入力の `cwd`
  - 事後条件: 次をすべて満たすときだけ真。(1) メインディレクトリ `main` を、`cwd` から `.git` を持つ祖先をたどり（`strict._repo_root`）、共通の git のディレクトリ（`strict._git_dir`）の親として求められる（求められなければ偽）。(2) `real = os.path.realpath(os.path.join(cwd, path))`（最後の要素のリンクも解く）の親が、`os.path.realpath(main)` に `handoff_dir`（`.ndf/handoff`）を字面で足したものと等しい。`.ndf` か `handoff` がリンクなら `realpath` がそこを外へ解くので等しくならない。(3) `os.path.basename(real)` が空でない
  - 副作用: 無い（ファイルを読まず、`os.path.realpath` と `.git` の読み取りだけ）。git のコマンドを起動しない
- `_ndf_args_pass`
  - 事前条件: `words` は `body_argv` から先頭の `ndf` を除いた語で、どの語も展開（変数・チルダ・グロブ・コマンド置換）を含まない（含めば呼ぶ前に止める）
  - 事後条件: 次をすべて満たすときだけ真。(1) `--cwd <値>` は 0 か 1 回で、あれば `os.path.join(cwd, 値)` を含むリポジトリのメインディレクトリが `cwd` のものと同じ（求められなければ偽）。(2) `--cwd` を除いた語が `relay context record` で始まり、残りが `--work`・`--event`・`--verdict`・`--reason` とその値だけ（`--opt=値` の形も含め、ほかの語があれば偽）か、`records handoff check` で始まり、残りが位置引数の本体 1 つ・`--trim`・`--history <値>` だけ。(3) `handoff check` の本体と `--history` の値（あれば）が、どちらも `_in_handoff(値, base, params["handoff_dir"])` を満たす。`base` は CLI（`records/cli.py` の `_root` と `_handoff`）と同じ基準で求める。`--cwd` があれば `os.path.join(cwd, 値)`、無ければ `cwd` を起点に、`.git` を持つ最も近い祖先（`strict._repo_root`。`git rev-parse --show-toplevel` と同じく、worktree ではその worktree の根）を `base` とする（求められなければ偽）。CLI は相対のパスをこの作業ツリーの根から解くので、Bash の `cwd` からは解かない。絶対のパスは `base` に依らない。例: worktree の `cwd` で `ndf records --cwd <メインディレクトリ> handoff check .ndf/handoff/next.md` は、メインディレクトリの根から解いて通す。`--cwd` 無しで worktree の `cwd` から同じ相対パスを渡すと、CLI は worktree の `.ndf/handoff/next.md` を読み書きするので止める。worktree の `cwd` で `cwd` がその根の下の `src` のとき `.ndf/handoff/next.md` を渡しても、`src/.ndf/handoff` でなく根から解く
  - 副作用: 無い（`_in_handoff` と同じ読み取りだけ）
- `_notice`・`_reason`
  - 事後条件: 文は PreToolUse・PostToolUse の注入の上限（`hook.budget.*` の `inject_chars` 600 字）以下。文脈の量は `_reason` に入れない（`deny_key` が `none` で数えないので、文が呼び出しごとに変わっても数えには効かないが、拒否の文を同じにしておく）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | 数え手が使えない | 排他を取れない・状態のファイルが壊れた | `Counter.bump` が 0 を返す | 知らせずに通す。拒否は数えないので変わらない | 何も出ない | 次の呼び出しで打ち直す |
| `check` | 上限の設定の行が無い | 契約の壊れ | `thresholds` の例外 | dispatch が `pass` にして計測に例外の種類を書く | 何も出ない | 契約を直す |
| `_passes` | Bash の構文木を読めない | 壊れたコマンド | `ctx.shell` が空 | 通さない（止める） | 拒否の文 | 引継ぎに要るコマンドだけを打ち直す |

### 処理の手順

| 条件の組み合わせ | 結果 |
| --- | --- |
| 入力に `agent_id` がある | None（判定しない） |
| PostToolUse・数え手が使えないか、この入力の後に知らせ済み | None（会話の記録を読まない） |
| `usage` が読めない | None |
| PreToolUse・`tokens < limit` | `passing(context_tokens=tokens)` |
| PreToolUse・`tokens ≥ limit`・`_passes` が真 | `passing(context_tokens=tokens)` |
| PreToolUse・`tokens ≥ limit`・`_passes` が偽 | `deny(_reason(t, under, handoff_dir))`。notes は `context_tokens`・`under_relay` |
| PostToolUse・`decide_from ≤ tokens < limit`・この入力の後で 1 回目 | `advise(_notice(tokens, t))` |
| PostToolUse・ほか | `passing(context_tokens=tokens)` |
| Stop・背景の作業が無いか、数え手が使えないか、この入力の後に促し済み | None（会話の記録を読まない） |
| Stop・背景の作業が残る・`tokens ≥ limit`・この入力の後で 1 回目 | `pushback(_reason(t, under, handoff_dir))`。notes は `context_tokens`・`background` |
| Stop・ほか | `passing(context_tokens=tokens)` |

`_passes` の判定:

| 呼び出し | 通すか |
| --- | --- |
| Read・Write・Edit で、`_in_handoff(file_path, ctx.cwd, params["handoff_dir"])` が真 | 通す（相対・絶対のどちらも。`..` とシンボリックリンクは `realpath` で解いた後の実際の先で照らす。引継ぎ文書の保存先はメインディレクトリだけなので、worktree の `cwd` からもメインディレクトリの `.ndf/handoff` を照らし、worktree の `.ndf/handoff` と別のリポジトリのものは外れる） |
| `params["pass_tools"]` に名前がある Tool（TaskStop） | 通す |
| Bash で、`ctx.shell.commands` が 1 つ以上あり、どれもラッパーを外した語（`body_argv`）が `ndf` で始まり、続く語が `_ndf_args_pass` を満たし（`--cwd <値>` は `handoff.md` が手順で付ける唯一の共通オプション。本体と `--trim` の履歴の実際のパスを引継ぎディレクトリに限る）、語が展開を含まず、どれも背景（`background`）でなく、コマンド置換（`$(...)`・バッククォート）・入れ子の起動（`bash -c`・`eval`。`ctx.shell.waits` が `commands` より多い）・ヒアドキュメントとヒアストリング（`ctx.shell.heredocs`）を含まず、`ctx.shell.redirects` のどれもが許すリダイレクト（下の段落）である（見分け方は `tdd-cycle` で構文木を走らせて決める）。`&&`・`;`・パイプでつないだものは単純なコマンドを 1 つずつ照らす | 通す |
| ほか | 止める |

許すリダイレクトは、引継ぎの 2 つのコマンドがファイルへ書かずに済む 2 つの形だけである。(1) ファイル記述子の複製で、演算子が `>&`・`<&` で先が数字だけか `-`（`2>&1`・`>&2`・`2>&-`）。(2) 演算子が `>`・`>>`・`&>`・`&>>` で、先が字面で `/dev/null`。ほか（ファイルへの `>`・`>>`・`>|`・`&>`・`&>>`・`<>`、入力の `<`、`>&` の先が数字でない語、先がプロセス置換 `>(...)`・`<(...)` か変数の展開を含む語）は止める。`ShellScript.redirects` は構文木のすべての `file_redirect` の（演算子, 先の字面, 先がプロセス置換か展開を含むか）の組で、`ShellScript.heredocs` は `heredoc_redirect`・`herestring_redirect` の数である。どちらも外側のスクリプトだけを見る（入れ子の起動は上の条件で先に止める）。

`ShellScript` の変更（`src/ndf/domains/hooks/context.py`）:

| 項目 | 型 | 既定値 | 集め方 |
| --- | --- | --- | --- |
| `redirects` | `tuple[Redirect, ...]`（`Redirect` は `NamedTuple`: `op: str`・`target: str`・`dynamic: bool`） | `()` | `_walk` が `file_redirect` の節で、演算子の子の字面と先の節の字面、先の節かその子孫が `process_substitution`・`expansion`・`simple_expansion`・`command_substitution` かを読む |
| `heredocs` | `int` | `0` | `_walk` が `heredoc_redirect`・`herestring_redirect` の節を数える |

`_reason` の文（中継の下）: 「文脈の量が運用上限（{limit} token）を超えた。次の手: 背景の監視（ndf lanes wait・Monitor）があれば TaskStop で止めて完了通知で確かめる。supervisor・ndf plan queue は止めずに報告を待つ。止める前に wait が戻っていたら結果を処理せず、引継ぎ文書の「現在地」の wait の行をそのまま残す。引継ぎ文書（オーガナイザーは {handoff_dir}parallel-<計画>.md）を書いて ndf records handoff check で確かめ、応答の最後に次のセグメントのブロックを書いて終える」。中継の外は最後を「ndf-next のブロックを出して新しいセッションで続ける」にする。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `events` | 設定値 | `contracts/hooks/guards.json` の `context-limit` | `["PreToolUse", "PostToolUse", "Stop"]` |
| `deny_key` | 設定値 | 同上 | `none`（今は `reason`） |
| `params.handoff_dir` | 設定値 | 同上 | `.ndf/handoff/`（変えない） |
| `params.pass_tools` | 設定値（運用上限以上でも通す Tool の名前） | 同上 | `["TaskStop"]` |
| `params.pass_ndf` | 設定値（運用上限以上でも通す `ndf` のサブコマンドの語の並びと、許す引数） | 同上 | `{"records handoff check": {"positional": 1, "flags": ["--trim"], "options": ["--history"], "paths": ["positional", "--history"]}, "relay context record": {"positional": 0, "flags": [], "options": ["--work", "--event", "--verdict", "--reason"], "paths": []}}`。`paths` は `_in_handoff` で照らす値 |
| `suggests` | 設定値 | 同上 | 今の `Write .ndf/handoff/next.md` のまま |
| `NOTICE_KEY` | 定数（数え手の `counts` のキー） | `context_limit.py` | `context-limit:notice` |
| `STOP_KEY` | 定数（同上。背景の作業が残る Stop の促し） | `context_limit.py` | `context-limit:stop` |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 中継の下と外の拒否の文、案内の文に切りどころと運用上限が入る。文は 600 字以下 | PreToolUse の 499,999（止めない）・500,000（止める）。PostToolUse の 249,999（何もしない）・250,000（知らせる）・500,000（知らせない）。同じ入力の 2 回目は知らせない。`Counter.reset("human")` の後は知らせ、`reset("relay")` の後は知らせない。知らせた後の同じ入力の PostToolUse を 10 回続けると、`read_tail` を差し替えて数えた読み取りが 0 回 | `agent_id` があれば 300,000 の PostToolUse も 600,000 の PreToolUse も None。`agent_type` だけなら判定する。`usage` が無ければ None。数え手が使えなければ知らせず、`read_tail` も呼ばない |
| `check`（Stop） | 500,000 で `background_tasks` が 1 件の Stop に、監視の TaskStop・supervisor の待ち・引継ぎ文書を書く文の `pushback` を返す（中継の下でも外でも。子は止めない） | 499,999 では返さない。同じ入力の 2 回目の Stop では返さず、`reset("human")` の後は返す | `background_tasks` が空の Stop と数え手が使えない Stop では `read_tail` が 0 回。`agent_id` があれば None |
| `_passes` | 500,000 で `.ndf/handoff/x.md`・`/abs/repo/.ndf/handoff/x.md` の Read・Write・Edit、worktree の `cwd` からメインディレクトリの `.ndf/handoff/x.md`、TaskStop、`ndf records --cwd <メインディレクトリ> handoff check <メインの .ndf/handoff/next.md> --trim --history <メインの .ndf/handoff/next-history.md>`（worktree の `cwd` からも）・worktree の `cwd` からの `ndf records --cwd <メインディレクトリ> handoff check .ndf/handoff/next.md --trim --history .ndf/handoff/next-history.md`（相対の 2 つをメインディレクトリの根から解く）・メインディレクトリの下の `src` を `cwd` にした `ndf records handoff check .ndf/handoff/x.md`（根から解く）・`ndf relay context record --event 引継ぎ開始 --work 3995`・`ndf records handoff check .ndf/handoff/x.md 2>&1`・`… 2>/dev/null` の Bash が通る。`/tmp/link`（メインディレクトリの `.ndf/handoff` を指すリンク）の下の `x.md` は実際の先が引継ぎディレクトリなので通る | — | `.ndf/handoff/../settings.json`・`other/handoff/x.md`・`.ndf/handoff/sub/x.md`・worktree の `.ndf/handoff/x.md`・別のリポジトリの `.ndf/handoff/x.md` の Write は止める。`.ndf/handoff/x.md` 自体がリポジトリの外のファイルを指すリンク・`handoff` がリンク・`.ndf` がリンクのときの Read・Write・Edit は止める。`--history` が `/home/u/notes.md`・`.ndf/handoff/../x.md`・worktree の `.ndf/handoff/h.md` を指す `handoff check`、本体がメインの `README.md` の `handoff check`、`--cwd` が別のリポジトリの `handoff check`、worktree の `cwd` から `--cwd` 無しの `ndf records handoff check .ndf/handoff/next.md`（CLI は worktree の文書を読み書きする）、`--history "$H"`・`~/x.md`・`*.md` の語、知らない引数（`--force`・2 つ目の位置引数）、`ndf records handoff other`・`ndf relay context record --work 1 --out f`・`ndf records handoff check x && rm y`・`ndf lanes status p1`・`ndf records handoff check x > settings.json`・`>> f`・`>| f`・`&> f`・`1<>f`・`< f`・`> >(cat)`・`>&out`・`> "$F"`・ヒアドキュメントつき・`&` つきの Bash は止める |
| `ShellScript` | `ndf records handoff check x 2>&1 >/dev/null` の `redirects` が 2 つの組で `heredocs` が 0 | リダイレクトの無い文は `redirects` が空 | `cat <<EOF` は `heredocs` が 1、`> >(cat)` と `> "$F"` は `dynamic` が真 |
| `run` | `deny_key` が `none` の guard の拒否は、違う呼び出しで 3 回続けても 3 回とも `deny` で、数え手が使えなくても `deny` | — | `deny_key` が `reason` の guard は今のとおり 3 回目に案内へ下がる |

## ラッパーと `ndf relay context`

満たす決定: 決定 1・4・5、受け入れ条件 1・3・7・9

ラッパーは子の背景の作業を、中継のマーカーの guard が書くシグナルファイル（`<中継の保存先>/busy.json`）で知る。書くのは 2 つの時点である。(1) 背景の作業を始める呼び出しの PreToolUse（Agent・`run_in_background` が真の Bash・Monitor。サブエージェントの中は除く）で、数を 1 つ増やす（開始）。(2) Stop で、`background_tasks` の数で置き換える（残り。終わった作業はここで数から消える）。起動の ID（`NDF_RELAY_LAUNCH`）を照らすので、止めた子の hook が書いた数を次の子の数として読まない。

ラッパーは数を 3 つに分けて扱う。今の起動の数が 0 のときだけ背景の作業が無いと確かめられたとし、1 以上は背景の作業あり、ファイルが無い・別の起動の ID・壊れている（今の起動の最初の Stop の前、書き込みの失敗を含む）は未確認とする。引継ぎのための強制終了は 0 のときだけ行い、背景の作業ありと未確認では保留する。書き込みに失敗した hook は `busy.json` を消す（古い数を残さず、未確認にする）。

背景の作業ありと確かめられる間の止まりの判定は、子の出力でなく会話の記録の更新で見る。背景の Agent（supervisor）は出力を出さなくても、Tool を呼ぶたびにサブエージェントの会話の記録へ書く。子の出力と、会話の記録の本体とサブエージェントの記録（`<本体の拡張子を除いたパス>/subagents/*.jsonl`）の最後の更新のうち新しいほうから `relay.stall`（1,800 秒）が過ぎたら、作業が止まったとして今のとおり同じ会話で起動し直す。サブエージェントの記録の置き場が見つからなければ、子の出力だけで見る（今の判定に戻る）。背景の Bash（`ndf plan queue`・`ndf lanes wait`）は会話の記録を書かないので、出力が止まれば今のとおり 1,800 秒で起動し直す。

見回りはふだん `busy.json` を読まない。読むのは、`switch_decide.needs_background` が真のとき（文脈の量 ≥ 運用上限・静か・このセグメントで引継ぎを求めていない、または出力の止まりが `relay.stall` 以上）だけで、会話の記録の更新を読むのは、さらに数が 1 以上で出力の止まりが `relay.stall` 以上のときだけである。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `wrapper_settings` | `src/ndf/domains/relay/cli.py` | 変更 | 変えない | `context_limit` を `thresholds(environ, limits.value).limit` にする（`environ` は今と同じ `dict(os.environ)`） |
| `_context_resolve` | `src/ndf/domains/relay/cli_context.py` | 変更 | 変えない | `thresholds(os.environ, limits.value)` を返す。結果は `items=[{"decide_from", "limit", "sources"}]`・`metrics={"decide_from", "limit"}`、`summary` は「判断開始の値 {d} / 運用上限 {l}」。`--root`・`--policy` を引数から消す |
| `_context_record` | 同上 | 変更 | 変えない | `thresholds(os.environ, limits.value)` を `record_row` へ渡す |
| `_context_report` | 同上 | 変更 | 変えない | `metrics` から `no_policy_rows` を消す |
| `record_row` | `src/ndf/domains/relay/context_records.py` | シグネチャを変える | `record_row(session_id, work, applied: Thresholds, event, reason) -> dict` | 行の `decide_from`・`limit`・`sources` を `applied` から書く。`policy`・`margin`・`config_error` は書かない |
| `write_busy` | `src/ndf/domains/relay/signals.py` | 新設 | `write_busy(directory: Path, launch: Optional[str], count: int, wait: float) -> bool` | Stop の残りの数で `busy.json` を `{"launch", "count", "written_at"}` に置き換える（排他の中で `write_json` を使う）。書けたら真。書けなければ `busy.json` を消して偽 |
| `mark_busy_start` | 同上 | 新設 | `mark_busy_start(directory: Path, launch: Optional[str], wait: float) -> bool` | 排他の中で、同じ起動の数があればそれに 1 を足し、無いか別の起動なら 1 を書く。書けなければ `busy.json` を消して偽 |
| `read_busy` | 同上 | 新設 | `read_busy(directory: Path, launch: str) -> Optional[int]` | 今の起動の数。ファイルが無い・`launch` が違う・読めない・形が違えば None（未確認） |
| `relay_mark.check` | `src/ndf/domains/hooks/guards/relay_mark.py` | 変更 | 変えない | 中継の下（`relay_dir` がある）で入力に `agent_id` が無いとき、PreToolUse は `_starts_background(ctx)` が真なら `mark_busy_start` を書いて None（ほかの PreToolUse はファイルに触れずに None）。Stop は、最後の応答とブロックの有無に依らず先に `write_busy(directory, relay_launch(ctx.env), len(ctx.background_tasks), wait)` を書き、その後は今のまま |
| `_starts_background` | 同上 | 新設 | `_starts_background(ctx) -> bool` | Tool の名前が `params.background_tools`（Agent・Monitor）にあるか、Bash で `tool_input.run_in_background` が真か |
| `activity_age` | `src/ndf/domains/relay/observe.py` | 新設 | `activity_age(transcript: Path, now: float) -> Optional[float]` | 会話の記録の本体と `<本体の拡張子を除いたパス>/subagents/*.jsonl` の最後の更新（`mtime`）からの秒。本体もサブエージェントの置き場も無ければ None |
| `SectionState.background`・`activity_seconds` | `src/ndf/domains/relay/switch_decide.py` | 変更（項目を足す） | `background: Optional[int] = None`・`activity_seconds: Optional[float] = None` | 今の起動の背景の作業の数（None は未確認か読んでいない）と、会話の記録の最後の更新からの秒（None は読んでいないか置き場が無い） |
| `needs_background` | 同上 | 新設 | `needs_background(state: SectionState) -> bool` | `over and quiet and not handover_asked`、または `stall_seconds` が正で `idle_seconds ≥ stall_seconds`。見回りが `busy.json` を読むかを決める（`background` を見ずに決まる） |
| `_alive` | 同上 | 変更 | 変えない | `handover` は `over and quiet and not handover_asked and background == 0` のときだけ返す（None・1 以上では返さない）。止まりの判定は、`background` が 1 以上で `activity_seconds` が None でなければ `min(idle_seconds, activity_seconds) ≥ stall_seconds`、ほかは今のとおり `idle_seconds ≥ stall_seconds` で `restart` |
| `Wrapper._state` | `src/ndf/domains/relay/wrapper.py` | 変更 | 変えない | 先に `background` 無しで状態を組み、`needs_background` が真のときだけ `signals.read_busy(self.dir, sec.launch)` を読み、さらに数が 1 以上で `idle_seconds ≥ stall_seconds` のときだけ `observe.activity_age(transcript, time.time())` を読んで、`dataclasses.replace` で足す |
| `HANDOVER_PROMPT` | `src/ndf/domains/relay/switch_decide.py` | 変更（定数） | — | 「文脈の量が運用上限を超えたので、ラッパーが子を止めて起動し直した（背景の作業は終わった）。オーガナイザーは引継ぎ文書の「現在地」に打ち直す ndf lanes wait の行を残す。引継ぎ文書を更新して ndf records handoff check で確かめ、次のセグメントのブロック（ndf-next）を出してセグメントを終える」 |

`relay.result.schema.json` は `items` の `policy`・`margin`・`config_error` と `metrics` の `no_policy_rows` を消し、`metrics` に `decide_from`（integer）を足す（`metrics` は追加の項目を許さない）。`relay.args.schema.json` は `context resolve` の `--root`・`--policy` を消す。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_context_resolve` | `--policy`・`--root` を渡した | 旧い引数 | argparse | 止める | 引数の誤り（終了コード 2） | 引数を外して打ち直す |
| `write_busy`・`mark_busy_start` | 排他を取れない・書けない | 並ぶ書き手・ディスク | `write_json` が偽 | `busy.json` を消して続ける（マーカーの判定は今のまま） | 無し（ラッパーは未確認として引継ぎを保留する） | 次の Stop で書き直す。古い数は残らない |
| `read_busy` | ファイルが無い・別の起動・JSON が壊れた・`count` が整数でない | 最初の Stop の前・書き込みの失敗・書きかけ・手での編集 | `read_busy` | None（未確認）として扱い、引継ぎを保留する | 無し（止まりの判定は子の出力で見て、1,800 秒で起動し直す） | 次の Stop で書き直される |
| `activity_age` | サブエージェントの記録の置き場が無い・読めない | Claude Code の置き場の変更・権限 | `activity_age` | 本体の更新だけで見る。本体も無ければ None（子の出力だけで見る） | 無し | 無い |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `wrapper_settings` → `switch_decide.decide` | 環境変数なしで 500,000 の静かな子に `handover`。文に `wait` の行と `ndf records handoff check` を含む | 499,999 で `wait`。`NDF_CONTEXT_LIMIT=300000` の環境から組んだ設定で 300,000 に `handover`、299,999 で `wait`（時刻は差し替えた時計で進め、実際には待たない）。500,000 で `background=0` なら `handover`、`background=1` と `None`（未確認）なら `wait` | 同じセグメントの 2 度目は求めない（今のまま）。`background=1` で出力が 1,800 秒止まっても `activity_seconds=10` なら `wait`、`activity_seconds=1800` なら `restart`。`background=1` で `activity_seconds=None` と、`background=None` では今のとおり出力の 1,800 秒で `restart` |
| `write_busy`・`mark_busy_start`・`read_busy` | 書いた数を同じ起動の ID で読み返せる。0 を書いた後の `mark_busy_start` で 1、もう 1 度で 2、Stop の `write_busy(0)` で 0 | ファイルが無ければ None、別の起動の ID なら None、別の起動の数の後の `mark_busy_start` は 1 | 壊れた JSON・`count` が文字列なら None。書き込みを失敗させると前の数のファイルが消え、`read_busy` が None |
| `relay_mark.check` | 中継の下の Stop で `background_tasks` が 2 件なら数が 2、空なら 0。次のセグメントのブロックが無い応答・最後の応答が空でも書く。Stop で 0 を書いた後、Agent・`run_in_background` の Bash・Monitor の PreToolUse で数が 1 になる | 中継の外（`relay_dir` が無い）では書かない。`run_in_background` の無い Bash・Read の PreToolUse と、`agent_id` がある入力では `busy.json` に触れない | 背景の作業が残るときのマーカーの変更要求は今のまま |
| 0 の後の起動（r2 の場面） | 同じ起動で Stop が 0 を書き、Agent の PreToolUse を通した後、次の Stop より先に 500,000・静かになった見回りが `handover` を返さない | 今の起動の最初の Stop の前の見回りも `handover` を返さない | — |
| `Wrapper._state` | 子の Stop が書いた数が見回りの状態の `background` に入る | 起動し直した後は前の起動の数を読まない（None） | 499,999 で静か・500,000 で静かでない・引継ぎを求め済み（出力の止まりは `relay.stall` 未満）の見回りでは、`read_busy` と `activity_age` を差し替えて数えた読み取りがどちらも 0 回。数が 0 のときと出力の止まりが `relay.stall` 未満のときは `activity_age` が 0 回 |
| `activity_age` | 本体とサブエージェントの記録の新しいほうの `mtime` から秒を返す | サブエージェントの置き場が無ければ本体だけ | 本体も無ければ None |
| `_context_resolve` | 既定で 250,000 / 500,000・出所 `default`、`NDF_CONTEXT_LIMIT=300000` で運用上限 300,000。出した JSON が結果のスキーマを通る | — | `--policy current`・`--root .` は引数の誤り |
| `record_row`・`report`・`calibrate` | 行に `decide_from`・`limit`・`sources` があり、`policy`・`margin`・`config_error` が無い | — | 旧い行（3 つの列を持つ）と新しい行を混ぜた記録で、`report` の区分と `calibrate` の見込みが旧い行だけのときと同じ |

## 契約と手順書

満たす決定: 決定 1・2・4・5・6・7、受け入れ条件 2・10・11・12・13・14

| ファイル | 変える中身 |
| --- | --- |
| `contracts/limits.json` | `context.decide-from`（`count` 250000）・`context.limit`（`count` 500000）を足し、`hook.context-limit`・`relay.context` を消す |
| `contracts/hooks/guards.json` | `context-limit` の `events` に PostToolUse・Stop を足し、`deny_key` を `none` に、`summary` を「PostToolUse で判断開始の値を利用者の入力ごとに 1 度知らせ、PreToolUse で運用上限以上の呼び出しを引継ぎに要るものを除いて止め続け、運用上限以上で背景の作業が残る Stop で 1 度、監視の停止と引継ぎを促す」にし、`params.pass_tools`・`pass_ndf` を足す。`relay-marker` の `events` に PreToolUse を足し、`params.background_tools`（`["Agent", "Monitor"]`）を足し、`summary` に「中継の下で背景の作業の開始（PreToolUse）と Stop の残りの数を busy.json へ書く（ラッパーは数が 0 と確かめられたときだけ引継ぎを求める）」を足す |
| `contracts/hooks/runtimes.json` | `claude` の `PreToolUse`・`PostToolUse` の `optional` に `agent_id` |
| `contracts/domains/env.json` | `NDF_CONTEXT_LIMIT` の説明を「文脈の量の運用上限（正の整数）。guard・中継のラッパー・ndf relay context resolve が読む。判断開始の値がこれを上回れば同じ値へ下げる。正の整数でなければ無視する」にし、`area` を `hooks` に。`NDF_CONTEXT_POLICY` の行を消す |
| `contracts/domains/ndf-files.json` | `context` の行を消す |
| `src/ndf/checks/children.json` | S1e の `packages` から `ndf.domains.relay.context_policy` を外す（新しいモジュールは M3b の持ち物として列挙しない） |
| `docs/contracts/*.md` | `ndf check --write-spec` で再生成する（env・files・ndf-settings・commands） |
| `README.md` | hook の表の `context-limit` の行を「判断開始の値で知らせ、運用上限で引継ぎに要る呼び出しを除いて止め続ける」に |
| `skills/development-workflow/references/context-window.md` | 「遅くとも 20 万」を 2 つの値（250,000 で次のカットポイントで切る・500,000 で止まる）へ直す。「上限を超えたら hook が止める」を、止める範囲（引継ぎに要る呼び出しを除くすべての Tool。数えずに止め続ける）・知らせ（PostToolUse で利用者の入力ごとに 1 度）・役ごとの切りどころ（レーンの conductor は 4 つのカットポイント、オーガナイザーは知らせを処理し終えて次の `wait` を張る前と利用者への報告を終えた時点）・止める口（`NDF_HOOK_OFF=context-limit`）・サブエージェントを見ないことで書き直し、「同じ起動の 2 度目を 1 度だけ通す」「ラッパーの下では止め続ける」の分け方を消す。`scripts/lib/token-guard-stages.txt` の参照を消す。「再起動の方針」のセクションを消し、「判定と再起動を記録する」は残す。statusline の行（238 行）の「hook の止める上限」を 500,000 にする |
| `skills/development-workflow/references/parallel.md` | 「セッションの切り替え」に、オーガナイザーの 2 つの値と切りどころ、運用上限で止められたときも TaskStop で `wait` を止めてから切ること（止める前に戻った `wait` は結果を処理せず行を残す）、新しい会話が引継ぎ文書の「現在地」の `wait` から見張りを続けることを書く |
| `skills/development-workflow/references/work-vessels.md` | 「会話を替えて続けるか（試行方式の判断帯）」を、hook が止める判断でなく `calibrate` の見込みの式（閾値の見直し #2908 の材料）として書き直し、「再起動の方針」への参照を消す |
| `skills/development-workflow/references/relay.md`・`glossary.md`・`SKILL.md` | 既定の 200,000 を 250,000 / 500,000 に、試行方式の記述と「1 度の通し」の記述を消す |
| `skills/statusline/SKILL.md` | 「hook の止める上限」を 500,000 に、試行方式の色の行と `applied.json` の読み取りの行を消す |
| `docs/glossary/glossary.json` | 設計文書の「用語」の表のとおり（意味の変更 2・廃止 4）。続けて `ndf records glossary render` と `meanings` |
