"""テンプレートから組んだプランの流れ（遷移の判断を台本の結果で回す）。各テストの docstring に確かめる Issue を書く。"""

from __future__ import annotations

import pytest

from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core.plan.transition_decide import Outcome

from ndf.domains.crossreview import drive

from ._flow import fake_bin, gate, git, plan_of, repo_with_base, run_step, step_of, stopped, walk


def test_fix_plan_runs_test_then_pr_then_merge_when_green():
    """C3-1（#1142 の要求の不足 a）: 即時修正のテンプレートは、テスト → PR → merge-when-green を順に流して終わる。"""
    visited, state = walk(plan_of("immediate_fix"), {})
    assert visited == ["test", "push", "pr", "conflict-check", "ci", "merge"]
    assert state.status == OK


def test_fix_plan_stops_on_failed_test_before_pr():
    """C3-1: 即時修正のテストが落ちたら PR を作らずに止まる（ルートは conductor）。"""
    visited, state = walk(plan_of("immediate_fix"), {"test": [stopped("test_failed")]})
    assert visited == ["test"]
    assert (state.status, state.kind) == (STOPPED, "test_failed")


def test_ci_fix_runs_failed_tests_locally_before_push():
    """#3188: CI の修正は、落ちたテストを手元で打って通してから push する（#3879 から push-fixed。レビューを経ずに CI へ戻る）。

    手元で落ちれば同じ原因の数え（repeat-check）を通って ci-fix へ戻り（push しない）、同じ原因で CI を往復しない。
    CI を待つのは修正の前と後の 2 回だけ。
    """
    script = {"ci": [stopped("test_failed")], "ci-local": [stopped("test_failed")]}
    visited, state = walk(plan_of("implement"), script)
    first_ci = visited.index("ci")
    after = visited[first_ci + 1 :]
    assert after[:6] == ["repeat-check", "ci-fix", "ci-local", "repeat-check", "ci-fix", "ci-local"]
    assert after[6] == "push-fixed"
    assert visited.count("ci") == 2
    assert state.status == OK


def test_push_retries_once_on_transient_rejection():
    """#1653: pr の前の push が一時的な拒否で落ちても、待ってから打ち直して続ける（プランを止めない）。"""
    visited, state = walk(plan_of("implement"), {"push": [stopped("push_rejected")]})
    assert visited[visited.index("push") + 1] == "push"
    assert state.status == OK


def test_push_rejected_twice_goes_to_fix_push():
    """#1653: 打ち直しても断られれば、push 前の検査の指摘を worker が直す（fix-push）へ進む。"""
    visited, _ = walk(plan_of("implement"), {"push": [stopped("push_rejected"), stopped("push_rejected")]})
    i = visited.index("fix-push")
    assert visited[i - 2 : i + 2] == ["push", "push", "fix-push", "push"]


def test_design_merge_returns_to_base_sync_on_conflict():
    """#2273: 設計 PR のマージのプランは、マージ先との衝突（DIRTY）で止まらず、マージ → 直し → push → 確かめ直しへ戻る。"""
    script = {"conflict-check": [stopped("base_conflict")], "base-sync": [stopped("base_conflict")], "merge-gate": [gate()]}
    visited, state = walk(plan_of("design_merge"), script, answers=["approved"])
    assert visited == ["conflict-check", "base-sync", "fix-base", "push", "conflict-check", "merge-gate", "merge"]
    assert state.status == OK


def test_conflicted_pr_is_synced_instead_of_waiting_for_ci():
    """#2752・#2680: 衝突して CI の始まらない PR を待たない。CI の待ちの前に衝突を確かめ、マージ先のマージへ進む。"""
    for purpose in ("implement", "check", "close"):
        visited, state = walk(plan_of(purpose), {"conflict-check": [stopped("base_conflict")]})
        i = visited.index("conflict-check")
        assert visited[i + 1] == "base-sync", purpose
        assert visited.index("ci") > i
        assert state.status == OK


