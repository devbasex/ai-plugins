"""残る状態の種類の一覧（#2229 の AC1）。唯一の置き場で、確定仕様と `run_state.py` の片付けはこの表を指す。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Kind:
    """残る状態の種類 1 つ。`creators` はこの種類を一時の置き場へ作るスクリプト（`plugins/ndf` からの相対）。"""

    name: str
    place: str
    owner: str
    life: str
    when: str
    creators: tuple[str, ...]


# 状態の一覧（AC1）。NDF のスクリプトが一時の置き場へ作るものは、どれかの行の `creators` に載る（テストが確かめる）。
KINDS: tuple[Kind, ...] = (
    Kind(
        "review-worktree",
        "<base>/<slug>/pr<n> と <base>/<slug>/rf<n>/{work,<ランタイム>}。記録は <base>/<slug>/{pr,rf}<n>.owner.json",
        "cross-review・cross-refactoring の実行（記録の run）",
        "持ち主が終わり、Pull Request が open でなく、作業（変更・未追跡・未 push のコミット）が無い",
        "merged の cleanup・cross-review と cross-refactoring の開始の sweep",
        (
            "scripts/lib/worktree_base.py",
            "scripts/merged-steps.py",
            "scripts/lib/run_state.py",
            "skills/cross-review/scripts/review_lib/workspace.py",
            "skills/cross-refactoring/scripts/refactor_lib/commands/setup.py",
        ),
    ),
    Kind(
        "session-state",
        "<tmp>/ndf-worktree-<session>.json（中の owner）",
        "ランタイムのセッション（プロセスの pid と開始時刻）",
        "持ち主のプロセスが居ない。記録が無ければ最終更新から寿命の窓を過ぎた",
        "sweep",
        ("scripts/hook_lib/worktree.py", "scripts/worktree-session.sh"),
    ),
    Kind(
        "deps-work",
        "<tmp>/ndf-deps.XXXXXX/（中の owner）",
        "worktree-deps.sh のプロセス（pid）",
        "持ち主のプロセスが居ない（cmdline に worktree-deps.sh を含む pid が無い）。記録が無ければ寿命の窓",
        "sweep",
        ("scripts/worktree-deps.sh",),
    ),
    Kind(
        "fix-files",
        "<tmp>/fix-pr<n>-*（CROSS_REVIEW_TMP_DIR が無いときの置き場）",
        "fix の実行",
        "最終更新から寿命の窓を過ぎた",
        "sweep",
        ("skills/fix/scripts/fix-steps.py",),
    ),
    Kind(
        "test-tmp",
        "<tmp>/ndf-tests-*",
        "テストのセッション",
        "最終更新から寿命の窓を過ぎた",
        "sweep・テストのセッションの終わり",
        ("conftest.py",),
    ),
    Kind(
        "post-item",
        "耐久の記録（種類 posts）と pending/dropped/",
        "積んだ実行",
        "送った・既投稿・控えへ外した・取り下げた",
        "Queue.flush（sweep は扱わない）",
        ("scripts/lib/post_queue.py",),
    ),
    Kind(
        "stale-copy",
        "<base>/<slug>/*.stale-<時刻>・work.stale-<時刻>",
        "退避した開始",
        "片付けない（現リポジトリの worktree でない中身で、何を持つか分からない）",
        "—",
        (),
    ),
    Kind(
        "out-of-scope",
        "<tmp> の下の Skill の置き場（ndf/・ndf-stages・ndf-guards・ndf-checks・ndf-usage・token-guard など）",
        "作るスクリプトの実行",
        "片付けない（この課題の対象外。持ち主と寿命は #2243 で決める）",
        "—",
        (
            "scripts/check-trigger.py",
            "scripts/ensure-retention.sh",
            "scripts/hook_lib/token_guard.py",
            "scripts/lib/gh_pr_info.py",
            "scripts/lib/sprint_loop_record.py",
            "scripts/lib/step-result.sh",
            "scripts/lib/step_result.py",
            "scripts/lib/usage_ledger.py",
            "scripts/lib/worktree-registry.sh",
            "scripts/progress-record.sh",
            "scripts/projects-sync.sh",
            "scripts/statusline-switch.sh",
            "scripts/supervise_lib/paths.py",
            "scripts/worktree-setup.sh",
            "skills/backlog-refinement/scripts/upkeep.py",
            "skills/development-workflow/scripts/lib/workflow-common.sh",
            "skills/external-ai/scripts/external-ai.py",
            "skills/pr-review/scripts/pr-review-steps.py",
        ),
    ),
)
SWEPT_KINDS = ("review-worktree", "session-state", "deps-work", "fix-files", "test-tmp")
