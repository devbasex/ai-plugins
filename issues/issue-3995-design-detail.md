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
│       │   ├── context.py                ShellScript にリダイレクト・ヒアドキュメント・変数の代入の列を足す
│       │   ├── dispatch.py               deny_key が none の拒否を数えず、案内へ下げない
│       │   └── guards/context_limit.py   PostToolUse で知らせ、PreToolUse で止め続ける。サブエージェントを外す
│       ├── relay/
│       │   ├── context_thresholds.py *   2 つの値を読む 1 つの関数と、判定の記録の語
│       │   ├── context_policy.py -       再起動の方針（方針名・余白・.ndf/context.json）
│       │   ├── cli.py                    wrapper_settings が thresholds の運用上限を使う
│       │   ├── cli_context.py            resolve の引数と結果、record の行、report の metrics
│       │   ├── context_records.py        record_row が Thresholds を受ける。語を context_thresholds から引く
│       │   ├── context_report.py         no_policy_rows を消す。語を context_thresholds から引く
│       │   └── switch_decide.py          HANDOVER_PROMPT の文
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
    └── relay/test_context_thresholds.py *・test_context_policy.py -・test_relay_decide.py・test_relay_cli_parse.py・test_context_report.py
docs/glossary/glossary.json・docs/glossary.md
```

依存の向き: `relay/context_thresholds.py` は標準ライブラリだけに依存し、上限の設定の引き方を引数で受ける。持ち主は M3b（`ndf.domains.relay` の既定の持ち主）で、S1e の個別の列挙には載せない。guard（M3a の hook の経路）は `hooks/tables.policy_value` を、`relay/cli.py`・`cli_context.py` は `ndf.contracts.limits.value` を渡す。`context_records.py`・`context_report.py` は `context_thresholds` の語と型だけを引く。hook の経路から `ndf.contracts` へは依存しない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/relay/context_thresholds.py` | 新設 | 判断開始の値と運用上限を上限の設定と環境変数から 1 つの規則で解く | 標準ライブラリ | モジュール・`Thresholds` | `thresholds`（新設）。`CONTINUE`・`DENY`・`RESTART`・`REASONS`・`MIN_SAMPLES` を `context_policy` から移す |
| `src/ndf/domains/relay/context_policy.py` | 削除 | — | — | — | `resolve`・`read_setting`・`parse_setting`・`setting_id`・`is_positive_int`・`Resolved` ほかすべて |
| `src/ndf/domains/hooks/guards/context_limit.py` | 変更 | 文脈の量で知らせ、止める | `tables`・`context`・`context_thresholds`・`signals`（`relay_dir` で中継の下かを見る。今のまま）・`strict` | モジュール | `check`（変更）・`_passes`（`_next_step` を置き換える）・`_in_handoff`・`_ndf_args_pass`・`_notice`・`_reason`（新設） |
| `src/ndf/domains/hooks/context.py` | 変更 | Bash の構文木を読む | — | `ShellScript` | `ShellScript` に `redirects`・`heredocs`・`assignments` を足す。`_walk` が集める（変更） |
| `src/ndf/domains/hooks/dispatch.py` | 変更 | guard の判定をまとめる | — | モジュール | `run`（変更） |
| `src/ndf/domains/relay/cli.py` | 変更 | ラッパーの設定を組む | `context_thresholds`・`limits` | モジュール | `wrapper_settings`（変更） |
| `src/ndf/domains/relay/cli_context.py` | 変更 | `ndf relay context` のサブコマンド | `context_thresholds`・`limits` | モジュール | `_context_resolve`・`_context_record`・`_context_report`・引数の定義（変更） |
| `src/ndf/domains/relay/context_records.py` | 変更 | 判定の記録の行と較正 | `context_thresholds` | モジュール | `record_row`（シグネチャを変える） |
| `src/ndf/domains/relay/context_report.py` | 変更 | 作業ごとの集計 | `context_thresholds` | モジュール | 集計の `no_policy_rows` を消す（変更） |
| `src/ndf/domains/relay/switch_decide.py` | 変更 | ラッパーの判断 | — | モジュール | `HANDOVER_PROMPT`（文を変える） |
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
| `check` | `src/ndf/domains/hooks/guards/context_limit.py` | 変更 | `check(ctx, svc) -> Verdict \| None`（変えない） | サブエージェントと読めない量を外し、PostToolUse で判断開始の値を知らせ、PreToolUse で運用上限を止める |
| `_passes` | 同上 | 新設（`_next_step` を置き換える） | `_passes(ctx, params: dict) -> bool` | 運用上限以上でも通す呼び出し（引継ぎ文書の Read・Write・Edit、`pass_tools`、`pass_ndf` だけの Bash）か |
| `_in_handoff` | 同上 | 新設 | `_in_handoff(path: str, cwd: str, handoff_dir: str) -> bool` | 実際の読み書きの先が、`cwd` を含むリポジトリのメインディレクトリの引継ぎディレクトリの直下か |
| `_ndf_args_pass` | 同上 | 新設 | `_ndf_args_pass(words: list[str], cwd: str, params: dict) -> bool` | `ndf` に続く語が通す 2 つのコマンドの形で、`handoff check` が読み書きする本体と履歴（CLI と同じく `--cwd` の作業ツリーの根から解いた先）がメインディレクトリの引継ぎディレクトリの中か |
| `_notice` | 同上 | 新設 | `_notice(tokens: int, t: Thresholds) -> str` | 判断開始の値を超えたことと役ごとの切りどころを書いた案内の文 |
| `_reason` | 同上 | 新設 | `_reason(t: Thresholds, under_relay: bool, handoff_dir: str) -> str` | 運用上限の拒否の文（背景の作業の扱い・引継ぎ文書・終え方） |
| `run` | `src/ndf/domains/hooks/dispatch.py` | 変更 | 変えない | `spec.deny_key == "none"` の行の拒否は `counter.allow_deny` を呼ばず、案内へ下げない |

