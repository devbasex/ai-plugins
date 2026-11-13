# #1142 の詳細設計: 止まった理由を種類で返し、分岐と例外の行き先をスクリプトが選ぶ

設計文書 [issue-1142-design-scripted-flow.md](issue-1142-design-scripted-flow.md) の「構成要素」と「入出力の契約」を、ファイルと関数の粒度へ下ろした文書である。書き方は `plugins/ndf/skills/design/references/detailed-design.md` にある。子 Issue の番号（SF1a・SF1b・SF2〜SF13。SF3 は #3153）は、設計文書の「子 Issue の案」の番号である。各行の「子」の列は、どの PR で直すかを表す。

**#3092（工程の進行表）は設計（PR #3171）だけが develop にあり、実装は PR #3191（OPEN）にある。** SF8 が触る 3 本は、2026-11-13 の時点でこの worktree にも develop にも無い。

| 項目 | 内容 |
| --- | --- |
| 無いファイル | `plugins/ndf/scripts/lib/progress_model.py`・`progress_run.py`・`plugins/ndf/scripts/progress.py` |
| 確かめた手段 | `git ls-tree -r --name-only origin/develop \| grep progress`。出たのは `progress-record.sh` と `supervise_lib/progress_watch.py` ほかで、3 本は出ない |
| SF8 の関数名の出所 | #3191 の詳細設計（`issues/issue-3092-design-detail.md`。`gh pr diff 3191` で読んだ）の名前。`progress_model.next_action`・`progress_run.run_next`・`progress_run.sprint_workflow`・`progress_run.act_plan`・`progress.cmd_next` |
| 後で行うこと | #3191 のマージの後に、実装の名前と突き合わせる |

この文書で使う語と、コードの識別子の対応:

| 語 | 識別子 |
| --- | --- |
| 止まった理由の表 | `plugins/ndf/contracts/causes.json` と、その読み手 `lib/causes.py` |
| 止まった理由（の種類） | 結果 JSON の `cause`（`cause.kind`） |
| 上限の宣言 | `plugins/ndf/contracts/limits.json` と、その読み手 `lib/limits.py` |
| 行き先 | `causes.Route`（`to` が `retry`・`step`・`end`・`stop`・`human` のどれか） |
| 判断の呼び出し | プランの `type: judge` のステップと、その実体 `supervise_lib/judge_call.py` |
| 実行の核 | `lib/run_core.py`（エントリポイントの本体を呼び、結果を確かめて出す層） |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設。テンプレートの 11 本とSF1b の 127 本は表の下にまとめる）:

```text
plugins/ndf/
├── contracts/
│   ├── causes.json *                    # 止まった理由の表（SF1a）
│   ├── limits.json                      # cause.<種類> の再試行の方針（SF1a）
│   ├── index.md
│   ├── forms/judge-answer.json *        # 判断の呼び出しの答えのスキーマ（SF6）
│   ├── forms/slow-answer.json *         # 遅れの見張りの答えのスキーマ（SF6）
│   └── entrypoints/
│       ├── plugins__ndf__scripts__ci-watch.py.json
│       ├── plugins__ndf__scripts__supervise.py.json
│       └── plugins__ndf__scripts__preflight.py.json *
├── agents/{worker.md, supervisor.md}
├── skills/development-workflow/{SKILL.md, references/*.md}
└── scripts/
    ├── lib/{causes.py *, step_result.py, step-result.sh, proc.py, exit_codes.py, run_core.py, durable.py, ci_watch.py, README.md, ...}
    ├── supervise_lib/{routes.py *, judge_call.py *, engine.py, flow.py, plan.py, limits.py, rerun.py, state.py, steps.py, pr.py, worker_steps.py, claude.py, slow.py, queue.py, commands.py, sprint.py, テンプレートの 11 本}
    ├── merged_lib/{checks.py, merge.py}
    ├── {ci-watch.py, supervise.py, preflight.py *, parallel.py, sprint-state.py, ...}
    └── tests/{test_causes.py *, test_routes.py *, test_plan_routes.py *, test_judge_call.py *, ...}
scripts/
├── check-ndf-contracts.py
├── contracts_lib/causes_decl.py *
└── tests/test_causes_decl.py *
```

