"""設計 PR の題を設計文書の H1 から作る（#1289 の決定 1・6・7・9）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
PY = sys.executable
sys.path.insert(0, str(SCRIPTS))
from supervise_lib import engine, pr as pr_step, sprint_waves  # noqa: E402

H1 = "設計の承認: 題から目的が読めない → 題と承認資料の先頭で読める（#1）"
DOC = "issues/issue-1-design.md"

# 呼ばれた引数を 1 行の JSON で残す。既存の PR の URL と今の題は環境変数で渡す
FAKE_GH = f"""#!{PY}
import json, os, sys
a = sys.argv[1:]
open(os.environ['FAKE_GH_LOG'], 'a').write(json.dumps(a, ensure_ascii=False) + '\\n')
if a[:2] == ['pr', 'list']:
    print(os.environ.get('FAKE_GH_EXISTING', ''))
elif a[:2] == ['pr', 'create']:
    print('https://github.com/o/r/pull/5')
elif a[:2] == ['pr', 'view']:
    if os.environ.get('FAKE_GH_VIEW_FAIL'):
        sys.exit(1)
    print(os.environ.get('FAKE_GH_TITLE', ''))
elif a[:2] == ['pr', 'edit'] and os.environ.get('FAKE_GH_EDIT_FAIL'):
    sys.exit(1)
sys.exit(0)
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def gh_log(tmp_path, monkeypatch) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH)
    (bindir / "gh").chmod(0o755)
    log = tmp_path / "gh.log"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    return log


def calls(log: Path, verb: str) -> list[list[str]]:
    if not log.exists():
        return []
    return [a for a in map(json.loads, log.read_text().splitlines()) if a[:2] == ["pr", verb]]


