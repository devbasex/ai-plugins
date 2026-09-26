# #1142 の設計の追加: 汎用の処理を外部ライブラリへ置き換える

**NDF のスクリプトが自作している汎用の処理を、外部ライブラリの上へ置き換える。** 2026-09-26 に利用者が
「自作の方針は撤回する。汎用的で車輪の再発明になる処理を抽出し、外部ライブラリを使うように設計を調整する」と
決めた（不足 j）。依存の解決は決定 17（1 つの宣言と lock・uv の環境へ起動し直す）の上に置く。計画の実行と
キュー（不足 i）は決定 21 で DBOS Transact に決めた。

## 何を「汎用の処理」とするか

**このリポジトリの業務（NDF の工程・語・計画の形・Claude Code の仕様）に固有でなく、どのプロジェクトでも同じ
形で要る処理を汎用とする。** 置き換え先のライブラリがすぐ見つからなくても、汎用なら対象に挙げる。

置き換えないのは次の 3 つだけである。

| 置き換えないもの | 理由 | 例 |
| --- | --- | --- |
| 標準ライブラリの素直な使用 | 包みが薄く、ライブラリにしても解く問題が無い | `datetime.fromisoformat`・`json` の JSONL・`string.Template`・`statistics` |
| 業務に固有の判定 | 汎用のライブラリに相当する機能が無い | CLI の出力に引用された誤りの文言の判定・Claude CLI の引数の仕分け・範囲の前方一致（glob を入れない方針 #266） |
| 定番の外部ツールをすでに使っているもの | 置き換え済み | シェルの `jq`・`flock -w 5`・git の CLI |

## 何が見つかったか（2026-09-26・`mission/m1142b` の `71963a1c`〜`fb4a4262`）

4 本の worker が `plugins/ndf/scripts/`（`lib/` とパッケージ）・`plugins/ndf/skills/*/scripts/`・hook と根の
`scripts/`・他のプラグインを分けて読み、関数の行数を構文木で数えた。汎用の処理の自作は 69 項目で、同じ種類を
まとめると次の 20 種類になる。

