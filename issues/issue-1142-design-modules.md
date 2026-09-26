# 1142: モジュールの分け方

[issue-1142-design.md](issue-1142-design.md) の続きである。移行の後のパッケージとモジュールの構成と、500 行を超える
ファイルを分ける先を、関数とクラスの単位で持つ。移行ステップ（[issue-1142-design-migration.md](issue-1142-design-migration.md) ）は
この表のとおりに移す。

**行数の区分は決定 18 のとおりである**（`markdown-writing` のルール 9 と同じ）。300 行までを目安にし、301〜500 行は
1 つの状態機械・1 つのクラスの本体・大半が表のファイルのように分けると理解が落ちるときだけ認め、501 行以上は置かない。
表の見積りの行数は、2026-09-26 の develop（b8a189e0）を構文木で測った各定義の行数（直前か直後の空行とコメントを含む）に、
import と docstring の分を足したものである。

**テストの差し替えを効かせたまま分けるため、呼ぶ側は移した先の関数をモジュールの属性として呼ぶ**（`github._gh_rest(...)` の形。
`from github import _gh_rest` で名前を取り込まない）。テストの差し替え先は定義したモジュールへ向け直す（決定 3）。
`raising=False` の差し替えは、名前が無くなっても落ちずに効かなくなるため、移すときに必ず向け直す。

## パッケージ・モジュール構成

新設は `+`、分けて中身を移すものは `>`、中身を薄くするエントリポイントは `=` で示す。

```text
plugins/ndf/scripts/
├── = supervise.py                # 使い方と main（約 175 行）
├── + supervise_lib/              # 19 本: __init__・paths・decl・plan・prompts・claude・state・slow・steps・worker_steps・pr・engine・templates・release_templates・mission_waves・mission・queue・commands・new_args
├── = relay.py                    # ランチャー（約 80 行）。プラグインでも複製でも同じバイト列
├── + relay_lib/                  # 11 本: __init__・common・proc・record・mark・claude・terminal・run・shellrc・version_dir・install
├── = instructions-check.py       # 引数の解析と検査の流れ（約 245 行）
├── + instructions_lib/           # 7 本: __init__・model・declaration・collect・findings・versions・report
└── lib/
    ├── = monitor.py              # エントリポイント（約 285 行）
    ├── + monitor_patterns.py  + monitor_scan.py  + monitor_proc.py  + monitor_types.py  + monitor_loop.py
    ├── + clock.py  + jsonio.py  + proc.py  + repo.py  + loop_drive.py  + usage_ledger.py  + deps.py
    ├── = gh_parts.py             # エントリポイントと再エクスポート（約 230 行）
    ├── + gh_call.py  + gh_quota.py  + gh_fields.py  + gh_rest.py  + gh_graphql.py  + gh_checks.py  + gh_pr_info.py  + gh_sections.py
    ├── = worktree-common.sh      # 約 290 行。下の 7 本を source する
    └── + worktree-declaration.sh  + worktree-branch.sh  + worktree-shell-lex.sh  + worktree-write-target.sh
        + worktree-write-target-scan.sh  + worktree-write-target-track.sh  + worktree-registry.sh
plugins/ndf/skills/cross-review/scripts/
├── = state.py                    # 副命令の引数の解析と main（約 245 行）
└── + review_lib/                 # 21 本: __init__・store・workspace・github・ci・categories・review_focus・participants・posts・findings・matching・fix_result・commands/（9 本）
plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/
├── = gitfacts.py                 # コミットの事実（約 255 行）。移した名前を再エクスポートする
└── + pathkinds.py  + process.py  + github.py  + worktree.py  + publish.py  + results.py
scripts/                          # リポジトリの根（開発用）
├── + check-script-structure.py  + script-structure-allow/
└── + measure/structure-baseline.py  + measure/claude-p-usage.py
```

501 行以上のファイルは、移行の後に 0 本になる（移行の範囲の外のものは例外リストに残る。決定 18）。301〜500 行に入るのは、
`relay_lib/run.py`（約 350）・`review_lib/commands/init.py`（約 435）・`review_lib/workspace.py`（約 375）・`review_lib/github.py`（約 365）・
`worktree-shell-lex.sh`（約 310）・`worktree-write-target.sh`（約 415）・`worktree-write-target-scan.sh`（約 475）・
`worktree-write-target-track.sh`（約 440）の 8 本で、理由は各節にある。

## 分け方

### supervise_lib
`supervise.py`（3401 行）の最上位の定義を、次の 19 本と `supervise.py` 本体へ分ける。予定の 15 本に対して `worker_steps`・`release_templates`・`mission_waves`・`new_args` の 4 本を足す。見積りの行数は、各定義の実測の行数（次の定義までの空行とコメントを含む）に、import と docstring の分を足したものである。

- **`worker_steps.py`:** 5 つのハンドラーのうち `PrStep` を除く 4 つを `steps.py` へ入れると約 310 行になる。`WorkStep` と `DriveStep` はどちらも `call_worker` で worker を起動する。このため、この 2 つを 1 本にまとめて分ける
- **`release_templates.py`:** `templates.py` にリリースのプランまで入れると約 405 行になる。リリースのプランは形（`release.form`）ごとの表 `RELEASE_FORMS` を持ち、`plan_release_package_plugin` だけで 109 行ある
- **`mission_waves.py`:** `mission.py` にミッションのすべてを入れると約 405 行になる。さらに、`plan_fast_design` が `plan_mission_design` を呼び、`mission_plans` と `close_waves` が `plan_fast_*` を呼ぶため、`pace: fast` の部分だけを別のモジュールへ出すと循環 import が起きる。そこで、ステージごとのプランを作る関数を下のモジュールへ、組み立てと拒否の判定を上のモジュールへ分ける
- **`new_args.py`:** `new` の引数の表と検査を `supervise.py` に残すと約 345 行になる。種別ごとの引数の表（`NEW_ARGS` 66 行・`NEW_KINDS` 26 行）と、`main` の中で `new` だけを検査する 40 行は、ほかの副命令と共有しない

