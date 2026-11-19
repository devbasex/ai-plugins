# #3851 #3856 #3854 の詳細設計: 待ちの guard・工程の出力の確かめ・棚卸しの順位

設計文書 [issue-3851-3856-3854-design.md](./issue-3851-3856-3854-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── hooks/guards.json
│   └── schemas/
│       ├── io/issues.result.schema.json
│       ├── io/progress.result.schema.json
│       └── ndf-settings/backlog.schema.json
├── docs/contracts/commands.md
├── agents/{supervisor.md, supervisor-waits.md}
├── skills/
│   ├── development-workflow/references/{waiting.md, agent-layers.md}
│   └── backlog-refinement/{SKILL.md, references/ranking.md, references/depths.md}
├── src/ndf/
│   ├── domains/hooks/
│   │   ├── context.py
│   │   └── guards/{sleep.py, process_wait.py*}
│   ├── progress/command.py
│   └── domains/backlog/
│       ├── model.py
│       ├── estimates_decide.py
│       ├── table_decide.py
│       ├── cli.py
│       ├── cli_input.py
│       └── collect.py
└── tests/
    ├── domains/hooks/{test_guards.py, test_context.py}
    ├── progress/{test_command.py, test_command_parser.py}
    └── domains/backlog/{test_issues_cli.py, test_selection_estimates.py, test_apply_actions.py, test_routes.py}
```

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `src/ndf/domains/hooks/context.py` | 変更 | 単純コマンドにループの位置とラッパーのコマンドを外した語を持たせ、`bash -c` 系・`eval` の中身を読み直す | `tree_sitter_bash`、`ndf.testing.weight` |
| `src/ndf/domains/hooks/guards/sleep.py` | 変更 | `body_argv` が `sleep` の単純コマンドを、ループの本体なら秒によらず、外なら上限で止める | `context.py`・`tables.py` |
| `src/ndf/domains/hooks/guards/process_wait.py` | 新設 | `-f` を付けた `pgrep` / `pkill` がループの条件か本体にあれば止める | `context.py` |
| `contracts/hooks/guards.json` | 変更 | `process-wait` の行を足し、`sleep` の要約を直す | 無し |
| `src/ndf/progress/command.py` | 変更 | `check` のサブコマンドを足す | `definition.py`・`stages.py` |
| `src/ndf/domains/backlog/model.py` | 変更 | 見積の入力の形を読み、根拠と区分と帯を確かめる。設定から `harm_guide` を読む | 無し |
| `src/ndf/domains/backlog/estimates_decide.py` | 変更 | 出どころに説明の表を足し、起点以前の見積の無い課題を分ける | `model.py` |
| `src/ndf/domains/backlog/table_decide.py` | 変更 | 11 系の順位の表の行を読む | 無し |
| `src/ndf/domains/backlog/cli.py` | 変更 | `rank --since-ref`・返りの `left_for_review` と `incomplete`、`candidates` の `metrics.since`、plan の `repo` と書き込み先の突き合わせ | `cli_input.py`・`collect.py` |
| `src/ndf/domains/backlog/cli_input.py` | 変更 | 説明の表を見積の出どころに渡し、起点の時刻を渡す | `issues_gh.py`（`milestone_rows`・`created_at` を今のまま使う）・`table_decide.py` |
| `src/ndf/domains/backlog/collect.py` | 変更 | 差分の棚卸しの `unscored` を起点の時刻より後に作られた課題に絞る | `estimates_decide.py` |
| 契約のスキーマ 3 つ | 変更 | `harm_guide`・`left_for_review`・`restored`・`since`、`progress check` の `metrics.outputs`（文字列の配列）を受ける | 無し |
| 手順書と文書 8 つ（`commands.md`・エージェントの定義 2 つ・`waiting.md`・`agent-layers.md`・`backlog-refinement` の 3 つ） | 変更 | 設計の決定 4・5・6・7 の記述 | 無し |

## クラスと関数の構造

型の関係は設計文書の「構造」のセクションのクラス図を指し、ここでは描き直さない。

| 保存先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `hooks/context.py` | `ShellCommand` | 欄 `loop_body`・`loop_condition`・`body_argv`（新設） |
| `hooks/context.py` | `ShellScript` | 欄 `waits`（新設）。`commands`・`loops` は今のまま |
| `hooks/context.py` | モジュール | `parse_shell`（変更）、`_collect`・`_loop_position`・`_strip_wrappers`・`_inner_scripts`（新設） |
| `hooks/guards/sleep.py` | モジュール | `check`（変更） |
| `hooks/guards/process_wait.py` | モジュール | `check`・`_full_match`（新設） |
| `progress/command.py` | モジュール | `_cmd_check`（新設）、`parser`（変更） |
| `backlog/model.py` | `Estimate`・`Config` | `Estimate` の欄 `why`・`impact`・`mitigation`・`restored`、`Config.harm_guide`（新設）。`parse_estimate`・`config_from`・`Estimate.to_json`（変更）、`check_band`（新設） |
| `backlog/estimates_decide.py` | モジュール | `estimates`・`_pick`（変更）、`left_for_review`（新設） |
| `backlog/table_decide.py` | モジュール | `parse_rows`（新設） |
| `backlog/cli.py` | モジュール | `cmd_rank`・`_rank_metrics`・`cmd_candidates`・`cmd_apply`・`build_parser`（変更）、`_check_repo`（新設） |
| `backlog/cli_input.py` | モジュール | `rank_board`（変更）、`table_rows`（新設） |
| `backlog/collect.py` | モジュール | `collect`（変更）。作成の時刻は `issues_gh._issue` の `created_at` を使う（変えない） |

## 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `parse_shell` | `hooks/context.py` | 変更 | `parse_shell(text: str) -> ShellScript`（今のまま） | 外側の単純コマンドを集め、ループの位置・本体の語を付ける。`_collect` で内側の単純コマンドを集めて `waits` に入れる | 無し | `commands`・`loops` は外側の単純コマンドだけで、今と同じ値（足した欄を除く）。`waits` は `commands` に内側の単純コマンドを足した列 | 無し |
| `_collect` | `hooks/context.py` | 新設 | `_collect(text: str, depth: int, outer: ShellCommand \| None) -> list[ShellCommand]` | 1 つのスクリプトの単純コマンドを集め、`outer` があればその `background`・`loop_body`・`loop_condition` を各単純コマンドの値と論理和で合わせ、`_inner_scripts` の中身を `depth + 1` で読み直して後ろに足す | `depth` が `_INNER_DEPTH` 未満なら読み直す | 外側を引き継いだ単純コマンドの列 | 無し |
| `_loop_position` | `hooks/context.py` | 新設 | `_loop_position(node) -> tuple[bool, bool]` | 祖先の `while_statement` の `body` / `condition` の欄の中かを返す | `node` が構文木の節 | （本体の中か, 条件の中か） | 無し |
| `_strip_wrappers` | `hooks/context.py` | 新設 | `_strip_wrappers(argv: tuple[str, ...]) -> tuple[str, ...]` | 先頭のラッパーのコマンドとその引数を、ラッパーごとのオプションの表（`_WRAPPER_ARGS`）で外す | 無し | 先頭がラッパーのコマンドでない語の列。全部がラッパーのコマンドとその引数か、表に無いオプション・コマンドを起こさない形なら空 | 無し |
| `_inner_scripts` | `hooks/context.py` | 新設 | `_inner_scripts(body_argv: tuple[str, ...]) -> list[str]` | `bash`・`sh`・`zsh`・`dash` の `-c` の引数と `eval` の引数を返す | 無し | 読み直す文字列の列 | 無し |
| `check` | `hooks/guards/sleep.py` | 変更 | `check(ctx, svc) -> Verdict \| None` | `waits` のうち `body_argv[0] == "sleep"` の単純コマンドを、前景でループの本体なら止め、外なら上限で止める。NDF のコマンドの後続の判定は今のまま `commands` で行う | 無し | 拒否か None | 無し |
| `check` | `hooks/guards/process_wait.py` | 新設 | `check(ctx, svc) -> Verdict \| None` | `waits` に、`pgrep` / `pkill` で `-f` を持ち、ループの条件か本体にある単純コマンドがあれば止める | 無し | 拒否か None | 無し |
| `_full_match` | `hooks/guards/process_wait.py` | 新設 | `_full_match(name: str, args: tuple[str, ...]) -> bool` | `pgrep` / `pkill` の引数を getopt と同じ順で読み、オプションとして `--full` か `-f`（束ねた `-af` を含む）を持つかを返す。`--` の後ろと値を取るオプションの値は見ない | `name` が `pgrep` か `pkill` | 真偽 | 無し |
| `_cmd_check` | `progress/command.py` | 新設 | `_cmd_check(a, _cwd, st) -> dict` | 出力を読み、工程の定義で `validate_stage_output` と `keep_declared` を呼んで書かずに返す | ステートマシンがある | 結果 JSON。ステートマシンは変わらない | ファイルを読む |
| `parse_estimate` | `backlog/model.py` | 変更 | `parse_estimate(raw, cfg, *, given: bool) -> Estimate` | 4 列を `{"step","why"}` で読む。`given` なら根拠を必須にし、帯を確かめる | 無し | `Estimate`。合わなければ `EstimateError`（理由の文） | 無し |
| `check_band` | `backlog/model.py` | 新設 | `check_band(ubv: int, impact: str, mitigation: str, cfg: Config) -> str \| None` | UBV が実害の目安の帯にあるかを確かめる | `cfg.harm_guide` が空でなく、`impact` がそのキー（空なら呼ばない） | None か、外れた理由の文 | 無し |
| `config_from` | `backlog/model.py` | 変更 | 今のシグネチャのまま | `harm_guide` を読む。無ければ既定を置き、既定の端のどれかが `scale` に無ければ空を置く | 無し | `Config.harm_guide`（空なら帯を確かめない） | 無し |
| `estimates` | `backlog/estimates_decide.py` | 変更 | `estimates(numbers, given, stored, previous, tables, cfg, digests, since=None, created=None)` | 出どころを 4 つの順に採り、外したものと見直しへ回すものと戻したものを分ける | `since` を渡すなら `created` も渡す | （見積, excluded, left_for_review, restored） | 無し |
| `parse_rows` | `backlog/table_decide.py` | 新設 | `parse_rows(description: str) -> dict[int, dict]` | 説明の `### 順位` のセクションの表の行を、番号 → 段階の辞書にする | 無し | 印の付いた段階を外した辞書。表が無ければ空 | 無し |
| `table_rows` | `backlog/cli_input.py` | 新設 | `table_rows(gh, issues) -> dict[int, dict]` | 課題のマイルストーンの説明から表の行を集める | 無し | 番号 → 行 | GitHub を読む（1 回の一覧） |
| `rank_board` | `backlog/cli_input.py` | 変更 | 今のまま | `table_rows` と起点の時刻を `estimates` に渡す | 無し | 今の返りに `left_for_review` を足す | GitHub を読む |
| `cmd_rank` | `backlog/cli.py` | 変更 | 今のまま | `excluded` があれば深さによらず `incomplete` で返す | 無し | 結果 JSON | 記録を書く |
| `cmd_candidates` | `backlog/cli.py` | 変更 | 今のまま | 差分の棚卸しで `since` を求め、`collect` へ渡し、`metrics.since` を返す | 無し | 結果 JSON | 記録を書く |
| `_check_repo` | `backlog/cli.py` | 新設 | `_check_repo(sd: Path, repo: str, plan: dict) -> None` | 記録の `candidates.json` の `metrics.repo` を、書き込み先と plan の `repo` に突き合わせる | 無し | 合えば何も返さない。合わなければ `Stop("precondition", …)` | 記録を読む |
| `cmd_apply` | `backlog/cli.py` | 変更 | 今のまま | 書く前に `_check_repo` を呼ぶ | 無し | 今のまま | GitHub を書く |
| `_checked_rank` | `backlog/cli.py` | 変更 | 今のまま | digest の照らしに加え、`rank.json` の `metrics.excluded` が空であることを確かめる | 無し | 外した課題を持つ順位なら `Stop("precondition", …)` | 記録を読む |
| `collect` | `backlog/collect.py` | 変更 | `Ask` に `since: Optional[str]` を足す | 差分の棚卸しで `since` があれば、`unscored` を作成の時刻が `since` より後の課題に絞る | 無し | 今のまま | 無し |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_collect` | 内側のスクリプトの入れ子が深い | `bash -c "bash -c '…'"` の繰り返し | `depth` が 5 に達した | 既定値で続ける（それ以上読み直さない） | 無し（外側の判定だけ） | 再実行できる |
| `parse_shell` | 構文木を作れない | 壊れたシェルの文 | tree-sitter の例外 | 呼び出し元へ返す（今の振り分けが通す。I4） | 無し | 再実行できる |
| `sleep.check` | ループの本体の `sleep` | 前景の待ちのループ | `loop_body` | 止める | 拒否の理由「ループの中で sleep して待つ呼び出しを止めた。条件で待つなら Monitor を、終わりを待つなら Bash の run_in_background を使う」 | 書き直して打ち直す |
| `process_wait.check` | ループのプロセス名の待ち | `until ! pgrep -f …` | `loop_body` か `loop_condition` と `_full_match` | 止める | 拒否の理由「pgrep -f / pkill -f は待つ側のコマンド行にも一致して終わらない。終わりは完了マーカーのファイルか Monitor で待つ」 | 書き直して打ち直す |
| `_cmd_check` | 出力を読めない | ファイルが無い・JSON でない | `read_json` | 呼び出し元へ返す | `finish` と同じ（`stopped`） | 直して打ち直す |
| `_cmd_check` | 規約に合わない | 規約外のキー・欠け | `validate_stage_output` | 止める | `stopped`・`refused`、終了コード 1。`summary` は「<工程> の出力が規約に合わない: <誤り>」 | 出力を直して打ち直す |
| `_cmd_check` | 工程が無い | 行名の誤り | `snap.stage` | 呼び出し元へ返す | `finish` と同じ | 直して打ち直す |
| `parse_estimate` | 形が違う | 整数の列・`step` の無い列 | 列の型 | `EstimateError` | `excluded` の理由「<列> は {"step", "why"} で書く」 | 見積を直して打ち直す |
| `parse_estimate` | 根拠が空 | `why` が空か無い（`given` のとき） | 列の `why` | `EstimateError` | `excluded` の理由「<列> の根拠（why）が空」 | 同上 |
| `parse_estimate` | 区分が無い | `harm_guide` が空でなく、`impact` がそのキーに無い | `check_band` | `EstimateError` | `excluded` の理由「UBV の区分 <値> が実害の目安に無い」 | 同上 |
| `check_band` | 帯の外 | UBV が帯の外 | 帯の比較 | 理由を返す | `excluded` の理由「UBV <値> が実害の目安 <区分> の帯 <下限>〜<上限> の外」 | 同上 |
| `config_from` | 設定の誤り | 書いた `harm_guide` の値が尺度に無い・下限 > 上限（書いていないときは誤りにしない） | 設定の読み込み | `ConfigError` | `stopped`・`invalid`、終了コード 2 | 設定を直して打ち直す |
| `parse_rows` | 表の行を読めない | 人が書き換えた行 | 行の正規表現 | その行を捨てる | 無し（ほかの出どころが無ければ `excluded` の「見積が無い」） | 再実行できる |
| `cmd_rank` | `--since-ref` の時刻を求められない | 無いタグ・SHA | `collect.ref_time` | 止める | `stopped`・`invalid`、終了コード 2 | 直して打ち直す |
| `cmd_rank` | `excluded` が 1 件以上 | 採点の欠け | `excluded` | 止める | `stopped`・`incomplete`、終了コード 6。`gaps` に番号と理由 | 段階を付けて打ち直す（記録は書く） |
| `_checked_rank` | 不完全な順位 | `rank` が `incomplete` で返した後に plan を作った | 書く前 | 止める | `stopped`・`precondition`、終了コード 3。「外した課題に段階を付けて rank を打ち直してから plan を作る」 | 書き込みは 0 件。採点して `rank` から打ち直す |
| `_check_repo` | 記録が無い・リポジトリが違う | 別のリポジトリへ打った | 書く前 | 止める | `stopped`・`precondition`、終了コード 3 | 書き込みは 0 件。正しい `--repo` で打ち直す |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_collect` | 内側の単純コマンド | `background`・`loop_body`・`loop_condition` を、それぞれ内側の値と外側の `bash -c` 系・`eval` の単純コマンドの値の論理和にする |
| `sleep.check` | 背景（`run_in_background` か、`waits` の要素の `background`。外側の `&` を引き継いだものを含む） | 上限・ループの判定をしない。NDF のコマンドの後続は今のまま止める |
| `sleep.check` | 前景・`loop_body` | 秒によらず止める |
| `sleep.check` | 前景・ループの外か `for` の中・秒が上限以下 | 通す |
| `sleep.check` | 前景・ループの外か `for` の中・秒が上限を超える（読めない秒を含む） | 止める |
| `process_wait.check` | `body_argv[0]` が `pgrep` / `pkill`・`_full_match`・`loop_body` か `loop_condition` | 前景でも背景でも止める |
| `process_wait.check` | 上のどれかが欠ける | 通す |
| `_full_match` | 語が `--` | 読むのを終える（後ろはすべて検索パターン。`pgrep -- -f` の `-f` はパターン） |
| `_full_match` | 語が `--full` | 真 |
| `_full_match` | 長い形で `_PGREP_VALUE_LONG` にある | `--name=値` は 1 語、`--name` は次の 1 語も飛ばす |
| `_full_match` | 短い形の束（`-` と 1 文字以上） | 先頭から 1 文字ずつ読む。`f` なら真。`_PGREP_VALUE_SHORT` の文字なら、その後ろが値（`-df` の `f` は区切りの文字）で、後ろが空なら次の 1 語を値として飛ばし、束を読み終える |
| `_full_match` | `pkill` で `-` の後が数字か信号の名前（`-9`・`-TERM`・`-SIGHUP`） | 信号の指定として飛ばす |
| `_full_match` | `-` で始まらない語 | 検索パターンとして飛ばし、読み続ける（getopt は語を並べ替えるため、`pgrep x -f` の `-f` もオプション） |
| `_strip_wrappers` | 先頭が `_WRAPPER_ARGS` の語 | その語を外し、下の「ラッパーの引数の読み方」でオプションと前置きの語を外し、残りの先頭を見直す |
| `_strip_wrappers` | オプションが `--` | `--` を外し、オプションの読み取りを終える |
| `_strip_wrappers` | 値を取るオプション（`_WRAPPER_ARGS` の `value`） | 長い形は `--name=値` なら 1 語、`--name` なら次の 1 語も外す。短い形は束の中でその文字より後ろが値で、後ろが空なら次の 1 語が値 |
| `_strip_wrappers` | 値を取らないオプション（`_WRAPPER_ARGS` の `flag`） | その語だけを外す。短い形の束（`-pv`）は文字ごとに読む |
| `_strip_wrappers` | 表に無いオプション | 本体の語を空にする（判定しない。I4 と同じく、読めない入力で止めない） |
| `_strip_wrappers` | コマンドを起こさない形（`command -v` / `-V`、`ionice -p` / `-P` / `-u`） | 本体の語を空にする |
| `_strip_wrappers` | 前置きの語（`timeout` の時間 1 語、`env` の `NAME=値` の語の並び） | オプションの後で外す。`env -S <文字列>` は文字列を `shlex.split` で分けて残りの語の前に置く |
| `_pick`（`estimates`） | `--scores` にある | 形と根拠と帯を確かめて採る（見直しは要約値も照らす。今のまま） |
| `_pick` | 記録 `estimates.json` にある | 採る（根拠の無い旧い行は `restored`） |
| `_pick` | `rank.json` の前回の行にある | 深さによらず採る（`restored`。番号を `metrics.restored` に載せる） |
| `_pick` | 説明の表の行にある | 深さによらず採る（`restored`。番号を `metrics.restored` に載せる） |
| `_pick` | どこにも無い・差分の棚卸し・`since` があり作成の時刻が `since` 以前 | `left_for_review` |
| `_pick` | どこにも無い・それ以外 | `excluded`（「見積が無い」） |
| `_checked_rank` | plan の `rank` があり、`rank.json` の `metrics.excluded` が 1 件以上 | `precondition` で止める（書き込みは 0 件） |
| `_check_repo` | 記録が無い | 止める |
| `_check_repo` | `metrics.repo` が `--repo`（省けば origin）と違う、または plan の `repo` があってそれと違う | 止める |

ラッパーの引数の読み方（`_WRAPPER_ARGS`。オプションは手元の uutils coreutils 0.10.0（`timeout`・`env`・`nice`・`stdbuf`・`nohup`）・util-linux 2.41.3（`ionice`）・GNU findutils 4.10.0（`xargs`）の `--help` と、bash の `help exec`・`help command` を 2026-11-19 に読んで決めた。GNU coreutils と GNU time は手元に無く読んでいない。`time` はシェルの予約語の `-p` に GNU time の文書の形を足したもので、実装のテストで確かめる）:

| ラッパー | 値を取らないオプション（`flag`） | 値を取るオプション（`value`） | オプションの後の前置きの語 | 例: 本体の語 |
| --- | --- | --- | --- | --- |
| `timeout` | `-f` `--foreground`・`-p` `--preserve-status`・`-v` `--verbose` | `-s` `--signal`・`-k` `--kill-after` | 時間 1 語 | `timeout --signal TERM 590 sleep 600` → `sleep 600`。`timeout -k 5 -sKILL 590 sleep 600` → `sleep 600` |
| `env` | `-i` `--ignore-environment`・`-0` `--null`・`-v` `--debug`・`--list-signal-handling` | `-u` `--unset`・`-C` `--chdir`・`-f` `--file`・`-a` `--argv0`・`-S` `--split-string`。`--ignore-signal` `--default-signal` `--block-signal` は `=値` の付いた 1 語だけ | `NAME=値` の語の並び（`-` も環境を空にする語として外す） | `env -u HOME X=1 sleep 600` → `sleep 600`。`env -S 'sleep 600'` → `sleep 600` |
| `nice` | 数字の短い形（`-10`。古い書き方） | `-n` `--adjustment` | 無し | `nice -n 10 sleep 600` → `sleep 600` |
| `ionice` | `-t` `--ignore` | `-c` `--class`・`-n` `--classdata` | 無し | `ionice -c 3 sleep 600` → `sleep 600` |
| `stdbuf` | 無し | `-i` `--input`・`-o` `--output`・`-e` `--error` | 無し | `stdbuf -oL sleep 600` → `sleep 600` |
| `nohup` | 無し | 無し | 無し | `nohup sleep 600` → `sleep 600` |
| `time` | `-p`・`-a` `--append`・`-v` `--verbose`・`-q` `--quiet` | `-f` `--format`・`-o` `--output` | 無し | `time -f %e sleep 600` → `sleep 600` |
| `exec` | `-c`・`-l` | `-a` | 無し | `exec -a w sleep 600` → `sleep 600` |
| `command` | `-p` | 無し（`-v`・`-V` はコマンドを起こさない） | 無し | `command -p sleep 600` → `sleep 600`。`command -v sleep` → 空 |
| `xargs` | `-0` `--null`・`-o` `--open-tty`・`-p` `--interactive`・`-r` `--no-run-if-empty`・`-t` `--verbose`・`-x` `--exit`・`--show-limits`。値を付けた 1 語だけを取る `-e` `--eof`・`-i` `--replace`・`-l` | `-a` `--arg-file`・`-d` `--delimiter`・`-E`・`-I`・`-L` `--max-lines`・`-n` `--max-args`・`-P` `--max-procs`・`-s` `--max-chars`・`--process-slot-var` | 無し | `xargs -n 1 sleep` → `sleep` |

- 繰り返しの終わる条件: `_collect` の読み直しは深さ 5 まで。`_strip_wrappers` は語の数だけ（1 語ごとに少なくとも 1 語を外す）
- 計算量の注意: `_loop_position` は単純コマンドごとに祖先をたどる。コマンド行の長さに比例し、1 回の事象で 1 度だけ作る今の形を保つ

## 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `_LOOP_NODES` | 定数 | `hooks/context.py` | `{"while_statement", "for_statement", "c_style_for_statement"}`（存在しない `until_statement` を外す） |
| `_WAIT_LOOPS` | 定数 | `hooks/context.py` | `{"while_statement"}`（`until` を含む） |
| `_INNER_DEPTH` | 定数 | `hooks/context.py` | `5` |
| `_SHELLS` | 定数 | `hooks/context.py` | `{"bash", "sh", "zsh", "dash"}` |
| `_PGREP_VALUE_SHORT` | 定数 | `hooks/guards/process_wait.py` | `"dgGOPstuUFrq"`（値を取る短い形。`q` は `pkill` だけ。procps-ng 4.0.4 の `pgrep --help`・`pkill --help` を 2026-11-19 に読んだ） |
| `_PGREP_VALUE_LONG` | 定数 | `hooks/guards/process_wait.py` | `--delimiter`・`--pgroup`・`--group`・`--older`・`--parent`・`--session`・`--signal`・`--terminal`・`--euid`・`--uid`・`--pidfile`・`--runstates`・`--cgroup`・`--ns`・`--nslist`・`--queue` |
| `_WRAPPER_ARGS` | 定数 | `hooks/context.py` | ラッパー → （`flag` の集合・`value` の集合・前置きの語の読み方）。値は「処理の手順」の「ラッパーの引数の読み方」の表。キーの集合は `weight.WRAPPERS` と同じで、テストで縛る |
| `ShellCommand.loop_body` / `loop_condition` / `body_argv` | 型 | `hooks/context.py` | `False` / `False` / `argv` から求めた値 |
| `process-wait` | 設定値 | `contracts/hooks/guards.json` | `order` 31（`sleep` の 30 の直後）・`events` PreToolUse・`tools` Bash・`deny_key` command・勧める Tool は `Monitor`（完了マーカーのファイルを待つ例） |
| `HARM_GUIDE` | 定数 | `backlog/model.py` | `{"全体": (13, 20), "機能": (5, 8), "表示": (2, 3), "なし": (1, 1)}` |
| `MITIGATIONS` | 定数 | `backlog/model.py` | `("防いでいる", "部分的", "無い")`。既定 `無い` |
| `Estimate.why` / `impact` / `mitigation` / `restored` | 型 | `backlog/model.py` | `{}` / `None` / `"無い"` / `False` |
| `LEFT_FOR_REVIEW` | 定数 | `backlog/estimates_decide.py` | metrics のキー名 `left_for_review` |
| `RESTORED` | 定数 | `backlog/estimates_decide.py` | metrics のキー名 `restored` |
| `harm_guide` | 設定値 | `.ndf/backlog.json`（スキーマ `backlog.schema.json`） | 無し（`HARM_GUIDE`。端が `scale` に無ければ空で、帯を確かめない） |

記録 `estimates.json` の 1 行の形（`Estimate.to_json`）:

```json
{"number": 3842, "digest": "<要約値>",
 "ubv": {"step": 5, "why": "<根拠>", "impact": "機能"}, "tc": {"step": 3, "why": "<根拠>"},
 "rr_oe": {"step": 1, "why": "<根拠>"}, "size": {"step": 3, "why": "<根拠>"},
 "mitigation": "無い", "waiting_decision": false, "observed_harm": false}
```

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| hook の判定 | 無し（入力だけを読む） | 要らない | 冪等 |
| `ndf progress check` と `finish` | ステートマシンの DB | `check` は読むだけ（`store.transaction` の読み取り）。書く操作の排他は今のまま | 冪等 |
| `ndf issues rank` と `apply` | 記録の保存先 | 今のまま（同じ保存先で 1 本ずつ打つ手順書の前提） | `rank` は打ち直せる。`apply` の照合は書く前なので、止まっても書き込みは 0 件 |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `sleep.check`・`process_wait.check` | 拒否の理由と代わりの待ち方 | hook の拒否の応答（今の振り分けの記録） | コマンド行の全文（`deny_key` の今の扱いのまま） |
| `_cmd_check` | 判定・定義のキー・捨てるキー | 結果 JSON（`metrics`・`warnings`） | 出力の値の中身 |
| `cmd_rank` | `excluded` の理由・`left_for_review` | 結果 JSON・`rank.json` | 無し |
| `_check_repo` | 食い違ったリポジトリの名前 | 結果 JSON の `summary` | 無し |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_loop_position` | `_in_background`（`context.py:93-100`） | 調査で読んだ | 使えない（`&` を探す別の判定）。祖先をたどる形を倣う |
| `_strip_wrappers` | `weight.WRAPPERS`（`testing/weight.py:18`）・`strict._after_wrapper`（`strict.py:318`） | `grep -n WRAPPERS` | 集合は使う。`_after_wrapper` は `gh`・シェル・`eval` だけを探す関数で、ラッパーのコマンドの引数を飛ばさないため使えない |
| `_inner_scripts` | `strict._inner_scripts`・`_shell_script`（`strict.py:255-272`） | 調査で読んだ | 使えない（shlex の字句の列が入力）。取り出す規則（`-c` の次の語・`eval` の後ろの語）を倣う |
| `_cmd_check` | `definition.validate_stage_output`・`stages.keep_declared` | 調査で読んだ（`command.py:449`・`stages.py:266`） | 使う |
| `check_band` | 10 系の `_check_ubv_band`（`ndf--v10.27.77` の `upkeep_rank_config.py:270-279`） | `git show` | 規則を倣う（11 系へ写す） |
| `parse_rows` | `table_decide.render`・`_row_line`（`table_decide.py:61-99`） | 読んだ | 書く側の形を読む。正規表現は `_row_line` の出力から作る |
| `_check_repo` | 10 系の `repo_mismatch`（`upkeep.py:386-394`） | `git show` | 規則を倣う |
| `left_for_review` | `collect.ref_time`（`collect.py:53`） | 読んだ | 使う |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `parse_shell` | `while` / `until` の本体と条件の位置が付く。`commands`・`loops` が今と同じ値 | 入れ子のループ・`for` の中（位置が付かない）・`bash -c` の中のループ・`bash -c 'while :; do sleep 1; done' &` の内側の `sleep` が `background` を持つ・内側の単純コマンドが `commands` に入らない | 深さ 5 を超える入れ子で止まらず返る |
| `_strip_wrappers` | `timeout 590 sleep 600` → `sleep 600`・「ラッパーの引数の読み方」の表の例の各行 | `timeout --signal TERM 590 sleep 600`・`timeout -s TERM -k5 590 sleep 600`・`timeout -- 590 sleep 600`・`env X=1 nohup sleep 1`・`env -u HOME sleep 1`・`nice -n 10 sleep 1`・ラッパーのコマンドだけの行・`command -v sleep`（空） | 表に無いオプション（`timeout --bogus 590 sleep 600`）で空。`_WRAPPER_ARGS` のキーが `weight.WRAPPERS` と食い違うと落ちる |
| `sleep.check` | 受け入れ条件 1〜4 の各形 | 上限ちょうどの秒・`$X` の秒 | ループの本体の `sleep` が拒否される |
| `process_wait.check` | 受け入れ条件 5・6 の各形 | `-af`・`--full`・`-x`・ループの外 | `NDF_HOOK_OFF=process-wait` で `until ! pgrep -f x >/dev/null; do :; done` が hook の判定の全体で通る。再現手順 1 のコマンドは同じ設定で `sleep` の guard が止める |
| `_full_match` | `pgrep -f x`・`pgrep -af x`・`pgrep x -f`・`pgrep -u root -f x`・`pkill -9 -f x`・`pkill --full x` が真 | `pgrep -- -f`・`pgrep -d f x`・`pgrep -df x`・`pgrep --delimiter -f x`・`pgrep -x f` が偽 | — |
| `_cmd_check` | 規約に合う出力で `ok` と定義のキー。結果 JSON が `progress.result.schema.json` に通る | `outputs` を省いた出力・定義しない文脈のキーで `warnings` | 規約外のキー・欠けで `refused`。`refused` の結果 JSON も同じスキーマに通る。前後で `show` が同じ |
| `parse_estimate` | 手順書の例の形 | `size` の `step` が `"over"`・`impact` が無い | 整数の形・`why` が空・知らない区分 |
| `config_from` | `harm_guide` を書かず既定の尺度で既定の帯 | `harm_guide` を書かずカスタムの尺度で空 | 書いた `harm_guide` の端が尺度に無いと `ConfigError` |
| `check_band` | 帯の中 | 帯の端・`防いでいる` で 1 つ下の帯 | 帯の外 |
| `estimates` | 4 つの出どころの順・見直しでも戻した見積を採り `restored` に入れる | `since` ちょうどの作成の時刻（以前に数える） | どこにも無い課題が深さと時刻で `excluded` と `left_for_review` に分かれる |
| `parse_rows` | 11 系の表の行 | `（ラベル）`・`（被依存）`・`（見積もり）`・`（判断待ち）` の印、「以下 N 件は表に載せていない」の行 | 読めない行を捨てる・表が無い説明で空 |
| `_checked_rank` | `excluded` 0 件の `rank.json` で書く | `left_for_review` か `restored` だけを持つ `rank.json` で書く | `excluded` 1 件以上で書き込み 0 件の `precondition` |
| `cmd_rank` | `excluded` 0 件で `ok` | `left_for_review` だけがあるとき `ok` | `excluded` 1 件以上で `incomplete`・終了コード 6 |
| `_check_repo` | 同じリポジトリで書く | plan に `repo` が無い | 記録が無い・`--repo` が違う・plan の `repo` が違うで書き込み 0 件 |