- `check`
  - 事前条件: dispatch が PreToolUse・PostToolUse で呼ぶ（`NDF_HOOK_OFF` に `context-limit` か `all` があれば呼ばれない）
  - 事後条件: `ctx.payload` に `agent_id` があれば None。PostToolUse で数え手が使えない（`svc.counter.usable` が偽）か、`svc.counter.data["counts"]` の `NOTICE_KEY` が 1 以上なら、会話の記録を読まずに None（`ctx.transcript_tail` に触れない）。文脈の量が None なら None。PreToolUse は `tokens ≥ limit` かつ `_passes` が偽のときだけ `deny(_reason(...))`、ほかは `passing`。PostToolUse は `decide_from ≤ tokens < limit` かつ `svc.counter.bump(NOTICE_KEY) == 1` のときだけ `advise(_notice(...))`、ほかは `passing`
  - 副作用: 数え手の `counts` を 1 つ増やす（PostToolUse の知らせの帯だけ）。会話の記録の末尾を読むのは、上の早い終わりに当たらないときだけ
- `_in_handoff`
  - 事前条件: `path` は入力の `file_path`、`cwd` は入力の `cwd`
  - 事後条件: 次をすべて満たすときだけ真。(1) メインディレクトリ `main` を、`os.path.realpath(cwd)` から `.git` を持つ祖先をたどり（`strict._repo_root`）、共通の git のディレクトリ（`strict._git_dir`）の親として求められる（求められなければ偽）。(2) `real = os.path.realpath(os.path.join(cwd, path))`（最後の要素のリンクも解く）の親が、`os.path.realpath(main)` に `handoff_dir`（`.ndf/handoff`）を字面で足したものと等しい。`.ndf` か `handoff` がリンクなら `realpath` がそこを外へ解くので等しくならない。(3) `os.path.basename(real)` が空でない
  - 副作用: 無い（ファイルを読まず、`os.path.realpath` と `.git` の読み取りだけ）。git のコマンドを起動しない