| モジュール | 持つもの | 見積りの行数 |
| --- | --- | ---: |
| `supervise.py` | 使い方の docstring（今の 1〜60 行）・`MAIN_EPILOG`・`main`（`new` の検査を `new_args.check_new` の呼び出しへ置き換えたもの）。`sv.Supervisor` などを参照しているテストのための再エクスポートは置かない | 約 175（実測: docstring の使い方 60・`main` 128 から `new` の検査 40 を除いた分・`MAIN_EPILOG` 9） |
| `supervise_lib/__init__.py` | パッケージの説明だけ | 約 5 |
| `paths.py` | `SELF`（`supervise.py` のパス）・`HERE`・`SKILLS`・`DRIVES`・`EXTERNAL_AI`・`PRESETS`・`WORKTREE_SETUP`・`CHECK_PY`・`MVV_PY`・`GLOSSARY_PY`・`SPEC_COPY_PY`・`PUSH_DESIGN`・`STEPS_PY`・`VERIFY_PY`・`MERGED_PY`・`MERGE_CMD`・`MERGE_PROBE`・`with_paths`・`state_dir_of`・`report_result`・`queue_done_path`・`queue_plans_path`・`wait_cursor_path`・`ensure_worktree`・`is_config_lock`・`WORKTREE_LOCK_*` | 約 125（実測 112） |
| `decl.py` | `DeclError`・`WORKTREE_DECL`・`SUPERVISE_DECL`・`read_decl`・`decl_roots`・`declared_base`・`sync_checks_of`・`NEEDS`・`apply_decls`・`decl_fields`・`with_decls` | 約 115（実測 103） |
| `plan.py` | プランの形と実行の決まりの docstring（今の docstring の 61〜202 行）・`PLAN_KEY_ALIASES`・`normalize_plan`・`expand_parts`・`pr_number`・`EXAMPLE` | 約 220（実測: 定義 69・docstring 143） |
| `prompts.py` | `WORK_SYSTEM`・`FULL_SYSTEM`・`PR_SYSTEM`・`JUDGE_SYSTEM`・`SLOW_SYSTEM`・`PROGRESS_PROMPT`・`WORKDIR_PROMPT`・`RESUME_PROMPT`・`REPORT_DONE` | 約 75（実測 69） |
| `claude.py` | `ClaudeRunner`（`call`＝今の `Supervisor.claude`・`note_limit`、帳簿への追記）・`ClaudeCall`・`UsageLimit`・`kill_group`・`run_ticking`・`claude_cmd`・`call_claude`・`is_usage_limit`・`limit_reset_at`・`fallback_env`・`WORK_TOOLS`・`FULL_TOOLS`・`SERENA_MCP`・`TAIL`・`LIMIT_*` | 約 290（実測 243） |
| `state.py` | `RunState`（詳細は `engine.py` の分け方の表） | 約 250（実測 191） |
| `slow.py` | `SlowAction`・`SlowWatch`（詳細は同じ表） | 約 240（実測 222） |
| `steps.py` | `StepHandler`・`StepContext`・`RunStep`・`JudgeStep`・`is_gate`・`parse_decision`・`last_json`（詳細は同じ表） | 約 175（実測 130） |
| `worker_steps.py`（新設） | `WorkStep`・`DriveStep`（詳細は同じ表） | 約 150（実測 127） |
| `pr.py` | `PrStep`・`user_changes`・`CHANGES_HEADING`・`PR_FOOTER`（詳細は同じ表） | 約 145（実測 125） |
| `engine.py` | `Engine`（詳細は同じ表） | 約 260（実測 232） |
| `templates.py` | `RULE_IMPL`・`RULE_CHECK`・`RULE_CHECK_SINCE`・`FIX_PROMPT`・`IMPL_RULES`・`plan_done`・`other_files`・`impl_prompt`・`plan_impl`・`plan_check`・`plan_check_since`・`cmd_new` | 約 270（実測 250） |
| `release_templates.py`（新設） | `RULE_RELEASE_DEV`・`RULE_RELEASE_PROD`・`RULE_RELEASE_PROD_MVV`・`QUEUE_PRS`・`plan_release`・`plan_release_package_plugin`・`RELEASE_FORMS` | 約 150（実測 135） |
| `mission_waves.py`（新設） | `RULE_DESIGN`・`DESIGN_GLOSSARY_NOTE`・`mission_branch`・`plan_mission_design`・`plan_mission_branch`・`plan_mission_impl`・`plan_mission_check`・`plan_mission_release`・`plan_fast_design`・`plan_fast_impl`・`plan_fast_check`・`plan_fast_release` | 約 195（実測 178） |
| `mission.py` | `mission_plans`・`fast_mission_plans`・`prod_version`・`close_plan`・`close_waves`・`fast_refusal`・`mvv_refusal`・`MANUAL_RELEASE`・`has_release_template`・`manual_release_wave`・`cmd_new_mission` | 約 230（実測 209） |
| `queue.py` | `run_batch`・`cmd_queue`・`NOT_RUN`・`queue_prs`・`fill_queue_prs`・`QUEUE_PR`・`plan_pr`・`named`・`fill_queue_pr`・`write_atomic`・`progress_size`・`notify_attention`・`attention_lines`・`cmd_wait`・`WAIT_DONE`・`WAIT_ATTENTION`・`WAIT_TIMEOUT` | 約 295（実測 275） |
| `commands.py` | `cmd_run`（今の `main` の末尾の 4 行: プランの読み込み・状態ディレクトリ・`Engine` の実行）・`sync_check`・`cmd_design_glossary`・`note_row`・`cmd_note`・`cmd_history_import`・`cmd_expected` | 約 210（実測 182） |
| `new_args.py`（新設） | `DECL_HELP`・`DECL_WORDS`・`NEW_KINDS`・`NEW_ARGS`・`NEW_REQUIRED`・`add_new_parsers`・`fill_new_defaults`・`check_new`（今の `main` の中の `new` の検査） | 約 180（実測: 定義 128・検査 40） |

`now_iso` は L0 の `lib/clock.py` へ移す（`state`・`slow`・`queue` が使う）。300 行に最も近いのは `queue.py`（約 295）と `claude.py`（約 290）である。

**import の向き:** 下から順に、`decl`・`plan`・`prompts`・`claude` は `lib/` だけを import する。`paths`（`decl`・`plan`）→ `state` → `slow`・`steps` → `worker_steps`・`pr` → `engine` の順に、上のモジュールは下のモジュールだけを import する。プランを作る側は `templates` → `release_templates` と、`mission` → `mission_waves` → `templates`・`release_templates` の向きだけを持ち、`engine` を import しない。`queue` は `paths` と `plan` だけを import し、プランは `SELF` の `run` として別プロセスで流す。`engine` を import するのは `commands` だけで、`supervise.py` は `commands`・`templates`・`mission`・`queue`・`new_args`・`decl`・`plan` を import する。これで循環は起きない。

**テストの差し替え先:** `monkeypatch.setattr` は 10 件ある。このうち `sv.run_batch` の 3 件は `supervise_lib.queue` に、`sv.ensure_worktree` の 1 件は `cmd_queue` が名前を引く `supervise_lib.paths` に向く。`sv.subprocess` の 3 件（`cmd_design_glossary` の 2 件・`ensure_worktree` の 1 件）と `sv.gh_parts` の 3 件（`Engine.gh_limit_wait`）は、`supervise.py` がそのモジュールを import しなくなるため、`subprocess.run` と `lib/gh_call.py` の `gh_call.RUNNER` を差し替える形に変わる（`RUNNER` は再エクスポートしない。lib/gh_parts.py の節）。

#### Engine と周りのクラス

`Supervisor`（1049 行・50 メソッド）のメソッドを次の 7 本へ分け、文面の定数を `prompts.py` へ置く。`__init__`（48 行）は `Engine` と `RunState` で分け、下の表の「メソッドの実測」には含めない。実測の行数は、次のメソッドまでの空行とコメントを含む。`state.py`・`worker_steps.py`・`pr.py` の 3 本は新設する。`supervise_lib` 全体の分け方は `supervise_lib の分け方` にある。

- **`state.py` を足す理由:** `RunState` を `engine.py` に置くと約 490 行になる。`Engine` と `RunState` の 2 つのクラスが入ったファイルは「1 つの状態機械やクラスの本体」に当たらず、301〜500 行を許す条件を満たさない。設計の集約の表で `RunState` の置き場所を書いている行（`supervise_lib.engine.RunState`）は `supervise_lib.state.RunState` に改める
- **`pr.py` と `worker_steps.py` を足す理由:** 5 つのハンドラーを `steps.py` へすべて入れると約 440 行になる。そこで次の 2 つを分ける
  - `PrStep`（113 行）: 最も大きく、push と GitHub への書き込みを行う唯一のハンドラー
  - `WorkStep` と `DriveStep`: どちらも `call_worker` で worker を起動する

| モジュール | 持つもの（クラス・メソッド・関数の名前） | 見積りの行数 |
| --- | --- | ---: |
| `engine.py` | `Engine`: `__init__`（プラン・ステップ・順序・作業場所の部分）・`run`・`next_of`・`keep_cwd`・`check_condition`・`take_gate`・`copy_presentation`・`gh_limit_wait`・`tick`・`ensure_worktree`（`paths.ensure_worktree` の呼び出し）。`run` はステップの型からハンドラーを引く辞書を持ち、記録は `RunState.record` へ渡す | 約 260（メソッドの実測 232） |
| `state.py`（新設） | `RunState`: `__init__`（results・log・llm・gates・fail_counts・progress・pcount・worker_* の部分）・`record`（`run` から出す state.json・出力ファイル・失敗の回数）・`add_usage`・`progress_write`・`attention`・`classify_worker`・`read_worker_lines`・`run_last_output`・`step_line`・`out_path`・`record_stage`・`write_report`（今の `report`）。関数の `counts_text`、定数の `PROGRESS_STOP`・`PROGRESS_GATE`・`PROGRESS_FAIL`・`REPORT_INTERVAL`・`TICK` | 約 250（実測 191） |
| `slow.py` | `SlowAction`・`SlowWatch`。`SlowWatch` のメソッドは `resolve_slow`・`start_watch`・`end_watch`・`slow_elapsed`・`slow_write`・`check_slow`・`handle_slow`・`probe_values`・`slow_probe`・`judge_slow`。定数の `SLOW_EXIT` | 約 240（実測 222） |
| `claude.py` | `ClaudeRunner`（`call`＝今の `claude`・`note_limit`、帳簿への追記）・`ClaudeCall`・`UsageLimit`。関数の `kill_group`・`run_ticking`・`claude_cmd`・`call_claude`・`is_usage_limit`・`limit_reset_at`・`fallback_env`。定数の `WORK_TOOLS`・`FULL_TOOLS`・`SERENA_MCP`・`TAIL`・`LIMIT_*` | 約 290（実測 243） |
| `steps.py` | `StepHandler`（Protocol）・`StepContext`（`fill_pr`・`base_branch`・`inputs_text`）・`RunStep`（`is_skip`・`no_reports`・`run_cmd`・`do_run`）・`JudgeStep`（`do_judge`）。関数の `last_json`・`parse_decision`・`is_gate` | 約 175（実測 130） |
| `worker_steps.py`（新設） | `WorkStep`（`issue_text`・`call_worker`・`do_work`）・`DriveStep`（`drive_cmd`・`drive_loop`・`do_drive`）。`drive_loop` は `RunStep.run_cmd` を使う | 約 150（実測 127） |
| `pr.py`（新設） | `PrStep`（`do_pr`・`git`・`with_appended`・`passed_stages`）。関数の `user_changes`、定数の `CHANGES_HEADING`・`PR_FOOTER` | 約 145（実測 125） |
| `prompts.py` | `Supervisor` が使う文面の定数（`WORK_SYSTEM`・`FULL_SYSTEM`・`PR_SYSTEM`・`JUDGE_SYSTEM`・`SLOW_SYSTEM`・`PROGRESS_PROMPT`・`WORKDIR_PROMPT`・`RESUME_PROMPT`・`REPORT_DONE`）。メソッドは持たない | 約 75（実測 69） |