def test_design_worker_gate_ends_plan_at_gate():
    """#2867: 設計のプランで worker が承認ゲートを返すと、失敗でなく承認ゲートで終わる（人の判断待ちへ移せる）。"""
    visited, state = walk(plan_of("design"), {"design": [gate()]})
    assert visited == ["design"]
    assert state.status == GATE


def test_close_plan_ends_without_pr_when_nothing_changed():
    """#3039: 閉じのプランは、確定仕様化が変更を作らなければ PR を作らずに終える（No commits between で止まらない）。"""
    visited, state = walk(plan_of("close"), {"changes-check": [stopped("not_applicable")]})
    assert visited == ["work-start", "spec", "changes-check"]
    assert state.status == OK


def test_merge_plan_fixes_non_test_check_failures_and_pushes():
    """#3474: マージのプランは、テスト以外のチェックの落ち（check_failed）でも止まらず、直して push し直してからマージする。

    落ちたテストが無いので、手元のテスト（ci-local）を打たずに push する。
    """
    visited, state = walk(plan_of("merge"), {"ci": [stopped("check_failed")]})
    assert visited == ["conflict-check", "ci", "check-repeat", "check-fix", "push", "conflict-check", "ci", "merge"]
    assert state.status == OK


def test_plan_to_spec_plan_delivers_the_spec_through_pr_and_merge():
    """#3474: 確定仕様化のプランは、書いた確定仕様を作業場所から push → PR → CI → マージでマージ先へ届ける。"""
    visited, state = walk(plan_of("plan_to_spec"), {})
    assert visited == ["work-start", "work", "changes-check", "push", "pr", "conflict-check", "ci", "merge"]
    assert state.status == OK


def test_docs_only_check_skips_whole_tests():
    """#3023: 文書だけの PR の検査は、全体テストとリファクタリングを打たずにコードレビューへ進む。"""
    visited, state = walk(plan_of("check"), {"assess": [stopped("not_applicable")], "merge-gate": [gate()]}, answers=["approved"])
    assert visited[:2] == ["assess", "review"]
    assert "test-all" not in visited and "refactor" not in visited
    assert state.status == OK


def test_merged_impl_plan_is_not_rerun():
    """#2349: マージ済みの実装のプランを流し直しても、worker も PR の作成も打たずに終える。"""
    visited, state = walk(plan_of("implement"), {"done-check": [stopped("not_applicable")]})
    assert visited == ["done-check"]
    assert state.status == OK


def test_behavior_is_rechecked_right_before_merge_gate():
    """#3003: 振る舞いの確認の後に head が進んでも（CI の修正の push）、マージの承認ゲートの直前に確かめ直す。"""
    script = {"ci": [stopped("test_failed")]}
    visited, _ = walk(plan_of("check"), script, answers=["approved"])
    gate_at = visited.index("merge-gate")
    assert visited[gate_at - 2 : gate_at] == ["ci", "behavior-recheck"]
    assert visited.index("ci-fix") < gate_at


def test_review_incomplete_continues_check():
    """#2370: コードレビューが欠けを残して終わっても（incomplete）、検査ごと落とさずに振る舞いの確認へ進む。"""
    visited, state = walk(plan_of("check"), {"review": [stopped("incomplete")]}, answers=["approved"])
    assert visited[visited.index("review") + 1] == "behavior"
    assert state.status == OK


def test_refactor_gets_sync_command():
    """#2049: 検査のプランのリファクタリングに --sync-command を渡す（生成元に触れる改善が push で止まらない）。"""
    step = step_of(plan_of("check", refactor=["ndf-refactor"], sync_command="make gen"), "refactor")
    assert step.run[step.run.index("--sync-command") + 1] == "make gen"