| # | 種類 | 自作の主な場所 | 行数 | 置き換え先 | 毎回起動される経路 |
| --- | --- | --- | ---: | --- | --- |
| 1 | GitHub の呼び出し（決定 16・17） | `review_lib/github.py`・`refactor_lib/github.py`・`upkeep.py` の `Gh`・`merged-steps.py`・`release-steps.py` ほか 20 ファイル・`fetch-pr-comments.sh`・`rotate-pr.sh` | 約 1,100 | githubkit（決定済み） | なし |
| 2 | 計画の実行とキュー（不足 i） | `supervise_lib` の `engine`・`state`・`queue`・`steps`・`slow`、`lib/post_queue.py:Queue`、2 本の `drive.py` | 約 2,300 | 試行中 | なし |
| 3 | Markdown の構造の読み取り（囲み・見出し・節・表・地の文・リンクとアンカー） | `lib/gh_sections.py`・`mission-state.py`・`mission-close.py`・`spec-copy.py`・`pr-steps.py`・`release-steps.py`・`doc-lint.py`・`glossary.py`・`instructions_lib/versions.py`・`relay_lib/mark.py`・`fix-steps.py`・`refactor_lib/vocabulary.py`・`lib/wait_notice.py`・根の `check-markdown-links.py`・`check-doc-staleness.py` ほか。囲みを追う実装が 9 本あり、規則がばらばら | 約 800 | markdown-it-py・mdit-py-plugins | `wait_notice.py`（hook）・`mark.py`（ラッパー） |
| 4 | Markdown の表の組み立て | `run_metrics.py`・`metrics.py`・`transcript_agents.py`・`skill-stats.py`・`refactor_lib/commands/report.py`・`refactor_lib/plan.py`・`review_lib/commands/report.py`・`glossary.py`・`mission-state.py`・根の `token-usage.py` ほか | 約 490 | tabulate（`tablefmt="github"`）。セルの `\|` のエスケープは包みが持つ | なし |
| 5 | JSON と設定の形の検証・型の変換 | `step_result.py`・`slow_step.py`・`limits.py`・`proposals.py`・`merge_fix.py`・`refactor_lib/plan.py`・`instructions_lib/declaration.py`・`supervise_lib/decl.py`・`release-steps.py:parse_steps`・`validate-runtime-plugins.sh`・playwright-kit の `config.py`。`isinstance(` は 132 行・36 ファイル | 約 650 | pydantic（githubkit が引き込むため依存は増えない） | なし |
| 6 | プロセスの生死・親子・木の停止・メモリ | `lib/monitor_proc.py`・`refactor_lib/process.py`・`relay_lib/proc.py`・`version_dir._alive`・`parallel-measure.read_meminfo` | 約 290 | psutil | `relay_lib/proc.py`（hook とラッパー） |
| 7 | ファイルロック（Python） | `lib/monitor_outcome.py`・`relay_lib/common.py`・`record.StartLimit`・`wait-notify.py:claim` | 約 75 | filelock | relay・wait-notify（hook） |
| 8 | シェルの字句・構文の解析 | `lib/worktree-shell-lex.sh`・`worktree-write-target*.sh`（4 本）・`token-guard.sh:split_commands`・`lib/token_guard_sleep.py`・`workflow-common.sh:wf_split`・根の `check-skill-shell-vars.py`。ヒアドキュメントの除去の自作が 3 本 | 約 1,960 | tree-sitter-bash（py-tree-sitter）。bashlex は `[[ -f y ]]` で ParsingError を出すので使えない | すべて（PreToolUse） |
| 9 | 版数の比較と一括の書き換え | `instructions_lib/versions.py`・`relay_lib/version_dir.version_key`・根の `token-usage.py:version_key`・`check-doc-staleness.py:base_of`・`release-steps.py` の `Editor`・`cmd_bump` ほか | 約 220 | semver（比較）・bump-my-version（15 箇所の書き換え） | `version_dir`（ラッパー） |
| 10 | パスのパターン照合 | `lib/pace.py:glob_re`・`glossary.declared_path_matches`・`collect._matches_file_pattern` | 約 30 | pathspec | なし |
| 11 | unified diff の解釈 | `glossary.added_lines`（`doc-lint` からも呼ぶ） | 30 | unidiff | なし |
| 12 | コードのコメントの除去 | `glossary.mask_comments` | 47 | pygments（`Token.Comment`） | なし |
| 13 | frontmatter と YAML の読み書き | `skill-stats.py:parse_front_matter`・根の `check-skill-frontmatter.py`・`build-runtime-plugins.sh` の埋め込み・`dev.kiro/install.sh`・mcp-serena の `project_yml.py` | 約 220 | ruamel.yaml（引用符とコメントを保つ往復の読み書き） | なし |
| 14 | 待ちとやり直し | `merged-steps.py:cmd_merge_when_green`・`watch_stuck_checks`・`lib/post_queue.py:retry`・`upkeep.py` の待ち・`engine.gh_limit_wait` | 約 290 | tenacity（上限の待ちは githubkit） | なし |
| 15 | HTTP と Slack の呼び出し | `lib/refresh.py`・`jev._post`・`wait-notify.py:slack_api` | 約 100 | httpx（githubkit が引き込む）・slack_sdk | `wait-notify.py`（hook） |
| 16 | `.env` の読み取り | `wait-notify.py` の `_parse_env`・`_find_env`・`load_env_file` | 30 | python-dotenv | `wait-notify.py`（hook） |
| 17 | `claude -p` の起動と結果の読み取り | `supervise_lib/claude.py` の `claude_cmd`・`call_claude`・`mvv-gate.py:ask`・`steps.last_json` | 約 100 | claude-agent-sdk（CLI をサブプロセスで起こす。サーバーは立てない） | なし |
| 18 | 擬似端末の中継 | `relay_lib/terminal.py:Terminal` | 213 | ptyprocess | ラッパー |
| 19 | テストの結果の読み取り・ファイル種別の判定 | `refactor_lib/testcmd.py:failed_nodes`・`pathkinds.py`・`review_lib/categories.py` の拡張子と shebang の判定 | 約 100 | pytest の `--junitxml`（読むのは標準の xml）・identify | なし |
| 20 | 同じ処理の重複 | remote の URL から owner/repo を取る処理が 5 か所・原子的な書き込みが 4 か所（`lib/jsonio.write_atomic` と重複）・中央値と分位点（`statistics` で足りる） | 約 80 | 1 か所へまとめる（URL は giturlparse の上で `lib/repo.py` へ。書き込みは `lib/jsonio`。統計は標準ライブラリ） | 一部 |