**import の向き:** `prompts`・`claude` は `lib/` だけを import する。`state` の上に `slow`・`steps` を置き、その上の `worker_steps` と `pr` は `steps`・`claude`・`prompts` を import する。`engine` はここまでのすべてを import し、`engine` を import するのは `commands` だけである。`SlowWatch` と各ハンドラーは、`RunState`・`ClaudeRunner`・`base_branch`・`pr_number` を `Engine` が渡す `ctx` から受け取る。このため、`slow` から `steps` へも、ハンドラーから `engine` へも import の向きが生じず、循環は起きない。

**テストの差し替え先:** `sv.gh_parts.RUNNER` の 3 件（`gh_limit_wait`）は `supervise.py` が `gh_parts` を import しなくなるため、`lib/gh_call.py` の `gh_call.RUNNER` を差し替える形に変わる（`RUNNER` は再エクスポートしない。lib/gh_parts.py の節）。`Supervisor` のメソッドを差し替えるテストは無いが、18 か所の `sv.Supervisor(...)` は `Engine` を使う形に、`.results`・`.log` の読み取りは `RunState` を通す形に変わる。

### review_lib
`state.py` のトップレベルの定義 299 個（関数・クラス・定数。計 5,068 行）を、14 個の副命令からたどれる範囲で 1 つずつモジュールへ割り当てる。行数は、定義の本体と直前の空行・コメントを実測した値に、import と docstring の見積り（6 行 + import するモジュール 1 つにつき 1 行）を足したもの。予定の 7 本（`__init__`・`store`・`workspace`・`github`・`categories`・`participants`・`findings`）に `ci`・`posts`・`matching`・`fix_result`・`review_focus` の 5 本を足し、`commands/` の 9 本と合わせて 21 本にする。どのモジュールも 500 行以下で、301〜500 行に入るのは `commands/init.py`・`workspace.py`・`github.py` の 3 本だけである。

| モジュール | 持つもの | 見積りの行数 |
| --- | --- | --- |
| `review_lib/__init__.py` | `_sh`・`die`・`info`・`_now` | 約 30 |
| `review_lib/store.py` | 状態ファイルと一時ディレクトリのパス（`_git_toplevel`・`_tmp_dir`・`_resolve_tmp_dir`・`_state_path`・`_payload_path`・`_existing_comments_path`・`_critique_path`）、読み書き（`_load`・`_save`・`_write_state`・`_summary_extra`・`_find_resumable_state`） | 約 145 |
| `review_lib/workspace.py` | worktree の作成（`_default_worktree_base`・`_is_registered_worktree`・`_create_worktree`）、head への同期（`HeadRef`・`_resolve_head_ref`・`_fetch_head`・`_resolve_sync_target`・`_sync_exclusions`・`_worktree_changes`・`_is_synced`・`_reset_worktree_head`・`_clean_untracked_files`・`_has_unpushed_commits`・`_sync_worktree`） | 約 375 |
| `review_lib/github.py` | gh と REST の呼び出し（`_gh_rest`・`RestResponse`・`_parse_rest_headers`・`_gh_output`）、リポジトリの特定（`_REPO_URL`・`_git_remote_url`・`_repo_from_git`・`_repo_from_resume`・`_repo_slug`）、PR の取得（`PrMetadata`・`_pr_metadata_of`・`_fetch_pr_metadata`・`_normalize_pr_file_status`・`_parse_pr_files_payload`・`_parse_pr_files_api_lines`・`_fetch_changed_files`）、コメントとスレッド（`FETCH_COMMENTS_SCRIPT`・`_fetch_existing_comments`・`_review_exists`・`_fetch_unresolved_threads`） | 約 365 |
| `review_lib/ci.py`（足す） | `CI_CODE_PATTERNS`・`CI_META_PATTERNS`・`_CI_META_RE`・`CI_FAILED_CONCLUSIONS`・`CHECK_RUNS_PER_PAGE`・`CHECK_RUNS_MAX_PAGES`・`CiClassification`・`_classify_ci`・`_classify_failed_names`・`_fetch_check_runs`・`_round_ci` | 約 145 |
| `review_lib/categories.py` | パスの分類の定数（`DOC_EXTENSIONS` から `INFRA_FILENAMES` まで・`DESIGN_DOC_SUFFIXES`・`PATH_CATEGORY_RULES`）、`_path_info`・`_contains_any`・`_path_tokens`・`_is_*_path` 14 個・`_classify_changed_files`・`_warn_oversized_design_docs` | 約 260 |
| `review_lib/review_focus.py`（足す） | レビュー観点のテンプレート 17 個（`COMMON_REVIEW_TEMPLATE` から `INFRA_REVIEW_TEMPLATE` まで）・`CATEGORY_TEMPLATES`・`_design_stage_fields`・`_round_stage`・`_auto_review_instructions`・`_combined_review_instructions`・`_extra_review_instructions` | 約 190 |
| `review_lib/participants.py` | 参加者の決め方（`NONE_WORD`・`PARTICIPANT_ARGS`・`REVIEW_RESUME_FIELDS`・`_normalize_participant_args`・`_resolve_reviewers`・`_apply_resume_args_block`・`LEGACY_AGENTS`・`AGENTS`・`_round_reviewers`）、参加者ごとの結果の読み方（`_is_pass`・`_skipped_by_only`・`_agent_intent`・`_no_result_agents`・`_round_passes`） | 約 280 |
| `review_lib/posts.py`（足す） | `NO_RESULT`・`_queue`・`_pending_posts`・`_flushed_review`・`_flushed_review_target`・`_confirm_flushed`・`_auto_flush` | 約 145 |
| `review_lib/findings.py` | 指摘の区分（`_verdicts`・`_classify_finding`・`_unrefuted_reason`・`_apply_classification`・`_counted_finding_ids`・`_classify_round`）、統合の順位（`_SEVERITY_RANK`・`_VERIFY_RANK`・`_verify_result`・`_absorb`）、引き継いだ指摘（`_record_carried_over`・`_carried_over_pending`） | 約 225 |
| `review_lib/matching.py`（足す） | `OSCILLATION_NEAR_LINES`・`OSCILLATION_BODY_CHARS`・`_OSCILLATION_DROP`・`_normalized_body`・`_finding_keys`・`_read_finding_payload`・`_comment_keys`・`_finding_match_kind`・`_counted_finding_keys`・`_new_finding_count`・`_evidence_completed`・`_OscillationOverlap`・`_oscillation_overlap` | 約 255 |
| `review_lib/fix_result.py`（足す） | `FIX_SOURCE_KEY`・`_read_fix_result`・`_read_explicit_fix_result`・`_find_fallback_fix_result`・`_round_started_unixtime`・`_is_fresh_fix_result`・`_is_fresh_mtime`・`_read_and_parse_fix_payload`・`_matches_pr` | 約 235 |
| `review_lib/commands/init.py` | 副命令 `init`：`cmd_init`・`_init_new_state`・`_InitResult`・`_print_init_result`・`_InitPRContext`・`_InitReviewContext`・`_InitWorkspaceContext`・`_InitialAssignment`・`_InitialStateContext`・`_refresh_resume_state`・`_resume_from_state`・`_sync_resume_worktree` | 約 435 |
| `review_lib/commands/start_round.py` | 副命令 `start-round`：`cmd_start_round`・`_resolve_previous_verdict`・`_require_fix_for_changes`・`_verify_resolved_threads`・`_guard_previous_round`・`_sync_before_round` | 約 180 |
| `review_lib/commands/read_result.py` | 副命令 `read-result`：`cmd_read_result`・`_record_no_result`・`_die_no_result`・`_read_review_result_file`・`_validate_review_result`・`_FINDING_DEFAULTS`・`_has_evidence`・`_load_payload`・`_collect_review_findings`・`_record_review_post` | 約 300 |
| `review_lib/commands/verify_findings.py` | 副命令 `verify-findings`：`cmd_verify_findings`・`VERIFY_TIMEOUT_SECONDS`・`VERIFY_REPRODUCED_CODES`・`_resolves_inside`・`_verify_argv`・`_run_verify`・`_merged_root`・`_run_finding_checks`・`_propagate_best_verification`・`_verify_findings`・`_merge_duplicates`・`_is_near` | 約 280 |
| `review_lib/commands/collect_critiques.py` | 副命令 `collect-critiques`：`cmd_collect_critiques`・`CRITIQUE_VERDICTS`・`_mark_evidence_round`・`_critique_targets`・`_put_critique`・`_load_critique_items`・`_critique_record`・`_record_unmatched_critique`・`_attach_critiques`・`_round_finding_index`・`_missing_critique_targets`・`_handle_incomplete_critiques`・`_same_round_no`・`_declared_duplicate_targets`・`_merge_declared_duplicates` | 約 285 |
| `review_lib/commands/judge.py` | 副命令 `judge`・`flush`：`cmd_judge`・`cmd_flush`・`_no_result_reasons`・`_abort_no_result_round`・`_record_relaunch`・`_handle_no_result_round`・`_evaluate_convergence`・`_collect_reviewer_intents`・`_finalize_round_if_converged`・`_finalize_converged_round`・`_print_judge_status` | 約 300 |
| `review_lib/commands/loop.py` | 副命令 `check-oscillation`・`should-rotate`・`set-current-pr`：`cmd_check_oscillation`・`cmd_should_rotate`・`cmd_set_current_pr`・`_head_branch_of` | 約 135 |
| `review_lib/commands/merge_fix.py` | 副命令 `merge-fix`：`cmd_merge_fix`・`_count`・`_normalize_dict_items`・`_merge_fix_records`・`_resolve_fix_aliases`・`_normalize_deferred_like`・`_normalize_fix_result`・`_thread_ids`・`_thread_positions`・`_build_round_fix`・`_record_fix_history` | 約 275 |
| `review_lib/commands/report.py` | 副命令 `verify-sweep`・`unresolved-threads`・`report`：`cmd_verify_sweep`・`cmd_unresolved_threads`・`_as_count`・`_read_sweep_result`・`_reconcile_sweep_result`・`_sweep_record`・`cmd_report`・`_print_round_summary`・`_print_participants`・`_resume_value`・`_print_sweep`・`_print_deferred_nits`・`_print_rejected` | 約 290 |
| `skills/cross-review/scripts/state.py`（エントリポイント） | `_add_*_parser` 14 個・`_SUBCOMMAND_REGISTRARS`・`_Subparsers`・`build_parser`・`main`・引数の型（`_runtime_or_none`・`_runtime_list`・`_seat_arg`） | 約 245 |