def test_same_ci_failure_streak_stops_to_human():
    """#2844: CI の落ちが同じ原因で続いたら（repeat-check が fix_limit）、修正を返す循環を止めて人へ戻す。"""
    visited, state = walk(plan_of("implement"), {"ci": [stopped("test_failed")], "repeat-check": [stopped("fix_limit")]})
    assert visited[-2:] == ["ci", "repeat-check"]
    assert (state.status, state.kind) == (STOPPED, "fix_limit")


# 現状固定（I-001）: 検査と設計 PR のマージのテンプレートが同じ形で持つマージの承認ゲートとマージ。
# 部品へまとめても、埋めたプランのステップと遷移が変わらないことを確かめる。
_MERGE_GATE = {"id": "merge-gate", "run": ["<gate>"], "on_answer": {"approved": "merge", "rejected": "end"}}


def test_merge_gate_and_merge_steps_are_fixed_in_check_and_design_merge():
    """現状固定: check と design_merge の merge-gate と merge は、埋めた後に同じステップになる。"""
    found = {p: [s for s in plan_of(p)["steps"] if s["id"] in ("merge-gate", "merge")] for p in ("check", "design_merge")}
    assert found["check"] == found["design_merge"]
    gate_step, merge = found["check"]
    assert gate_step == _MERGE_GATE
    assert (merge["run"], merge["next"]) == (
        ["ndf", "ci", "merge", "--branch", "<branch>", "--method", "<merge_method>"],
        "end",
    )


def test_merge_gate_rejected_ends_without_merge():
    """現状固定: マージの承認ゲートで rejected と回答すると、merge を打たずに ok で終わる。"""
    expected_tail = {"check": ["ci", "behavior-recheck", "merge-gate"], "design_merge": ["conflict-check", "merge-gate"]}
    for purpose, tail in expected_tail.items():
        visited, state = walk(plan_of(purpose), {"merge-gate": [gate()]}, answers=["rejected"])
        assert visited[-len(tail) :] == tail, purpose
        assert "merge" not in visited, purpose
        assert state.status == OK, purpose


def test_merge_gate_approved_merges_then_ends():
    """現状固定: マージの承認ゲートで approved と回答すると、merge を打って終わる。"""
    for purpose in ("check", "design_merge"):
        visited, state = walk(plan_of(purpose), {"merge-gate": [gate()]}, answers=["approved"])
        assert visited[-2:] == ["merge-gate", "merge"], purpose
        assert state.status == OK, purpose


def test_same_local_test_failure_streak_stops_to_human():
    """手元のテストの落ち（test → fix → test、検査では test-all → fix → test-all）も同じ原因の数え（test-repeat）を通り、
    続けば fix_limit で人へ戻す（プラン全体のステップの上限まで worker とテストを繰り返さない）。"""
    for purpose, test_id in (("implement", "test"), ("check", "test-all")):
        script = {test_id: [stopped("test_failed")] * 5, "test-repeat": [Outcome(OK), stopped("fix_limit")]}
        visited, state = walk(plan_of(purpose), script)
        i = visited.index(test_id)
        assert visited[i:] == [test_id, "test-repeat", "fix", test_id, "test-repeat"], purpose
        assert (state.status, state.kind) == (STOPPED, "fix_limit"), purpose


def test_design_plan_checks_ci_after_push():
    """#2932（#2741 の形）: 設計のプランは push の後に CI を待つ。CI の合格は push の前の worker（design）でなく、
    push → PR → レビュー → push の後の CI の待ちが確かめる。"""
    visited, state = walk(plan_of("design"), {})
    assert visited == ["design", "rebuild", "push", "pr", "head-check", "review", "push-reviewed", "conflict-check", "ci"]
    assert state.status == OK