候補の見つからないものは 4 つある。シェルの設定ファイルの管理ブロック（`relay_lib/shellrc.py`・37 行）・
ラッパーのバージョンディレクトリの複製と世代の管理（`version_dir.VersionDir`・137 行。`uv tool install` が近い）・
テストのコマンドの書き換え（`testcmd.py`・105 行）・パスの字面での正規化（`worktree-common.sh`・43 行）。
この 4 つは置き換えず、候補が見つかった時点で起票する。

見つかった不具合: 根の `scripts/token-usage.py:version_key` は `dev.10` を `dev.9` より前に並べる（接尾辞を
文字列で比べる）。即時修正で直す。

## 決定 19: 汎用の処理は、ライブラリを包む 1 か所だけが呼ぶ

**ライブラリは `lib/` の包みのモジュールが 1 か所で呼び、エントリポイントとパッケージは包みを呼ぶ。** 包みは、
ライブラリの振る舞いとこのリポジトリの契約の差（誤りの文言を日本語で返す・`\|` のエスケープ・右寄せの列・
`-dev.<n>` の版の形）を 1 か所で吸収する。包みを置かずに呼ぶと、同じ差を呼び出し側がそれぞれ埋め直し、
今の重複（囲みを追う実装が 9 本）と同じ形が戻る。

| 包み（新設） | 呼ぶライブラリ | 受け持つ種類 |
| --- | --- | --- |
| `lib/md.py` | markdown-it-py・mdit-py-plugins | 3（読み取り。書き込みは節の外を変えないため、読み取った位置 `token.map` を使った行の操作で残す） |
| `lib/mdtable.py` | tabulate | 4 |
| `lib/schema.py` | pydantic | 5（`ValidationError` を日本語の 1 行へ直す。語彙に無い値を最低の重要度へ下げるなどの寛容な読みは validator で書く） |
| `lib/procs.py` | psutil | 6（cgroup の読み取りは psutil に無いので残す） |
| `lib/locks.py` | filelock | 7（シェルの `lock-common.sh` は、呼ぶ側が決定 20 で Python になるため消える） |
| `lib/shparse.py` | tree-sitter-bash | 8（hook の 1 本のエントリポイントが呼ぶ。決定 20） |
| `lib/versions.py` | semver（・bump-my-version） | 9（packaging は `-dev.1` を `.dev1` へ変えるため使わない。2026-09-26 に実測） |
| `lib/pathmatch.py` | pathspec | 10（今の `*` は `/` をまたぐ。またがない側へ揃え、`.ndf/pace.json` の宣言を読み替える） |
| `lib/textparse.py` | unidiff・pygments | 11・12 |
| `lib/yamlio.py` | ruamel.yaml | 13 |
| `lib/waits.py` | tenacity | 14 |
| `lib/notify.py` | slack_sdk・python-dotenv | 15・16 |

GitHub は `lib/gh_parts.py`（決定 16・17）、`claude -p` は `supervise_lib/claude.py`、擬似端末は
`relay_lib/terminal.py` が包みを兼ねる。

**依存は `plugins/ndf/pyproject.toml` の extra に種類ごとに分けて宣言する**（決定 17 の形。`deps.require("md")` の
ように名前で引く）。起動し直しの上乗せは 2 回目以降 0.12〜0.18 秒で（決定 17 の実測）、hook ではない
エントリポイントには十分小さい。根の `scripts/`（開発と CI の検査）は、リポジトリの根に `pyproject.toml` と
`uv.lock` を新設して同じ形で解決する。playwright-kit はすでに自分の `pyproject.toml` と `uv.lock` を持つため、
そこへ足す。mcp-serena の `project_yml.py` は、mcp-serena に `pyproject.toml` と `uv.lock` を新設して同じ形で解決する
（D8 に含める）。

**構造チェックに I14 を足す**: 包みが受け持つ標準ライブラリの部品（`fcntl`・`pty`・`termios`・
`urllib.request`・`/proc/` の読み取り・囲みの正規表現）を、包みの外のモジュールが使ったら落とす。自作が
戻るのを行数ではなく import で止める。

## 決定 20: hook とラッパーも依存を持ち、hook は 1 本の Python のエントリポイントへまとめる（I13 を改める）

**汎用の自作のうち約 2,300 行（種類 8 の全部と、3・6・7・15・16・18 の一部）が、I13（hook とラッパーの
バージョンディレクトリは外部パッケージを使わない）の経路にある。** 最も大きいのはシェルの字句解析（約 1,960 行・
自作が 3 本）で、PreToolUse の hook が Bash と Edit のたびに呼ぶ。2026-09-26 に利用者が「判断が要る箇所は、最終的に
一番綺麗になるやり方で」と決めたため、I13 を保って重複だけをまとめる案は採らない。