足す 5 本の理由は次のとおり。
- **`ci`**: 失敗したチェックの名前を振り分ける規則を持ち、`judge` と `merge-fix` だけが使う。PR やコメントの取得とは責務が違う。
- **`review_focus`**: レビュー観点の文章を持つ。パスの分類の規則（コード）と、読む人も直す頻度も違う。
- **`posts`**: 投稿のキューと投稿済みの確認を持つ。GitHub への書き込みの状態を見るもので、指摘の区分の規則とは別である。
- **`matching`**: 指摘どうし・指摘とコメントを照合する規則を持つ。新規性の数え方と振動の判定が同じ照合を使う。
- **`fix_result`**: 修正の結果ファイルを見つけ、それが今のラウンドのものかを確かめる。取り込んだ値の正規化とは分ける。

301〜500 行に入る 3 本は分けない。`commands/init.py` は再開と新規の 2 つの経路が `_InitResult` と `_print_init_result` を共有しているため、`workspace.py` は `_sync_worktree` の 1 つの手順を 10 個の関数に分けたものであるため、`github.py` は gh と REST の失敗の扱いを 1 か所に集めるため（クラス図の `GitHubClient`）である。

`commands/` は 1 ファイル 1 副命令にしない。50 行に満たない副命令（`flush`・`unresolved-threads`・`should-rotate`・`set-current-pr`）を 1 本ずつ置くと、drive.py が続けて呼ぶ一連の処理が別々のファイルに散るためである。まとめる相手は、drive.py の中で続けて呼ぶ副命令にした。`flush` は `judge` が終了コード 8 を返したときの続きなので `judge.py` に置く。`check-oscillation`・`should-rotate`・`set-current-pr` は、ループを止めるか・PR を巻き直すか・巻き直した PR へ移るかを決めるので `loop.py` に置く。`verify-sweep` と `unresolved-threads` は、どちらも GitHub 上に残っている未解決の指摘を数えて完了報告の数を確かめるので、`report.py` に置く。

import の向きは、`__init__` ← `store`・`github`・`categories`・`review_focus` ← `workspace`・`ci`・`findings`（いずれも `github` を使う）・`posts`（`github`・`store`）・`fix_result`（`store`） ← `participants`（`posts` の `NO_RESULT` を使う） ← `matching`（`findings`・`participants`・`store`） ← `commands/*` ← `state.py` の一方向にする。`commands/` どうしは import し合わず、`commands/` より下のモジュールは `commands/` を import しない。割り当てた後の依存をたどっても、循環は起きない。`classifications`・`assignment`・`auth` などの `scripts/` にあるモジュールは `state.py` を import しないので、`review_lib` から import しても循環しない。

テストのうち、名前を渡して `state_mod` を差し替えている 131 か所（ast で数えた数。改行をまたぐ呼び出しを含む）は、定義を置いたモジュールへ向く。内訳は `review_lib.github` 67（`_gh_rest` 12・`_repo_from_git` 11・`_review_exists` 11・`_fetch_pr_metadata` 10・`_fetch_changed_files` 7・`_fetch_unresolved_threads` 7・`_git_remote_url` 4・`FETCH_COMMENTS_SCRIPT` 2・`_gh_output` 2・`_fetch_existing_comments` 1）、`review_lib` 23（`_sh`）、`review_lib.workspace` 20（`_is_registered_worktree` 8・`_sync_worktree` 8・`_create_worktree` 4）、`review_lib.ci` 9（`_round_ci` 4・`_fetch_check_runs` 4・`_classify_ci` 1）、`review_lib.commands.start_round` 5（`_sync_before_round` 4・`_guard_previous_round` 1）、`review_lib.posts` 4（`_auto_flush` 3・`_pending_posts` 1）、`review_lib.findings` 2（`_record_carried_over`）である。conftest.py が変数で渡している 1 か所（`_GITHUB_LOOKUPS`）は、`_fetch_check_runs` を `review_lib.ci` へ、`_fetch_pr_metadata` を `review_lib.github` へ分けて向ける。`state_mod.subprocess`・`state_mod.auth` など、モジュールの属性を差し替えている 37 か所は同じモジュールのオブジェクトを指すので、向きは変わらない。`skills/fix/tests/test_fix_steps.py` は差し替えずに `_normalize_fix_result` を呼んでいるだけなので、呼び先は `review_lib.commands.merge_fix` になる。この差し替えが効くように、呼ぶ側は `from ... import 関数` で名前を取り込まず、`github._gh_rest` のようにモジュールの属性として呼ぶ。

使われていない定義 `CI_CODE_PATTERNS` と `_classify_round` は、C2 で消す（state.py の中からもテストからも参照されていない。2026-09-26 に確かめた）。

### relay_lib
`scripts/relay.py`（2092 行）の最上位の関数・クラス・定数を次のように置く。予定の 10 本に `shellrc.py` を 1 本足して 11 本にする。見積りの行数は、今のコードでの各定義の行数（後続の空行とコメントを含む）の実測に、import と docstring の分を足したものである。`version_dir.py` の新しい部分とランチャーだけは今のコードに無いため見積りで出す。

