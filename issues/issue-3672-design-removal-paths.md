# #3672 の削除の一覧のパス

[issue-3672-design-removal.md](issue-3672-design-removal.md) の削除の一覧のうち、複数のファイルにまたがる行（D32・D33・D34・D37）のパスを 1 ファイル 1 行で並べた文書である。
実装はこの表の各行を `scripts/legacy-branch-decl.json` の 1 行にする。`scripts/check-legacy-branch-decl.py` は、各行のパスに消す語が残らないことを照らす（受け入れ条件 21）。
行の番号は `D<番号>-<連番>` で、消す語と置き換え先は削除の一覧の同じ番号の行のとおりである。

## 数えたコマンド

2026-11-16、このブランチの設計のコミット 356720a0c で、次の語の集合を数えた。

```bash
W='branch_flow|production_merge|version_store|base_branch|production_branch|promotion|dev_workflow|"delivery"|\.delivery'
grep -rlE "$W" plugins/ndf/skills plugins/ndf/agents --include=*.md | grep -v /tests/ | grep -v 'worktree/references/declaration.md'   # D32
grep -rlE "$W" plugins/ndf-next --include=*.md | grep -v 'worktree/references/declaration.md'                                        # D33
grep -rlE "$W" docs/specifications                                                                                                    # D34
grep -rlE "$W" plugins/ndf/scripts/tests plugins/ndf/skills/*/tests plugins/ndf-next/tests scripts/tests                              # D37
```

`worktree/references/declaration.md` は削除の一覧の D31 の行が持つため、D32・D33 から外した。
実装の時点までに当たるファイルが増えたときは、同じコマンドで数え直し、増えた分を `scripts/legacy-branch-decl.json` に足す。

## D32: 10 系の Skill の本文とエージェントの定義

| # | パス |
| --- | --- |
| D32-1 | `plugins/ndf/agents/code-reviewer.md` |
| D32-2 | `plugins/ndf/skills/cherry-pick-pr/SKILL.md` |
| D32-3 | `plugins/ndf/skills/cross-review/docs/02-fix-and-rotation.md` |
| D32-4 | `plugins/ndf/skills/cross-review/docs/04-contracts.md` |
| D32-5 | `plugins/ndf/skills/deploy/SKILL.md` |
| D32-6 | `plugins/ndf/skills/development-workflow/references/agent-layers.md` |
| D32-7 | `plugins/ndf/skills/development-workflow/references/document-destinations.md` |
| D32-8 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| D32-9 | `plugins/ndf/skills/development-workflow/references/loop.md` |
| D32-10 | `plugins/ndf/skills/development-workflow/references/pace.md` |
| D32-11 | `plugins/ndf/skills/development-workflow/references/parallel.md` |
| D32-12 | `plugins/ndf/skills/development-workflow/references/parallel-work.md` |
| D32-13 | `plugins/ndf/skills/development-workflow/references/production-merge.md` |
| D32-14 | `plugins/ndf/skills/development-workflow/references/project-analysis.md` |
| D32-15 | `plugins/ndf/skills/development-workflow/SKILL.md` |
| D32-16 | `plugins/ndf/skills/merged/SKILL.md` |
| D32-17 | `plugins/ndf/skills/ndf-policies/SKILL.md` |
| D32-18 | `plugins/ndf/skills/pr/SKILL.md` |
| D32-19 | `plugins/ndf/skills/release/references/cd-setup.md` |
| D32-20 | `plugins/ndf/skills/release/references/form-package-plugin.md` |
| D32-21 | `plugins/ndf/skills/release/references/release-steps.md` |
| D32-22 | `plugins/ndf/skills/release/SKILL.md` |
| D32-23 | `plugins/ndf/skills/retrospective/SKILL.md` |
| D32-24 | `plugins/ndf/skills/worktree/SKILL.md` |

## D33: 11.0 の Skill の本文・契約の文書・エージェントの定義

