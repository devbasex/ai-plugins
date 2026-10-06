"""new sprint が計画を書く前に、--design に無い課題の受け入れ条件を確かめる（#1767 の受け入れ条件 1〜5）。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SUPERVISE = SCRIPTS / "supervise.py"
REPO = SCRIPTS.parents[2]
PY = sys.executable

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import supervise  # noqa: E402
from supervise_lib import sprint_inputs  # noqa: E402
from test_supervise_pace_auto import AUTO, make_repo, sprint_state  # noqa: E402

NO_SECTION = "## 目的\n\n直す\n\n## 何をするか\n\n- 直す\n"


def sprint_args(tmp_path: Path, pace: str = "normal", issues=("11", "12"), design=("11",)) -> tuple[list[str], Path]:
    """new sprint の引数と、打つ場所。fast と auto は承認済みのスプリント MVV の状態を渡す。"""
    repo = make_repo(tmp_path, {"auto": AUTO} if pace == "auto" else None)
    args = ["new", "sprint", "--name", "m30", "--worktree", str(repo if pace != "fast" else tmp_path), "--issue", *issues]
    args += ["--design", *design] if design else []
    args += ["--version", "10.18.0-dev.1", "--out", str(tmp_path / "m")]
    if pace != "normal":
        args += ["--pace", pace, "--state", str(sprint_state(tmp_path))]
    return args, (repo if pace != "fast" else REPO)


def run(args: list[str], cwd: Path) -> tuple[subprocess.CompletedProcess, dict]:
    p = subprocess.run([PY, str(SUPERVISE), *args], capture_output=True, text=True, cwd=cwd)
    return p, json.loads(p.stdout.strip().splitlines()[-1])


def written(out: Path) -> dict[str, str]:
    return {str(f.relative_to(out)): f.read_text() for f in sorted(out.rglob("*")) if f.is_file()} if out.exists() else {}


# ---------- 受け入れ条件 1・2・4 ----------


def test_an_issue_without_acceptance_criteria_stops_before_any_plan_is_written(tmp_path, issue_bodies):
    issue_bodies.body(12, NO_SECTION).body(11, NO_SECTION)
    args, cwd = sprint_args(tmp_path)
    p, res = run(args, cwd)
    assert p.returncode != 0 and res["status"] == "stopped"
    assert not (tmp_path / "m").exists()
    assert res["items"] == [{"issue": 12, "reason": sprint_inputs.NO_SECTION}]
    # --design の課題（11）は確かめない
    assert issue_bodies.calls() == [12]
    assert "#12 を `--design` へ入れて打ち直す" in res["next"] and "--design 11 12" in res["next"]
    assert "#12 の本文に `## 受け入れ条件` を書いてから" in res["next"]


def test_every_lacking_issue_is_listed_in_one_result(tmp_path, issue_bodies):
    issue_bodies.body(12, NO_SECTION).body(13, "## 受け入れ条件\n\n\n## 由来\n\nx\n")
    args, cwd = sprint_args(tmp_path, issues=("11", "12", "13"))
    _, res = run(args, cwd)
    assert res["status"] == "stopped"
    assert [(x["issue"], x["reason"]) for x in res["items"]] == [(12, sprint_inputs.NO_SECTION), (13, sprint_inputs.EMPTY_SECTION)]
    assert "#12 #13" in res["summary"] and "--design 11 12 13" in res["next"]


# ---------- 受け入れ条件 3 ----------


def test_an_unreadable_body_stops_with_the_gh_output(tmp_path, issue_bodies):
    issue_bodies.fail(12, rc=1, err="HTTP 404: Could not resolve to an issue\n")
    args, cwd = sprint_args(tmp_path)
    p, res = run(args, cwd)
    assert p.returncode != 0 and res["status"] == "stopped" and not (tmp_path / "m").exists()
    (row,) = res["items"]
    assert row["issue"] == 12 and row["reason"] == sprint_inputs.UNREADABLE
    assert row["gh"]["returncode"] == 1 and "Could not resolve" in row["gh"]["stderr"]
    assert "`gh issue view 12` が通ることを確かめてから" in res["next"]


# ---------- 前提 2 ----------


@pytest.mark.parametrize(
    ("body", "lacks"),
    [
        ("## 受け入れ条件\n", sprint_inputs.EMPTY_SECTION),
        ("## 受け入れ条件\n\n   \n\n## 次\n- a\n", sprint_inputs.EMPTY_SECTION),
        ("## 受け入れ条件\n\n### 小見出しだけ\n", None),
        ("## 受け入れ条件\n\n1. 動く（`- [ ]` でなくてよい）\n", None),
        ("## 何をするか\n\n- 直す\n", sprint_inputs.NO_SECTION),
    ],
)
def test_the_section_needs_one_non_blank_line(body, lacks):
    got = sprint_inputs.body_lack(5, body)
    assert (got.reason if got else None) == lacks


# ---------- 受け入れ条件 5 ----------


@pytest.mark.parametrize("pace", ["normal", "fast", "auto"])
def test_the_plans_are_the_same_as_without_the_check(tmp_path, issue_bodies, monkeypatch, pace):
    args, cwd = sprint_args(tmp_path, pace=pace)
    out = tmp_path / "m"
    real = sprint_inputs.refusal
    monkeypatch.setattr(sprint_inputs, "refusal", lambda a, command: None)
    monkeypatch.setattr(sys, "argv", ["supervise.py", *args])
    monkeypatch.chdir(cwd)
    with pytest.raises(SystemExit) as e:
        supervise.main()
    assert e.value.code in (0, None)
    unchecked = written(out)
    assert unchecked
    shutil.rmtree(out)
    monkeypatch.setattr(sprint_inputs, "refusal", real)
    issue_bodies.body(12, "## 受け入れ条件\n\n- [ ] 1. 動く\n")
    p, res = run(args, cwd)
    assert res["status"] == "ok", p.stdout + p.stderr
    assert issue_bodies.calls() == [12]
    assert written(out) == unchecked