- `_ndf_args_pass`
  - 事前条件: `words` は `body_argv` から先頭の `ndf` を除いた語で、どの語も展開（変数・チルダ・グロブ・コマンド置換）を含まない（含めば呼ぶ前に止める）。CLI の起点は Bash の `cwd` と同じでなければならないので、作業ディレクトリか Git の解決先を変える形は呼ぶ前に止める。(a) ラッパーのコマンドを持たない（`argv == body_argv`。`env -C <dir>`・`env GIT_DIR=…`・`timeout`・`nice` などを先頭に付けた `ndf` は止める。`body_argv` はラッパーを外すので、外した後の語だけを見ると `env -C <worktree>` の起点の変化を見落とす）。(b) 外側のスクリプトに変数の代入（前置きの `GIT_DIR=… ndf …` と単独の `GIT_WORK_TREE=…;` のどちらも。`ShellScript.assignments`）が無い（`argv` は代入の語を含まないので、`argv` だけでは見えない）。(c) `cd`・`pushd`・`export` などは `ndf` で始まらないので、下の `_passes` の条件で既に止まる。手順書（`handoff.md`・`relay.md`・`context-window.md`）の 2 つのコマンドはどれもラッパーも代入も付けないので、止まる形は手順に無い
  - 事後条件: 次をすべて満たすときだけ真。(1) `--cwd <値>` は 0 か 1 回で、あれば起点 `start = os.path.realpath(os.path.join(cwd, 値))`（字面でなく実パス。CLI の Git はリンクを解いた先の作業ツリーを使うので、それにそろえる）を含むリポジトリのメインディレクトリが `os.path.realpath(cwd)` のものと同じ（求められなければ偽）。(2) `--cwd` を除いた語が `relay context record` で始まり、残りが `--work`・`--event`・`--verdict`・`--reason` とその値だけ（`--opt=値` の形も含め、ほかの語があれば偽）か、`records handoff check` で始まり、残りが位置引数の本体 1 つ・`--trim`・`--history <値>` だけ。(3) `handoff check` の本体と `--history` の値（あれば）が、どちらも `_in_handoff(値, base, params["handoff_dir"])` を満たす。`base` は CLI（`records/cli.py` の `_root` と `_handoff`）と同じ基準で求める。`--cwd` があれば `start`、無ければ `os.path.realpath(cwd)`（CLI の `Path.cwd()` は実パスを返す）を起点に、`.git` を持つ最も近い祖先（`strict._repo_root`。`git rev-parse --show-toplevel` と同じく、worktree ではその worktree の根）を `base` とする（求められなければ偽）。CLI は相対のパスをこの作業ツリーの根から解くので、Bash の `cwd` からは解かない。絶対のパスは `base` に依らない。例: worktree の `cwd` で `ndf records --cwd <メインディレクトリ> handoff check .ndf/handoff/next.md` は、メインディレクトリの根から解いて通す。`--cwd` 無しで worktree の `cwd` から同じ相対パスを渡すと、CLI は worktree の `.ndf/handoff/next.md` を読み書きするので止める。worktree の `cwd` で `cwd` がその根の下の `src` のとき `.ndf/handoff/next.md` を渡しても、`src/.ndf/handoff` でなく根から解く。メインディレクトリの `cwd` で `--cwd /tmp/wt-src`（worktree の `src` を指すリンク）なら、リンク元の祖先（`/tmp`）でなく worktree の根から解き、相対の本体は worktree の `.ndf/handoff` になるので止める
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

`_passes` の判定:

| 呼び出し | 通すか |
| --- | --- |
| Read・Write・Edit で、`_in_handoff(file_path, ctx.cwd, params["handoff_dir"])` が真 | 通す（相対・絶対のどちらも。`..` とシンボリックリンクは `realpath` で解いた後の実際の先で照らす。引継ぎ文書の保存先はメインディレクトリだけなので、worktree の `cwd` からもメインディレクトリの `.ndf/handoff` を照らし、worktree の `.ndf/handoff` と別のリポジトリのものは外れる） |
| `params["pass_tools"]` に名前がある Tool（TaskStop） | 通す |
| Bash で、`ctx.shell.commands` が 1 つ以上あり、どれもラッパーを持たず（`argv == body_argv`）、外側のスクリプトに変数の代入が無く（`ctx.shell.assignments` が 0）、語（`body_argv`）が `ndf` で始まり、続く語が `_ndf_args_pass` を満たし（`--cwd <値>` は `handoff.md` が手順で付ける唯一の共通オプション。本体と `--trim` の履歴の実際のパスを引継ぎディレクトリに限る）、語が展開を含まず、どれも背景（`background`）でなく、コマンド置換（`$(...)`・バッククォート）・入れ子の起動（`bash -c`・`eval`。`ctx.shell.waits` が `commands` より多い）・ヒアドキュメントとヒアストリング（`ctx.shell.heredocs`）を含まず、`ctx.shell.redirects` のどれもが許すリダイレクト（下の段落）である（見分け方は `tdd-cycle` で構文木を走らせて決める）。`&&`・`;`・パイプでつないだものは単純なコマンドを 1 つずつ照らす | 通す |
| ほか | 止める |

