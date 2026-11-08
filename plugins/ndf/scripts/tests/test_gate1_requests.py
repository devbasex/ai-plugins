"""承認の依頼の部品（#2510）: 取り込みのコミットだけかの判定（本物の git）・前のループの待ちの読み出しと当て方。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import design_pr_merge  # noqa: E402
import gate1_requests as g1  # noqa: E402
import parallel_lane as pl  # noqa: E402
import parallel_plan as pp  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def git(cwd, *args):
    return subprocess.run([*GIT, *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def commit(cwd, name, text):
    (cwd / name).parent.mkdir(exist_ok=True)
    (cwd / name).write_text(text)
    git(cwd, "add", "-A")
    git(cwd, "commit", "-qm", name)
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    """起点 develop と設計のブランチ、refs/pull/7/head を持つ origin の複製。"""
    bare = tmp_path / "o.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", str(bare)], check=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "clone", "-q", str(bare), str(wt)], check=True, capture_output=True)
    (wt / ".ndf").mkdir()
    (wt / ".ndf/worktree.json").write_text('{"version": 1, "base_branch": "develop", "production_branch": "main"}\n')
    commit(wt, "a.txt", "1")
    git(wt, "push", "-q", "origin", "HEAD:develop")
    git(wt, "switch", "-qc", "design")
    old = commit(wt, "issues/issue-7-design.md", "設計")
    git(wt, "push", "-q", "origin", "HEAD:refs/pull/7/head")
    git(wt, "switch", "-q", "develop")
    return SimpleNamespace(wt=wt, old=old)


def judge(repo, new):
    git(repo.wt, "push", "-qf", "origin", f"{new}:refs/pull/7/head")
    return g1.merge_only(design_pr_merge.run_cmd, repo.wt, 7, 7, repo.old, new)


def merge_base_in(repo):
    commit(repo.wt, "b.txt", "2")
    git(repo.wt, "push", "-q", "origin", "HEAD:develop")
    git(repo.wt, "switch", "-q", "design")
    git(repo.wt, "merge", "-q", "--no-edit", "develop")
    return git(repo.wt, "rev-parse", "HEAD")


def test_a_base_merge_only_is_true(repo):
    assert judge(repo, merge_base_in(repo)) is True


def test_a_commit_on_the_branch_is_false(repo):
    git(repo.wt, "switch", "-q", "design")
    assert judge(repo, commit(repo.wt, "c.txt", "3")) is False


def test_a_design_doc_change_inside_the_merge_is_false(repo):
    merge_base_in(repo)
    (repo.wt / "issues/issue-7-design.md").write_text("変えた")
    git(repo.wt, "add", "-A")
    git(repo.wt, "commit", "-q", "--amend", "--no-edit")
    assert judge(repo, git(repo.wt, "rev-parse", "HEAD")) is False


def test_a_merge_of_something_outside_the_base_is_false(repo):
    git(repo.wt, "switch", "-qc", "side", repo.old)
    side = commit(repo.wt, "s.txt", "s")
    git(repo.wt, "switch", "-q", "design")
    git(repo.wt, "merge", "-q", "--no-edit", "--no-ff", side)
    assert judge(repo, git(repo.wt, "rev-parse", "HEAD")) is False


def test_it_cannot_decide_without_a_stamp_or_a_fetch(repo):
    new = merge_base_in(repo)
    assert g1.merge_only(design_pr_merge.run_cmd, repo.wt, 7, 7, "", new) is None
    assert g1.merge_only(design_pr_merge.run_cmd, repo.wt, 99, 7, repo.old, new) is None  # refs/pull/99/head が無い


def loop_file(main, name, rows):
    p = Path(main) / ".ndf/loop" / name / "loop.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"name": name, "waiting": rows}))


def plan_file(monkeypatch):
    lanes = [SimpleNamespace(input=SimpleNamespace(kind="loop", loop="lroot3"))]
    monkeypatch.setattr(pp, "load_valid_plan", lambda main, name: SimpleNamespace(lanes=lanes))


WAIT = {"number": 12, "pr": 412, "lap": 3, "reason": "判定できない", "verdict": "unknown", "stamp": "a" * 40}


def test_prior_waits_are_rows_of_loops_outside_the_plan(tmp_path, monkeypatch):
    plan_file(monkeypatch)
    loop_file(tmp_path, "lroot3", [{**WAIT, "number": 1}])  # レーンのループ
    loop_file(tmp_path, "lroot2", [WAIT, {**WAIT, "number": 2, "answer": "approved"}, {**WAIT, "number": 3, "pr": None}])
    (tmp_path / ".ndf/loop/broken").mkdir()
    (tmp_path / ".ndf/loop/broken/loop.json").write_text("{")
    run = lambda cmd, cwd=None, stdin=None: (0, "b" * 40, "")  # noqa: E731
    msgs, skipped = g1.prior_requests(tmp_path, "p1", "t", run)
    assert [(m["id"], m["loop"], m["issue"], m["pr"], m["stamp"]) for m in msgs] == [("lroot2-w12-l3", "lroot2", 12, 412, "b" * 40)]
    assert "lane" not in msgs[0] and msgs[0]["kind"] == "gate1"
    assert [s["loop"] for s in skipped] == ["broken"]
    assert g1.prior_requests(tmp_path, "p1", "later", run)[0][0]["id"] == msgs[0]["id"]  # 同じ行は同じ id


def test_a_row_held_by_another_running_plan_is_left_to_it(tmp_path, monkeypatch):
    plan_file(monkeypatch)
    loop_file(tmp_path, "lroot2", [{**WAIT, "request": "x", "plan": "live"}, {**WAIT, "number": 5, "request": "y", "plan": "done"}])
    monkeypatch.setattr(pl, "run_status", lambda main, name: "PENDING" if name == "live" else "SUCCESS")
    assert [w["number"] for _, w in g1.prior_rows(tmp_path, "p1")[0]] == [5]


class Gh:
    def __init__(self, head):
        self.head, self.calls, self.code, self.out = (
            head,
            [],
            0,
            '{"tool": "sprint-state", "status": "ok", "summary": "x", "items": [], "metrics": {"status": "answered"}}',
        )

    def __call__(self, cmd, cwd=None, stdin=None):
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return 0, self.head, ""
        return (self.code, self.out, "断った") if "loop" in cmd else (1, "", "")


Q = {"id": "lroot2-w12-l3", "loop": "lroot2", "issue": 12, "pr": 412, "lap": 3, "stamp": "a" * 40}


def loop_args(gh):
    return next(c[c.index("loop") :] for c in gh.calls if "loop" in c)


def test_an_approval_on_the_same_head_is_applied_through_loop_answer(tmp_path):
    gh = Gh("a" * 40)
    assert g1.answer_prior(tmp_path, tmp_path, Q, {"result": "approved"}, run=gh) == {}
    assert loop_args(gh) == ["loop", "answer", "lroot2", "12", "--approved", "--pr", "412"]


def test_an_approval_on_a_moved_head_asks_again_with_the_new_head(tmp_path, monkeypatch):
    monkeypatch.setattr(g1, "merge_only", lambda *a: False)
    got = g1.answer_prior(tmp_path, tmp_path, Q, {"result": "approved"}, run=Gh("b" * 40))
    assert got["reask"]["id"] == "lroot2-w12-l3-" + "b" * 12 and got["reask"]["stamp"] == "b" * 40


def test_a_merge_only_move_is_applied(tmp_path, monkeypatch):
    monkeypatch.setattr(g1, "merge_only", lambda *a: True)
    assert g1.answer_prior(tmp_path, tmp_path, Q, {"result": "approved"}, run=Gh("b" * 40)) == {}


def test_a_revise_note_goes_to_the_plan_folder(tmp_path):
    gh = Gh("a" * 40)
    assert g1.answer_prior(tmp_path, tmp_path, Q, {"result": "revise", "note": "- 絞る"}, run=gh) == {}
    args = loop_args(gh)
    assert args[4] == "--revise" and Path(args[6]).read_text(encoding="utf-8") == "- 絞る" and Path(args[6]).parent.name == "prior"
    gh = Gh("a" * 40)
    g1.answer_prior(tmp_path, tmp_path, Q, {"result": "rejected"}, run=gh)
    assert loop_args(gh)[4:] == ["--rejected"]


def test_a_refused_or_regated_loop_answer_is_unmatched(tmp_path):
    gh = Gh("a" * 40)
    gh.code = 1
    assert "断った" in g1.answer_prior(tmp_path, tmp_path, Q, {"result": "approved"}, run=gh)["unmatched"]
    gh = Gh("a" * 40)
    gh.out = '{"tool": "sprint-state", "status": "ok", "summary": "x", "items": [], "metrics": {"status": "regated"}}'
    assert "答え直し" in g1.answer_prior(tmp_path, tmp_path, Q, {"result": "approved"}, run=gh)["unmatched"]


def test_a_stale_row_stamp_is_answered_again_once(tmp_path):
    """行の印が古い（問いは送る時点の先頭）と loop answer は答え直しを求める。先頭は確かめたので 1 回だけ打ち直す。"""
    gh = Gh("a" * 40)
    outs = [
        '{"tool": "sprint-state", "status": "ok", "summary": "x", "items": [], "metrics": {"status": "regated"}}',
        '{"tool": "sprint-state", "status": "ok", "summary": "x", "items": [], "metrics": {"status": "answered"}}',
    ]
    base = gh.__call__

    def run(cmd, cwd=None, stdin=None):
        if "loop" in cmd:
            gh.out = outs.pop(0)
        return base(cmd, cwd, stdin)

    assert g1.answer_prior(tmp_path, tmp_path, Q, {"result": "approved"}, run=run) == {} and outs == []


def test_a_head_moved_after_the_check_is_not_approved_on_the_retry(tmp_path):
    """先頭を確かめた後に設計 PR が動き、loop answer が答え直しを求めたら、動いた先頭を打ち直しで承認しない。"""
    gh = Gh("a" * 40)
    gh.out = '{"tool": "sprint-state", "status": "ok", "summary": "x", "items": [], "metrics": {"status": "regated"}}'
    base = gh.__call__

    def run(cmd, cwd=None, stdin=None):
        if "loop" in cmd:
            gh.head = "b" * 40
        return base(cmd, cwd, stdin)

    got = g1.answer_prior(tmp_path, tmp_path, Q, {"result": "approved"}, run=run)
    assert "動いた" in got["unmatched"] and sum("loop" in c for c in gh.calls) == 1


def test_an_answer_to_a_replaced_question_needs_a_merge_only_move(tmp_path, monkeypatch):
    monkeypatch.setattr(g1, "merge_only", lambda *a: False)
    got = g1.answer_prior(tmp_path, tmp_path, {**Q, "stamp": "b" * 40}, {"result": "rejected"}, "a" * 40, run=Gh("b" * 40))
    assert "取り込み以外" in got["unmatched"]
    monkeypatch.setattr(g1, "merge_only", lambda *a: True)
    assert g1.answer_prior(tmp_path, tmp_path, {**Q, "stamp": "b" * 40}, {"result": "rejected"}, "a" * 40, run=Gh("b" * 40)) == {}
