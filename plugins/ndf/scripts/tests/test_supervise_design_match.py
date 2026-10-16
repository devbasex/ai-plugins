"""3 層の実装プランの設計との突き合わせと、pr のステップの「設計と違う点」の節（#1241）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SUPERVISE = SCRIPTS / "supervise.py"
PY = sys.executable

sys.path.insert(0, str(SCRIPTS))
from supervise_lib import engine, pr as pr_step  # noqa: E402

HEADING = "## 設計と違う点"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def new_impl(tmp_path, *extra) -> dict:
    out = tmp_path / "plan.json"
    p = subprocess.run(
        [PY, str(SUPERVISE), "new", "impl", "--issue", "1", "--worktree", "/w", "--tests", "t", "--title", "T", "--out", str(out), *extra],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(out.read_text())


def order(plan: dict) -> list[str]:
    st = {s["id"]: s for s in plan["steps"]}
    seq, cur = [], "test-limited"
    while cur in st and cur not in seq:
        seq.append(cur)
        cur = st[cur].get("next")
    return seq


def test_standard_plan_runs_the_design_match_between_tests_and_pr(tmp_path):
    plan = new_impl(tmp_path, "--mode", "standard")
    st = {s["id"]: s for s in plan["steps"]}
    assert order(plan)[:5] == ["test-limited", "design-tests", "design-specs", "design-match", "pr"]
    assert st["design-tests"]["skip_to"] == "pr" and st["design-tests"]["on_fail"] == "design-specs"
    assert "design-match.py tests --issue 1" in st["design-tests"]["cmd"]
    assert "design-match.py specs --base origin/" in st["design-specs"]["cmd"]
    assert st["design-match"]["type"] == "work" and st["design-match"]["inputs"] == ["design-tests", "design-specs"]
    assert st["pr"]["diff_section"] == "{state_dir}/work/design-diff.md" and st["pr"]["on_section_missing"] == "judge"
    assert "design-tests" in st["judge"]["choices"] and "pr" not in st["judge"]["choices"]
    assert "範囲テストなら design-tests" in plan["規則"]  # judge の選択肢と規則が同じ行き先を指す
    assert "#1" in st["design-match"]["prompt"]  # 対象の課題を worker へ渡す
    assert st["fix"]["next"] == "test-limited"


@pytest.mark.parametrize("mode", ["light", "operation", "documentation"])
def test_other_modes_keep_the_steps_unchanged(tmp_path, mode):
    plan = new_impl(tmp_path, "--mode", mode)
    st = {s["id"]: s for s in plan["steps"]}
    assert not {"design-tests", "design-specs", "design-match"} & set(st)
    assert st["test-limited"]["next"] == "pr" and "diff_section" not in st["pr"]
    assert st["judge"]["choices"] == ["fix", "pr", "doc-lint", "stop"]
    assert "範囲テストなら pr" in plan["規則"]


def test_section_replaces_the_llm_heading_and_keeps_the_footer():
    body = f"要約\n\n{HEADING}\n\n- 無し\n\n{pr_step.PR_FOOTER}\n"
    got, placed = pr_step.with_design_diff(body, f"{HEADING}\n\n| 決定 3 | 違う | 理由 |")
    assert placed and got.count(HEADING) == 1 and "| 決定 3 |" in got and "- 無し" not in got
    assert got.rstrip().endswith(pr_step.PR_FOOTER)
    same, placed = pr_step.with_design_diff("要約\n", f"{HEADING}\n\n- 無し")
    assert same == "要約\n" and not placed


@pytest.fixture
def pr_repo(tmp_path, monkeypatch):
    root = tmp_path / "r"
    root.mkdir()
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "a.txt").write_text("a\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    git(root, "remote", "add", "origin", str(origin))
    git(root, "push", "-q", "-u", "origin", "main")
    git(root, "checkout", "-q", "-b", "feat/x")
    (root / "b.txt").write_text("b\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Add: b")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(
        f"#!{PY}\nimport os, sys\na = sys.argv[1:]\n"
        "if a[:2] == ['pr', 'create']:\n"
        "    open(os.environ['FAKE_GH_BODY'], 'w').write(a[a.index('--body') + 1])\n"
        "    print('https://github.com/o/r/pull/5')\n"
        "sys.exit(0)\n"
    )
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_BODY", str(tmp_path / "body.txt"))
    return root


def run_pr(tmp_path, root, tests_exit: int, diff: str | None):
    state = tmp_path / "state"
    (state / "work").mkdir(parents=True)
    if diff is not None:
        (state / "work" / "design-diff.md").write_text(diff)
    plan = {
        "フェーズ": "実装",
        "課題": [1],
        "作業場所": str(root),
        "steps": [
            {"id": "design-tests", "type": "run", "cmd": f"sh -c 'exit {tests_exit}'", "next": "pr", "on_fail": "pr", "skip_to": "pr"},
            {"id": "pr", "type": "pr", "base": "main", "body": "template", "next": "end",
             "diff_section": "{state_dir}/work/design-diff.md", "on_section_missing": "back"},
            {"id": "back", "type": "run", "cmd": "true", "next": "end"},
        ],
    }  # fmt: skip
    eng = engine.Engine(plan, state)
    report = eng.run()
    body = tmp_path / "body.txt"
    return eng, report, (body.read_text() if body.is_file() else None)


def test_pr_puts_the_written_section_once_before_the_footer(tmp_path, pr_repo):
    _, report, body = run_pr(tmp_path, pr_repo, 1, f"{HEADING}\n\n| 決定 6 | 実装しなかった | 別の課題へ分けた |\n")
    assert "結果: 完了" in report
    assert body.count(HEADING) == 1 and body.index("決定 6") < body.index(pr_step.PR_FOOTER)


def test_pr_puts_not_applicable_only_when_there_is_no_design(tmp_path, pr_repo):
    _, report, body = run_pr(tmp_path, pr_repo, 3, None)
    assert "結果: 完了" in report
    assert f"{HEADING}\n\n- 該当なし（設計文書が無い）" in body and body.count(HEADING) == 1


@pytest.mark.parametrize("code", [0, 1, 2])
def test_pr_stops_without_the_section_when_the_match_should_have_run(tmp_path, pr_repo, code):
    eng, report, body = run_pr(tmp_path, pr_repo, code, None)
    assert body is None  # PR を作らない
    assert eng.state.results["pr"]["section_missing"] is True
    assert eng.state.results["back"]["exit"] == 0  # on_section_missing の行き先へ進む
    assert git(pr_repo, "ls-remote", "--heads", "origin", "feat/x") == ""  # push もしない