許すリダイレクトは、引継ぎの 2 つのコマンドがファイルへ書かずに済む 2 つの形だけである。(1) ファイル記述子の複製で、演算子が `>&`・`<&` で先が数字だけか `-`（`2>&1`・`>&2`・`2>&-`）。(2) 演算子が `>`・`>>`・`&>`・`&>>` で、先が字面で `/dev/null`。ほか（ファイルへの `>`・`>>`・`>|`・`&>`・`&>>`・`<>`、入力の `<`、`>&` の先が数字でない語、先がプロセス置換 `>(...)`・`<(...)` か変数の展開を含む語）は止める。`ShellScript.redirects` は構文木のすべての `file_redirect` の（演算子, 先の字面, 先がプロセス置換か展開を含むか）の組で、`ShellScript.heredocs` は `heredoc_redirect`・`herestring_redirect` の数である。どちらも外側のスクリプトだけを見る（入れ子の起動は上の条件で先に止める）。

`ShellScript` の変更（`src/ndf/domains/hooks/context.py`）:

| 項目 | 型 | 既定値 | 集め方 |
| --- | --- | --- | --- |
| `redirects` | `tuple[Redirect, ...]`（`Redirect` は `NamedTuple`: `op: str`・`target: str`・`dynamic: bool`） | `()` | `_walk` が `file_redirect` の節で、演算子の子の字面と先の節の字面、先の節かその子孫が `process_substitution`・`expansion`・`simple_expansion`・`command_substitution` かを読む |
| `heredocs` | `int` | `0` | `_walk` が `heredoc_redirect`・`herestring_redirect` の節を数える |
| `assignments` | `int` | `0` | `_walk` が外側のスクリプトの `variable_assignment` の節（単純コマンドの前置きと単独の代入の両方）を数える |