| モジュール | 持つもの | 見積りの行数 |
| --- | --- | --- |
| `scripts/relay.py`（ランチャー） | 隣に `relay_lib/` があればそれを、無ければ `relay.current` が指すバージョンディレクトリを `sys.path` の先頭に置き、`relay_lib` を import して `main` を呼ぶ。標準ライブラリだけを使う | 約 80（見積り。設計の値） |
| `relay_lib/__init__.py` | 副命令の説明（今のモジュールの docstring）・`main`（副命令の振り分けと `is-child`） | 約 60（実測 58） |
| `relay_lib/common.py` | ファイル名の定数（`MARK_FILE`・`STOP_FILE`・`LOCK_FILE`・`PID_FILE`・`CHILD_FILE`・`LOG_FILE`・`COUNT_LOCK`・`INSTALL_LOCK`・`COPY_LOCK`・`QUESTION_FILE`・`QUESTION_LOCK`・`ASKED_FILE`・`HELD_FILE`・`BLOCK_OPEN`・`BLOCK_CLOSE`）・`_num`・`quiet_seconds`・`now_iso`・`parse_iso`・`state_root`・`data_dir`・`relay_running`・`write_json_atomic`・`read_json`・`remove`・`fallback_cwd`・`_flock_wait`・`_lock`・`_unlock`・`LockBusy`・`_read`・`_read_bytes`・`_write_file`・`config_dir`・`copy_path`。時刻と JSON の 4 つは `lib/clock.py`・`lib/jsonio.py` を呼ぶ形にする（決定 6） | 約 210（実測 199） |
| `relay_lib/proc.py` | `proc_info`・`is_direct_child`・`relay_position`・`under_relay`・`relay_child_pid` | 約 100（実測 86） |
| `relay_lib/record.py` | `RelayRecord`（`log.jsonl` への追記・`next.json` の読み書きと削除。run の `Relay.log`・`Relay.read_mark`、mark の `log_mark_skipped`・`cmd_mark` の `next.json` の書き込み）・`current_section`・`StartLimit`・`make_relay_dir` | 約 165（実測 128。run 由来 97・mark 由来 31） |
| `relay_lib/mark.py` | Stop hook と PreToolUse / PostToolUse hook とカットポイントのアナウンスの本体: `FENCE_RE`・`next_blocks`・`running_tasks`・`background_running`・`hold_reason`・`hold_once`・`asked_after`・`cmd_mark`・`cmd_stop`・`DENY_REASON`・`cmd_question`・`notice_lines`・`cmd_notice` | 約 250（実測 228） |
| `relay_lib/claude.py` | 引数の定数（`PASS_FLAGS`・`SUBCOMMANDS`・`BOOL_FLAGS`・`VARIADIC_FLAGS`・`REQUIRED_FLAGS`・`SECTION_FLAGS`・`DROP_ENV`）・`_is_self`・`resolve_claude`・`needs_no_relay`・`carried_args`・`say`・`depth`・`passthrough`・`child_env`・`plugin_cli`・`read_plugin`・`update_plugin`（`Relay.update` から）・会話の記録の読み取り（`file_snap`・`_iter_transcript_rows`・`replied_after`・`_is_user_prompt`・`_starts_background`・`after_mark`） | 約 280（実測 261） |
| `relay_lib/terminal.py` | `Terminal`（今のメソッドと、`Relay` から移す `_child_exec`・`_read_errno`・`spawn`・`stop_child`・`screen`）・`StartFailed`・`exit_code`・`cmd_run` から移す端末の raw の設定と戻し | 約 240（実測 202 と raw の設定 約 15） |
| `relay_lib/run.py` | `Relay`（セッションを切り替える状態機械）・`cmd_run` | 約 350（実測 340 から raw の設定 約 15 を除く） |
| `relay_lib/shellrc.py`（足す） | シェルの設定ファイルの管理ブロックの読み書き: `DEF_RE`・`UNSAFE`・`READS_BASHRC_RE`・`shellrc_path`・`loader_file`・`rc_files`・`login_files`・`login_file`・`bash_look`・`shell_rc`・`reads_bashrc`・`login_shadow`・`login_warning`・`sh_quote`・`shellrc_body`・`loader_line`・`loader_inner`・`loader_body`・`_records`・`_add_record`・`_drop_records`・`_backup`・`blocks_of`・`rc_blocks`・`has_definition`・`rewrite_blocks`・`block_inner`・`has_direct_alias`・`_auto_blocks`・`_has_loader`・`_startup_record_noticed`・`_startup_notice_message` | 約 265（実測 243） |
| `relay_lib/version_dir.py` | `VersionDir`（新しい部分。`ensure(版)`: `MANIFEST` と digest の計算・`.tmp` へのコピー・rename・`relay.current` の原子的な書き込み・古いバージョンディレクトリを 2 つ残して消す・書きかけの `.tmp` の掃除・`relay.current` の読み取り）と、今の複製の処理（`VERSION_RE`・`copy_version_path`・`old_copy_path`・`plugin_version`・`version_key`・`_self_body`・`place_copy_to`・`_startup_copy`・`_startup_refresh_old_copy`） | 約 225（見積り。今のコードから移す分の実測 77、新しい部分 約 130） |
| `relay_lib/install.py` | `out`・`_take_both`・`cmd_install`・`_install_locked`・`cmd_uninstall`・`_uninstall_locked`・`_same`・`cmd_status`・`session_line`・`startup_once`・`cmd_startup` | 約 290（実測 269） |

`shellrc.py` を足すのは、今の導入の部分（1304〜1963 行、660 行）が 500 行を超えるためである。シェルの設定ファイルの管理ブロックの読み書きは、副命令（install・uninstall・status・startup）の手順とは別の責務で、どの副命令からも同じ関数を呼ぶ。自動追加ブロックの記録と知らせ（`_startup_record_noticed`・`_startup_notice_message`）も管理ブロックの記録を読み書きするため、こちらに置く。301〜500 行に入るのは `run.py` だけで、1 つの状態機械（`Relay`）を分けないためである。

import は一方向で、ランチャー → `__init__` → run・mark・install・proc、run → terminal・claude・record・mark・common、install → shellrc・version_dir・proc・common、mark → record・proc・common の向きになる。proc・record・claude・terminal・shellrc・version_dir は common だけを import し、common は標準ライブラリと `lib/clock.py`・`lib/jsonio.py` だけを import するので、循環は無く I2 を満たす。循環と逆向きを作らないため、`LockBusy`（mark の `cmd_question` が使う）と `config_dir`・`copy_path`（shellrc が使う）を common に置く。run が起動時に全モジュールを import する（決定 5）のは一方向の import なので、この向きを崩さない。

`test_relay.py` の差し替えは 12 件ある（`monkeypatch.setattr` 10・`monkeypatch.setitem` 1・`Relay._tick` への代入 1）。差し替え先が変わるのは 3 件で、`make_relay_dir` は `relay_lib.record`、`proc_info` は `relay_lib.proc`、`Relay._tick` は `relay_lib.run.Relay` へ向ける（決定 3）。前の 2 件が効くように、run は `record.make_relay_dir()`、install は `proc.proc_info()` とモジュール越しに呼ぶ。残りの 9 件は変えない。内訳は `os.isatty` 3・`os.execve` 1・`os.getpid` 1・`time.gmtime` 1・`sys.platform` 2・`sys.modules["pty"]` 1 で、どれも標準ライブラリの属性なので、どのモジュールから呼んでも効く。差し替え以外の `mod.` の参照は、定義元（claude 4・shellrc 3・common 3・run 5・mark 3・record 1・install 5・version_dir 1・`__init__` 1）へ向け直す。

**動いている `run` が使うバージョンディレクトリは、そのディレクトリの中の `inuse-<pid>` が示す。** `run` は起動時に読み込んだ
バージョンディレクトリへ `inuse-<pid>` を置き、終わるときに消す。古いバージョンディレクトリを消す `startup` は、生きている pid の
`inuse-<pid>` があるディレクトリを消さず、死んだ pid のものは消してから判定する。`relay.pid` の形は変えない（I11）。

### lib/monitor.py
| モジュール | 持つもの（関数・クラス・表の名前） | 見積りの行数 |
| --- | --- | --- |
| `lib/monitor.py`（エントリポイント） | モジュールの docstring・`_lib_dir`・`_seat_or_both`・`main`・`_resolve_agents`・`_run_all`・`_emit_results`・`if __name__ == "__main__"`。下の 5 本へ移した名前の再エクスポート（`_TMP_DIR_OVERRIDE` は除く） | 約 285（実測 266 + 再エクスポート 20） |
| `lib/monitor_patterns.py`（新設。照合の表） | `USAGE_LIMIT_FATAL`・`EARLY_ERROR_FATAL`・`EARLY_ERROR_FATAL_WARNING_SHAPED`・`EARLY_ERROR_WARN`・`EARLY_ERROR_BENIGN`・`EARLY_ERROR_BENIGN_KEEP_WARNINGS`・`CLI_TIMEOUT_AFTER_EXIT`・`CODEX_SENTINEL`・`ANSI_ESCAPE`・`CLAUDE_STDOUT_FATAL`・`CLAUDE_STDOUT_USAGE_LIMIT`・`_match_is_quoted`・`_unescaped_count`・`_strip_ansi` | 約 190（実測 172 + 15） |
| `lib/monitor_scan.py`（新設。ログの読み取りと致命の判定） | `_read_tail`・`_safe_size`・`_tail_last_nonempty_line`・`_scan_patterns`・`_scan_early_fatal`・`_scan_early_warn`・`_scan_claude_stdout`・`_scan_claude_stdout_fatal`・`_scan_claude_stdout_usage_limit`・`_scan_codex_sentinel`・`EarlyFatal`・`_scan_usage_limit`・`_scan_fatal`・`_early_error` | 約 210（実測 190 + 20） |
| `lib/monitor_proc.py`（新設。PID とプロセスグループ） | `_read_pidfile`・`_proc_state`・`_pid_alive`・`_is_zombie`・`_leads_own_group`・`_kill_pid`・`_pid_cmdline_matches` | 約 120（実測 103 + 15） |
| `lib/monitor_types.py`（新設。既定値・一時ディレクトリ・状態の型） | `DEFAULT_TIMEOUT`・`DEFAULT_STALL`・`DEFAULT_STALL_AGENT_BUILTIN`・`DEFAULT_POLL`・`RESULT_AGE_GRACE`・`DEFAULT_NO_EARLY_ERROR`・`_safe_int_env`・`_agent_runtime`・`_agent_stall_default`・`_TMP_DIR_OVERRIDE`・`TMP_DIR_ENV_VARS`・`_tmp_dir`・`DEFAULT_STEM_TEMPLATE`・`MonitorConfig`・`AgentPaths`・`MonitorOutcome`・`AgentStatus` | 約 210（実測 191 + 20） |
| `lib/monitor_loop.py`（新設。1 者の監視ループ） | `monitor_agent`・`_initialize_monitor`・`_validate_pid_cmdline`・`_update_progress`・`_lingering_completion`・`_timeout_outcome`・`_early_error_outcome`・`_process_exit_outcome`・`_stall_outcome`・`_finish_monitor`・`_emit_progress`・`_emit_log` | 約 300（実測 282 + 20） |
| `lib/monitor_outcome.py`（既存へ追加） | 既存の語彙と読み書きに `_record_outcome` を足す | 約 290（既存 236 + 実測 52 + 3） |