@pytest.mark.parametrize("kind", ["test_failed", "check_failed"])
def test_design_plan_ci_failure_is_fixed_then_pushed_and_rechecked(kind):
    """#2932: 設計の CI が落ちたら（テストでも、設計文書の検査・静的解析などテスト以外のチェックでも）同じ原因の数えを
    通って worker が直し、push してから CI を待ち直す（止まらない）。"""
    visited, state = walk(plan_of("design"), {"ci": [stopped(kind)]})
    i = visited.index("ci")
    assert visited[i + 1 :] == ["repeat-check", "ci-fix", "push-reviewed", "conflict-check", "ci"]
    assert state.status == OK


def test_design_plan_stops_before_review_when_pr_head_lags():
    """#2932（#2721 の形）: PR の作成の後、手元のコミットが PR の head に入っていなければ、間を置いて 3 回まで読み直し、
    それでも違えばレビューへ進まずに止まる。"""
    visited, state = walk(plan_of("design"), {"head-check": [stopped("precondition")] * 4})
    assert visited[-5:] == ["pr", "head-check", "head-check", "head-check", "head-check"]
    assert "review" not in visited
    assert (state.status, state.kind) == (STOPPED, "precondition")


def test_design_plan_rereads_pr_head_until_github_catches_up():
    """#3292: 既存の PR へ push した直後は GitHub の PR の head の更新が遅れる。読み直して揃えばレビューへ進む。"""
    visited, state = walk(plan_of("design"), {"head-check": [stopped("precondition")]})
    i = visited.index("head-check")
    assert visited[i : i + 3] == ["head-check", "head-check", "review"]
    assert state.status == OK


def test_design_review_gets_rebuild_findings():
    """#2444: 組み直しが残した「足すべき内容」（rebuild の --out のファイル）を、後のレビューの --findings へ渡す。

    レビューが欠けを残して終わっても（incomplete）設計のプランを落とさずに push と CI へ進む。
    """
    plan = plan_of("design", rebuild=["ndf-rebuild"], review=["ndf-review"])
    rebuild, review = step_of(plan, "rebuild"), step_of(plan, "review")
    out = rebuild.run[rebuild.run.index("--out") + 1]
    assert review.run[review.run.index("--findings") + 1] == out
    visited, state = walk(plan, {"review": [stopped("incomplete")]})
    assert visited.index("rebuild") < visited.index("review")
    assert visited[visited.index("review") + 1] == "push-reviewed"
    assert state.status == OK


def test_impl_plan_ends_when_ci_passes_at_the_reviewed_head():
    """#3879 決定 2: 実装のプランは単位の PR をレビューしてから CI を待ち、CI が通った HEAD がレビューした HEAD なら終える。"""
    visited, state = walk(plan_of("implement"), {})
    assert visited == [
        "done-check", "work", "acceptance", "test", "push", "pr", "conflict-check", "base-fetch", "review",
        "push-reviewed", "review-head", "ci", "head-reviewed", "resolved",
    ]  # fmt: skip
    assert state.status == OK


def test_impl_plan_reviews_the_commits_after_the_reviewed_head():
    """#3879 決定 2: CI の修正の後に CI が通っても、レビューした HEAD の後のコミットがあれば、その差分をレビューして
    レビューした HEAD を書き直し、CI を待ち直してから終える。"""
    script = {"ci": [stopped("test_failed")], "head-reviewed": [stopped("unreviewed")]}
    visited, state = walk(plan_of("implement"), script)
    first = visited.index("head-reviewed")
    assert visited[visited.index("ci") + 1 : first] == ["repeat-check", "ci-fix", "ci-local", "push-fixed", "ci"]
    assert visited[first:] == ["head-reviewed", "review-delta", "push-reviewed", "review-head", "ci", "head-reviewed", "resolved"]
    assert state.status == OK