`_reason` の文（中継の下）: 「文脈の量が運用上限（{limit} token）を超えた。次の手: 背景の監視（ndf lanes wait・Monitor）があれば TaskStop で止めて完了通知で確かめる。supervisor・ndf plan queue は止めずに報告を待つ。止める前に wait が戻っていたら結果を処理せず、引継ぎ文書の「現在地」の wait の行をそのまま残す。引継ぎ文書（オーガナイザーは {handoff_dir}parallel-<計画>.md）を書いて ndf records handoff check で確かめ、応答の最後に次のセグメントのブロックを書いて終える」。中継の外は最後を「ndf-next のブロックを出して新しいセッションで続ける」にする。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `events` | 設定値 | `contracts/hooks/guards.json` の `context-limit` | `["PreToolUse", "PostToolUse"]` |
| `deny_key` | 設定値 | 同上 | `none`（今は `reason`） |
| `params.handoff_dir` | 設定値 | 同上 | `.ndf/handoff/`（変えない） |
| `params.pass_tools` | 設定値（運用上限以上でも通す Tool の名前） | 同上 | `["TaskStop"]` |
| `params.pass_ndf` | 設定値（運用上限以上でも通す `ndf` のサブコマンドの語の並びと、許す引数） | 同上 | `{"records handoff check": {"positional": 1, "flags": ["--trim"], "options": ["--history"], "paths": ["positional", "--history"]}, "relay context record": {"positional": 0, "flags": [], "options": ["--work", "--event", "--verdict", "--reason"], "paths": []}}`。`paths` は `_in_handoff` で照らす値 |
| `suggests` | 設定値 | 同上 | 今の `Write .ndf/handoff/next.md` のまま |
| `NOTICE_KEY` | 定数（数え手の `counts` のキー） | `context_limit.py` | `context-limit:notice` |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 中継の下と外の拒否の文、案内の文に切りどころと運用上限が入る。文は 600 字以下 | PreToolUse の 499,999（止めない）・500,000（止める）。PostToolUse の 249,999（何もしない）・250,000（知らせる）・500,000（知らせない）。同じ入力の 2 回目は知らせない。`Counter.reset("human")` の後は知らせ、`reset("relay")` の後は知らせない。知らせた後の同じ入力の PostToolUse を 10 回続けると、`read_tail` を差し替えて数えた読み取りが 0 回 | `agent_id` があれば 300,000 の PostToolUse も 600,000 の PreToolUse も None。`agent_type` だけなら判定する。`usage` が無ければ None。数え手が使えなければ知らせず、`read_tail` も呼ばない |
| `_passes` | 500,000 で `.ndf/handoff/x.md`・`/abs/repo/.ndf/handoff/x.md` の Read・Write・Edit、worktree の `cwd` からメインディレクトリの `.ndf/handoff/x.md`、TaskStop、`ndf records --cwd <メインディレクトリ> handoff check <メインの .ndf/handoff/next.md> --trim --history <メインの .ndf/handoff/next-history.md>`（worktree の `cwd` からも）・worktree の `cwd` からの `ndf records --cwd <メインディレクトリ> handoff check .ndf/handoff/next.md --trim --history .ndf/handoff/next-history.md`（相対の 2 つをメインディレクトリの根から解く）・メインディレクトリの下の `src` を `cwd` にした `ndf records handoff check .ndf/handoff/x.md`（根から解く）・`ndf relay context record --event 引継ぎ開始 --work 3995`・`ndf records handoff check .ndf/handoff/x.md 2>&1`・`… 2>/dev/null` の Bash が通る。`/tmp/link`（メインディレクトリの `.ndf/handoff` を指すリンク）の下の `x.md` は実際の先が引継ぎディレクトリなので通る。`--cwd /tmp/main-src`（メインディレクトリの `src` を指すリンク）の `handoff check .ndf/handoff/next.md` は通る | — | `.ndf/handoff/../settings.json`・`other/handoff/x.md`・`.ndf/handoff/sub/x.md`・worktree の `.ndf/handoff/x.md`・別のリポジトリの `.ndf/handoff/x.md` の Write は止める。`.ndf/handoff/x.md` 自体がリポジトリの外のファイルを指すリンク・`handoff` がリンク・`.ndf` がリンクのときの Read・Write・Edit は止める。`--history` が `/home/u/notes.md`・`.ndf/handoff/../x.md`・worktree の `.ndf/handoff/h.md` を指す `handoff check`、本体がメインの `README.md` の `handoff check`、`--cwd` が別のリポジトリの `handoff check`、メインディレクトリの `cwd` から `--cwd /tmp/wt-src`（worktree の `src` を指すリンク）の `handoff check .ndf/handoff/next.md`（CLI は worktree の文書を更新する）、worktree の `cwd` から `--cwd` 無しの `ndf records handoff check .ndf/handoff/next.md`（CLI は worktree の文書を読み書きする）、メインディレクトリの `cwd` から `env -C <worktree> ndf records handoff check .ndf/handoff/next.md --trim --history .ndf/handoff/next-history.md`（CLI は worktree の文書を読み書きする）・`env GIT_DIR=<worktree の .git> ndf records handoff check …`・`GIT_WORK_TREE=<worktree> ndf records handoff check …`・`GIT_DIR=x; ndf records handoff check …`・`timeout 60 ndf records handoff check …`・`cd <worktree> && ndf records handoff check …`、`--history "$H"`・`~/x.md`・`*.md` の語、知らない引数（`--force`・2 つ目の位置引数）、`ndf records handoff other`・`ndf relay context record --work 1 --out f`・`ndf records handoff check x && rm y`・`ndf lanes status p1`・`ndf records handoff check x > settings.json`・`>> f`・`>| f`・`&> f`・`1<>f`・`< f`・`> >(cat)`・`>&out`・`> "$F"`・ヒアドキュメントつき・`&` つきの Bash は止める |
| `_ndf_args_pass` と CLI の更新先 | 一時のメインディレクトリ・worktree・それぞれの `src` を指すリンクを作り、リンクを経由する `--cwd` とリンクの無い `--cwd`・`--cwd` 無しの組ごとに、`_ndf_args_pass` が真になった引数を `ndf records … handoff check --trim` で実際に打つと、更新されたファイル（`mtime` の変化）がいつもメインディレクトリの `.ndf/handoff` の下にある | `base` は `records/cli.py` の `_root`（実際の `git rev-parse --show-toplevel`）が同じ引数で返す根と、どの組でも等しい | 偽になった組（worktree を指すリンクの `--cwd` と相対の本体）を打つと worktree の文書が更新される（止める理由の確かめ） |
| `ShellScript` | `ndf records handoff check x 2>&1 >/dev/null` の `redirects` が 2 つの組で `heredocs`・`assignments` が 0 | リダイレクトの無い文は `redirects` が空 | `cat <<EOF` は `heredocs` が 1、`> >(cat)` と `> "$F"` は `dynamic` が真。`GIT_DIR=x ndf a` と `GIT_DIR=x; ndf a` は `assignments` が 1 |
| `run` | `deny_key` が `none` の guard の拒否は、違う呼び出しで 3 回続けても 3 回とも `deny` で、数え手が使えなくても `deny` | — | `deny_key` が `reason` の guard は今のとおり 3 回目に案内へ下がる |

