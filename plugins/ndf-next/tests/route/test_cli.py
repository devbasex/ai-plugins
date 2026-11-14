"""副命令 `ndf route`: 雛形の一覧・プランを書く・書いて流す・同じ原因の続きを数える・省いた置き場を決める。"""

from __future__ import annotations

import json
from pathlib import Path

from ndf.core import route

from ._flow import git


def _last(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _fix_args(out: Path, repo: Path) -> list[str]:
    return [
        "immediate_fix", "--out", str(out), "--set", "branch=fix/x", "--set", "title=直す", "--set", "body_file=b.md",
        "--set", "test_command=pytest -q", "--add", "required=test", "--set", f"repo={repo}", "--set", f"worktree={repo}",
    ]  # fmt: skip


def test_templates_lists_every_template(capsys):
    assert route.main(["templates"]) == 0
    found = _last(capsys)
    assert {i["purpose"] for i in found["items"]} >= {"immediate_fix", "implement", "check", "close", "design", "design_merge"}


def test_render_writes_plan_and_points_to_plan_next(tmp_path, capsys):
    out = tmp_path / "plans" / "fix.json"
    assert route.main(["render", *_fix_args(out, tmp_path)]) == 0
    found = _last(capsys)
    assert found["next"]["argv"] == ["ndf", "plan", "next", str(out)]
    assert [s["id"] for s in json.loads(out.read_text())["steps"]][0] == "test"


def test_run_is_one_entrypoint_for_immediate_fix(tmp_path, monkeypatch):
    """C3-1（#1142 の要求の不足 a）: conductor は ndf route run immediate_fix 1 つで、雛形を埋めたプランを流す。"""
    called: list[list[str]] = []
    monkeypatch.setattr(route.plan_cli, "main", lambda argv: called.append(argv) or 0)
    out = tmp_path / "fix.json"
    assert route.main(["run", *_fix_args(out, tmp_path)]) == 0
    assert called == [["next", str(out)]]
    ids = [s["id"] for s in json.loads(out.read_text())["steps"]]
    assert ids == ["test", "push", "pr", "conflict-check", "ci", "merge"]


def test_render_refuses_missing_value_as_invalid(tmp_path, capsys):
    code = route.main(["render", "immediate_fix", "--out", str(tmp_path / "p.json"), "--set", f"repo={tmp_path}"])
    found = _last(capsys)
    assert found["cause"]["kind"] == "invalid"
    assert code != 0


def test_set_and_add_same_name_is_refused(tmp_path, capsys):
    args = ["render", "immediate_fix", "--out", str(tmp_path / "p.json"), "--set", "required=a", "--add", "required=b"]
    route.main(args)
    assert "両方で渡した" in _last(capsys)["cause"]["detail"]


def _stopped_ci(path: Path, nodes: list[str]) -> None:
    found = {"tool": "ci", "status": "stopped", "summary": "落ちた", "items": [], "metrics": {"failed_tests": nodes},
             "cause": {"kind": "test_failed", "detail": "CI"}}  # fmt: skip
    path.write_text(json.dumps(found) + "\n", encoding="utf-8")


def test_repeat_stops_when_same_cause_continues(tmp_path, capsys):
    """#2844: 同じ落ちたテストの集まりで CI が続けて落ちたら、上限（supervise.step-rerun の same_cause）を超えた回で fix_limit。

    別の原因が挟まれば数え直す。summary の文は鍵に入れない。
    """
    res, log = tmp_path / "ci.json", tmp_path / "causes.jsonl"
    args = ["repeat", "--result", str(res), "--log", str(log)]
    _stopped_ci(res, ["tests/a.py::t1"])
    assert route.main(args) == 0
    assert route.main(args) == 0
    assert route.main(args) != 0
    assert _last(capsys)["cause"]["kind"] == "fix_limit"
    _stopped_ci(res, ["tests/b.py::t2"])
    assert route.main(args) == 0
    assert _last(capsys)["metrics"]["streak"] == 1


def test_repeat_refuses_result_that_did_not_stop(tmp_path, capsys):
    res = tmp_path / "ci.json"
    res.write_text(json.dumps({"tool": "ci", "status": "ok", "summary": "", "items": [], "metrics": {}}) + "\n", encoding="utf-8")
    route.main(["repeat", "--result", str(res), "--log", str(tmp_path / "l.jsonl")])
    assert _last(capsys)["cause"]["kind"] == "invalid"


def test_cause_key_ignores_summary_and_test_order():
    a = {"status": "stopped", "summary": "x", "cause": {"kind": "test_failed"}, "metrics": {"failed_tests": ["b", "a"]}}
    b = {**a, "summary": "y", "metrics": {"failed_tests": ["a", "b"]}}
    assert route.cause_key(a) == route.cause_key(b)
    assert route.repeated([route.cause_key(a)] * 2, route.cause_key(a), 2)
    assert not route.repeated([route.cause_key(a)], route.cause_key(a), 2)


def test_omitted_worktree_is_under_submodule_worktree_not_git_modules(tmp_path):
    """#2054: サブモジュールのリポジトリで省いた作業場所は、サブモジュールのworktreeの根の下（.git/modules の下でない）。"""
    sub = tmp_path / "sub"
    sub.mkdir()
    git(sub, "init", "-q", "-b", "main")
    (sub / "a.txt").write_text("a\n", encoding="utf-8")
    git(sub, "add", ".")
    git(sub, "commit", "-q", "-m", "a")
    sup = tmp_path / "super"
    sup.mkdir()
    git(sup, "init", "-q", "-b", "main")
    git(sup, "submodule", "add", "-q", str(sub), "mod")
    inner = sup / "mod"
    t = route.choose("immediate_fix")
    got = route.derived(t, {"branch": "fix/x"}, inner)
    assert Path(got["repo"]).resolve() == inner.resolve()
    assert Path(got["worktree"]).resolve() == (inner / ".worktrees" / "fix" / "x").resolve()
    assert ".git/modules" not in str(got["worktree"])