def test_impl_reviews_the_fix_of_the_last_round_before_ci():
    """PR 3888 の指摘: レビューが最後のラウンドの修正のコミットを残して incomplete で終えると、review-head が HEAD の
    食い違い（unreviewed）を見つけ、CI の前にその差分をレビューする。PR 3904 の指摘: 未解決の印を置いて CI まで進め、
    最後の resolved が印を見つけて incomplete で止まる（未解決のまま終えない）。"""
    script = {"review": [stopped("incomplete")], "review-head": [stopped("unreviewed")], "resolved": [stopped("incomplete")]}
    visited, state = walk(plan_of("implement"), script)
    first = visited.index("review")
    assert visited[first:] == [
        "review", "mark-unresolved", "push-reviewed", "review-head", "review-delta", "push-reviewed", "review-head", "ci",
        "head-reviewed", "resolved",
    ]  # fmt: skip
    assert (state.status, state.kind) == (STOPPED, "incomplete")


def test_impl_keeps_unresolved_findings_past_the_delta_review(tmp_path):
    """PR 3904 の指摘: 単位のレビューが未解決を残すと印を置き、差分のレビュー（別の状態ディレクトリ）が通っても印は残って
    resolved が incomplete で止まる。印を消すのは単位のレビューの始めだけ。"""
    repo = repo_with_base(tmp_path, {"a.py": "a\n"})
    state = tmp_path / "state"
    state.mkdir()
    fill = {"state_dir": str(state)}
    bindir = fake_bin(tmp_path, "review-ok", 'while [ "$1" != --reviewed-out ]; do shift; done; git rev-parse HEAD > "$2"')
    plan = plan_of("implement", review=[str(bindir / "review-ok")])
    assert run_step(step_of(plan, "review"), repo, fill=fill)["status"] == OK
    assert run_step(step_of(plan, "resolved"), repo, fill=fill)["status"] == OK
    assert run_step(step_of(plan, "mark-unresolved"), repo, fill=fill)["status"] == OK
    assert run_step(step_of(plan, "review-delta"), repo, fill=fill)["status"] == OK
    found = run_step(step_of(plan, "resolved"), repo, fill=fill)
    assert (found["status"], found["kind"]) == (STOPPED, "incomplete")
    assert run_step(step_of(plan, "review"), repo, fill=fill)["status"] == OK
    assert run_step(step_of(plan, "resolved"), repo, fill=fill)["status"] == OK