| # | パス |
| --- | --- |
| D33-1 | `plugins/ndf-next/agents/code-reviewer.md` |
| D33-2 | `plugins/ndf-next/docs/contracts/commands.md` |
| D33-3 | `plugins/ndf-next/docs/contracts/declarations.md` |
| D33-4 | `plugins/ndf-next/skills/cherry-pick-pr/SKILL.md` |
| D33-5 | `plugins/ndf-next/skills/cross-review/docs/02-fix-and-rotation.md` |
| D33-6 | `plugins/ndf-next/skills/cross-review/docs/04-contracts.md` |
| D33-7 | `plugins/ndf-next/skills/deploy/SKILL.md` |
| D33-8 | `plugins/ndf-next/skills/development-workflow/references/agent-layers.md` |
| D33-9 | `plugins/ndf-next/skills/development-workflow/references/document-destinations.md` |
| D33-10 | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| D33-11 | `plugins/ndf-next/skills/development-workflow/references/loop.md` |
| D33-12 | `plugins/ndf-next/skills/development-workflow/references/pace.md` |
| D33-13 | `plugins/ndf-next/skills/development-workflow/references/parallel.md` |
| D33-14 | `plugins/ndf-next/skills/development-workflow/references/parallel-work.md` |
| D33-15 | `plugins/ndf-next/skills/development-workflow/references/production-merge.md` |
| D33-16 | `plugins/ndf-next/skills/development-workflow/references/project-analysis.md` |
| D33-17 | `plugins/ndf-next/skills/development-workflow/SKILL.md` |
| D33-18 | `plugins/ndf-next/skills/merged/SKILL.md` |
| D33-19 | `plugins/ndf-next/skills/ndf-policies/SKILL.md` |
| D33-20 | `plugins/ndf-next/skills/pr/SKILL.md` |
| D33-21 | `plugins/ndf-next/skills/release/references/cd-setup.md` |
| D33-22 | `plugins/ndf-next/skills/release/references/form-package-plugin.md` |
| D33-23 | `plugins/ndf-next/skills/release/references/release-steps.md` |
| D33-24 | `plugins/ndf-next/skills/release/SKILL.md` |
| D33-25 | `plugins/ndf-next/skills/retrospective/SKILL.md` |
| D33-26 | `plugins/ndf-next/skills/worktree/SKILL.md` |

## D34: 確定仕様

| # | パス |
| --- | --- |
| D34-1 | `docs/specifications/cross-review-review-scope.md` |
| D34-2 | `docs/specifications/ndf-branch-flow-declaration.md` |
| D34-3 | `docs/specifications/ndf-branch-flow-review-units.md` |
| D34-4 | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| D34-5 | `docs/specifications/ndf-entrypoint-contracts.md` |
| D34-6 | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| D34-7 | `docs/specifications/ndf-next-repo-neutral-defaults.md` |
| D34-8 | `docs/specifications/ndf-parallel-issue-bundles.md` |
| D34-9 | `docs/specifications/ndf-parallel-organizer.md` |
| D34-10 | `docs/specifications/ndf-production-release-record.md` |
| D34-11 | `docs/specifications/ndf-release-approval-on-pr.md` |
| D34-12 | `docs/specifications/ndf-release-approved-commit.md` |
| D34-13 | `docs/specifications/ndf-release-production-admin-merge.md` |
| D34-14 | `docs/specifications/ndf-repo-assumptions-check.md` |
| D34-15 | `docs/specifications/ndf-supervise-base-sync.md` |
| D34-16 | `docs/specifications/ndf-supervise-production-default-branch.md` |
| D34-17 | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| D34-18 | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| D34-19 | `docs/specifications/pr-review-steps-script.md` |
| D34-20 | `docs/specifications/README.md` |

## D37: テスト