## ラッパーと `ndf relay context`

満たす決定: 決定 1・4・5、受け入れ条件 1・3・7・9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `wrapper_settings` | `src/ndf/domains/relay/cli.py` | 変更 | 変えない | `context_limit` を `thresholds(environ, limits.value).limit` にする（`environ` は今と同じ `dict(os.environ)`） |
| `_context_resolve` | `src/ndf/domains/relay/cli_context.py` | 変更 | 変えない | `thresholds(os.environ, limits.value)` を返す。結果は `items=[{"decide_from", "limit", "sources"}]`・`metrics={"decide_from", "limit"}`、`summary` は「判断開始の値 {d} / 運用上限 {l}」。`--root`・`--policy` を引数から消す |
| `_context_record` | 同上 | 変更 | 変えない | `thresholds(os.environ, limits.value)` を `record_row` へ渡す |
| `_context_report` | 同上 | 変更 | 変えない | `metrics` から `no_policy_rows` を消す |
| `record_row` | `src/ndf/domains/relay/context_records.py` | シグネチャを変える | `record_row(session_id, work, applied: Thresholds, event, reason) -> dict` | 行の `decide_from`・`limit`・`sources` を `applied` から書く。`policy`・`margin`・`config_error` は書かない |
| `HANDOVER_PROMPT` | `src/ndf/domains/relay/switch_decide.py` | 変更（定数） | — | 「文脈の量が運用上限を超えたので、ラッパーが子を止めて起動し直した（背景の作業は終わった）。オーガナイザーは引継ぎ文書の「現在地」に打ち直す ndf lanes wait の行を残す。引継ぎ文書を更新して ndf records handoff check で確かめ、次のセグメントのブロック（ndf-next）を出してセグメントを終える」 |

`relay.result.schema.json` は `items` の `policy`・`margin`・`config_error` と `metrics` の `no_policy_rows` を消し、`metrics` に `decide_from`（integer）を足す（`metrics` は追加の項目を許さない）。`relay.args.schema.json` は `context resolve` の `--root`・`--policy` を消す。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_context_resolve` | `--policy`・`--root` を渡した | 旧い引数 | argparse | 止める | 引数の誤り（終了コード 2） | 引数を外して打ち直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `wrapper_settings` → `switch_decide.decide` | 環境変数なしで 500,000 の静かな子に `handover`。文に `wait` の行と `ndf records handoff check` を含む | 499,999 で `wait`。`NDF_CONTEXT_LIMIT=300000` の環境から組んだ設定で 300,000 に `handover`、299,999 で `wait`（時刻は差し替えた時計で進め、実際には待たない） | 同じセグメントの 2 度目は求めない（今のまま） |
| `_context_resolve` | 既定で 250,000 / 500,000・出所 `default`、`NDF_CONTEXT_LIMIT=300000` で運用上限 300,000。出した JSON が結果のスキーマを通る | — | `--policy current`・`--root .` は引数の誤り |
| `record_row`・`report`・`calibrate` | 行に `decide_from`・`limit`・`sources` があり、`policy`・`margin`・`config_error` が無い | — | 旧い行（3 つの列を持つ）と新しい行を混ぜた記録で、`report` の区分と `calibrate` の見込みが旧い行だけのときと同じ |

## 契約と手順書

満たす決定: 決定 1・2・4・5・6・7、受け入れ条件 2・10・11・12・13・14

| ファイル | 変える中身 |
| --- | --- |
| `contracts/limits.json` | `context.decide-from`（`count` 250000）・`context.limit`（`count` 500000）を足し、`hook.context-limit`・`relay.context` を消す |
| `contracts/hooks/guards.json` | `context-limit` の `events` に PostToolUse を足し、`deny_key` を `none` に、`summary` を「PostToolUse で判断開始の値を利用者の入力ごとに 1 度知らせ、PreToolUse で運用上限以上の呼び出しを引継ぎに要るものを除いて止め続ける」にし、`params.pass_tools`・`pass_ndf` を足す |
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