def test_impl_rechecks_the_delta_findings_after_resuming_from_the_unit_review(tmp_path):
    """PR 3904 の指摘 r2-1: 差分のレビューが未解決を残して止まった後に単位のレビューから再開しても、差分の印は消えない。
    単位のレビューが今の HEAD を記録しても review-head が unreviewed を返し、差分のレビューを同じ起点・同じ状態ディレクトリで
    打ち直す。それが通るまで resolved は incomplete で止まる。"""
    repo = repo_with_base(tmp_path, {"a.py": "a\n"})
    state = tmp_path / "state"
    state.mkdir()
    fill = {"state_dir": str(state)}
    base = git(repo, "rev-parse", "HEAD").strip()
    (state / "reviewed-head").write_text(base + "\n", encoding="utf-8")
    (repo / "b.py").write_text("b\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "fix")
    # 偽のレビュー: 引数を残し、印のファイル（$0.fail）があれば incomplete、無ければ今の HEAD を記録して ok
    bindir = fake_bin(
        tmp_path,
        "review",
        'printf "%s\\n" "$@" > "$0.out"; [ ! -e "$0.fail" ] || exit 1; '
        'while [ "$1" != --reviewed-out ]; do shift; done; git rev-parse HEAD > "$2"',
    )
    plan = plan_of("implement", review=[str(bindir / "review")])
    (bindir / "review.fail").touch()
    assert run_step(step_of(plan, "review-delta"), repo, fill=fill)["status"] == STOPPED
    assert run_step(step_of(plan, "mark-delta-unresolved"), repo, fill=fill)["status"] == OK
    assert run_step(step_of(plan, "resolved"), repo, fill=fill)["kind"] == "incomplete"
    (bindir / "review.fail").unlink()
    # 単位のレビューから再開する。単位のレビューは差分の指摘を知らずに通り、今の HEAD を記録する
    assert run_step(step_of(plan, "review"), repo, fill=fill)["status"] == OK
    assert run_step(step_of(plan, "resolved"), repo, fill=fill)["kind"] == "incomplete"
    found = run_step(step_of(plan, "review-head"), repo, fill=fill)
    assert (found["status"], found["kind"]) == (STOPPED, "unreviewed")
    assert run_step(step_of(plan, "review-delta"), repo, fill=fill)["status"] == OK
    args = (bindir / "review.out").read_text().split()
    assert args[args.index("--base") + 1] == base
    assert args[args.index("--state-dir") + 1] == f"{state}/cross-review-delta"
    for step_id in ("review-head", "head-reviewed", "resolved"):
        assert run_step(step_of(plan, step_id), repo, fill=fill)["status"] == OK
    assert not (state / "delta-base").exists()


def test_impl_review_steps_record_the_head_the_review_read(tmp_path):
    """#3879 構成要素 1・PR 3888 の指摘: review と review-delta はレビューの起動に --reviewed-out を足し、レビューした HEAD を
    起動に書かせる。review-head・head-reviewed は書かれた HEAD と今の HEAD を比べ、進めば unreviewed、記録が無ければ止まる。
    review-delta はレビューした HEAD を起点（--base）に、最初のレビューと分けた状態ディレクトリで打つ。"""
    repo = repo_with_base(tmp_path, {"a.py": "a\n"})
    state = tmp_path / "state"
    state.mkdir()
    fill = {"state_dir": str(state)}
    head = git(repo, "rev-parse", "HEAD").strip()
    for step_id in ("review-head", "head-reviewed"):
        assert run_step(step_of(plan_of("implement"), step_id), repo, fill=fill)["status"] == STOPPED
    bindir = fake_bin(
        tmp_path,
        "write-head",
        'printf "%s\\n" "$@" > "$0.out"; while [ "$1" != --reviewed-out ]; do shift; done; git rev-parse HEAD > "$2"',
    )
    plan = plan_of("implement", review=[str(bindir / "write-head")])
    assert run_step(step_of(plan, "review"), repo, fill=fill)["status"] == OK
    assert (state / "reviewed-head").read_text().strip() == head
    for step_id in ("review-head", "head-reviewed"):
        assert run_step(step_of(plan, step_id), repo, fill=fill)["status"] == OK
    (repo / "b.py").write_text("b\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "fix")
    for step_id in ("review-head", "head-reviewed"):
        found = run_step(step_of(plan, step_id), repo, fill=fill)
        assert (found["status"], found["kind"]) == (STOPPED, "unreviewed")
    run_step(step_of(plan, "review-delta"), repo, fill=fill)
    args = (bindir / "write-head.out").read_text().split()
    assert args[args.index("--base") + 1] == head
    assert args[args.index("--state-dir") + 1] == f"{state}/cross-review-delta"
    assert args[args.index("--reviewed-out") + 1] == f"{state}/reviewed-head"
    assert run_step(step_of(plan, "head-reviewed"), repo, fill=fill)["status"] == OK


def test_cross_review_takes_a_remote_ref_or_a_commit_as_the_base(tmp_path):
    """#3879 の未確認の 1 行: cross-review は --base を差分（base...HEAD）の起点にだけ使い、ブランチ名として扱わない。
    実装のプランが渡す <リモート>/<base> と、review-delta が渡すコミットのどちらでも、変わったファイルを読める。"""
    repo = repo_with_base(tmp_path, {"doc.md": "# 見出し\n"})
    sha = git(repo, "rev-parse", "origin/develop").strip()
    for base in ("origin/develop", sha):
        assert drive.changed_sections(drive.git.Git(repo), base, repo) == ["doc.md#見出し"]