import の向きは `monitor.py` → `monitor_loop` → `monitor_scan`・`monitor_proc` → `monitor_types`・`monitor_patterns` の一方向で（`monitor_loop` は `monitor_types`・`monitor_patterns` も直接読む）、`monitor_proc` と `monitor_patterns` は標準ライブラリだけ、`monitor_types` は `assignment`・`limits` だけを読むため、循環は起きない。`monitor_outcome` は `_record_outcome` が `AgentPaths.for_` を呼ぶために `monitor_types` を読み、`monitor_types` からは `monitor_outcome` を読まない。`supervise.py` は `USAGE_LIMIT_FATAL` を `monitor_patterns` から読む。移した先の名前は、別のモジュールからは `monitor_proc._kill_pid(...)` のようにモジュールの属性として引き、`from ... import` で名前を取り込まない（`monitor.py` の再エクスポートと、`_run_all` が裸で呼ぶ `monitor_agent`・`_record_outcome` だけが例外）。

テストの差し替え 43 か所のうち、`monitor_agent`・`_record_outcome` の 2 か所（`test_seat_names.py`。呼ぶ側の `_run_all` が `monitor.py` に残る）は差し替え先を変えず、`_tmp_dir` の 9 か所と `_TMP_DIR_OVERRIDE` の 4 か所は `monitor_types` へ、`_pid_alive` 11・`_kill_pid` 6・`_pid_cmdline_matches` 6・`_is_zombie` 5 の計 28 か所は `monitor_proc` へ差し替え先を変える（呼ぶ側が移った先のモジュールの名前空間で名前を引くため、`monitor.py` 側で差し替えても効かない。`_TMP_DIR_OVERRIDE` は `raising=False` のため効かなくても落ちない）。`main` は `global _TMP_DIR_OVERRIDE` の代わりに `monitor_types._TMP_DIR_OVERRIDE` へ代入し、シムの名前空間を確かめる `test_monitor_generic_stem.py` の `monitor_agent.__globals__` の assert は `_run_all.__globals__` を見る形に変える。

### lib/worktree-common.sh
| ファイル | 持つもの（関数の名前。多ければ接頭辞でまとめ、件数を添える） | 見積りの行数 |
| --- | --- | --- |
| `worktree-common.sh` | 定数（`WT_*`）、`wt_tool_matcher`、位置の解決（`_wt_read_lines` `_wt_abs_in` `_wt_abs` `_wt_resolve` `wt_main_dir` `wt_in_worktree`）、パスの判定（`wt_allow_paths` `wt_is_allowed_path` `wt_is_safe_relative` `wt_compose_project` `wt_relative_to_main`）、パスの正規化（`_wt_lexical_normalize` `wt_normalize_path`）、下の 6 本の source。計 14 関数 | 約 290 |
| `worktree-declaration.sh`（足す） | 宣言ファイル（`_wt_local_analysis` `_wt_local_overrides` `wt_declaration*` 5 件）。計 7 関数 | 約 185（実測 172） |
| `worktree-branch.sh` | worktreeの一覧と追従先（`wt_dev_worktrees` `wt_follow_target` `wt_follow_enabled`）、ブランチ（`wt_default_branch` `wt_branch_exists` `_wt_declaration_string` `wt_declaration_branch` `wt_base_branch` `wt_production_branch`）、`wt_dirty_paths`。計 10 関数 | 約 195 |
| `worktree-shell-lex.sh` | 字句解析器（`_wt_tok_*` 7 件と `_wt_tokenize`）。計 8 関数 | 約 310 |
| `worktree-write-target.sh` | 書き込み先の抽出のエントリポイント（`wt_extract_write_target` `_wt_extract_preprocess_cmd` `_wt_extract_tokenize` `_wt_is_separator` `_wt_is_not_target`）、heredoc の除去（`_wt_strip_heredocs` `_wt_heredoc_parse_delim` `_wt_scan_expanded_line`）、`wt_extract_patch_target`。計 9 関数 | 約 415 |
| `worktree-write-target-scan.sh` | 走査の本体 `_wt_extract_scan`（状態の局所変数と語ごとの繰り返し）、リダイレクト（`_wt_scan_redir_target` `_wt_scan_redir_span` `_wt_take_redirect_operand`）、書き込み先を出す関数（`_wt_scan_emit` `_wt_extract_sed_targets` `_wt_extract_cp_mv_target` `_wt_scan_emit_word`）、`_wt_scan_command_position`。計 9 関数 | 約 475 |
| `worktree-write-target-track.sh` | 現在地と入れ子の追跡（`_wt_scan_track_word` `_wt_scan_cd`）、入れ子の積み下ろし（`_wt_scan_push_group` `_wt_scan_pop_group` `_wt_scan_push_subshell` `_wt_scan_pop_subshell` `_wt_scan_close_function_body`）、`||` の右辺（`_wt_scan_or_group_exits` `_wt_scan_or_exit_redirs`）。計 9 関数 | 約 440 |
| `worktree-registry.sh` | テスト環境の採番とレジストリ（`wt_common_git_dir` `wt_registry_*` 4 件 `_wt_registry_*` 2 件 `_wt_slug` `wt_env_name` `wt_port_for` `wt_duration_seconds`）、排他（`lock-common.sh` の source と `wt_lock_*` 3 件 `_wt_lock_*` 2 件）、スロット（`wt_slot_*` 5 件と `_wt_slot_set_field`）。計 22 関数 | 約 275 |

`_wt_extract_scan` の中で定義している 17 個の関数は、最上位の関数として `worktree-write-target-scan.sh` と `worktree-write-target-track.sh` へ置き、接頭辞のない 10 個（`_emit` `_push_group` `_redir_target` など）は `_wt_scan_` を付けた名前にする。これらは `_wt_extract_scan` の局所変数を動的スコープで読み書きするため、`_wt_extract_scan` の中からだけ呼ぶ。

source の順序: 呼び出し側は `worktree-common.sh` だけを source し、`worktree-common.sh` は定数と基本の関数を定義した後、末尾で `$(dirname "${BASH_SOURCE[0]}")` からの相対パスで declaration → branch → shell-lex → write-target → write-target-scan → write-target-track → registry の順に source し、1 本でも読めなければ 1 を返す。source の時点で実行されるのは定数の代入と registry の `lock-common.sh` の source だけで、関数どうしの呼び出しは実行時に解決されるため、ファイルの間に定義の順序の制約はない（bash 3.2 でも同じ）。

テストへの影響: 関数を呼ぶテストは `worktree-common.sh` を source するだけで、分けた後もそのまま通る。ファイルの本文を読むテスト 3 本（`test_declaration_check.py` の `unreadable` の置き場所、`test_registry.py` の `_lock_body`、`test_lock_common.py` の `lock-common.sh` の source 行）は、読む先を `worktree-declaration.sh` と `worktree-registry.sh` へ変える。

`worktree-branch.sh` は、宣言ファイルの読み取り（172 行）が独立して読める単位なので `worktree-declaration.sh` へ分ける。

### instructions-check.py
エントリポイントは `plugins/ndf/scripts/instructions-check.py` のまま変えない（パス・引数・出力・終了コード）。移した名前（先頭が `_` のものも含む）は、本体が `from instructions_lib.<モジュール> import ...` で再エクスポートする。見積りは ast で測った定義ごとの行数（直前の空行とコメントを含む）に、docstring と import の分を足したものである。

