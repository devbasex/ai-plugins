"""`new sprint` がブランチ運用の宣言（#3121）どおりにプランを組むこと。

宣言の無いリポジトリは変更前の値（設計文書の「変更前の値」の表）のまま（受け入れ条件 2・13）。選んで集める宣言では、
起点が本番チャネル、設計 PR の宛先がスプリントブランチ、検査の取り込みの元が本番チャネルになる（受け入れ条件 4・14・I4）。
開始時のスナップショットを目録へ書き、宣言を変えても同じスプリントは開始時の値で組む（受け入れ条件 10・I3）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS))
import inproc  # noqa: E402
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import branch_flow  # noqa: E402

SUPERVISE = SCRIPTS / "supervise.py"
SELECTED = {"start": "production", "integrate": "check", "doc_prs": "sprint", "promotion": "selected"}

pytestmark = pytest.mark.usefixtures("issue_bodies")


def make_repo(tmp_path: Path, flow=None) -> Path:
    repo = tmp_path / "repo"
    (repo / ".ndf").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "develop", str(repo)], check=True)
    write_flow(repo, flow)
    (repo / ".ndf" / "supervise.json").write_text(json.dumps({"test": {"command": "true"}}))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], check=True)
    return repo


def write_flow(repo: Path, flow) -> None:
    wt = {"version": 1, "base_branch": "develop", "production_branch": "main"}
    if flow is not None:
        wt["branch_flow"] = flow
    (repo / ".ndf" / "worktree.json").write_text(json.dumps(wt))


def new_sprint(tmp_path, repo, name="s1"):
    out = tmp_path / name
    args = ["new", "sprint", "--name", name, "--worktree", str(repo), "--issue", "11", "--design", "11", "--out", str(out)]
    return inproc.run(SUPERVISE, *args, cwd=repo), out


def plans(out: Path) -> dict[str, dict]:
    got = {}
    for e in json.loads((out / "sprint.json").read_text(encoding="utf-8"))["ステージ"]:
        for path in e.get("plans") or []:
            got[Path(path).stem.split("-", 1)[1]] = json.loads(Path(path).read_text(encoding="utf-8"))
    return got


def step(plan: dict, sid: str) -> dict:
    return next(s for s in plan["steps"] if s["id"] == sid)


def values(out: Path) -> dict:
    """設計文書の「変更前の値」の表の行。"""
    p = plans(out)
    design, branch, check = p["design-11"], p["sprint-branch"], p["check"]
    return {
        "design.起点": design["起点"],
        "design.pr.base": step(design, "pr")["base"],
        "design.glossary": "--diff origin/" + step(design, "glossary-check")["cmd"].split("--diff origin/")[1].split()[0],
        "branch.from": "--from" in step(branch, "sprint-branch")["cmd"],
        "check.pr.base": step(check, "pr")["base"],
        "check.sync": step(check, "base-sync")["cmd"].split("--base ")[1].split()[0],
    }


BASELINE = {
    "design.起点": "origin/develop",
    "design.pr.base": "develop",
    "design.glossary": "--diff origin/develop",
    "branch.from": False,
    "check.pr.base": "develop",
    "check.sync": "develop",
}


@pytest.mark.parametrize("flow", [None, {}, dict(branch_flow.DEFAULT)], ids=["no-key", "empty", "default-row"])
def test_without_a_declaration_the_plan_keeps_the_values_before_the_change(tmp_path, flow):
    """受け入れ条件 2・13・I2: 宣言が無い・外した・既定値の行のどれでも、変更前の値と一致する。"""
    repo = make_repo(tmp_path, flow)
    p, out = new_sprint(tmp_path, repo)
    assert p.returncode == 0, p.stdout + p.stderr
    assert values(out) == BASELINE
    names = [e["name"] for e in json.loads((out / "sprint.json").read_text())["ステージ"]]
    assert names.index("設計") < names.index("スプリントブランチ")


def test_selected_flow_starts_from_production_and_sends_design_prs_to_the_sprint_branch(tmp_path):
    """受け入れ条件 4・14・I4: 起点は origin/<本番チャネル>、設計 PR はスプリントブランチ、検査はベースブランチ宛てで、
    取り込みの元は本番チャネル。スプリントブランチは設計 PR より前に切る。"""
    repo = make_repo(tmp_path, SELECTED)
    p, out = new_sprint(tmp_path, repo)
    assert p.returncode == 0, p.stdout + p.stderr
    assert values(out) == {
        "design.起点": "origin/sprint/s1",
        "design.pr.base": "sprint/s1",
        "design.glossary": "--diff origin/sprint/s1",
        "branch.from": True,
        "check.pr.base": "develop",
        "check.sync": "main",
    }
    assert "--from main" in step(plans(out)["sprint-branch"], "sprint-branch")["cmd"]
    manifest = json.loads((out / "sprint.json").read_text())
    assert manifest["ステージ"][0]["name"] == "スプリントブランチ"
    snap = manifest[branch_flow.SNAPSHOT_KEY]
    assert snap["promotion"] == "selected" and snap["start_branch"] == "main" and snap["base_branch"] == "develop"


def test_a_running_sprint_keeps_the_snapshot_after_the_declaration_changes(tmp_path):
    """受け入れ条件 10・I3: 開始時のスナップショットを目録へ書き、宣言を変えて打ち直しても同じスプリントは開始時の値で組む。"""
    repo = make_repo(tmp_path, None)
    p, out = new_sprint(tmp_path, repo)
    assert p.returncode == 0, p.stdout + p.stderr
    write_flow(repo, SELECTED)
    p, out = new_sprint(tmp_path, repo)
    assert p.returncode == 0, p.stdout + p.stderr
    assert values(out) == BASELINE
    # 新しく始めるスプリントは変えた宣言で組む
    p, other = new_sprint(tmp_path, repo, name="s2")
    assert p.returncode == 0, p.stdout + p.stderr
    assert values(other)["design.pr.base"] == "sprint/s2"


@pytest.mark.parametrize(
    "flow, key",
    [({"promotion": "selected"}, "start"), ({"start": "trunk"}, "start"), ({"unknown": 1}, "unknown"), ("selected", "(全体)")],
)
def test_a_broken_declaration_stops_new_sprint_with_the_key_to_fix(tmp_path, flow, key):
    """受け入れ条件 1・I1: 表に合わない組・知らないキー・型の違う値は、既定値へ倒さずに終了コード 2 と直すキーで止まる。"""
    repo = make_repo(tmp_path, flow)
    p, out = new_sprint(tmp_path, repo)
    assert p.returncode == 2
    assert f"branch_flow.{key}" in p.stderr
    assert not (out / "sprint.json").exists()