| 経路 | 今の起動 | 実測（2026-09-26） |
| --- | --- | --- |
| `worktree-guard.sh`（Bash・Edit ほか全件） | bash と jq だけ | 1 回 30〜32 ms |
| `python3 -c pass` | 素の python3 | 6〜7 ms |
| lock 済みの `uv run --frozen` | uv の環境 | 1 回目 235 ms・2 回目以降 13〜26 ms |
| venv の python で bashlex を import して解析 | 参考 | 66〜88 ms |

**hook は、PreToolUse・Stop・Notification を受ける 1 本の Python のエントリポイント（`scripts/hook.py`）へまとめる。**
今の `worktree-guard.sh`・`token-guard.sh`・`wait-notify.py`・`relay.py` の hook の入口は、その中の関数になる。
シェルの字句解析は tree-sitter-bash（bashlex は `[[ -f y ]]` で ParsingError を出すので使えない）、`.env` と Slack と
ロックはライブラリへ置き換え、bash と jq の fork が消える。

**hook は `uv run` を挟まず、用意済みの環境の python を直に起動する。** SessionStart の hook が
`deps.require()` と同じ手順で `~/.cache/ndf/venv/<版>` を `uv sync --frozen` で用意し、PreToolUse ほかの hook の
コマンドはその環境の `bin/python` を指す。毎回の上乗せは python の起動と import だけになる。環境がまだ無い
（SessionStart より前・uv を入れられない）ときは、hook は判定をせずにパススルーで終わる（hook が止まると Tool の
呼び出しが全部止まる。今の guard も拒否しない案内が主である）。

**ラッパーのバージョンディレクトリは、複製のときに同じ lock から環境を作る**（複製先で `uv sync --frozen`）。
`relay_lib/terminal.py` は ptyprocess、`proc`・`common`・`record` は psutil と filelock の上に置く。

**I13 は「hook とラッパーの経路は、決まった環境の python から起動し、`deps.require()` を呼ばない」へ改める。**
環境を用意するのは SessionStart とラッパーの複製の 2 か所だけで、ほかの hook は用意の有無だけを見る。

**試行 T2（`experimental/`）は、選び直すためではなく成り立ちと退行を確かめるために行う。** 今の hook のテストの
入力のすべてを tree-sitter-bash で解析して同じ判定になるか、hook 1 回の所要（今の 30〜32 ms と比べる）、環境が
無いときにパススルーで終わるかを測る。どれかが成り立たなければ、そこで利用者へ戻す。

## 決定 21: 計画の実行とキューは DBOS Transact（SQLite）の上に置く（不足 i）

2026-09-26 の試行（`experimental/runner-trial.py`・PR #1258）で、LangGraph（SqliteSaver）・Burr（SQLitePersister）・
DBOS Transact（SQLite）の 3 つとも、サーバーを立てず SQLite のファイルだけで、実装の計画の雛形（`on_fail`・judge の
選択・上限 20・承認ゲート）・キュー・kill -9 からの再開（流れていたステップから流し直し、済んだステップは流さない）・
今の進捗ログと `supervise.py wait` を保てた。

| 観点 | LangGraph | Burr | DBOS |
| --- | --- | --- | --- |
| キュー（同時の本数・資源のタグごとの枠） | 持たない（子プロセスと flock で約 60 行を自作） | 持たない（同じく約 60 行） | `register_queue` の `worker_concurrency` と、枠ごとのキューの `concurrency` |
| 承認ゲート | `interrupt()`。続けるときノードを頭から流し直す | `halt_before` | `DBOS.recv` の待ちのまま抜け、`DBOS.launch()` が記録から続ける |
| 6 本の所要 | 6.9 秒 | 6.0 秒 | 12.4 秒（キューの問い合わせの間隔 0.1 秒の分） |
| 大きさ・依存 | 54.9 MB・41 件 | 20.2 MB・2 件 | 60.6 MB・11 件（SQLite でも SQLAlchemy・psycopg-binary） |
| import（冷えて / 温まって） | 0.82 / 0.26 秒 | 0.06 / 0.04 秒 | 0.78 / 0.24 秒 |