| モジュール | 持つもの（関数・クラスの名前） | 見積りの行数 |
| --- | --- | --- |
| `instructions-check.py` | `SCOPES`・`in_development_repo`・`run_refresh`・`_Parser`・`build_parser`・`main`・`resolve_latest`・`collect_scope_roots`・`measure_targets`・`finalize`・`check`・`criteria_is_stale`、先頭の docstring、`sys.path` の設定、`import refresh as refresh_lib`、再エクスポート | 約 245（定義 163） |
| `instructions_lib/__init__.py` | なし（パッケージの目印） | 約 3 |
| `instructions_lib/model.py` | `CheckError`・`Source`・`Finding`・`Target`・`ScopeRoot`・`Criteria`・`load_criteria`・`KNOWN_CRITERIA`・`SUPPORTED_CRITERIA_VERSIONS`・複数のモジュールが使う正規表現（`HEADING_RE`・`FENCE_RE`・`BULLET_RE`） | 約 135（定義 124） |
| `instructions_lib/declaration.py` | `Declaration`・`_declaration_from`・`_populate_declaration`・`_apply_field_constraints`・`_validate_scopes`・`_validate_imports`・`_typed`・`_positive`・`_parse_date`・`_validate_released`・`DEFAULT_FILES`・`DEFAULT_IMPORT_SYNTAX`・`DEFAULT_IMPORT_DEPTH`・`DEFAULT_REVIEW_INTERVAL_DAYS`・`SUPPORTED_DECLARATION_VERSIONS`・`DECLARATION_RELATIVE_PATH` | 約 180（定義 169） |
| `instructions_lib/collect.py` | 指示書を集める処理と本文の読み方: `collect_project`・`collect_declared`・`_collect_scope_entry`・`_build_scope_metadata`・`_enumerate_scope_files`・`_matches`・`_expand`・`display_path`・`mask_code`・`interprets_imports`・`references`・`_inside_root`・`resolve`・`IMPORT_RE` | 約 190（定義 179） |
| `instructions_lib/findings.py` | @インポート・読み込み量・指示の数の判定: `import_findings`・`stale_allowance_findings`・`read_size`・`budget_findings`・`count_instructions`・`count_findings`・`SENTENCE_END` | 約 175（定義 161） |
| `instructions_lib/versions.py` | リリース済み版の段落の判定: `version_findings`・`_inline_pending_findings`・`paragraph_starts`・`released_versions`・`_read_changelog_lines`・`_read_tag_lines`・`_valid_suffix`・`version_key`・`base_triple`・`VERSION_RE`・`VERSION_AT_START`・`LEAD_RE` | 約 190（定義 179） |
| `instructions_lib/report.py` | 扱いの判定と出力: `action_of`・`format_finding`・`issue_title`・`report`・`Measurements`・`ReportInput`・`ACTION_FIX`・`ACTION_FILE`・`ACTION_REPORT` | 約 115（定義 103） |

instructions_lib を 2 本（`__init__`・`model`）より増やすのは、2 本では 300 行に収まらないためである。型と宣言と観点を 1 本にすると定義だけで 293 行、docstring と import を足して約 310 行になる。さらに本体に判定を残すと約 830 行になる。宣言を `declaration.py` に分け、判定を観点ごとに `collect`・`findings`・`versions`・`report` の 4 本へ出すと、すべてが 300 行以内になる。

import の向きは `model` ← `declaration` ← `collect` ← `findings`・`versions` ← `report` ← 本体の一方向で、下位のモジュールは上位も本体も import しないので循環しない（`versions` は `collect` の `_inside_root`・`display_path` を、`report` は `collect` の `display_path` を使う）。`__file__` を使う `in_development_repo`（スクリプトの実体が `--root` の下にあるかの判定）と `main`（観点のデータの既定の位置）は本体に残す。本体は `instructions_lib` を import する前に、スクリプトのディレクトリと `lib/` の両方を `sys.path` の先頭へ入れる（テストは `spec_from_file_location` で読み込むので、スクリプトのディレクトリが `sys.path` に入らない）。`declaration.py` はそのあとで `import refresh as refresh_lib`（`DEFAULT_TIMEOUT_SECONDS` を使う）を行う。

テストの差し替え先は変わらない。`test_instructions_check.py` の差し替えは `module.refresh_lib.fetch` の代入だけで（`monkeypatch.setattr` / `mock.patch` は 0 件）、`refresh` のモジュールオブジェクトは本体と `declaration.py` で共有され、`run_refresh` も本体に残るからである。変えるのは `install_script` だけで、`instructions_lib/` のディレクトリ全体（7 本）を `shutil.copytree(..., ignore=shutil.ignore_patterns("__pycache__"))` で複製の対象に足す。

### lib/gh_parts.py

`lib/gh_parts.py` はエントリポイント（`python3 gh_parts.py pr-info ...` などの CLI）として残す。CLI と、`step_result` の形で結果を返す 2 つのコマンド（`body_section`・`review_post`）を持ち、移した公開の名前を再エクスポートする。移す先は `lib/` の直下のフラットなモジュールで、名前は `gh_` で始める。ほかのスクリプトが `gh_parts.<名前>` で参照している 10 の名前（`GhResult`・`RestResponse`・`parse_rest_headers`・`gh`・`is_rate_limited`・`view_json`・`unresolved_threads`・`fetch_check_runs`・`fold_check_runs`・`check_result`）と、`test_gh_parts.py` が `gp.<名前>` で呼ぶ公開の名前は、すべて `gh_parts` から引ける。**`RUNNER` だけは再エクスポートしない。** 再エクスポートすると `setattr(gh_parts, "RUNNER", ...)` が通っても効かず、テストが黙って GitHub を呼ぶ。再エクスポートしなければ、向け直し忘れた差し替えは `AttributeError` で落ちる。`RUNNER` を参照しているのはテストだけである。

見積りの行数は、ast で測った定義ごとの行数（直前の空行とコメントを含む。括弧の中の「定義」）に、docstring と import の分を足したものである。L0 で足す部分は今のコードに無いので、決定 16 の表の操作ごとに見積もった値で、**見積り**である。左が標準ライブラリで `gh api` を呼ぶ場合、右が githubkit の上に置く場合（決定 17）の値である。

| モジュール | 持つもの（関数・クラス・定数の名前） | 見積りの行数（標準ライブラリ / githubkit） |
| --- | --- | --- |
| `gh_parts.py` | エントリポイント: `body_section`・`review_post`・`_parser`・`main`、再エクスポート（今の公開の名前 31 個と、L0 で足す公開の名前） | 約 230 / 約 230（定義 151） |
| `gh_call.py` | GitHub の呼び出しの最下層: `GhResult`・`_subprocess_gh`・`RUNNER`・`gh`・`_output_via_runner`・`RestResponse`・`parse_rest_headers`・`rest`・`_REPO_URL`・`resolve_repo`。L0: `rest` の ETag 付きの要求（`If-None-Match`・304 の判定・`RestResponse` に状態コード）・`rest_cached`（プロセスの中の ETag のキャッシュ `_ETAGS`）。githubkit の場合は `client()`（`gh auth token` の値と `http_cache`・`auto_retry` で作る）を足し、`rest` をその上に書き直す | 約 170 / 約 160（定義 103。L0 は約 +45 / 約 +40） |
| `gh_quota.py` | 枠の使い分けと上限: `_RATE_LIMIT_MARKERS`・`is_rate_limited`。L0: `rate_limits`（`rate_limit` の端点で GraphQL と REST の残りと reset を読む）・`with_fallback`（片方の枠が上限なら、代われる操作をもう片方で行う）・`wait_for_reset`（代われない操作を回復の時刻まで待つ。`sleep` と時刻は引数で差し替える）・`PollInterval`（変化が無い間は 10 秒から 60 秒へ伸ばし、変化があったら縮める）・`poll_until`（ETag 付きの読み直しと間隔の伸長を組む） | 約 190 / 約 170（定義 12。L0 は約 +160 / 約 +140） |
| `gh_fields.py` | REST の応答を GraphQL の `--json` の形へ変える対応表: `_rest_state`・`_rest_merge_commit`・`_VIEW_FIELDS`・`_VIEW_REST_PATH`。L0: 単発の読み取りと一覧で使うフィールド（`isDraft`・`author`・`headRefName`・`headRefOid`・`baseRefName`・`labels`・`mergedAt` など）の追加と、一覧の 1 件を変える `to_json_shape` | 約 100 / 約 100（定義 38。L0 は約 +45） |
| `gh_rest.py` | PR と課題の単発の読み書き（REST）: `view_json`・`fetch_body`・`update_body`・`viewer_login`。L0: `pr_list`・`issue_list`（ページ送り・状態とラベルの絞り込み）・`pr_create`・`issue_create`・`pr_edit`・`issue_edit`・`comment`・`pr_merge`、`view_json` を REST を先に読む形へ変える | 約 240 / 約 170（定義 48。L0 は約 +180 / 約 +110） |
| `gh_graphql.py` | 入れ子の読み取りと REST に無い操作（GraphQL）: `UNRESOLVED_THREADS_QUERY`・`UNRESOLVED_THREADS_JQ`・`unresolved_threads`。L0: `graphql`（クエリと変数・ページ送りの 1 回の要求） | 約 90 / 約 80（定義 54。L0 は約 +20） |
| `gh_checks.py` | チェックジョブ: `CHECK_RUNS_PER_PAGE`・`CHECK_RUNS_MAX_PAGES`・`FAILED_CONCLUSIONS`・`fetch_check_runs`・`_run_order`・`fold_check_runs`・`run_result`・`check_result`・`_JOB_ID`・`_safe`・`save_failed_log` | 約 130 / 約 130（定義 111） |
| `gh_pr_info.py` | `pr-info` の組み立て: `PR_VIEW_FIELDS`・`WITH_PARTS`・`_meta_from_graphql`・`_meta_from_rest`・`fetch_pr_meta`・`default_out_dir`・`_save_diff`・`pr_info` | 約 165 / 約 165（定義 148） |
| `gh_sections.py` | 本文の節の文字列の操作（GitHub を呼ばない）: `SECTION_END`・`_FENCE`・`_HEADING`・`_Span`・`_lines`・`_heading_lines`・`_find`・`get_section`・`_section_block`・`replace_section`・`append_line` | 約 125 / 約 125（定義 114） |