def design_repo(tmp_path: Path, doc: str | None) -> Path:
    """origin を持つ設計のブランチ。doc があれば設計文書としてコミットする。"""
    root = tmp_path / "wt"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(root, "config", k, v)
    (root / "README.md").write_text("r\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    git(root, "remote", "add", "origin", str(origin))
    git(root, "push", "-q", "-u", "origin", "main")
    git(root, "checkout", "-q", "-b", "design/issue-1")
    if doc is not None:
        (root / "issues").mkdir()
        (root / DOC).write_text(doc)
    (root / "d.md").write_text("d\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Add: 設計")
    return root


def run_pr_step(tmp_path: Path, root: Path) -> None:
    plan = {
        "フェーズ": "設計",
        "課題": [1],
        "作業場所": str(root),
        "steps": [{"id": "pr", "type": "pr", "base": "main", "body": "template", "title_doc": DOC, "title": "設計: #1", "next": "end"}],
    }
    state = tmp_path / "state"
    (state / "work").mkdir(parents=True)
    assert "結果: 完了" in engine.Engine(plan, state).run()


def title_of(create: list[str]) -> str:
    return create[create.index("--title") + 1]


# --- H1 の読み方 ---------------------------------------------------------------


def test_first_h1_outside_fences_is_the_title(tmp_path):
    f = tmp_path / "d.md"
    f.write_text(f"前置き\n\n```markdown\n# 雛形の H1\n~~~\n# 囲みの中\n```\n\n#  {H1}  \n\n# 2 つ目\n")
    assert pr_step.design_title(f) == H1


@pytest.mark.parametrize("text", [None, "本文だけ\n## 節\n", "# \n# 後の H1\n", "```\n# 閉じない囲みの中\n"])
def test_missing_or_empty_h1_gives_no_title(tmp_path, text):
    f = tmp_path / "d.md"
    if text is not None:
        f.write_text(text)
    assert pr_step.design_title(f) is None


def test_title_length_is_counted_in_code_points(tmp_path):
    f = tmp_path / "d.md"
    f.write_text("# " + "題" * pr_step.TITLE_MAX + "\n")
    assert pr_step.design_title(f) == "題" * pr_step.TITLE_MAX
    f.write_text("# " + "題" * (pr_step.TITLE_MAX + 1) + "\n")
    assert pr_step.design_title(f) is None


# --- pr のステップ -------------------------------------------------------------


def test_new_pr_takes_the_h1_as_its_title(tmp_path, gh_log):
    run_pr_step(tmp_path, design_repo(tmp_path, f"# {H1}\n\n## 目的\n"))
    (create,) = calls(gh_log, "create")
    assert title_of(create) == H1


@pytest.mark.parametrize("doc", [None, "## 目的\n", "# " + "題" * 257 + "\n"])
def test_unreadable_h1_falls_back_to_the_step_title(tmp_path, gh_log, doc):
    run_pr_step(tmp_path, design_repo(tmp_path, doc))
    (create,) = calls(gh_log, "create")
    assert title_of(create) == "設計: #1"


def test_existing_pr_gets_the_h1_title_with_the_body(tmp_path, gh_log, monkeypatch):
    monkeypatch.setenv("FAKE_GH_EXISTING", "https://github.com/o/r/pull/5")
    run_pr_step(tmp_path, design_repo(tmp_path, f"# {H1}\n"))
    (edit,) = calls(gh_log, "edit")
    assert "--body" in edit and title_of(edit) == H1
    assert calls(gh_log, "create") == []


def test_existing_pr_keeps_its_title_when_the_h1_is_unreadable(tmp_path, gh_log, monkeypatch):
    monkeypatch.setenv("FAKE_GH_EXISTING", "https://github.com/o/r/pull/5")
    run_pr_step(tmp_path, design_repo(tmp_path, None))
    (edit,) = calls(gh_log, "edit")
    assert "--body" in edit and "--title" not in edit


# --- sync-title ----------------------------------------------------------------


def sync_title(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, str(SCRIPTS / "supervise.py"), "sync-title", "--pr", "5", "--doc", DOC],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def doc_dir(tmp_path) -> Path:
    (tmp_path / "issues").mkdir()
    (tmp_path / DOC).write_text(f"# {H1}\n")
    return tmp_path


def test_sync_title_rewrites_a_different_title(doc_dir, gh_log, monkeypatch):
    monkeypatch.setenv("FAKE_GH_TITLE", "設計: #1")
    p = sync_title(doc_dir)
    assert p.returncode == 0, p.stderr
    assert [title_of(e) for e in calls(gh_log, "edit")] == [H1]


def test_sync_title_writes_nothing_when_the_title_matches(doc_dir, gh_log, monkeypatch):
    monkeypatch.setenv("FAKE_GH_TITLE", H1)
    assert sync_title(doc_dir).returncode == 0
    assert calls(gh_log, "edit") == []


def test_sync_title_keeps_the_title_when_the_h1_is_unreadable(doc_dir, gh_log, monkeypatch):
    (doc_dir / DOC).write_text("## 目的\n")
    monkeypatch.setenv("FAKE_GH_TITLE", "人が付けた題")
    assert sync_title(doc_dir).returncode == 0
    assert calls(gh_log, "edit") == []


@pytest.mark.parametrize("fail", ["FAKE_GH_VIEW_FAIL", "FAKE_GH_EDIT_FAIL"])
def test_sync_title_failure_does_not_stop_the_plan(doc_dir, gh_log, monkeypatch, fail):
    monkeypatch.setenv("FAKE_GH_TITLE", "設計: #1")
    monkeypatch.setenv(fail, "1")
    p = sync_title(doc_dir)
    assert p.returncode == 0 and p.stderr.strip()


# --- スプリントの設計のプラン ----------------------------------------------------


def plan_args(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(base="main", mode="standard", name="m1", worktree=str(tmp_path), state=str(tmp_path / "state"))


def steps_of(plan: dict) -> dict:
    return {s["id"]: s for s in plan["steps"]}


def test_sprint_design_pr_reads_the_design_doc_and_falls_back(tmp_path):
    ds = steps_of(sprint_waves.plan_sprint_design(plan_args(tmp_path), 42, str(tmp_path)))
    assert ds["pr"]["title_doc"] == "issues/issue-42-design.md" and ds["pr"]["title"] == "設計: #42"
    assert ds["push-glossary"]["cmd"].endswith(" sync-title --pr {pr} --doc issues/issue-42-design.md")


def test_both_design_pushes_of_the_mvv_plan_sync_the_title(tmp_path):
    ds = steps_of(sprint_waves.plan_mvv_design(plan_args(tmp_path), 42, str(tmp_path)))
    assert ds["glossary-recheck"]["on_fail"] == "push-glossary-gate" and ds["push-glossary-gate"]["next"] == "gate"
    for i in ("push-glossary", "push-glossary-gate"):
        cmd = ds[i]["cmd"]
        assert cmd.startswith("git push -q && ") and cmd.endswith(" sync-title --pr {pr} --doc issues/issue-42-design.md")
