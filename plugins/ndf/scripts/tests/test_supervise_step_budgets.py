"""実装の上限と構造改善の想定最大時間を、.ndf/supervise.json か new の引数で決める（#1916）。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SUPERVISE = SCRIPTS / "supervise.py"
PY = sys.executable

sys.path.insert(0, str(SCRIPTS))
from supervise_lib import decl, paths, sprint_waves  # noqa: E402
from supervise_lib.verify_steps import with_margin  # noqa: E402

# new sprint は --design に無い課題の本文を gh で読む（#1767）。見本の本文で答える
pytestmark = pytest.mark.usefixtures("issue_bodies")

IMPL_DEFAULT = 3600  # 変更の前の impl の timeout（#1916 の AC1・AC8 は変えないことを求める）


def repo(tmp_path, **sv) -> Path:
    root = tmp_path / "r"
    (root / ".ndf").mkdir(parents=True)
    (root / ".ndf" / "worktree.json").write_text('{"version": 1, "base_branch": "main"}\n')
    (root / ".ndf" / "supervise.json").write_text(json.dumps({"version": 1, "test": {"command": "make test {paths}"}, **sv}))
    return root


def cli(root, *args):
    return subprocess.run([PY, str(SUPERVISE), "new", *args, "--worktree", str(root)], capture_output=True, text=True, cwd=root)


def steps(path) -> dict:
    return {s["id"]: s for s in json.loads(Path(path).read_text())["steps"]}


def new_impl(root, out, *extra):
    return cli(root, "impl", "--issue", "1", "--tests", "t", "--title", "T", "--out", str(out), *extra)


def new_check(root, out, *extra):
    return cli(root, "check", "--pr", "5", "--out", str(out), *extra)


def sprint_plans(root, out, *extra) -> dict:
    p = cli(root, "sprint", "--name", "m1", "--issue", "11", "12", "--out", str(out), *extra)
    assert p.returncode == 0, p.stderr
    waves = {w["name"]: w for w in json.loads((out / "sprint.json").read_text())["ステージ"]}
    return {"impl": [steps(f)["impl"] for f in waves["実装"]["plans"]], "refactor": [steps(f)["refactor"] for f in waves["検査"]["plans"]]}


def close_refactors(root, out, *extra) -> list[dict]:
    p = cli(root, "close", "--name", "m1", "--issue", "11", "--state", str(root / "s.json"), "--out", str(out), *extra)
    assert p.returncode == 0, p.stderr
    first = json.loads((out / "sprint.json").read_text())["ステージ"][0]
    return [steps(f)["refactor"] for f in first["plans"] if "refactor" in steps(f)]


def assert_budget(refactor: dict, minutes: int) -> None:
    assert f"--budget-minutes {minutes}" in refactor["cmd"]
    assert refactor["timeout"] == with_margin(minutes * 60)


def test_without_values_impl_and_refactor_cmd_stay_and_refactor_timeout_follows_the_default_budget(tmp_path):
    """AC3・AC8: 値が無ければ impl の timeout と refactor の cmd は変更前のまま、refactor の timeout は既定の予算から出る。"""
    root = repo(tmp_path)
    assert new_impl(root, tmp_path / "i.json").returncode == 0
    assert steps(tmp_path / "i.json")["impl"]["timeout"] == IMPL_DEFAULT
    assert new_check(root, tmp_path / "c.json").returncode == 0
    refactor = steps(tmp_path / "c.json")["refactor"]
    assert "--budget-minutes" not in refactor["cmd"]
    assert refactor["timeout"] == with_margin(paths.default_refactor_budget_minutes() * 60)


def test_refactor_timeout_follows_a_changed_default_budget(tmp_path, monkeypatch):
    """AC3: cross-refactoring の既定の予算を変えると timeout も追う（値を写していない）。"""
    from supervise_lib import templates

    monkeypatch.setattr(paths._refactor_vocabulary(), "DEFAULT_BUDGET_MINUTES", 7)
    a = argparse.Namespace(refactor_budget_minutes=None)
    refactor = next(s for s in templates._inspect_steps(a, "5", "x") if s["id"] == "refactor")
    assert refactor["timeout"] == with_margin(7 * 60) and "--budget-minutes" not in refactor["cmd"]


def test_the_default_budget_is_cross_refactorings_constant():
    sys.path.insert(0, str(paths.DRIVES["cross-refactoring"].parent))
    try:
        from refactor_lib import vocabulary
    finally:
        sys.path.pop(0)
    assert paths.default_refactor_budget_minutes() == vocabulary.DEFAULT_BUDGET_MINUTES


@pytest.mark.parametrize("by", ["decl", "arg"])
def test_values_from_the_declaration_or_the_argument_reach_impl_and_refactor(tmp_path, by):
    """AC1・AC2・AC4: 既存の supervise.json か引数で渡した値がプランに入る。"""
    root = repo(tmp_path, **({"impl_timeout": 5000, "refactor_budget_minutes": 45} if by == "decl" else {}))
    impl_arg = ("--impl-timeout", "5000") if by == "arg" else ()
    budget_arg = ("--refactor-budget-minutes", "45") if by == "arg" else ()
    assert new_impl(root, tmp_path / "i.json", *impl_arg).returncode == 0
    assert steps(tmp_path / "i.json")["impl"]["timeout"] == 5000
    assert new_check(root, tmp_path / "c.json", *budget_arg).returncode == 0
    assert_budget(steps(tmp_path / "c.json")["refactor"], 45)
    assert sorted(p.name for p in (root / ".ndf").iterdir()) == ["supervise.json", "worktree.json"]


def test_the_argument_wins_over_the_declaration(tmp_path):
    """AC6"""
    root = repo(tmp_path, impl_timeout=5000, refactor_budget_minutes=45)
    assert new_impl(root, tmp_path / "i.json", "--impl-timeout", "4000").returncode == 0
    assert steps(tmp_path / "i.json")["impl"]["timeout"] == 4000
    assert new_check(root, tmp_path / "c.json", "--refactor-budget-minutes", "20").returncode == 0
    assert_budget(steps(tmp_path / "c.json")["refactor"], 20)


@pytest.mark.parametrize("by", ["decl", "arg"])
def test_sprint_and_close_pass_the_same_values_to_every_child_plan(tmp_path, by):
    """AC5: new sprint の課題ごとの実装のプランと検査のプラン、new close の最終の検査に同じ値が入る。"""
    root = repo(tmp_path, **({"impl_timeout": 5000, "refactor_budget_minutes": 45} if by == "decl" else {}))
    extra = ("--impl-timeout", "5000", "--refactor-budget-minutes", "45") if by == "arg" else ()
    plans = sprint_plans(root, tmp_path / "s", *extra)
    assert [s["timeout"] for s in plans["impl"]] == [5000, 5000]
    assert plans["refactor"]
    for r in plans["refactor"]:
        assert_budget(r, 45)
    refactors = close_refactors(root, tmp_path / "c", *(("--refactor-budget-minutes", "45") if by == "arg" else ()))
    assert refactors
    for r in refactors:
        assert_budget(r, 45)


def test_sprint_without_values_keeps_the_impl_timeout(tmp_path):
    """AC8"""
    plans = sprint_plans(repo(tmp_path), tmp_path / "s")
    assert [s["timeout"] for s in plans["impl"]] == [IMPL_DEFAULT, IMPL_DEFAULT]
    for r in plans["refactor"]:
        assert "--budget-minutes" not in r["cmd"]


def test_fast_sprint_plans_take_the_values_through_decl_fields(tmp_path):
    """AC5: pace: fast の実装と検査のプランも decl_fields から同じ値を受ける。"""
    a = argparse.Namespace(**{k: None for k in decl.decl_fields(argparse.Namespace())})
    a.__dict__.update(
        impl_timeout=5000, refactor_budget_minutes=45, base="main", name="m1", tests=None, mode="standard", issue=[11], state=None
    )
    impl = next(s for s in sprint_waves.plan_fast_impl(a, 11, str(tmp_path))["steps"] if s["id"] == "impl")
    assert impl["timeout"] == 5000
    check = sprint_waves.plan_fast_check(a, str(tmp_path), "m1-final", final=True)
    assert_budget(next(s for s in check["steps"] if s["id"] == "refactor"), 45)


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "x"])
@pytest.mark.parametrize("flag", ["--impl-timeout", "--refactor-budget-minutes"])
def test_a_bad_argument_stops_without_writing_the_plan(tmp_path, flag, value):
    """AC7（引数）"""
    root = repo(tmp_path)
    out = tmp_path / "p.json"
    p = new_impl(root, out, flag, value) if flag == "--impl-timeout" else new_check(root, out, flag, value)
    assert p.returncode == 2 and flag in p.stderr and not out.exists()


@pytest.mark.parametrize("value", [0, -1, 1.5, "60", True])
@pytest.mark.parametrize("key", ["impl_timeout", "refactor_budget_minutes"])
def test_a_bad_declaration_stops_without_writing_the_plan_even_with_the_argument(tmp_path, key, value):
    """AC7（宣言）: 引数を渡しても宣言の形を見る。"""
    root = repo(tmp_path, **{key: value})
    out = tmp_path / "p.json"
    p = new_impl(root, out, "--impl-timeout", "100")
    assert p.returncode == 2 and key in p.stderr and not out.exists()


def test_help_lists_the_optional_settings(tmp_path):
    for kind, keys in {"sprint": ["impl_timeout", "refactor_budget_minutes"], "check": ["refactor_budget_minutes"], "impl": ["impl_timeout"]}.items():
        out = subprocess.run([PY, str(SUPERVISE), "new", kind, "--help"], capture_output=True, text=True, cwd=tmp_path).stdout
        assert "任意の設定" in out and all(k in out for k in keys), kind