どのモジュールも 300 行以下で、例外リストに載せるものは無い。今の 822 行のうち、定義は 57 個で 779 行、残りは docstring 26 行・import 14 行・`__main__` の 2 行である。

**import の向き:** `gh_call` は標準ライブラリ（githubkit の場合は githubkit も）だけを import し、`gh_fields` と `gh_sections` は標準ライブラリだけを import する。`gh_quota`・`gh_checks`・`gh_graphql` は `gh_call` を、`gh_rest` は `gh_call`・`gh_quota`・`gh_fields` を、`gh_pr_info` は `gh_call`・`gh_quota`・`gh_checks`・`gh_graphql`・`gh_rest`・`step_result` を import し、`gh_parts` だけがすべてと `step_result`（`post_queue`・`result_posts` は関数の中で遅れて import）を import する。どの `gh_*` も `gh_parts` を import せず、`step_result`・`post_queue`・`result_posts` も `gh_*` を import しないので、循環しない。モジュールどうしは `gh_call.gh(...)` のようにモジュールの属性として呼ぶ（`from gh_call import gh` で取り込まない）。

**テストの差し替え先:** `RUNNER` の差し替え 6 件（`test_supervise.py` の `sv.gh_parts` 3 件・`test_release_steps.py` の `mod.gh_parts` 2 件・`test_gh_parts.py` の `fake` フィクスチャの `gp` 1 件）は、定義元の `gh_call.RUNNER` へ向け直す。`test_gh_parts.py` は `gh_parts.py` を `ndf_lib_gh_parts` の名前で読み込むが、移す先は `sys.path` から通常の名前で読まれ、スクリプトと同じ `gh_call` を共有するので、`gh_call` を import して差し替えれば全部に効く。`setattr(result_posts, "post_review", ...)` は `review_post` が `result_posts.post_review` を属性として呼ぶので変えなくてよい。githubkit の場合は、REST の差し替え先が `gh_call.client` になり、`RUNNER` は `gh auth token` と `gh run view --log-failed` だけの差し替え先になるので、`gh api` の argv で応答を返している `FakeGh` のテストは REST のパスで応答を返す形へ書き直す。

**`gh api` は 304 のとき終了コード 1 を返す**（2026-09-26 に実測: stdout にヘッダー、stderr に `gh: HTTP 304`）。`gh_call.rest` は終了コードではなく、ヘッダーの状態コードで 304 を見分け、変わっていないことを失敗として扱わない。

### refactor_lib/gitfacts.py
`refactor_lib/gitfacts.py` は残し、コミットの事実を取る関数を持ったまま、移した名前（先頭が `_` のものも含む）を `from .<モジュール> import ...` で再エクスポートする。公開する関数の名前と、`from .gitfacts import ...` / `from ..gitfacts import ...` で使っている 14 のモジュールの import は変えない。移す先はすべて `refactor_lib/` の直下に置く（`_CREDENTIAL_LIB` の `parents[4]` が同じディレクトリを指すため）。見積りは ast で測った定義ごとの行数（直前の空行とコメントを含む）に、docstring と import の分を足したものである。

| モジュール | 持つもの（関数・クラスの名前） | 見積りの行数 |
| --- | --- | --- |
| `gitfacts.py` | コミットの事実: `safe_int`・`reported_shas`・`commits_in_range`・`commit_trailers`・`_parse_trailer_paragraph`・`commit_diff_lines`・`commit_files`・`commit_test_changes`・`tracked_markdown`・`commit_touches_tests`・`commit_time`・`collect_commit_facts`、再エクスポート | 約 255（定義 206） |
| `pathkinds.py` | テストとプロダクションコードの判定: `is_test_path`・`_has_shebang`・`_is_code_path`・`production_code_changes`・`TEST_PATH_MARKERS`・`TEST_NAME_MARKERS`・`CODE_EXTENSIONS` | 約 80（定義 67） |
| `process.py` | プロセスの実行と打ち切り: `run_with_timeout`・`_process_group_alive`・`_kill_process_group`・`run_test_at` | 約 140（定義 126） |
| `github.py` | GitHub の照会: `resolved_threads_on_github`・`_fetch_review_threads_page`・`_REVIEW_THREADS_QUERY`・`check_run_result`・`_gh_api_get` | 約 120（定義 107） |
| `worktree.py` | 取り消しとworktreeの掃除: `revert_item_commits`・`reset_hard`・`revert_range`・`replay_commits`・`_order_newest_first`・`_worktree_changes`・`_control_prefix`・`_dirty_paths`・`_discard_worktree_changes`・`discard_impl_leftovers`・`_require_clean_worktree` | 約 240（定義 224） |
| `publish.py` | 生成物の同期と push: `_sync_generated`・`_run_sync_command`・`_write_plan_file`・`_publish_commit_message`・`_commit_sync_changes`・`push_head`・`push_with_retry_marker`・`flush_pending_push`・`_push_with_credential_fallback`・`credential_fallback_args`・`gh_available`・`_CREDENTIAL_LIB` | 約 215（定義 193） |
| `results.py` | 結果ファイルの読み取りと記録: `read_result`・`note_stopped`・`STOPPED_REASONS`・`record_observed_model` | 約 70（定義 58） |

`pathkinds`・`process`・`github`・`worktree`・`results` は `paths`・`gh_parts`・`statefile`・`models`・`monitor_outcome` と `refactor_lib` の `die`・`info` だけを import する。`publish` はそこに `worktree`（`_dirty_paths`・`_discard_worktree_changes`・`_require_clean_worktree`）・`process`（`run_with_timeout`）・`plan`・`vocabulary`・`timeline` を足す。`gitfacts` は `pathkinds`（`is_test_path`）と `process`（`run_test_at`）を使い、再エクスポートのために全部を import する。移す先のどれも `gitfacts` を import せず、`plan`・`paths`・`timeline`・`vocabulary` も `gitfacts` を import しないので、循環しない。

`patch_lib`（`refactor_lib` の下で同じ名前を持つモジュールすべてを差し替える）を通る差し替え（`sh`・`run_with_timeout`・`push_head`・`_sync_generated`・`commits_in_range` など）は、定義元・再エクスポートした `gitfacts`・使う側のどれにも当たるので変えなくてよい。`gitfacts` を直に指す 17 件は定義元へ向け直す。`test_git_facts.py` の `setattr(gitfacts, "sh", ...)` 13 件は `github`（`check_run_result` は `_gh_api_get` の `sh` を呼ぶ）へ、`test_push_credential_fallback.py` の `_CREDENTIAL_LIB` 1 件と `gh_available` 3 件は `publish` へ向け直す（`gh_available` は `raising=False` なので、向け直さないと差し替えが効かないまま黙って通る）。あわせて `tests/conftest.py` の `_MODULES` に移す先の 6 本を足し、フィクスチャから取れるようにする。

`refactor_lib/github.py` の関数は、GitHub への呼び出しを `lib/gh_parts.py` の関数で行う（決定 16）。このモジュールが持つのは cross-refactoring が読む形への変換だけである。