| # | パス |
| --- | --- |
| D37a-1 | `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/check.json` |
| D37a-2 | `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/close/1-close.json` |
| D37a-3 | `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/close/2-check.json` |
| D37a-4 | `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/close/3-release.json` |
| D37a-5 | `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/close/4-release-prod.json` |
| D37a-6 | `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/close/sprint.json` |
| D37a-7 | `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/fix.json` |
| D37a-8 | `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/impl.json` |
| D37a-9 | `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/release.json` |
| D37a-10 | `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/sprint/10-release-prod.json` |
| D37a-11 | `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/sprint/6-impl-16.json` |
| D37a-12 | `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/sprint/6-impl-17.json` |
| D37a-13 | `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/sprint/7-check.json` |
| D37a-14 | `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/sprint/8-release.json` |
| D37a-15 | `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/sprint/sprint.json` |
| D37a-16 | `plugins/ndf/scripts/tests/fixtures/wait_notice_corpus.json` |
| D37a-17 | `plugins/ndf/scripts/tests/release_repo.py` |
| D37a-18 | `plugins/ndf/scripts/tests/test_branch_flow.py` |
| D37a-19 | `plugins/ndf/scripts/tests/test_bump_keeps_instructions.py` |
| D37a-20 | `plugins/ndf/scripts/tests/test_check_trigger.py` |
| D37a-21 | `plugins/ndf/scripts/tests/test_delivery.py` |
| D37a-22 | `plugins/ndf/scripts/tests/test_design_pr_merge.py` |
| D37a-23 | `plugins/ndf/scripts/tests/test_doc_lint.py` |
| D37a-24 | `plugins/ndf/scripts/tests/test_drive_review.py` |
| D37a-25 | `plugins/ndf/scripts/tests/test_experimental.py` |
| D37a-26 | `plugins/ndf/scripts/tests/test_gate1_requests.py` |
| D37a-27 | `plugins/ndf/scripts/tests/test_impl_format_steps.py` |
| D37a-28 | `plugins/ndf/scripts/tests/test_lib_decl.py` |
| D37a-29 | `plugins/ndf/scripts/tests/test_lib_proc.py` |
| D37a-30 | `plugins/ndf/scripts/tests/test_parallel_external.py` |
| D37a-31 | `plugins/ndf/scripts/tests/test_parallel_lane.py` |
| D37a-32 | `plugins/ndf/scripts/tests/test_parallel_plan.py` |
| D37a-33 | `plugins/ndf/scripts/tests/test_parallel_run.py` |
| D37a-34 | `plugins/ndf/scripts/tests/test_production_default_branch.py` |
| D37a-35 | `plugins/ndf/scripts/tests/test_project_decl_branch_flow.py` |
| D37a-36 | `plugins/ndf/scripts/tests/test_project_decl.py` |
| D37a-37 | `plugins/ndf/scripts/tests/test_pr_steps.py` |
| D37a-38 | `plugins/ndf/scripts/tests/test_push_fix.py` |
| D37a-39 | `plugins/ndf/scripts/tests/test_release_approval_trail.py` |
| D37a-40 | `plugins/ndf/scripts/tests/test_release_approved_commit.py` |
| D37a-41 | `plugins/ndf/scripts/tests/test_release_case.py` |
| D37a-42 | `plugins/ndf/scripts/tests/test_release_cd.py` |
| D37a-43 | `plugins/ndf/scripts/tests/test_release_notes_changelog.py` |
| D37a-44 | `plugins/ndf/scripts/tests/test_release_production_flow.py` |
| D37a-45 | `plugins/ndf/scripts/tests/test_release_record.py` |
| D37a-46 | `plugins/ndf/scripts/tests/test_release_replaced.py` |
| D37a-47 | `plugins/ndf/scripts/tests/test_release_selected.py` |
| D37a-48 | `plugins/ndf/scripts/tests/test_release_steps.py` |
| D37a-49 | `plugins/ndf/scripts/tests/test_release_version_merge.py` |
| D37a-50 | `plugins/ndf/scripts/tests/test_release_version.py` |
| D37a-51 | `plugins/ndf/scripts/tests/test_review_units.py` |
| D37a-52 | `plugins/ndf/scripts/tests/test_run_state.py` |
| D37a-53 | `plugins/ndf/scripts/tests/test_sprint_branch_flow.py` |
| D37a-54 | `plugins/ndf/scripts/tests/test_sprint_close_merge_green.py` |
| D37a-55 | `plugins/ndf/scripts/tests/test_sprint_loop.py` |
| D37a-56 | `plugins/ndf/scripts/tests/test_sprint_procedures.py` |
| D37a-57 | `plugins/ndf/scripts/tests/test_sprint_start_record.py` |
| D37a-58 | `plugins/ndf/scripts/tests/test_stage_skills.py` |
| D37a-59 | `plugins/ndf/scripts/tests/test_step_scripts.py` |
| D37a-60 | `plugins/ndf/scripts/tests/test_supervise_contract.py` |
| D37a-61 | `plugins/ndf/scripts/tests/test_supervise_new.py` |
| D37a-62 | `plugins/ndf/scripts/tests/test_supervise_pace_auto.py` |
| D37a-63 | `plugins/ndf/scripts/tests/test_supervise_pace_manual.py` |
| D37a-64 | `plugins/ndf/scripts/tests/test_supervise_pace.py` |
| D37a-65 | `plugins/ndf/scripts/tests/test_supervise_pace_verify.py` |
| D37a-66 | `plugins/ndf/scripts/tests/test_supervise_pr_marks.py` |
| D37a-67 | `plugins/ndf/scripts/tests/test_supervise.py` |
| D37a-68 | `plugins/ndf/scripts/tests/test_supervise_step_budgets.py` |
| D37a-69 | `plugins/ndf/scripts/tests/test_tool_paths.py` |
| D37a-70 | `plugins/ndf/scripts/tests/test_worktree_default_branch.py` |
| D37a-71 | `plugins/ndf/scripts/tests/test_worktree_lib_split.py` |
| D37b-1 | `plugins/ndf/skills/backlog-refinement/tests/fixtures/overhaul-20261101.json` |
| D37b-2 | `plugins/ndf/skills/backlog-refinement/tests/test_related_skills_characterization.py` |
| D37b-3 | `plugins/ndf/skills/cross-refactoring/tests/crossref_helpers.py` |
| D37b-4 | `plugins/ndf/skills/cross-refactoring/tests/test_prepare_worktrees.py` |
| D37b-5 | `plugins/ndf/skills/cross-review/tests/test_review_criteria_flow.py` |
| D37b-6 | `plugins/ndf/skills/cross-review/tests/test_review_scope.py` |
| D37b-7 | `plugins/ndf/skills/cross-review/tests/test_rotate_pr_queue.py` |
| D37b-8 | `plugins/ndf/skills/cross-review/tests/test_state_judge_ci.py` |
| D37b-9 | `plugins/ndf/skills/cross-review/tests/test_state_offline_fetch.py` |
| D37b-10 | `plugins/ndf/skills/cross-review/tests/test_state_reopen.py` |
| D37b-11 | `plugins/ndf/skills/cross-review/tests/test_state_rest_metadata.py` |
| D37b-12 | `plugins/ndf/skills/cross-review/tests/test_state_resume_args.py` |
| D37b-13 | `plugins/ndf/skills/cross-review/tests/test_state_review_pool.py` |
| D37b-14 | `plugins/ndf/skills/development-workflow/tests/test_workflow_guard.py` |
| D37b-15 | `plugins/ndf/skills/pr-review/tests/test_pr_review_steps.py` |
| D37b-16 | `plugins/ndf/skills/worktree/tests/test_base_branch.py` |
| D37b-17 | `plugins/ndf/skills/worktree/tests/test_branch_resolution.py` |
| D37b-18 | `plugins/ndf/skills/worktree/tests/test_create_from.py` |
| D37b-19 | `plugins/ndf/skills/worktree/tests/test_declaration_local.py` |
| D37b-20 | `plugins/ndf/skills/worktree/tests/test_production_branch.py` |
| D37b-21 | `plugins/ndf/skills/worktree/tests/test_session.py` |
| D37b-22 | `plugins/ndf/skills/worktree/tests/test_setup.py` |
| D37b-23 | `plugins/ndf/skills/worktree/tests/worktree_helpers.py` |
| D37c-1 | `plugins/ndf-next/tests/domains/backlog/test_settle.py` |
| D37c-2 | `plugins/ndf-next/tests/domains/hooks/_hooks.py` |
| D37c-3 | `plugins/ndf-next/tests/domains/project/test_project_cli.py` |
| D37c-4 | `plugins/ndf-next/tests/domains/project/test_project_measure.py` |
| D37c-5 | `plugins/ndf-next/tests/domains/records/test_cli.py` |
| D37c-6 | `plugins/ndf-next/tests/domains/records/test_mvv.py` |
| D37c-7 | `plugins/ndf-next/tests/domains/release/test_release_approval.py` |
| D37c-8 | `plugins/ndf-next/tests/domains/release/test_release_verify.py` |
| D37c-9 | `plugins/ndf-next/tests/domains/test_branch_flows.py` |
| D37c-10 | `plugins/ndf-next/tests/domains/test_declarations.py` |
| D37c-11 | `plugins/ndf-next/tests/domains/worktree/conftest.py` |
| D37c-12 | `plugins/ndf-next/tests/domains/worktree/test_worktree_decide.py` |
| D37c-13 | `plugins/ndf-next/tests/domains/worktree/test_worktree_env_decide.py` |
| D37c-14 | `plugins/ndf-next/tests/domains/worktree/test_worktree_envs.py` |
| D37c-15 | `plugins/ndf-next/tests/domains/worktree/test_worktree_git.py` |
| D37c-16 | `plugins/ndf-next/tests/domains/worktree/test_worktree_merged.py` |
| D37c-17 | `plugins/ndf-next/tests/progress/lanes.py` |
| D37c-18 | `plugins/ndf-next/tests/progress/test_definition.py` |
| D37c-19 | `plugins/ndf-next/tests/progress/test_invoke.py` |
| D37c-20 | `plugins/ndf-next/tests/progress/test_run.py` |
| D37c-21 | `plugins/ndf-next/tests/progress/test_sprint_templates.py` |
| D37c-22 | `plugins/ndf-next/tests/route/test_cli.py` |
| D37c-23 | `plugins/ndf-next/tests/skills/test_schema_refs.py` |
| D37d-1 | `scripts/tests/test_base_branch_consistency.py` |
| D37d-2 | `scripts/tests/test_check_ndf_contracts.py` |
| D37d-3 | `scripts/tests/test_ci_heavy_skip.py` |
| D37d-4 | `scripts/tests/test_pr_base_guard.py` |