依存の向きは一方向である。`lib/causes.py` は標準ライブラリと `lib/limits.py` だけを読み、`lib/step_result.py`・`lib/proc.py`・`lib/run_core.py` がそれを読む。`supervise_lib/routes.py` は `lib/causes.py` を読み、`supervise_lib/engine.py` が `routes` を読む。進行表（#3092 の `lib/progress_model.py`）も `lib/causes.py` だけを読み、`supervise_lib` を読まない。`lib/causes.py` は `step_result` も `supervise_lib` も読まないため、輪はできない。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/contracts/causes.json` | 新設 | 種類ごとの意味・標準の誤りの型・終了コード・既定の行き先・再試行の方針の名前（SF1a） | 無し |
| `plugins/ndf/scripts/lib/causes.py` | 新設 | 止まった理由の表の唯一の読み手と、行き先を選ぶ純粋な関数 `pick`（SF1a） | `lib/limits.py`・`causes.json` |
| `plugins/ndf/contracts/limits.json` | 変更 | 方針 `cause.ci-pending`・`cause.infra-wait`・`cause.usage-limit` を足す（SF1a） | 無し |
| `scripts/contracts_lib/causes_decl.py` | 新設 | 止まった理由の表の形と、コードが書く種類の文字列の照合（SF1a） | `lib/causes.py` |
| `scripts/check-ndf-contracts.py` | 変更 | `causes_decl.violations` を呼び、契約の一覧へ表を描く（SF1a） | `contracts_lib/causes_decl.py` |
| `plugins/ndf/contracts/index.md` | 変更 | 「止まった理由の表」の表を足す（生成物。SF1a） | `check-ndf-contracts.py` |
| `plugins/ndf/scripts/lib/README.md` | 変更 | 結果 JSON の `cause`（SF1b）と `routes`（SF13）、止まった理由の表の節（SF1a） | 無し |
| `plugins/ndf/scripts/lib/step_result.py` | 変更 | `cause` を必須にし、`stopped` の終了コードを種類から引く（SF1b）。`routes` と `gate` を足す（SF13） | `lib/causes.py` |
| `plugins/ndf/scripts/lib/step-result.sh` | 変更 | `sr_emit` が `--cause` と `--route` を受ける（SF1b・SF13） | `lib/step_result.py` |
| `plugins/ndf/scripts/lib/proc.py` | 変更 | `StepError` が種類を必須で受け、終了コードを種類から引く（SF1b） | `lib/causes.py` |
| `plugins/ndf/scripts/lib/exit_codes.py` | 変更 | 3 の説明を「前提が無い・正常な否定の結果。種類で分ける」へ直す（SF1b） | 無し |
| `plugins/ndf/scripts/lib/run_core.py` | 変更 | 核が組む止まりに種類を付ける（SF1b） | `lib/step_result.py`・`lib/causes.py` |
| `plugins/ndf/scripts/lib/deps.py` | 変更 | 依存の欠けの止まりを種類 `deps_missing` で `exit_early` へ渡す（SF1b。`exit_early` の今の唯一の呼び手で、`stopped` を書かないため「SF1b の移す先」の数え方に入らない） | `lib/run_core.py` |
| `plugins/ndf/agents/worker.md` | 変更 | 結果ファイルの `stopped`・`gate` の書き方に `--cause` を足す（SF1b） | 無し |
| `plugins/ndf/scripts/supervise_lib/routes.py` | 新設 | ステップの止まりから cause を決め、`causes.pick` で行き先を返す。プランの検査（SF2） | `lib/causes.py`・`lib/run_core.py` の契約の引き方 |
| `plugins/ndf/scripts/supervise_lib/engine.py` | 変更 | 失敗の分岐を `routes` へ置き換え、`gh_limit_wait` と `_next_on_exit` を消す（SF2）。判断の答えの扱い（SF6） | `supervise_lib/routes.py` |
| `plugins/ndf/scripts/supervise_lib/flow.py` | 変更 | 打ち直しの前に耐久の待ちを挟む（SF2） | `lib/durable.py` |
| `plugins/ndf/scripts/lib/durable.py` | 変更 | `DBOS.sleep` を呼ぶ関数 `sleep` を足す（SF2） | `dbos` |
| `plugins/ndf/scripts/supervise_lib/plan.py` | 変更 | `on_exit_error`・`fail_kind_matches`・`ON_FAIL_ONLY`・`PLAN_KEY_ALIASES`・`normalize_plan` を消し、説明と例を `on_cause` にする（SF2） | 無し |
| `plugins/ndf/scripts/supervise_lib/limits.py` | 変更 | 遷移の照合を `on_cause` の行き先で数える（SF2） | 無し |
| `plugins/ndf/scripts/supervise_lib/rerun.py` | 変更 | 同じ原因の鍵を種類と対象から作る（SF2） | `lib/causes.py` |
| `plugins/ndf/scripts/supervise_lib/state.py` | 変更 | 打ち直しの回数 `retry_counts` を持ち運ぶ（SF2）。attention に種類を付ける（SF7） | 無し |
| `plugins/ndf/scripts/supervise_lib/steps.py` | 変更 | `RunStep.is_skip` を消す（SF2）。`JudgeStep` を判断の呼び出しへ、`parse_decision` を消す（SF6） | `supervise_lib/judge_call.py` |
| `plugins/ndf/scripts/supervise_lib/pr.py` | 変更 | 節の欠けを `check_failed`、push の拒否を `push_rejected` で残す（SF2） | `lib/causes.py` |
| `plugins/ndf/scripts/supervise_lib/worker_steps.py` | 変更 | worker の結果ファイルの `cause` をコピーし、無ければ `work_stopped` を付ける（SF2） | `lib/causes.py` |
| `plugins/ndf/skills/cross-refactoring/SKILL.md` | 変更 | 終了コード 6 の行の `on_exit` を `on_cause` の `incomplete` へ（SF2） | 無し |
| `plugins/ndf/scripts/lib/ci_watch.py` | 変更 | 分割ジョブの照合・マージの結果の引き継ぎ（SF3）。種類を返す（SF4） | 無し |
| `plugins/ndf/scripts/ci-watch.py` | 変更 | マージの後の未完了を読み直す（SF3）。種類で返す（SF4） | `lib/ci_watch.py` |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__ci-watch.py.json` | 変更 | 終了コードの意味（SF3・SF1b） | 無し |
| `plugins/ndf/scripts/merged_lib/checks.py` | 変更 | 落ちたチェックをテストとテスト以外に分ける（SF4） | `lib/test_strategy.py` |
| `plugins/ndf/scripts/merged_lib/merge.py` | 変更 | マージの拒否を `not_ci`・`base_conflict` に分ける（SF4）。承認ゲートの `routes`（SF13） | `lib/step_result.py` |
| `plugins/ndf/agents/supervisor.md` | 変更 | CI を `gh pr checks` で手で読む手順を外す（SF4） | 無し |
| `plugins/ndf/scripts/supervise_lib/commands.py` | 変更 | `cmd_design_gate` を足す（SF5）。`routes` を付ける（SF13） | `lib/step_result.py` |
| `plugins/ndf/scripts/supervise.py` | 変更 | サブコマンド `design-gate` を足す（SF5）。`judge_call` が使う `jsonschema` は、data の DC1 が最初の `deps.require(...)` に足す（SF6 の前の波） | `supervise_lib/commands.py`・`lib/deps.py` |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__supervise.py.json` | 変更 | `design-gate` の引数（SF5） | 無し |
| `plugins/ndf/scripts/supervise_lib/judge_call.py` | 新設 | 判断の呼び出し: スキーマを組み、claude を呼び、答えを確かめて 1 回だけ打ち直す（SF6） | `supervise_lib/claude.py`・`jsonschema` |
| `plugins/ndf/scripts/supervise_lib/claude.py` | 変更 | `claude -p --json-schema` を渡せるようにする（SF6） | 無し |
| `plugins/ndf/scripts/supervise_lib/slow.py` | 変更 | 遅れの判定を判断の呼び出しで行う（SF6） | `supervise_lib/judge_call.py` |
| `plugins/ndf/contracts/forms/judge-answer.json` | 新設 | 選択肢のモードの答えの形（SF6） | 無し |
| `plugins/ndf/contracts/forms/slow-answer.json` | 新設 | 遅れの判定の答えの形（SF6） | 無し |
| `plugins/ndf/scripts/supervise_lib/queue.py` | 変更 | wait の attention と時間切れに種類と打てる `next` を載せる（SF7） | `lib/step_result.py` |
| `plugins/ndf/skills/development-workflow/references/waiting.md` | 変更 | 「終わり方」の表を `next` を打つ 1 行へ（SF7）。承認の後に `routes` を打つ（SF13） | 無し |
| `plugins/ndf/scripts/lib/progress_model.py` | 変更（#3191 が新設） | 工程の失敗の行き先を選ぶ（SF8） | `lib/causes.py` |
| `plugins/ndf/scripts/lib/progress_run.py` | 変更（#3191 が新設） | 工程の失敗の事象に `cause` を載せ、打ち直しを耐久の待ちで済ませ、`run_next` の結果に `cause` と `routes` を載せる（SF8） | `lib/progress_model.py` |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md` | 変更 | conductor の表を `routes` を打つ 1 行へ（SF8） | 無し |
| `plugins/ndf/scripts/parallel.py` | 変更 | `reply` が種類を受ける（SF9）。承認ゲートの `routes`（SF13） | `lib/step_result.py` |
| `plugins/ndf/scripts/lib/parallel_cli.py` | 変更 | wait の結果の行き先を `next` に載せる（SF9） | `parallel.py` の `reply` |
| `plugins/ndf/scripts/lib/parallel_status.py` | 変更 | 失われた計画の判定を種類で返す（SF9） | 無し |
| `plugins/ndf/scripts/lib/parallel_answer.py` | 変更 | リリースの差し戻しの候補を返す（SF9） | 無し |
| `plugins/ndf/skills/development-workflow/references/parallel.md` | 変更 | 手順 3 の表を `next` を打つ 1 行へ（SF9） | 無し |
| `plugins/ndf/scripts/preflight.py` | 新設 | 手順 0 の 3 つの確かめを 1 回で打つ（SF10） | `lib/step_result.py` |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__preflight.py.json` | 新設 | `preflight.py` の契約（SF10） | 無し |
| `plugins/ndf/skills/development-workflow/SKILL.md` | 変更 | 手順 0 を `preflight.py` の 1 行へ（SF10） | 無し |
| `plugins/ndf/scripts/lib/sprint_loop_cli.py` | 変更 | 断りと止まりに種類と `next` を載せる（SF11） | `lib/step_result.py` |
| `plugins/ndf/skills/development-workflow/references/loop.md` | 変更 | 手順の表を `next` を打つ 1 行へ（SF11） | 無し |
| `plugins/ndf/scripts/hook_lib/token_guard.py` | 変更 | 文脈の上限の案内が 1 つのコマンドを返す（SF12） | 無し |
| `plugins/ndf/scripts/lib/transcript_agents.py` | 変更 | 中断の点検が相手ごとに次の 1 手を返す（SF12） | 無し |
| `plugins/ndf/skills/development-workflow/references/interrupt-resume.md` | 変更 | 点検の後の表を `next` を打つ形へ（SF12） | 無し |
| `plugins/ndf/scripts/sprint-state.py` | 変更 | 承認ゲートの答えの記録が `routes` を返す（SF13） | `lib/step_result.py` |
| `plugins/ndf/scripts/supervise_lib/sprint.py` | 変更 | `--pace` の断りが `normal` の起動を `routes` で返す（SF13） | `lib/step_result.py` |
| `plugins/ndf/scripts/lib/ask_policy.py` | 変更 | 問いの区分を結果に載せる（SF13） | 無し |
| `plugins/ndf/skills/development-workflow/references/asking.md` | 変更 | 問いの後に `routes` を打つ（SF13） | 無し |
| `plugins/ndf/skills/development-workflow/references/pace.md` | 変更 | 手順の表 4 を `routes` を打つ 1 行へ（SF13） | 無し |

表の外にまとめたファイル:

| まとまり | 置き場 | ファイル | 補足 |
| --- | --- | --- | --- |
| テンプレートの 11 本（SF2） | `plugins/ndf/scripts/supervise_lib/` | `verify_steps.py`・`templates.py`・`procedures.py`・`paths.py`・`release_templates.py`・`delivery_templates.py`・`design_steps.py`・`sprint_close_steps.py`・`sprint_waves.py`・`sprint_lane_close.py`・`sprint_bundles.py` | 書き換えの対応は「処理の手順」の `テンプレートの書き換え` の行にある |
| 承認ゲートを返す 7 本（SF13） | `plugins/ndf/scripts/` | `handoff.py`・`merged-steps.py`（`merged_lib/merge.py` の `gate_stop`）・`mvv-gate.py`・`parallel.py`・`revise-note.py`・`sprint-close.py`・`supervise.py`（`supervise_lib/commands.py`） | 契約の一覧の `exit` に 10〜19 を持つものを、`plugins/ndf/contracts/entrypoints/*.json` から数えた（7 本。20〜29 を持つものは 0 本） |
| SF1b の 127 本 | 下の「SF1b の移す先」 | — | — |

### SF1b の移す先

`stopped` を出すか `StepError` を投げるファイルは 127 本ある（テストと実験版を除く）。

| 項目 | 内容 |
| --- | --- |
| 数えた手段（2026-11-13） | `grep -rlE '"stopped"\|StepError\(\|core_stopped\(' plugins/ndf --include=*.py` と `grep -rl sr_emit plugins/ndf --include=*.sh` の和から、`/tests/`・`/experimental/` を除いた |
| 値の意味 | 上限の値である。`stopped` を計画やレーンの状態の語として使うだけのファイル（例: `relay_lib/hub.py`）も含む |
| 直す箇所 | `result(..., "stopped", ...)`・`StepError(`・`core_stopped(`・`sr_emit --status stopped` の箇所だけ |

| 置き場 | 本数 | ファイル |
| --- | ---: | --- |
| `plugins/ndf/scripts/` | 35 | `acceptance-check.py`・`ask.py`・`behavior-check.py`・`check-trigger.py`・`ci-watch.py`・`context-policy.py`・`design-match.py`・`design-survey.py`・`doc-lint.py`・`estimate.py`・`gate-rejections.py`・`glossary.py`・`handoff.py`・`issue-file.py`・`issue-items.py`・`merged-steps.py`・`mode-materials.py`・`parallel.py`・`plan-to-spec-steps.py`・`progress-record.sh`・`project-decl.py`・`project-mvv.py`・`projects-sync.sh`・`pr-steps.py`・`release-steps.py`・`release-verification-steps.py`・`revise-note.py`・`spec-copy.py`・`sprint-close.py`・`sprint-state.py`・`stage-skills.py`・`state-home.py`・`supervise.py`・`test-run.py`・`training-optout.py` |
| `plugins/ndf/scripts/lib/` | 37 | `design_docs.py`・`design_pr_merge.py`・`drive_pause.py`・`gh_parts.py`・`gh_pr_info.py`・`gh_pr_status.py`・`glossary_lookup.py`・`json_pick.py`・`lease_stage.py`・`legacy_values.py`・`loop_drive.py`・`merged_range.py`・`parallel_cli.py`・`parallel_end.py`・`parallel_external_cli.py`・`parallel_guard.py`・`parallel_hold_cli.py`・`parallel_lane_cli.py`・`parallel_lane.py`・`parallel_launch.py`・`parallel_machine.py`・`parallel_observe.py`・`parallel_route.py`・`parallel_run.py`・`proc.py`・`run_core.py`・`runner_issue_request.py`・`sprint_loop_cli.py`・`sprint_loop_gate.py`・`sprint_loop.py`・`sprint_loop_record.py`・`sprint_loop_select.py`・`state_get.py`・`step_read.py`・`step_result.py`・`step-result.sh`・`tool_paths.py` |
| `plugins/ndf/scripts/merged_lib/` | 5 | `base_sync.py`・`checks.py`・`cleanup_metrics.py`・`main_lock.py`・`merge.py` |
| `plugins/ndf/scripts/release_lib/` | 12 | `approval.py`・`bump.py`・`changed.py`・`deploy.py`・`included.py`・`instructions.py`・`materials.py`・`names.py`・`promotion.py`・`publish.py`・`record.py`・`version_merge.py` |
| `plugins/ndf/scripts/supervise_lib/` | 13 | `advance.py`・`commands.py`・`design_merge.py`・`design_stage.py`・`engine.py`・`queue.py`・`queue_resume.py`・`sprint_bundles.py`・`sprint_inputs.py`・`sprint.py`・`state.py`・`templates.py`・`worker_steps.py` |
| `plugins/ndf/scripts/` のほかの下位 | 7 | `project_lib/mvv_collect.py`・`project_lib/mvv_propose_io.py`・`pr_steps_lib/gh_pr.py`・`relay_lib/hub_client.py`・`relay_lib/hub.py`・`relay_lib/__init__.py`・`sprint_close_lib/collect.py` |
| `plugins/ndf/skills/` | 18 | `backlog-refinement/scripts/` の `upkeep_actions.py`・`upkeep_depth.py`・`upkeep_gh.py`・`upkeep_overhaul.py`・`upkeep_owner.py`・`upkeep.py`・`upkeep_rank_cmd.py`・`upkeep_rank_inputs.py`・`upkeep_report.py`・`upkeep_research.py`、`cross-refactoring/scripts/refactor_lib/` の `commands/report.py`・`results.py`・`template_check.py`、`cross-review/scripts/drive.py`、`external-ai/scripts/external-ai.py`、`fix/scripts/fix-steps.py`、`google-workspace/scripts/gws_common.py`、`pr-review/scripts/pr-review-steps.py` |

種類の割り当ての規則（レビューはこの規則との突き合わせで見る）:

| 今の書き方 | 付ける種類 |
| --- | --- |
| `StepError(msg, EXIT_UNREADABLE)`・`code=2` の `stopped` | `invalid` |
| `StepError(msg, EXIT_PRECONDITION)`・`code=3` の `stopped` で、前提の欠け（宣言・認証・ファイル・gh が無い） | `precondition` |
| `code=3` の `stopped` で、立たない・変更なし・飛ばしてよい（`check-trigger.py`・`glossary_lookup.py`・`ci-watch.py` の「CI 以外」を除く） | `not_applicable` |
| `EXIT_BASE_CONFLICT`（4）・`EXIT_CD_FAILED`（5）・`EXIT_DEPS_MISSING`（69）・`EXIT_INFRA_WAIT`（75） | `base_conflict`・`deploy_failed`・`deps_missing`・`infra_wait` |
| `gaps` を持つ `stopped`（6） | `incomplete` |
| テストのコマンドの失敗（`test-run.py`・`acceptance-check.py`・`behavior-check.py`） | `test_failed` |
| 検査の違反（`doc-lint.py`・`design-survey.py`・`design-match.py`・`glossary.py`・`project-decl.py check`・`check-trigger.py` の違反） | `check_failed` |
| 改修・収束の回数の上限（`ci-watch.py` の上限・`drive.py` の上限） | `fix_limit` |
| 不変条件で断った（`sprint-state.py`・`parallel_guard.py`・`parallel_lane.py` の断り） | `refused` |
| 待ちの上限で返った（`supervise.py wait`・`parallel.py wait` の時間切れ） | `wait_timeout` |
| それ以外の `StepError(msg)`（終了コード 1。外部のコマンドの失敗を含む） | `unknown` |

## クラスと関数の構造

型の関係は設計文書の「構成要素」の図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/causes.py` | モジュール `causes`・型 `CauseRow`・`Route`・`CausesError` | `table`・`use_contract`・`validate`・`cause`・`errors`・`exit_code`・`policy_of`・`pick`・`backoff`（すべて新設） |
| `scripts/contracts_lib/causes_decl.py` | モジュール `causes_decl` | `violations`・`index_rows`（新設） |
| `scripts/check-ndf-contracts.py` | `Checker` | 契約の検査の並びに `causes_decl.violations` を足す（変更。シグネチャは変えない） |
| `plugins/ndf/scripts/lib/step_result.py` | モジュール `step_result` | `result`・`validate_result`・`code_for`・`emit`・`gh_json`・`main_with`・`cli_parser`・`_cli_result`（変更）、`gate`（新設。SF13） |
| `plugins/ndf/scripts/lib/proc.py` | `StepError` | `__init__`（変更）・`cause`（新設のプロパティ）。`run`（変更） |
| `plugins/ndf/scripts/lib/run_core.py` | モジュール `run_core` | `core_stopped`・`exit_early`・`_from_code`（変更） |
| `plugins/ndf/scripts/lib/deps.py` | モジュール `deps` | `_stop`（変更） |
| `plugins/ndf/scripts/lib/step-result.sh` | シェルの関数 | `sr_emit`（変更） |
| `plugins/ndf/scripts/supervise_lib/routes.py` | モジュール `routes` | `step_cause`・`retry_policy`・`route_of`・`plan_errors`（新設） |
| `plugins/ndf/scripts/supervise_lib/engine.py` | `Engine` | `_route_failure`（新設）、`_next_after_step`・`_run_step`・`setup`・`_next_after_judge`（変更）、`_next_on_exit`・`gh_limit_wait`（削除） |
| `plugins/ndf/scripts/supervise_lib/flow.py` | モジュール `flow` | `_plan_steps`（変更） |
| `plugins/ndf/scripts/lib/durable.py` | モジュール `durable` | `sleep`（新設） |
| `plugins/ndf/scripts/supervise_lib/plan.py` | モジュール `plan` | `on_exit_error`・`fail_kind_matches`・`normalize_plan`（削除）。`EXAMPLE` の書き直し |
| `plugins/ndf/scripts/supervise_lib/limits.py` | モジュール `limits` | `_edge_targets`・`default_next`（変更） |
| `plugins/ndf/scripts/supervise_lib/rerun.py` | モジュール `rerun` | `key_of`・`count_failure`（変更） |
| `plugins/ndf/scripts/supervise_lib/state.py` | `RunState` | `retry_counts`（属性の新設）、`attention`（変更。SF7） |
| `plugins/ndf/scripts/supervise_lib/steps.py` | `RunStep`・`JudgeStep` | `RunStep.is_skip`・`parse_decision`（削除）、`JudgeStep.execute`（変更。SF6） |
| `plugins/ndf/scripts/supervise_lib/pr.py` | `PrStep` | `execute`・`_push`（変更） |
| `plugins/ndf/scripts/supervise_lib/worker_steps.py` | `WorkStep` | `execute`（変更） |
| テンプレートの 11 本 | 各モジュール | `merge_steps`・`ci_watch_steps`・`pre_test_steps`・`lint_whole_steps`・`base_sync_steps`・`push_fix_steps`・`design_diff_on_pr`・`gate_steps` ほか、ステップの辞書を返す関数（変更。キーだけを書き換え、シグネチャは変えない） |
| `plugins/ndf/scripts/lib/ci_watch.py` | モジュール `ci_watch` | `is_test_check`・`decide_exit`（変更。SF3）、`merge_failures`（新設。SF3）、`peek_cause`（新設。SF4） |
| `plugins/ndf/scripts/ci-watch.py` | モジュール | `cmd_peek`（変更）、`_settle_after_merge`（新設。SF3） |
| `plugins/ndf/scripts/merged_lib/checks.py` | `GreenWatch` | `_stop_failed`（変更。SF4） |
| `plugins/ndf/scripts/merged_lib/merge.py` | モジュール `merge` | `_merge_pr`（変更。SF4）、`gate_stop`（変更。SF13） |
| `plugins/ndf/scripts/supervise_lib/verify_steps.py` | モジュール | `judge_step`（変更。SF4・SF6） |
| `plugins/ndf/scripts/supervise_lib/commands.py` | モジュール `commands` | `cmd_design_gate`（新設。SF5） |
| `plugins/ndf/scripts/supervise_lib/design_steps.py` | モジュール | `gate_steps`（変更。SF5） |
| `plugins/ndf/scripts/supervise_lib/judge_call.py` | モジュール `judge_call`・型 `JudgeAnswer` | `answer_schema`・`check_answer`・`ask`（新設） |
| `plugins/ndf/scripts/supervise_lib/claude.py` | モジュール | `claude_cmd`（変更） |
| `plugins/ndf/scripts/supervise_lib/slow.py` | `SlowWatch` | `judge_slow`（変更） |
| `plugins/ndf/scripts/supervise_lib/queue.py` | モジュール `queue` | `_attention_response`・`_timeout_response`（変更） |
| `plugins/ndf/scripts/lib/progress_model.py` | モジュール（#3191 が新設） | `route_failed`（新設） |
| `plugins/ndf/scripts/lib/progress_run.py` | モジュール（#3191 が新設） | `sprint_workflow`・`run_next`（変更） |
| `plugins/ndf/scripts/progress.py` | モジュール（#3191 が新設） | `cmd_next`（変えない。`run_next` の戻りをそのまま返す） |
| `plugins/ndf/scripts/parallel.py` | モジュール | `reply`（変更） |
| `plugins/ndf/scripts/lib/parallel_cli.py` | モジュール | `wait_plan`（変更） |
| `plugins/ndf/scripts/lib/parallel_answer.py` | モジュール | `release_rejectable`（新設） |
| `plugins/ndf/scripts/preflight.py` | モジュール | `cmd_check`（新設） |
| `plugins/ndf/scripts/lib/sprint_loop_cli.py` | モジュール | `run_loop_command`（変更） |
| `plugins/ndf/scripts/hook_lib/token_guard.py` | モジュール | `_context_deny`（変更） |
| `plugins/ndf/scripts/lib/transcript_agents.py` | モジュール | `interrupted`（変更） |
| `plugins/ndf/scripts/sprint-state.py` | モジュール | `cmd_gate`（変更） |
| `plugins/ndf/scripts/supervise_lib/sprint.py` | モジュール | `_new_refusal`（変更） |
| `plugins/ndf/scripts/lib/ask_policy.py` | モジュール | `judge_ask`（変更） |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `causes.table` | `plugins/ndf/scripts/lib/causes.py` | 新設 | `table() -> dict[str, CauseRow]` | 止まった理由の表を 1 プロセスに 1 回読み、`validate` を通した行を種類ごとに返す | 無し | 返る表は IS10・IS11 を満たす。読んだ表は置き場ごとにキャッシュする | ファイル（`contracts/causes.json` を読む） |
| `causes.use_contract` | `plugins/ndf/scripts/lib/causes.py` | 新設 | `use_contract(path: str \| Path \| None) -> None` | 読み込み先を差し替える（テストが使う）。`None` でプラグインの表へ戻す | 無し | 次の `table` が `path` を読む | 無し |
| `causes.validate` | `plugins/ndf/scripts/lib/causes.py` | 新設 | `validate(raw: object, where: str) -> dict[str, CauseRow]` | 表の版・行のキー・標準の誤りの型（8 つ）・終了コード（`exit_codes.STOPPED_CODES`）・既定の行き先の語・方針の名前を確かめる | 無し | 正しければ種類ごとの行。誤りは `CausesError`（`where` と行の種類を含む） | 無し |
| `causes.cause` | `plugins/ndf/scripts/lib/causes.py` | 新設 | `cause(kind: str, detail: str, *, instance: str \| None = None, retry_after: float \| None = None) -> dict` | 結果 JSON の `cause` を組む | `kind` は表の値・`detail` は空でない | `errors` が空の辞書を返す。`instance` と `retry_after` は渡したときだけ置く | ファイル（初回だけ `table` が表を読む） |
| `causes.errors` | `plugins/ndf/scripts/lib/causes.py` | 新設 | `errors(value: object) -> list[str]` | `cause` の値の形の誤りを並べる（IS1） | 無し | 空なら `kind` が表にあり、`detail` が空でない文字列で、任意のキーの型が合う | ファイル（初回だけ `table` が表を読む） |
| `causes.exit_code` | `plugins/ndf/scripts/lib/causes.py` | 新設 | `exit_code(kind: str) -> int` | 種類の終了コードを返す（IS11） | `kind` は表の値 | 返る値は `STOPPED_CODES` にある | ファイル（初回だけ `table` が表を読む） |
| `causes.policy_of` | `plugins/ndf/scripts/lib/causes.py` | 新設 | `policy_of(kind: str) -> limits.Policy \| None` | 種類の再試行の方針を上限の宣言から引く | `kind` は表の値 | 既定の行き先が `retry` でない種類は `None` | ファイル（初回だけ `limits.json` を読む） |
| `causes.pick` | `plugins/ndf/scripts/lib/causes.py` | 新設 | `pick(kind: str, on_cause: Mapping[str, str], on_fail: str \| None, used: int, policy: limits.Policy \| None, retry_after: float \| None, cap: float \| None) -> Route` | IS4 の順で行き先を 1 つ選ぶ。文を受けない（IS7） | `kind` は表の値・`used >= 0` | `Route.to` は `retry`・`step`・`end`・`stop`・`human` のどれか。`retry` なら `wait > 0` で `used < policy.attempts` | 無し |
| `causes.backoff` | `plugins/ndf/scripts/lib/causes.py` | 新設 | `backoff(policy: limits.Policy, used: int, retry_after: float \| None, cap: float \| None) -> float` | 次の待ちの秒を決める | `used >= 0` | `retry_after` があればその値、無ければ `interval × factor^used`。どちらも `max_interval` と `cap` の小さい方で頭打ち | 無し |
| `causes_decl.violations` | `scripts/contracts_lib/causes_decl.py` | 新設 | `violations(root: Path, files: list[str]) -> list[tuple[str, str, str, str]]` | 表の形を読み手の `validate` で確かめ、方針の名前が上限の宣言にあり、コードが文字列で書く種類が表にあるかを確かめる | `files` はコードのパスの並び（`limits_decl.code_files` と同じ） | （規則, パス, 名前, 理由）の並び。空なら違反なし | ファイル（表・上限の宣言・コードを読む） |
| `causes_decl.index_rows` | `scripts/contracts_lib/causes_decl.py` | 新設 | `index_rows(root: Path) -> list[str]` | 契約の一覧の逆引きへ描く表の行を返す | 表が `validate` を通る | 種類・意味・終了コード・既定の行き先・方針の Markdown の行 | ファイル（表を読む） |
| `step_result.result` | `plugins/ndf/scripts/lib/step_result.py` | 変更 | `result(tool, status, summary, items=None, metrics=None, presentation_path=None, next=None, *, gaps=None, process=None, decisions=None, cause: dict \| None = None, routes: dict \| None = None) -> dict` | 結果を組む。`cause`（SF1b）と `routes`（SF13）は渡したときだけ置く | 無し | 渡した値をそのまま置く（形は `emit` が確かめる） | 無し |
| `step_result.validate_result` | `plugins/ndf/scripts/lib/step_result.py` | 変更 | `validate_result(obj, code: int \| None = None) -> list[str]` | 形の誤りに IS1・IS2・IS11・IS12（SF1b）と IS9（SF13）を足す | 無し | 空なら、`stopped` は `cause` を持ち、終了コードが種類のものと同じで、`gaps` と `incomplete` が揃う。10〜19 は `routes` を持つ | ファイル（初回だけ `causes.table` が表を読む） |
| `step_result.code_for` | `plugins/ndf/scripts/lib/step_result.py` | 変更 | `code_for(obj) -> int \| None` | 既定の終了コードを決める。`stopped` は `cause.kind` の終了コード | 無し | `ok` は 0、`gate` は 10、`stopped` は種類の終了コード。`cause` が無い・読めなければ `None` | ファイル（初回だけ `causes.table` が表を読む） |
| `step_result.emit` | `plugins/ndf/scripts/lib/step_result.py` | 変更 | `emit(obj: dict, code: int \| None = None, stream=None) -> NoReturn` | 結果を 1 行で出して終える。`stopped` で渡した `code` は種類の終了コードと同じでなければ形の誤り | 無し | 形が正しければ 1 行を出し、その終了コードで終える。誤りは標準エラーへ出して 2 | 標準出力・標準エラー・プロセスの終了 |
| `step_result.gh_json` | `plugins/ndf/scripts/lib/step_result.py` | 変更 | `gh_json(root, args, what) -> object` | gh を打って JSON を読み、失敗を種類つきの `StepError` にする | 無し | 成功なら読んだ値。gh が無い → `precondition`、API の上限 → `infra_wait`（`retry_after` は `gh_quota.rate_limits` の reset から）、出力を読めない → `invalid`、ほか → `unknown` | 外部コマンド（`gh`）・ネットワーク |
| `step_result.main_with` | `plugins/ndf/scripts/lib/step_result.py` | 変更 | `main_with(ap, tool_of, argv=None)` | `StepError` を、その `cause` を持つ `stopped` の結果にして終える | 無し | `StepError` の種類と終了コードの結果を出す | 標準出力・プロセスの終了 |
| `step_result.cli_parser` | `plugins/ndf/scripts/lib/step_result.py` | 変更 | `cli_parser() -> argparse.ArgumentParser` | `emit` に `--cause KIND DETAIL`・`--cause-instance`・`--retry-after`（SF1b）と `--route ANSWER CMD`（SF13）を足す | 無し | `--code` は `gate` のときだけ受ける | 無し |
| `step_result.gate` | `plugins/ndf/scripts/lib/step_result.py` | 新設 | `gate(tool: str, summary: str, routes: dict[str, str \| None], *, presentation_path=None, items=None, metrics=None) -> dict` | 承認ゲートの結果を `routes` つきで組む（SF13） | `routes` は `approved` と `rejected` のキーを持つ | `status` が `gate` で `routes` を持つ辞書 | 無し |
| `StepError.__init__` | `plugins/ndf/scripts/lib/proc.py` | 変更 | `__init__(self, msg: str, kind: str, *, instance: str \| None = None, retry_after: float \| None = None)` | 手順の失敗を種類つきで表す。`code` は種類から引く | `kind` は表の値 | `self.cause` は `causes.cause` の値、`self.code` は `causes.exit_code(kind)` | ファイル（初回だけ `causes.table` が表を読む） |
| `proc.run` | `plugins/ndf/scripts/lib/proc.py` | 変更 | `run(cmd, cwd=None, check=True, env=None, timeout=None, input=None) -> CompletedProcess` | `check` で 0 以外を `StepError(..., "unknown", instance=<コマンドの先頭>)` にする | 無し | 成功なら結果 | 外部コマンド |
| `run_core.core_stopped` | `plugins/ndf/scripts/lib/run_core.py` | 変更 | `core_stopped(entry_id: str, kind: str, gap_kind: str, name: str, summary: str) -> tuple[dict, int]` | 核が組む止まりに種類を付ける。例外は `unknown`、結果の形の誤りは `invalid`、打ち切りは `interrupted` | `kind` は表の値 | 結果は `gaps` を持つため `incomplete` 以外の種類では `gaps` を置かない（IS12）。`gap_kind` は `metrics.core` へ移す | 無し |
| `run_core.exit_early` | `plugins/ndf/scripts/lib/run_core.py` | 変更 | `exit_early(summary: str, kind: str, gaps: list[dict] \| None = None) -> NoReturn` | 核に入る前の止まりを種類つきの結果にして終える | `kind` は表の値。`gaps` は `kind` が `incomplete` のときだけ渡す（IS12） | 終了コードは種類のもの。`gaps` を渡さなければ結果に `gaps` を置かない（今の既定の 1 要素は消す） | 標準出力・結果ファイル・プロセスの終了 |
| `deps._stop` | `plugins/ndf/scripts/lib/deps.py` | 変更 | `_stop(msg: str, groups: Sequence[str] = ()) -> NoReturn` | 依存の欠けを `exit_early(msg, "deps_missing")` で返す。欠けたグループ（無ければ `uv`）は `cause.detail` の文に書き、`gaps` には置かない（IS12） | 無し | 終了コードは `deps_missing` の 69。`run_core` を読めなければ今どおり結果を出さずに 69 で終える | 標準出力・標準エラー・プロセスの終了 |
| `run_core._from_code` | `plugins/ndf/scripts/lib/run_core.py` | 変更 | `_from_code(entry_id: str, code: int) -> tuple[dict, int]` | 結果 JSON を出さない本体の終わりを結果にする。`stopped` は `unknown` | 無し | 終了コードから種類を推さない。`stopped` の結果の終了コードは `unknown` の 1 | 無し |
| `sr_emit` | `plugins/ndf/scripts/lib/step-result.sh` | 変更 | `sr_emit --tool T --status S --summary X [--cause KIND DETAIL] [--route ANSWER CMD]...` | 溜めた値と種類を `step_result.py emit` へ渡す | `--status stopped` なら `--cause` を渡す | `emit` と同じ | 外部コマンド（`python3`）・標準出力・プロセスの終了 |
| `routes.step_cause` | `plugins/ndf/scripts/supervise_lib/routes.py` | 新設 | `step_cause(step: dict, cur: dict) -> dict` | 失敗したステップの `cause` を、ハンドラーが置いた `cur["cause"]` → 出力の最後の結果 JSON の `cause` → ステップの `exit_cause` → `unknown` の順で決める | `cur` は終わったステップの記録（`exit`・`text`） | `causes.errors` が空の辞書を返す。文の中身から種類を推さない | 無し |
| `routes.retry_policy` | `plugins/ndf/scripts/supervise_lib/routes.py` | 新設 | `retry_policy(kind: str, step: dict, decl: dict) -> limits.Policy \| None` | 上限の宣言 → `.ndf/supervise.json` の `causes.<種類>` → ステップの `retry.<種類>` の順に重ねた方針を返す | `decl` は読んだ `supervise.json`（無ければ空） | 打ち直さない種類は `None`。キーの名前は上限の宣言と同じ | 無し |
| `routes.route_of` | `plugins/ndf/scripts/supervise_lib/routes.py` | 新設 | `route_of(step: dict, cause: dict, used: int, decl: dict) -> causes.Route` | ステップの `on_cause`・`on_fail` と方針を `causes.pick` へ渡す | `cause` は `step_cause` の値 | `pick` の事後条件。`on_cause` の `retry` は種類の方針で打ち直す | 無し |
| `routes.plan_errors` | `plugins/ndf/scripts/supervise_lib/routes.py` | 新設 | `plan_errors(steps: list[dict]) -> str \| None` | IS3 を確かめる。`on_cause`・`exit_cause`・`retry` のキーの種類、`on_cause` の行き先、NDF のエントリポイントを打つステップの `exit_cause`、消したキー（`on_exit` ほか）の残り | 無し | 誤りの 1 文か `None` | ファイル（初回だけ表と契約の一覧を読む） |
| `Engine._route_failure` | `plugins/ndf/scripts/supervise_lib/engine.py` | 新設 | `_route_failure(self, sid: str, step: dict) -> tuple[str \| None, str \| None, str \| None]` | 失敗したステップの `cause` を記録に置き、`route_of` の行き先を（次のステップ, 結果, 理由）にする | ステップが失敗で終わった（`exit` が 0 でも承認ゲートでもない） | `retry` なら（同じ id, None, None）で `cur["retry_wait"]` に秒を置き `retry_counts` を 1 増やす。`human`・`stop` は（None, "止まった", 理由）で `cur["cause"]` を残す | ファイル（`progress.jsonl` へ `cause` の行） |
| `Engine._next_after_step` | `plugins/ndf/scripts/supervise_lib/engine.py` | 変更 | `_next_after_step(self, sid, step, ok) -> tuple[str \| None, str \| None, str \| None]` | 成功・承認ゲートの分岐を残し、失敗の分岐を `_route_failure` だけにする（`skip_to`・`on_exit`・`on_section_missing`・`on_fail_only` の分岐を消す） | 無し | 失敗の行き先は `_route_failure` の値 | ファイル（`progress.jsonl`） |
| `Engine._run_step` | `plugins/ndf/scripts/supervise_lib/engine.py` | 変更 | `_run_step(self, sid, step) -> tuple[str \| None, str \| None, str \| None]` | 差し込みの残りを `invalid`、利用上限を `usage_limit`、認証を `precondition` の `cause` で残す | 無し | 返す前に `cur["cause"]` がある（止まるとき） | ファイル（`progress.jsonl`）・外部コマンド |
| `Engine.setup` | `plugins/ndf/scripts/supervise_lib/engine.py` | 変更 | `setup(self, start) -> tuple[str, str] \| None` | 流す前の検査に `routes.plan_errors` を使う | 無し | 誤りがあれば（"止まった", 理由） | ファイル（状態ディレクトリ） |
| `flow._plan_steps` | `plugins/ndf/scripts/supervise_lib/flow.py` | 変更 | `_plan_steps(eng, prefix, start, resources, began) -> dict` | 前のステップの記録に `retry_wait` があれば、`durable.sleep` で待ってから同じステップを流す。打ち直しはステップの上限に数えない | 耐久ワークフローの中で呼ぶ | 待ちが落ちても DBOS が残りの秒から続ける | DBOS の耐久の記録 |
| `durable.sleep` | `plugins/ndf/scripts/lib/durable.py` | 新設 | `sleep(seconds: float) -> None` | 耐久ワークフローの中で `DBOS.sleep` を呼ぶ（I16。DBOS を import するのはここだけ） | 耐久ワークフローの中 | 回復した実行は残りの秒だけ待つ | DBOS の耐久の記録 |
| `rerun.key_of` | `plugins/ndf/scripts/supervise_lib/rerun.py` | 変更 | `key_of(result: dict) -> str \| None` | 同じ原因の鍵を `cause.kind` と `cause.instance` から作る（`summary` を読まない） | 無し | 同じ種類と対象なら同じ原因の鍵 | 無し |
| `rerun.count_failure` | `plugins/ndf/scripts/supervise_lib/rerun.py` | 変更 | `count_failure(cause_counts: dict, sid: str, result: dict) -> None` | 既定の行き先が `retry` の種類を数えない（`gh_quota.is_rate_limited` の文の判定を外す） | 無し | 数える種類のキーだけが 1 増える | 無し |
| `limits._edge_targets` | `plugins/ndf/scripts/supervise_lib/limits.py` | 変更 | `_edge_targets(steps, step) -> list[tuple[str, str, bool]]` | 遷移に `on_cause` の行き先を数える（`skip_to`・`on_exit`・`on_section_missing` を外す） | 無し | `not_applicable` の行き先は成功の遷移に数える | 無し |
| `limits.default_next` | `plugins/ndf/scripts/supervise_lib/limits.py` | 変更 | `default_next(steps, sid) -> str \| None` | 失敗のときだけ通るステップに `on_cause` の行き先（`not_applicable` を除く）を含める | 無し | 並びの次は失敗の経路のステップを飛ばす | 無し |
| `PrStep.execute` | `plugins/ndf/scripts/supervise_lib/pr.py` | 変更 | `execute(self, ctx, step) -> tuple[bool, str]` | 「設計と違う点」の節の欠けを `cause` `check_failed`（`instance` は節の見出し）で残す | 無し | `section_missing` のフラグを置かない | 外部コマンド（`git`・`gh`）・ネットワーク |
| `PrStep._push` | `plugins/ndf/scripts/supervise_lib/pr.py` | 変更 | `_push(self, ctx) -> tuple[str, str \| None]` | push 前の検査の拒否を `cause` `push_rejected` で残す | 無し | `push_check` のフラグを置かない | 外部コマンド（`git push`）・ネットワーク |
| `WorkStep.execute` | `plugins/ndf/scripts/supervise_lib/worker_steps.py` | 変更 | `execute(self, ctx, step) -> tuple[bool, str]` | worker の結果ファイルの `cause` を `cur["cause"]` へコピーする。ファイルが無い・読めないときは `work_stopped` | 無し | 失敗なら `cur["cause"]` がある | 外部コマンド（`claude`）・ファイル |
| `ci_watch.is_test_check` | `plugins/ndf/scripts/lib/ci_watch.py` | 変更 | `is_test_check(names: list[str], label: str, name: str) -> bool` | 分割ジョブ（`<名前> (k/n)`）を、その名前のテストのチェックとして数える（SF3） | 無し | 名前の完全一致か、末尾の `(数/数)` を外した名前の一致 | 無し |
| `ci_watch.merge_failures` | `plugins/ndf/scripts/lib/ci_watch.py` | 新設 | `merge_failures(state_dir: str \| Path) -> list[str]` | 状態ディレクトリの最後の `NN-merge*.out` の結果 JSON から、`result` が `failed` のチェックの名前を読む（SF3） | 無し | 無い・読めないときは空 | ファイル（読む） |
| `ci_watch.decide_exit` | `plugins/ndf/scripts/lib/ci_watch.py` | 変更 | `decide_exit(result: str, head, rows, limit, after_merge: bool, merge_failed: list[str]) -> tuple[int, int]` | マージの結果がテストのチェックの失敗を持てば、読み直しが `pending` でも `failed` として扱う（SF3） | 無し | `after_merge` で `merge_failed` が空でなければ 3 を返さない | 無し |
| `ci-watch._settle_after_merge` | `plugins/ndf/scripts/ci-watch.py` | 新設 | `_settle_after_merge(root, pr: int, names: list[str], budget: float, clock=time.monotonic, sleep=time.sleep) -> tuple` | `--after-merge` で未完了なら、方針 `test-triage.check-wait` の間隔で結論が出るか `budget` まで読み直す（SF3） | 無し | `_read_test_checks` と同じ形の値。`budget` を超えたら最後に読んだ値 | 外部コマンド（`gh`）・ネットワーク |
| `ci-watch.cmd_peek` | `plugins/ndf/scripts/ci-watch.py` | 変更 | `cmd_peek(a) -> NoReturn` | 引き継いだ失敗と読み直しで判定する（SF3）。止まりを `peek_cause` の種類で返す（SF4） | 無し | `--after-merge` で落ちたテストがあれば `ci-fix` へ進む値を返す | 外部コマンド（`gh`・`git`）・ファイル（`ci-watch.jsonl`）・標準出力 |
| `ci_watch.peek_cause` | `plugins/ndf/scripts/lib/ci_watch.py` | 新設 | `peek_cause(verdict: str, stuck: bool, over_limit: bool, after_merge: bool) -> str \| None` | 監視の判定から種類を返す（SF4）。`failed` → `test_failed`、上限・同じ head → `fix_limit`、マージの後の未完了 → `ci_pending`、マージの後で落ちたテストが無い → `not_ci` | 無し | `ok` に当たるときは `None` | 無し |
| `GreenWatch._stop_failed` | `plugins/ndf/scripts/merged_lib/checks.py` | 変更 | `_stop_failed(self, reading: CheckReading) -> NoReturn` | 落ちたチェックをテストのチェック（`test_strategy.ci_checks` の名前と分割ジョブ）とそれ以外に分け、テストがあれば `test_failed`、無ければ `check_failed` で止まる（SF4） | 無し | `items` の各チェックに `test: true/false` を付ける | 標準出力・プロセスの終了 |
| `merge._merge_pr` | `plugins/ndf/scripts/merged_lib/merge.py` | 変更 | `_merge_pr(root, a, n, watch, items, waits)` | `gh pr merge` の拒否を、衝突なら `base_conflict`、保護の規則・権限なら `not_ci`、ほかは `unknown` で返す（SF4） | 無し | 拒否の理由の判定は `_conflicted` と `gh pr view --json mergeStateStatus` の値で行い、標準エラーの文を読まない | 外部コマンド（`gh`）・ネットワーク |
| `verify_steps.judge_step` | `plugins/ndf/scripts/supervise_lib/verify_steps.py` | 変更 | `judge_step(after_tests: str, match: bool) -> dict` | 引数 `ci_merge` を外し（マージの失敗は `on_cause` の `test_failed` が `ci-fix` へ送る。SF4）、問いを選択肢の説明だけにする（SF6） | 無し | `choices` は `fix`・`after_tests`・`doc-lint`・`stop` | 無し |
| `commands.cmd_design_gate` | `plugins/ndf/scripts/supervise_lib/commands.py` | 新設 | `cmd_design_gate(state_dir: str) -> tuple[dict, int]` | `review` と `glossary-recheck` のステップの結果 JSON を読み、承認資料（用語チェックの当たりを含む）を書いて承認ゲートの終了コード 10 を返す（SF5） | 2 つのステップが状態ディレクトリに結果を残した | `status` が `gate` で `presentation_path` を持つ。読めなければ `invalid` で止まる | ファイル（結果を読み、承認資料を書く） |
| `design_steps.gate_steps` | `plugins/ndf/scripts/supervise_lib/design_steps.py` | 変更 | `gate_steps() -> list[dict]` | `gate` を `type: judge` から `supervise.py design-gate` の run へ替える（SF5） | 無し | Claude を起動するステップが無い | 無し |
| `judge_call.answer_schema` | `plugins/ndf/scripts/supervise_lib/judge_call.py` | 新設 | `answer_schema(choices: list[str] \| None, schema: str \| None) -> dict` | 選択肢のモードは `forms/judge-answer.json` の `decision` の `enum` を選択肢にしたスキーマ、任意の JSON のモードは渡したファイルのスキーマを返す | `choices` と `schema` のどちらか 1 つだけ | 返るスキーマは draft 2020-12 で検査できる | ファイル（スキーマを読む） |
| `judge_call.check_answer` | `plugins/ndf/scripts/supervise_lib/judge_call.py` | 新設 | `check_answer(answer: object, schema: dict) -> list[str]` | 答えを `jsonschema` で確かめる（IS6a・IS6b） | 無し | 空なら合う。文から近い選択肢を拾わない | 無し |
| `judge_call.ask` | `plugins/ndf/scripts/supervise_lib/judge_call.py` | 新設 | `ask(ctx, question: str, material: str, schema: dict, timeout: int) -> JudgeAnswer` | 問いと材料を `claude -p --json-schema` で渡し、合わなければ 1 回だけ打ち直す | `schema` は `answer_schema` の値 | `JudgeAnswer.ok` が真なら `answer` はスキーマに合う。偽なら `cause` が `needs_human` で、2 回の答えの先頭 200 文字を `detail` に持つ | 外部コマンド（`claude`）・ネットワーク・ファイル（使用量の帳簿） |
| `JudgeStep.execute` | `plugins/ndf/scripts/supervise_lib/steps.py` | 変更 | `execute(self, ctx, step) -> dict` | ステップの `choices` か `schema` から `ask` を呼ぶ。入力のステップの `cause` を構造のまま材料へ入れる | 無し | 返る辞書は `ok`・`decision`（選択肢のモード）か `answer`、合わなければ `cause` | 外部コマンド（`claude`）・ファイル（`progress.jsonl`） |
| `Engine._next_after_judge` | `plugins/ndf/scripts/supervise_lib/engine.py` | 変更 | `_next_after_judge(self, sid, step) -> tuple[str \| None, str \| None, str \| None]` | 答えが合わなければ `cause` `needs_human` で止まる。知らない値の分岐を消す（スキーマが選択肢の外を断る） | 無し | 選択肢の値ごとに今の行き先 | ファイル（`progress.jsonl`） |
| `claude_cmd` | `plugins/ndf/scripts/supervise_lib/claude.py` | 変更 | `claude_cmd(system, tools, ..., json_schema: dict \| None = None) -> list[str]` | `json_schema` があれば `--json-schema <JSON>` を足す | 無し | 無ければ今の引数のまま | 無し |
| `SlowWatch.judge_slow` | `plugins/ndf/scripts/supervise_lib/slow.py` | 変更 | `judge_slow(self, w: StepWatch, el: float) -> dict` | `forms/slow-answer.json` のスキーマで `ask` を呼ぶ。合わなければ `wait` ではなく `needs_human` で止める | 無し | 返る `decision` は `retry`・`fix`・`stop`・`wait` のどれか | 外部コマンド（`claude`）・ネットワーク |
| `RunState.attention` | `plugins/ndf/scripts/supervise_lib/state.py` | 変更 | `attention(self, reason: str, text: str, cause: dict \| None = None) -> None` | attention の行に、止まりなら `cause`、承認ゲートなら `gate: true` を載せる（SF7） | 無し | 同じ理由の行は 1 度だけ書く | ファイル（`progress.jsonl`） |
| `queue._attention_response` | `plugins/ndf/scripts/supervise_lib/queue.py` | 変更 | `_attention_response(found: list[dict], done_path: Path) -> tuple[str, dict, int]` | `next` に打てるコマンド（続きの `supervise.py wait`）を載せ、`items` の各行に `cause` か `gate` を持たせる（SF7） | 無し | `next` は文の説明でなくコマンド | 無し |
| `queue._timeout_response` | `plugins/ndf/scripts/supervise_lib/queue.py` | 変更 | `_timeout_response(timeout: float, done_path: Path) -> tuple[str, dict, int]` | `cause` `wait_timeout` と、同じ wait の `next` を返す（SF1b で種類、SF7 で `next`） | 無し | 終了コードは `wait_timeout` の 3 | 無し |
| `progress_model.route_failed` | `plugins/ndf/scripts/lib/progress_model.py` | 新設 | `route_failed(stage: dict, cause: dict, used: int, decl: dict) -> causes.Route` | 工程の定義の `metadata.ndf.on_cause` と方針を `causes.pick` へ渡す（SF8。DBOS を import しない） | `stage` は定義のスナップショットの 1 工程 | `pick` の事後条件 | 無し |
| `progress_run.sprint_workflow` | `plugins/ndf/scripts/lib/progress_run.py` | 変更 | `sprint_workflow(table_id: str, rev: int, stale: str \| None) -> dict` | アクティビティ（`act_plan`）の出力の `cause` を `failed` の事象に載せ、`route_failed` が `retry` なら `durable.sleep` の後に同じ工程を流し直し、`step` なら定義の行き先の工程へ進む（SF8） | 耐久ワークフローの中 | 人へ戻す止まりだけが `failed` で実行を終える | DBOS の耐久の記録・進行表の DB |
| `progress_run.run_next` | `plugins/ndf/scripts/lib/progress_run.py` | 変更 | `run_next(store: Store, table_id: str) -> tuple[Report, int]` | 工程の失敗の `stopped` に、失敗の事象の `cause` と `routes`（`fixed` → `progress.py retry <ID> <工程> --reason <理由>`、`abandon` → `progress.py cancel <ID> --reason <理由>`）を載せる（SF8） | 無し | 終了コードは `cause.kind` から引く（#3191 の詳細設計の「工程が失敗した → 6」を種類の終了コードへ直す。IS11・IS12） | 標準出力・進行表の DB |
| `parallel.reply` | `plugins/ndf/scripts/parallel.py` | 変更 | `reply(status, summary, items=None, kind: str \| None = None, next_step=None, routes=None, **metrics) -> NoReturn` | 終了コードの代わりに種類を受けて結果を出す（SF9）。承認ゲートは `routes`（SF13） | `stopped` なら `kind` を渡す | 終了コードは種類から引く | 標準出力・プロセスの終了 |
| `parallel_cli.wait_plan` | `plugins/ndf/scripts/lib/parallel_cli.py` | 変更 | `wait_plan(a, reply, main: Path, self_path: Path) -> NoReturn` | 失われた計画は `next` に `start`、依頼の中の `mvv.eligible` は `next` に `answer`、差し戻しの候補は `items` にフラグを付けて返す（SF9） | 無し | どの返り値も `next` がコマンドか `None` | ファイル（台帳・耐久の記録を読む）・標準出力 |
| `parallel_answer.release_rejectable` | `plugins/ndf/scripts/lib/parallel_answer.py` | 新設 | `release_rejectable(main: Path, name: str, q: dict) -> dict \| None` | リリースの依頼の中身が空か、版数が出し済みかを台帳とタグで確かめ、差し戻しの理由を返す（SF9） | `q` はリリースの承認の依頼 | 当たらなければ `None` | 外部コマンド（`git tag`）・ファイル（台帳） |
| `preflight.cmd_check` | `plugins/ndf/scripts/preflight.py` | 新設 | `cmd_check(a) -> NoReturn` | `worktree-setup.sh check`・`project-decl.py check`・`project-mvv.py check` を順に打ち、結果を 1 つにまとめて `next` に進む先を返す（SF10） | 無し | 3 つが通れば `ok`。どれかが止まれば、その結果の `cause` と、設定を用意するコマンドの `next` | 外部コマンド（3 本のスクリプト）・標準出力・プロセスの終了 |
| `sprint_loop_cli.run_loop_command` | `plugins/ndf/scripts/lib/sprint_loop_cli.py` | 変更 | `run_loop_command(a, update) -> dict \| None` | 断りと止まりに種類と打てる `next` を載せる（SF11） | 無し | `metrics.blocked` を読ませずに `next` で次の 1 手が決まる | ファイル（ループの記録） |
| `token_guard._context_deny` | `plugins/ndf/scripts/hook_lib/token_guard.py` | 変更 | `_context_deny(total: int, limit: int, nxt: str, notice: str \| None, cause: str \| None = None) -> dict` | 上限を超えたら切ると数で決め、打つコマンド（`handoff.py write`）を 1 つだけ案内に載せる（SF12） | 無し | 案内は続けるか切るかの選択肢を持たない | 無し |
| `transcript_agents.interrupted` | `plugins/ndf/scripts/lib/transcript_agents.py` | 変更 | `interrupted(...) -> list[dict]` | 中断した相手ごとに次の 1 手（続けさせる・起動し直す・待つ）とそのコマンドを返す（SF12） | 無し | 各行が `next` を持つ | ファイル（会話の記録を読む） |
| `sprint_state.cmd_gate` | `plugins/ndf/scripts/sprint-state.py` | 変更 | `cmd_gate(a) -> dict` | 答えを記録した結果に、次のステージのコマンドを `routes` で載せる（SF13） | 無し | `approved` と `rejected` の値が打てるコマンドか `null` | ファイル（スプリントの状態） |
| `merge.gate_stop` | `plugins/ndf/scripts/merged_lib/merge.py` | 変更 | `gate_stop(n, verdict, info=None, next_cmd=None, plan_step="merge-approved", approved_sha=None, out=None) -> NoReturn` | 承認ゲートを `step_result.gate` で `routes` つきで返す（SF13） | 無し | `approved` は `--from merge-approved` の打ち直し | ファイル（承認資料）・標準出力 |
| `sprint._new_refusal` | `plugins/ndf/scripts/supervise_lib/sprint.py` | 変更 | `_new_refusal(a, pace: str) -> dict \| None` | `--pace` を使えないとき、`normal` で作り直すコマンドと承認へ戻るコマンドを `routes` で返す（SF13） | 無し | 断りの結果は `refused` の種類を持つ | 無し |
| `ask_policy.judge_ask` | `plugins/ndf/scripts/lib/ask_policy.py` | 変更 | `judge_ask(tool_input, decl: Declaration) -> Verdict` | 問いの区分（レッドライン・戻せない操作）を判定の結果に載せる（SF13） | 無し | `Verdict` が区分を持つ | 無し |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `causes.table` | 表が無い・壊れている | 導入先のファイルの欠け・手の書き換え | `causes.table` の読み込みと `validate` | 止める（`CausesError`） | 呼んだエントリポイントの実行の核が `invalid` の止まりにする。標準エラーに表のパスと行。2 | 何も書かない。表を直して打ち直せる |
| `causes.cause` | 表に無い種類・空の説明 | コードの誤り | `causes.cause` | 止める（`ValueError`） | 実行の核が `unknown` の止まりにし、例外の跡を標準エラーへ。1 | 契約の検査（`causes_decl`）が同じ誤りを CI で先に落とす |
| `causes.errors` | 表を読めない | `causes.table` と同じ | `causes.table` | 呼び出し元へ返す（`CausesError`） | `causes.table` と同じ | 同上 |
| `causes.exit_code` | 表に無い種類 | コードの誤り | `causes.exit_code` | 止める（`KeyError` を `CausesError` にする） | 2 | 同上 |
| `causes.policy_of` | 方針が上限の宣言に無い | 表と宣言の食い違い | `limits.policy` | 止める（`LimitsError`） | 2 | 契約の検査が先に落とす |
| `causes_decl.violations` | 表が形の誤り・方針の名前が無い・コードの種類の文字列が表に無い | 表かコードの誤り | `causes_decl.violations` | 呼び出し元へ返す（違反の行） | `check-ndf-contracts.py` が違反を並べて 1 | 直して打ち直せる |
| `causes_decl.index_rows` | 表を読めない | 同上 | `causes.validate` | 呼び出し元へ返す | 同上 | 同上 |
| `step_result.validate_result` | 表を読めない | `causes.table` と同じ | `causes.table` | 呼び出し元へ返す | `emit` が 2 で終える | 同上 |
| `step_result.code_for` | `cause` が無い・表を読めない | 書き手の誤り | `step_result.code_for` | 既定値で続ける（`None` を返し、`emit` が形の誤りにする） | 2 | 同上 |
| `step_result.emit` | 形の誤り（`cause` の欠け・種類と終了コードの食い違い・`gaps` と `incomplete` の食い違い・`routes` の欠け） | 書き手の誤り | `validate_result` | 止める | 標準エラーに誤りの並び。2 | 出さない。直して打ち直せる |
| `step_result.gh_json` | gh が無い・失敗・API の上限・出力を読めない | 環境・GitHub | `step_result.gh_json` | 呼び出し元へ返す（種類つきの `StepError`） | `main_with` が `precondition`（3）・`unknown`（1）・`infra_wait`（75）・`invalid`（2）で止める | 上限は `retry_after` の後にプランが打ち直す |
| `step_result.main_with` | 本体の `StepError` | 本体が止めた | `step_result.main_with` | 止める | 種類の終了コード | 本体の後始末に任せる |
| `StepError.__init__` | 表に無い種類 | コードの誤り | `causes.exit_code` | 止める（`CausesError`） | 実行の核が `invalid` の止まりにする。2 | 契約の検査が先に落とす |
| `proc.run` | コマンドが 0 以外 | 外部のコマンドの失敗 | `proc.run` | 呼び出し元へ返す（`StepError`・`unknown`） | 1 | 呼び手が種類を細かくできる（`except StepError` で読み替えずに、呼び手が先に結果を読んで種類を選ぶ） |
| `run_core.exit_early` | 核に入る前の止まり（依存の欠け・契約に無い） | 環境 | `run_core.exit_early` の呼び手 | 止める | 種類の終了コード（`deps_missing` の 69 ほか） | 依存を入れて打ち直せる |
| `deps._stop` | 依存を import できない・uv が無い・`run_core` を読めない | 環境 | `deps.require` | 止める（`deps_missing`） | 結果 JSON の `cause.detail` に欠けたグループ、終了コード 69。`run_core` を読めなければ結果を出さずに 69 | 依存を入れて打ち直せる |
| `sr_emit` | `--status stopped` に `--cause` が無い | 書き手の誤り | `step_result.py emit` | 止める | 標準エラーに誤り。2 | 直して打ち直せる |
| `routes.plan_errors` | 表に無い種類・知らない行き先・NDF のエントリポイントを打つステップの `exit_cause`・消したキーの残り | プランの JSON の誤り | `Engine.setup` | 止める | プランの結果が「止まった」と理由。queue の `done` に載る | プランの JSON を直して打ち直せる。何も流していない |
| `Engine._route_failure` | `progress.jsonl` に書けない | ディスク | `RunState.progress_write` | 止める（例外を耐久ステップの失敗にする） | プランの記録が残らず、queue の `done` に耐久ステップの失敗 | DBOS が同じステップから回復する |
| `Engine._next_after_step` | 同上 | 同上 | 同上 | 同上 | 同上 | 同上 |
| `Engine._run_step` | 利用上限の待ちの上限・認証が無い・差し込みの残り | LLM の上限・認証・プランの誤り | `ClaudeRunner.call`・`leftover_placeholders` | `_route_failure` へ渡す | `usage_limit`（`retry_after` が無ければ人へ）・`precondition`・`invalid` でプランが止まる | 上限は解除の後に `--resume` で続きから |
| `Engine.setup` | 状態ディレクトリを作れない | ディスク・権限 | `Engine.setup` | 止める | 「止まった」と理由 | 打ち直せる |
| `flow._plan_steps` | 待ちの途中でプロセスが落ちた | 再起動・OOM | DBOS の回復 | 再試行（残りの秒から待ち直す） | 無し（待ちの行は `progress.jsonl` の `retry` に残る） | 回復した実行が続ける |
| `durable.sleep` | 耐久ワークフローの外で呼んだ | コードの誤り | `DBOS.sleep` | 止める（例外） | 実行の核が `unknown` にする。1 | テストが落とす |
| `PrStep.execute` | 節の欠け・PR の作成の失敗 | 本文・GitHub | `PrStep.design_diff`・`_publish` | `_route_failure` へ渡す（`check_failed`・`unknown`） | プランの行き先で `judge` か人へ | 打ち直せる |
| `PrStep._push` | push 前の検査の拒否・push の失敗 | hook・ネットワーク | `pr.is_push_check_failure` | `_route_failure` へ渡す（`push_rejected`・`unknown`） | 行き先で `fix-push` か人へ | 直して打ち直せる |
| `WorkStep.execute` | 結果ファイルが無い・読めない・`stopped` | worker の中断・作業の欠け | `WorkStep.execute` | `_route_failure` へ渡す（ファイルの `cause` か `work_stopped`） | 行き先で判断か人へ | 続きの worker を起こせる |
| `ci_watch.merge_failures` | マージの出力が無い・読めない | マージのステップを通っていない | `ci_watch.merge_failures` | 既定値で続ける（空） | 無し（読み直しの判定に戻る） | 無し |
| `ci-watch._settle_after_merge` | gh の失敗・`budget` を超えた | GitHub・CI の遅れ | `_read_test_checks` | 既定値で続ける（最後に読んだ値） | SF3 では 3（CI 以外）、SF4 からは `ci_pending`（1）で止まる | `ci_pending` はプランが方針 `cause.ci-pending` で打ち直す |
| `ci-watch.cmd_peek` | 記録に書けない・gh の失敗 | ディスク・GitHub | `ci_watch.append_log`・`_read_test_checks` | 既定値で続ける（gh の失敗は `unknown` の判定として記録して進める。今の振る舞い） | `ok` と要約「取得できない」 | 次の監視で読み直す |
| `GreenWatch._stop_failed` | テストのチェックの名前の宣言が読めない | 宣言の欠け | `test_strategy.ci_checks` | 既定値で続ける（宣言が無ければ、すべてのチェックをテストとみなす。今の `is_test_check` の空の規則） | `test_failed`。1 | 無し |
| `merge._merge_pr` | マージの拒否の理由を読めない | GitHub | `gh pr view` | 既定値で続ける（`unknown`） | 1 で人へ | 打ち直せる |
| `commands.cmd_design_gate` | 2 つのステップの結果が無い・読めない・承認資料を書けない | 前のステップの欠け・ディスク | `cmd_design_gate` | 止める（`invalid`） | 2 で人へ | 設計のプランを `--from review` で打ち直せる |
| `judge_call.answer_schema` | スキーマのファイルを読めない | 導入先のファイルの欠け | `answer_schema` | 止める（`invalid`） | プランが 2 で止まる | 直して打ち直せる |
| `judge_call.ask` | claude の失敗・答えがスキーマに合わない（2 回） | LLM・入力の不足 | `check_answer` | 再試行（1 回）。なお合わなければ呼び出し元へ返す（`needs_human`） | プランが `needs_human` で止まり、問い・2 回の答え・入力の参照が結果に載る | 入力を足して打ち直せる |
| `JudgeStep.execute` | 同上 | 同上 | `judge_call.ask` | 呼び出し元へ返す | 同上 | 同上 |
| `Engine._next_after_judge` | 答えが合わない | 同上 | `JudgeStep.execute` の `ok` | 止める（`needs_human`） | 「止まった」と `cause` | 同上 |
| `SlowWatch.judge_slow` | 答えが合わない（2 回）・claude の失敗 | LLM | `judge_call.ask` | 止める（`needs_human`） | プランが止まり、遅れの調べの材料が結果に載る | `--resume` で続きから |
| `RunState.attention` | 書けない | ディスク | `progress_write` | 止める | 耐久ステップの失敗 | DBOS が回復する |
| `progress_run.sprint_workflow` | 事象を書けない・待ちの途中で落ちた | DB のロック・再起動 | #3191 の `step_append` | 再試行（`step_append` の 3 回と DBOS の回復） | 尽きれば `stopped`（`unknown`） | 回復した実行が続ける |
| `progress_run.run_next` | 工程が失敗した・進行表を読めない | アクティビティの止まり・DB の欠け | `sprint_workflow` の戻り・#3191 の読み手 | 呼び出し元へ返す（失敗の事象の `cause`。読めなければ `invalid`） | 種類の終了コードと `routes` | `routes` の `fixed` で打ち直せる |
| `parallel.reply` | 形の誤り | 書き手の誤り | `step_result.emit` | 止める | 2 | 直して打ち直せる |
| `parallel_cli.wait_plan` | 台帳・耐久の記録を読めない | ディスク・失われた計画 | `pl.record_exists`・`plg.plan_state` | 呼び出し元へ返す（`precondition` と `next` に `start`） | 3 | `next` を打てば作り直せる |
| `parallel_answer.release_rejectable` | タグを読めない | git | `git tag` | 既定値で続ける（`None`。差し戻しの候補にしない） | 無し（今どおり人へ問う） | 無し |
| `preflight.cmd_check` | 3 本のどれかが止まった・結果を読めない | 設定の欠け | `preflight.cmd_check` | 呼び出し元へ返す（その結果の `cause`。読めなければ `invalid`） | 止めた結果の種類の終了コードと `next` | `next` の設定のコマンドの後に打ち直せる |
| `sprint_loop_cli.run_loop_command` | ループの記録に書けない | ディスク・排他 | `sprint_loop_record` | 止める（`unknown`） | 1 | 打ち直せる |
| `transcript_agents.interrupted` | 会話の記録を読めない | ファイルの欠け | `interrupted` | 既定値で続ける（その相手を `待つ` にする） | 無し | 次の点検で読み直す |
| `sprint_state.cmd_gate` | 状態ファイルの排他・書き込みの失敗 | 並行の書き込み | `sprint-state.sprint-lock` | 止める（`unknown`） | 1 | 打ち直せる（答えの記録は冪等） |
| `merge.gate_stop` | 承認資料を書けない | ディスク | `approval_present` | 止める（`unknown`） | 1 | 打ち直せる |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `causes.pick` | `on_cause` に種類がある | その値（ステップの id なら `step`、`end`・`stop`、`retry` なら下の打ち直しの行と同じ判定） |
| `causes.pick` | `on_cause` に無い・既定が打ち直し・`used < attempts`・（`usage_limit` なら `retry_after` がある） | `retry`（`wait` は `backoff`） |
| `causes.pick` | `on_cause` に無い・既定が打ち直しで上限に当たった | 既定を外して次へ: `on_fail` があれば `step`、無ければ `human` |
| `causes.pick` | `on_cause` に無い・既定が人へ（`unknown` を含む） | `human`（`on_fail` を見ない。IS4・IS8） |
| `causes.pick` | `on_cause` に無い・既定がステップ・`on_fail` がある | `step`（`on_fail`） |
| `causes.pick` | `on_cause` に無い・既定がステップ・`on_fail` が無い | `human` |
| `routes.step_cause` | `cur["cause"]` がある（エンジンのハンドラーが置いた） | それ |
| `routes.step_cause` | 出力の最後の結果 JSON が `stopped` で `cause` を持つ | それ（`step_read.last_json`） |
| `routes.step_cause` | 結果 JSON が無い・ステップが `exit_cause` を持ち、終了コードがそのキーにある | その種類（`detail` は「終了コード N」） |
| `routes.step_cause` | どれにも当たらない | `unknown`（`instance` はステップの id） |
| `Engine._next_after_step` | `ok` | 今どおり（`pr_from`・`back_or_next`） |
| `Engine._next_after_step` | 終了コードが 10〜19 | 今どおり（`gate_as_ok`・`take_gate`・`gate_next`） |
| `Engine._next_after_step` | それ以外 | `_route_failure` |
| `ci_watch.decide_exit` | `after_merge`・`merge_failed` が空でない | `failed` として扱い、改修の上限の判定へ（1 か上限なら 2） |
| `ci_watch.decide_exit` | `after_merge`・`merge_failed` が空・`_settle_after_merge` の後も `pending` | SF3 では 3、SF4 からは `peek_cause` が `ci_pending` |
| `ci_watch.decide_exit` | `after_merge`・`merge_failed` が空・読み直しで `passed` | 3（SF4 からは `not_ci`） |
| `テンプレートの書き換え` | `merge_steps` の `on_exit` `75: stop`・`4: on_conflict` | 消す（`infra_wait` は既定で打ち直し、`on_cause` `{"base_conflict": <on_conflict>}`。渡さなければキーを置かず人へ） |
| `テンプレートの書き換え` | `ci_watch_steps` の `on_exit` `2: stop`・`3: stop`・`on_fail: ci-fix` | `on_cause` `{"test_failed": "ci-fix"}`。`fix_limit`・`not_ci` は既定で人へ。`on_fail` を消す |
| `テンプレートの書き換え` | `base_sync_steps` の `on_exit` `1: fix-base` | `on_cause` `{"base_conflict": "fix-base", "push_rejected": "fix-base"}`（`merged-steps.py base-sync` が 2 つの種類を返す） |
| `テンプレートの書き換え` | `push_fix_steps` の `on_fail_only: push-check` | `on_cause` `{"push_rejected": "fix-push"}`。`on_fail` を消す |
| `テンプレートの書き換え` | `design_diff_on_pr` の `on_section_missing` | `on_cause` `{"check_failed": <on_missing>}` |
| `テンプレートの書き換え` | `skip_to`（13 か所。`paths.py` の `behavior-targets` ほか） | `on_cause` `{"not_applicable": <skip_to>}`。`skip_code` も消す |
| `テンプレートの書き換え` | `release_templates` の `BUMP_STOP`・`INFRA_WAIT_STOP`・`CD_FAILED_STOP` | 消す（`invalid`・`not_applicable`・`deploy_failed` は既定で人へ、`infra_wait` は既定で打ち直し） |
| `テンプレートの書き換え` | `config-check-on-fail` の `on_exit` `0: stop` | `next: "stop"`（成功でも止める） |
| `テンプレートの書き換え` | `templates` のリファクタリングの `on_exit` `6: review`・`sprint_waves` の `6: review-settled` | `on_cause` `{"incomplete": "review"}`・`{"incomplete": "review-settled"}` |
| `テンプレートの書き換え` | `sprint_waves` の `review-settled` の `1: resolve-sync`・`2: ready` | `on_cause` `{"check_failed": "resolve-sync", "invalid": "ready"}` |
| `テンプレートの書き換え` | `design_steps` の `design-review-check`（`grep -qx`）の `1: design-fix` | `exit_cause` `{"1": "check_failed"}` と `on_cause` `{"check_failed": "design-fix"}` |
| `テンプレートの書き換え` | 静的解析と整形の `on_exit` `2: stop`（`verify_steps` の 3 か所） | 消す（`invalid` は既定で人へ） |
| `テンプレートの書き換え` | `sprint_close_steps`・`sprint_lane_close` の `on_exit` `10: ...` | `gate_next`（承認ゲートの行き先の今のキー）へ移す |
| `テンプレートの書き換え` | `sprint_bundles` の `on_exit` の行き先の付け替え | `on_cause` の行き先を付け替える |

- 繰り返しの終わる条件: 打ち直しは種類ごとの `attempts`（`cause.ci-pending` 10・`cause.infra-wait` 3・`cause.usage-limit` 1）で終わる。上限の後は `on_fail` か人へ進む。判断の呼び出しは 2 回で終わる。`_settle_after_merge` は `budget`（ステップの上限から 30 秒を引いた秒）で終わる。ステップの数の上限（`上限`）は打ち直しを数えない
- 計算量の注意: 無し（表は 21 行、プランのステップは 100 前後で、引くのは失敗のときだけ）

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| 止まった理由の表 | 設定値 | `plugins/ndf/contracts/causes.json` | 設計文書の「止まった理由の表」の 21 行。形は `{"version": 1, "causes": [{"kind", "meaning", "std_type", "exit", "route", "retry"}]}`。`route` は `retry`・`step`・`human` |
| `CauseRow` | 型 | `plugins/ndf/scripts/lib/causes.py` | `NamedTuple(kind, meaning, std_type, exit, route, retry)`。`retry` は方針の名前か `None` |
| `Route` | 型 | `plugins/ndf/scripts/lib/causes.py` | `NamedTuple(to, target, wait, kind)`。`to` は `retry`・`step`・`end`・`stop`・`human` |
| `STD_TYPES` | 定数 | `plugins/ndf/scripts/lib/causes.py` | STD1 の 8 つの型（configuration・validation・expression・authentication・authorization・timeout・communication・runtime）。標準の語で、表の値ではない |
| `cause.ci-pending` | 設定値 | `plugins/ndf/contracts/limits.json` | `attempts` 10・`interval` 60・`factor` 1.5・`max_interval` 600 |
| `cause.infra-wait` | 設定値 | `plugins/ndf/contracts/limits.json` | `attempts` 3・`interval` 600・`factor` 2・`max_interval` 3600 |
| `cause.usage-limit` | 設定値 | `plugins/ndf/contracts/limits.json` | `attempts` 1。1 回の待ちの上限は既存の `supervise.limit-wait-max`（10800 秒）を引き、値をコピーしない |
| `.ndf/supervise.json` の `causes.<種類>` | 設定値 | 利用先のリポジトリ | 無し（方針のキー `attempts`・`interval`・`factor`・`max_interval` だけを受ける） |
| ステップの `on_cause`・`retry`・`exit_cause` | 設定値 | プランの JSON（テンプレートが書く） | 無し |
| `RunState.retry_counts` | 型 | `plugins/ndf/scripts/supervise_lib/state.py` | `{ステップの id: {種類: 回数}}`。耐久ステップの出力のスナップショット（`snapshot`）が運ぶ |
| `cur["cause"]`・`cur["retry_wait"]` | 型 | `plugins/ndf/scripts/supervise_lib/state.py` の `RunState.cur` | 無し |
| `JudgeAnswer` | 型 | `plugins/ndf/scripts/supervise_lib/judge_call.py` | `NamedTuple(ok, answer, cause, attempts, cost)` |
| 判断の答えの形 | 設定値 | `plugins/ndf/contracts/forms/judge-answer.json` | `{"decision": enum, "reason": 文字列, "basis": 文字列の配列}`。`enum` は呼ぶときに選択肢で埋める |
| 遅れの判定の答えの形 | 設定値 | `plugins/ndf/contracts/forms/slow-answer.json` | `{"decision": "retry" \| "fix" \| "stop" \| "wait", "reason", "wait_seconds"}`。選択肢は `slow_step.DECISIONS` と同じで、`DECISIONS` は消してこのファイルから引く |
| 判断の打ち直しの回数 | 定数 | `plugins/ndf/scripts/supervise_lib/judge_call.py` の `ATTEMPTS` | 2（決定 60。上限の宣言の方針ではなく決定で固定した回数） |

同じ事実を 2 か所に持たない: 種類の語彙・終了コード・既定の行き先は `causes.json` だけ、再試行の値は `limits.json` だけが持つ。コードは種類を `causes.cause("<種類>", ...)` の引数の文字列でだけ書き、種類の定数を置かない（`causes_decl.violations` が文字列を表と照らす）。`exit_codes.EXIT_*` は、終了コードの値の唯一の置き場のまま残す。`causes.json` の `exit` は数で書き、`causes.validate` が `STOPPED_CODES` にあるかを確かめる。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ queue の中の複数のプラン（耐久ワークフロー） | `causes.json`・`limits.json`（読むだけ） | 取らない（読むだけで、プロセスごとに 1 回読んでキャッシュする） | 読み直しで同じ値 |
| 1 本のプランの打ち直しの待ち | そのプランの状態ディレクトリ・DBOS の記録 | プランの実行の鍵（今の `running` の登録簿と DBOS の実行 id） | `durable.sleep` は耐久の待ちで、落ちても残りの秒から再開する。`retry_counts` は耐久ステップの出力で運ぶため、回復しても回数が戻らない |
| `ci-peek-merge` とマージのステップ | 状態ディレクトリの `NN-merge*.out` | 取らない（同じプランの中で順に流れ、マージのステップが終わってから読む） | 読むだけ |
| `ci-watch.py peek` の記録 | `work/ci-watch.jsonl` | 今の `jsonio.append_jsonl` の追記 | 同じ head の行は 2 度目で `fix_limit` になる（今の規則） |
| 判断の呼び出し | 使用量の帳簿 | 今の `ClaudeRunner.record_usage` | 打ち直しの 2 回目も帳簿に残す |
| 進行表の `progress.py next`（SF8） | 進行表の DB | #3191 の `run_lock`（#3092 の I14） | 打ち直しは進行表のワークフローの中の耐久の待ちで、`next` を打ち直しても重ならない |
| SF1b の PR と、リファクタリング計画の子（#2963・#2990・#2992〜#2996） | 127 本のファイル | Issue の順序（後に流す） | 無し |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `step_result.emit` | `cause` を含む結果 JSON の 1 行 | 標準出力・結果ファイル | `detail` に認証情報（gh の標準エラーは `secret_redact` を通した要旨だけ） |
| `Engine._route_failure` | `{"kind": "cause", "step", "cause", "to", "target", "attempt", "wait"}` の 1 行 | 進捗記録（`progress.jsonl`） | 出力の本文 |
| `flow._plan_steps` | `{"kind": "retry", "step", "waited", "attempt"}` | 進捗記録 | 無し |
| `judge_call.ask` | 問いの種類・試行の回数・答えが合ったか・費用 | 進捗記録（今の `judge` の行に `attempts` と `valid` を足す）・使用量の帳簿 | 材料の本文（状態ディレクトリの `NN-<ステップ>.out` にだけ残す） |
| `routes.plan_errors` | 誤りの 1 文 | プランの結果（`report.md` と queue の `done`） | 無し |
| `ci-watch.cmd_peek` | 判定・引き継いだ失敗・読み直しの回数 | 結果 JSON の `metrics`・`ci-watch.jsonl` | 失敗したジョブのログの認証情報（`secret_redact`） |
| `causes_decl.violations` | 違反の行 | `check-ndf-contracts.py` の結果 JSON | 無し |
| `preflight.cmd_check` | 3 本の結果の要旨 | 結果 JSON の `items` | 宣言の中身 |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `causes.table` | `doc_kinds` の読み込み（`DECLARATION` の探し方・`use_contract`・`DocKindsError`）と `limits.read_table` | `sed -n 1,50p plugins/ndf/scripts/lib/doc_kinds.py`・`grep -n '^def ' plugins/ndf/scripts/lib/limits.py` | 使う（同じ探し方とキャッシュの仕方に倣う。共通の関数にはしない。2 つの読み手は表の形が違い、共通にしても 10 行に満たないため） |
| `causes.use_contract` | `doc_kinds.use_contract`・`limits.use` | 同上 | 使う（同じ形） |
| `causes.validate` | `limits.validate`・`doc_kinds` の行の検査 | 同上 | 使う（誤りを `where` つきの例外にする形を合わせる）。`jsonschema` はSF1a の時点で依存に無い（data の DT1 が足す）ため使えない |
| `causes.cause` | `step_result._record_errors` | `grep -n '^def ' plugins/ndf/scripts/lib/step_result.py` | 使えない（`gaps` の要素のキーの検査で、種類の表の照合を持たない） |
| `causes.errors` | 同上 | 同上 | 使えない（`step_result` と `proc` が `causes` を読むため、`causes` から呼ぶと `step_result → causes → step_result` の輪になる。空でない文字列の検査は `causes.errors` の中に書く。2 行で、共通の部品にはしない） |
| `causes.exit_code` | `exit_codes.label_of` | `cat plugins/ndf/scripts/lib/exit_codes.py` | 使えない（終了コードから見出しを引く向きで、種類から引く表を持たない） |
| `causes.policy_of` | `limits.policy` | `grep -n 'def policy' -A8 plugins/ndf/scripts/lib/limits.py` | 使う |
| `causes.pick` | `Engine._next_after_step`・`plan.fail_kind_matches` | `sed -n 370,402p plugins/ndf/scripts/supervise_lib/engine.py` | 使えない（終了コードとフラグで分岐し、`summary` を理由に添える。置き換える対象） |
| `causes.backoff` | `gh_quota.wait_for_reset`・`Engine.gh_limit_wait` の倍々の待ち・`merged_lib/checks.py` の `merged.green-wait` の倍率 | `grep -n '^def ' plugins/ndf/scripts/lib/gh_quota.py`・`sed -n 128,162p plugins/ndf/scripts/supervise_lib/engine.py` | 使えない（どれも待ちのループの中に計算が埋まり、秒だけを返す関数が無い）。`gh_limit_wait` はSF2 で消す |
| `causes_decl.violations` | `contracts_lib/limits_decl.violations`・`limits_decl.code_files` | `sed -n 1,40p scripts/contracts_lib/limits_decl.py` | 使う（`code_files` をそのまま呼び、構文木で文字列を集める形に倣う） |
| `causes_decl.index_rows` | `limits_decl` の逆引きの表 | 同上 | 使う（同じ描き方） |
| `step_result.gate` | `step_result.result`・`approval_present` | `grep -n '^def ' plugins/ndf/scripts/lib/step_result.py` | 使う（`result` を呼んで `routes` を足す） |
| `routes.step_cause` | `step_read.last_json`（`steps.last_json`） | `grep -n 'last_json' plugins/ndf/scripts/supervise_lib/steps.py` | 使う |
| `routes.retry_policy` | `decl.read_decl`・`limits.policy` | `grep -n '^def ' plugins/ndf/scripts/supervise_lib/decl.py` | 使う |
| `routes.route_of` | `causes.pick` | SF1a の設計 | 使う |
| `routes.plan_errors` | `plan.on_exit_error`・`limits.unknown_targets`・`run_core.entry_of_script`・`run_core.contract_of` | `sed -n 303,316p plugins/ndf/scripts/supervise_lib/plan.py`・`grep -n 'def entry_of_script\|def contract_of' plugins/ndf/scripts/lib/run_core.py` | 使う（行き先の照合は `unknown_targets`、エントリポイントの判定は `run_core` の 2 つ）。`on_exit_error` は消す |
| `Engine._route_failure` | `Engine._next_on_exit`・`RunState.progress_write`・`rerun.stop_reason` | `sed -n 403,425p plugins/ndf/scripts/supervise_lib/engine.py` | `progress_write` と `rerun.stop_reason` は使う。`_next_on_exit` は置き換えて消す |
| `durable.sleep` | `durable.recv`・`durable.patched`（DBOS を呼ぶ同じ形） | `sed -n 380,400p plugins/ndf/scripts/lib/durable.py` | 使う（同じ呼び方）。`DBOS.sleep` は `site-packages/dbos/_dbos.py` にある（設計文書の LIB1） |
| `ci_watch.merge_failures` | `RunState.out_path`（`NN-<ステップ>.out` の名前）・`step_read.last_json` | `grep -n 'def out_path' -A2 plugins/ndf/scripts/supervise_lib/state.py` | 使う（名前の形と最後の JSON の読み） |
| `ci-watch._settle_after_merge` | `merged_lib/checks.GreenWatch.wait`・方針 `test-triage.check-wait` | `grep -n '    def ' plugins/ndf/scripts/merged_lib/checks.py`・`limits.json` の一覧 | 方針は使う。`GreenWatch` はマージまでを持つ待ちで、読み直しだけを取り出せないため使えない |
| `ci_watch.peek_cause` | `ci_watch.decide_exit` | `sed -n 62,76p plugins/ndf/scripts/lib/ci_watch.py` | 使う（`decide_exit` の判定の後に種類へ対応づける） |
| `commands.cmd_design_gate` | `step_result.approval_present`・`commands.cmd_design_glossary` | `grep -n '^def ' plugins/ndf/scripts/supervise_lib/commands.py` | 使う（承認資料は `approval_present`、用語チェックの当たりは `cmd_design_glossary` の出力の形） |
| `judge_call.answer_schema` | `contracts/forms/` の既存の形（`parallel-request.json` ほか） | `ls plugins/ndf/contracts/forms` | 使う（同じ置き場） |
| `judge_call.check_answer` | `jsonschema`（data の LIB1）・`pydantic`（依存のグループ `schema`） | `grep -n '"schema"' plugins/ndf/scripts/lib/deps.py` | `jsonschema` を使う（data の DT1 が依存に足した後）。`pydantic` はスキーマのファイルを実行時に読む形に向かない |
| `judge_call.ask` | `ClaudeRunner.call`・`steps.parse_decision`・`slow.judge_slow` の答えの読み | `grep -n 'def parse_decision' -A8 plugins/ndf/scripts/supervise_lib/steps.py`・`sed -n 270,317p plugins/ndf/scripts/supervise_lib/slow.py` | `ClaudeRunner.call` は使う。`parse_decision` と `judge_slow` の正規表現の読みは文から JSON を拾う形で、置き換えて消す |
| `parallel_answer.release_rejectable` | `parallel_answer` の `mvv_mark`・`release_lib/names.py` のタグの名前 | `grep -n 'eligible' plugins/ndf/scripts/lib/parallel_*.py` | 使う（依頼の判定の形と、タグの名前の唯一の置き場） |
| `preflight.cmd_check` | `worktree-setup.sh check`・`project-decl.py check`・`project-mvv.py check` | `development-workflow` の `SKILL.md` の手順 0 | 使う（3 本を子プロセスで打ち、結果 JSON を読む） |
| `progress_model.route_failed` | `causes.pick` | SF1a の設計 | 使う |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `causes.table` | プラグインの表を読むと 21 行で、各行が型と終了コードを持つ | `use_contract` で 1 行だけの表を読む | 表が無い・JSON が壊れている・版が違う → `CausesError` |
| `causes.validate` | 正しい表を通す | 終了コード 75・69（範囲の端） | 標準の型が 8 つの外・終了コードが `STOPPED_CODES` の外・`route` が知らない語・`retry` の方針が上限の宣言に無い・種類の重複 → それぞれ誤り |
| `causes.cause` | 種類と説明から辞書を組む | `instance`・`retry_after` を省く | 表に無い種類・空の説明 → `ValueError` |
| `causes.errors` | 正しい `cause` で空 | 任意のキーだけを持つ | `kind` が表に無い・`detail` が空・`retry_after` が負 → 誤り |
| `causes.exit_code` | `base_conflict` → 4・`not_applicable` → 3 | 終了コードを共有する 2 つの種類 | 表に無い種類 → `CausesError` |
| `causes.pick` | IS4 の 6 行（処理の手順）をそれぞれ 1 件で確かめる | `used == attempts - 1` は打ち直し、`used == attempts` は次へ。`usage_limit` は `retry_after` の有無で分かれる | 既定が人への種類は `on_fail` があっても `human`。同じ種類で文だけ違う 2 件が同じ行き先（引数に文が無いことを型で確かめる） |
| `causes.backoff` | `interval × factor^used` | `max_interval` と `cap` で頭打ち。`retry_after` が `cap` を超える | `factor` が無い方針は間隔のまま |
| `causes_decl.violations` | プラグインの表とコードで違反なし | 方針の名前を引くだけの種類 | 表に無い種類の文字列・宣言に無い方針・形の誤り → 違反 |
| `step_result.validate_result` | `cause` を持つ `stopped`・`routes` を持つ 10 | `stopped` の 6 と `gaps` と `incomplete` の組 | `cause` の無い `stopped`・`ok` の `cause`・種類と違う終了コード・`gaps` があるのに `incomplete` でない・`routes` の無い 10 → 誤り |
| `step_result.code_for` | `stopped` の `precondition` → 3 | `gate` → 10 | `cause` の無い `stopped` → `None` |
| `step_result.emit` | 正しい結果を 1 行出してその終了コード | `stopped` で `code` を省く | 形の誤り → 標準エラーと 2 |
| `step_result.gh_json` | 成功で値 | API の上限 → `infra_wait` と `retry_after` | gh が無い → `precondition`・読めない → `invalid`・ほか → `unknown` |
| `step_result.main_with` | `StepError` の種類の結果 | 無し | 無し |
| `StepError.__init__` | 種類から終了コード | `instance`・`retry_after` | 表に無い種類 → `CausesError` |
| `run_core.core_stopped` | 例外 → `unknown`・形の誤り → `invalid`・打ち切り → `interrupted` | 無し | 無し |
| `run_core._from_code` | 結果 JSON を出さずに 1 → `unknown` | 0・10 | 表に無い終了コード → `invalid` |
| `run_core.exit_early` | `deps_missing` で 69 と `cause` を持つ `stopped`、`gaps` を置かない | `incomplete` と `gaps` を渡す → 6 | `incomplete` でない種類に `gaps` を渡す → `emit` の形の誤り（IS12） |
| `deps._stop` | 欠けたグループの名前を `cause.detail` に持つ `deps_missing` の結果と 69 | グループを渡さない → `detail` に `uv` | `run_core` を読めない → 結果を出さずに 69 |
| `sr_emit` | `--cause` つきの `stopped` を出す | `--cause` の説明に改行 | `--cause` の無い `stopped` → 2 |
| `routes.step_cause` | 4 つの順をそれぞれ 1 件 | `exit_cause` に無い終了コード → `unknown` | 結果 JSON の `cause` が形の誤り → `unknown` |
| `routes.retry_policy` | 宣言・プロジェクト・ステップの 3 つの重ね | 1 つのキーだけを上書き | 打ち直さない種類 → `None` |
| `routes.plan_errors` | 今のすべてのテンプレート（`supervise.py new` の各種類）を通す | `on_cause` の `retry`・`end`・`stop` | 表に無い種類・知らない行き先・NDF のエントリポイントのステップの `exit_cause`・`on_exit` の残り → 誤り |
| `Engine._route_failure` | `infra_wait` が 3 回まで待って打ち直し、4 回目は `on_fail` | 待ちの途中で落としても続きの待ちから再開する（DBOS を使う 1 件） | 既定が人への種類は止まり、結果に `cause` が載る |
| `flow._plan_steps` | 打ち直しをステップの上限に数えない | 上限ちょうど | 無し |
| `rerun.key_of` | 同じ種類と対象で同じ原因の鍵 | `instance` が無い | 文だけ違う 2 件で同じ原因の鍵 |
| `PrStep.execute` | 節の欠けで `check_failed` | 無し | 無し |
| `PrStep._push` | push 前の検査の拒否で `push_rejected` | 無し | ほかの push の失敗 → `unknown` |
| `WorkStep.execute` | 結果ファイルの `cause` をコピーする | 無し | 結果ファイルが無い → `work_stopped` |
| `ci_watch.is_test_check` | 完全一致・分割ジョブ `pytest (0/2)` | `names` が空 | `pytest-lint` は `pytest` に数えない |
| `ci_watch.merge_failures` | 最後のマージの出力の落ちたチェック | マージの出力が 2 つ（`merge` と `merge-approved`） | 無い・壊れた出力 → 空 |
| `ci_watch.decide_exit` | #3153 の再現（マージが `pytest (1/2)` で落ち、まとめが `pending`）で `ci-fix` へ | 改修の上限ちょうど | 落ちたテストが無く読み直しでも `pending` |
| `ci-watch._settle_after_merge` | 2 回目の読みで結論が出る | `budget` ちょうど | gh の失敗 |
| `ci_watch.peek_cause` | 4 つの判定ごとの種類 | 無し | 無し |
| `GreenWatch._stop_failed` | テストのチェックの失敗 → `test_failed` | テストとテスト以外が両方落ちる → `test_failed` | 宣言が無い → `test_failed` |
| `merge._merge_pr` | 衝突 → `base_conflict`・保護の規則 → `not_ci` | 無し | 理由を読めない → `unknown` |
| `commands.cmd_design_gate` | 2 つの結果から承認資料と 10 | 用語チェックの当たりが 0 件 | 結果が無い → `invalid` |
| `judge_call.answer_schema` | 選択肢から `enum` | 選択肢 1 つ | `choices` と `schema` の両方・どちらも無い → 誤り |
| `judge_call.check_answer` | 選択肢の中の答え・任意の JSON の答え | 任意の JSON のモードで `decision` の無い答え | 選択肢の外・`reason` が無い |
| `judge_call.ask` | 1 回目で合う | 1 回目が外れ 2 回目で合う | 2 回とも外れる → `needs_human`・答えの文に選択肢の語を含んでも拾わない |
| `JudgeStep.execute` | 入力の `cause` が材料に構造のまま入る | 入力が無い | `ask` の失敗 |
| `SlowWatch.judge_slow` | 4 つの決定 | `wait_seconds` が無い | 答えが合わない → `needs_human`（`wait` にしない） |
| `queue._attention_response` | `next` が続きの wait のコマンド | attention が承認ゲートだけ | 無し |
| `queue._timeout_response` | `wait_timeout` と同じ wait の `next` | 無し | 無し |
| `progress_model.route_failed` | 工程の `on_cause` が選ばれる | 定義に `on_cause` が無い | 既定が人への種類は `human` |
| `progress_run.sprint_workflow` | `infra_wait` の工程が待って流し直される | 上限ちょうど | 既定が人への種類は `failed` で終わる |
| `progress_run.run_next` | 人へ戻す止まりで `routes` の 2 つと種類の終了コード | 打ち直しの種類は返らずに済む | 進行表を読めない → `invalid` |
| `parallel_cli.wait_plan` | 失われた計画で `next` が `start` | 時間切れで `wait_timeout` | 台帳を読めない |
| `parallel_answer.release_rejectable` | 中身が空・版数が出し済みで理由 | 1 件だけ含む | タグを読めない → `None` |
| `preflight.cmd_check` | 3 本が通る | 2 本目で止まる | 結果を読めない → `invalid` |
| `sprint_state.cmd_gate` | 答えの記録と `routes` | 同じ答えの 2 度目 | 排他の失敗 |
| `step_result.gate` | `approved` と `rejected` を持つ | 値が `null` | 無し |

各子 Issue は、上の行のうち自分の関数の行を新しいテストとして書き、置き換えた範囲の古いテストを同じ PR で消す（設計文書の決定 65）。消す古いテストは次のとおりで、ファイルごと消すものと関数だけを消すものがある。
数えた手段は `grep -lc 'on_exit\|skip_to\|_next_after_step\|on_fail_only\|on_section_missing\|"type": "judge"\|parse_decision' plugins/ndf/scripts/tests/*.py` である。


| 子 | ファイルごと消す | 関数だけを消す（新しいテストへ移す） |
| --- | --- | --- |
| 1b | `plugins/ndf/scripts/tests/test_step_result.py`・`test_step_result_readers.py`（`test_causes.py` と作り直した `test_step_result.py` へ） | `test_run_core.py` の終了コードから結果を組む関数と、依存の欠けの結果が `gaps` に `deps` を持つことを見る関数 |
| 2 | `plugins/ndf/scripts/tests/test_supervise_step_rerun.py`・`test_push_fix.py`・`test_supervise_limits.py`（`test_routes.py`・`test_plan_routes.py` へ） | `test_supervise.py`（`on_exit`・`skip_to` を見る 27 か所）・`test_supervise_pace.py`・`test_supervise_design_match.py`・`test_supervise_new.py`・`test_base_sync.py`・`test_supervise_ci_only.py`・`test_sprint_lane_close.py`・`test_design_review_settled.py`・`test_impl_format_steps.py`・`test_sprint_bundles.py`・`test_supervise_prior_inputs.py`・`test_supervise_pace_verify.py`・`test_gate_rejections.py`・`test_project_mvv.py`・`test_review_criteria.py`・`test_usage_ledger.py`・`test_runtime_policy.py`・`test_drive_review.py` の該当の関数 |
| 3 | 無し | `test_ci_watch.py` の `decide_exit` の `after_merge` の関数 |
| 4 | 無し | `test_ci_watch.py` の終了コード 2・3 を見る関数・`test_merged_probe.py` の CI の失敗の終了コードの関数 |
| 6 | 無し | `test_slow_step.py`・`test_supervise_slow.py` の答えを読めないときに `wait` へ倒す関数 |
| 7 | `plugins/ndf/scripts/tests/test_queue_waits.py`（作り直す） | 無し |
| 11 | 無し | `test_sprint_loop.py` の終了コードと `metrics.blocked` を見る関数 |
| 12 | 無し | `test_token_guard.py` の案内の文を見る関数 |