**DBOS を選ぶ。** キューと資源のタグごとの枠（#889 の GraphQL の共有の枠）をライブラリが持ち、自作が残らないのは
DBOS だけである。Burr は軽いが、キューの自作が残る（決定 19 の基準に反する）。import の 0.24 秒と所要の差は、
1 本が数分〜数十分かかる計画の実行では小さい。計画の実行は hook の経路ではないため、決定 20 の所要の制約を受けない。

`supervise_lib` の `engine`・`state`・`queue`・`steps`・`slow` と `lib/post_queue.py:Queue`・2 本の `drive.py` の
ループを DBOS のワークフローとステップへ置き換え、計画の JSON（`steps` と `next`・`on_fail`）の形と、
`supervise.py` のエントリポイントの引数と出力・進捗ログの行の形は変えない。`--files` の重なり（#1248）と
共有の一覧の衝突（#1249）は、キューへ入れる前の検査として置き換えの中で入れる。移行はミッション 2c
（ミッション 2b の後）で、ステップの切り方は 2b の後に決める。

## 移行の順序（ミッション 2b。ミッション 2 の開発版の後、ミッション 3 の前）

**ミッション 3（語の移行）の前に置く。** 語の移行は同じファイルを触るため、先に中身を置き換えておけば、語を
直す行が 1 回で済む。計画の実行とキュー（不足 i）の置き換えは試行の結果で別のミッション（2c）にする。

| ステージ | 移行ステップ | 触るファイル |
| --- | --- | --- |
| 0 | T2 試行（決定 20 の成り立ちと hook の所要・claude-agent-sdk と bump-my-version が今の契約を満たすか） | `experimental/` だけ |
| 0 | 即時修正: `token-usage.py:version_key`、#1249（並列の計画が例外リストで衝突する） | 根の `scripts/token-usage.py`・構造チェックと実装の雛形 |
| 1 | L1 包み | `lib/` の包み 12 本（新設。各 300 行以下）・`plugins/ndf/pyproject.toml` と `uv.lock` の extra・根の `pyproject.toml` と `uv.lock`（新設）・構造チェックの I14・`lib/README.md`・テスト。**呼び出し側はまだ変えない** |
| 2 | D1 プランの実行 | C1 と同じファイル（`claude -p` の包みは T2 の結果で） |
| 2 | D2 cross-review | C2 と同じファイル |
| 2 | D3 外部 CLI と記録 | C3 と同じファイル（`wait-notify.py` の hook の入口は D5 が移す） |
| 2 | D4 cross-refactoring | C4 と同じファイル |
| 2 | D7 リリースと文書 | C7 と同じファイルと `glossary.py`・`doc-lint.py`・`spec-copy.py`・`pr-steps.py` |
| 2 | D8 根の scripts・playwright-kit・mcp-serena | 根の `scripts/`（`check-*.py`・`build-runtime-plugins.sh`・`validate-runtime-plugins.sh`・`token-usage*.py`）・playwright-kit の `config.py` |
| 3 | D5 hook（決定 20） | `scripts/worktree-*.sh`・`token-guard.sh`・`lib/worktree-*.sh`・`lib/token_guard_sleep.py`・`workflow-common.sh`・`wait-notify.py`・`hooks/*.json` |
| 3 | D6 ラッパー（決定 20） | `relay_lib/`（`proc`・`common`・`record`・`terminal`・`version_dir`・`mark`） |

**ステージ 2 は 6 本で、並列の上限に収まる。** 触るファイルは C1〜C7 の区切りに合わせてあり、重ならない。
ステージ 0 の #1249 を先に直すのは、ミッション 2 のステージ 2 で 7 本のうち 4 本が例外リストの衝突と範囲の
重なりで止まったためである。

**テストは差し替え先と import だけを変える**（決定 3 と同じ）。振る舞いが変わるもの（`*` が `/` をまたがなく
なる・frontmatter の値の形）は、その移行ステップの PR が変わる入力をテストで固定し、本文に書く。

## 受け入れ条件（#1142 へ足す）

- 上の 20 種類が包みの上に置き換わり、自作の関数が消えている（候補の見つからない 4 つを除く）
- 構造チェックの I14 が、包みの外で包みの受け持つ部品を使うモジュールを落とす
- 置き換えないものの一覧（この文書）に載っていない汎用の自作が、新しく足されていない
- hook 1 回の所要が、移行の前（`worktree-guard.sh` の 30〜32 ms）と比べて悪くなっていない（T2 と同じ測り方）
