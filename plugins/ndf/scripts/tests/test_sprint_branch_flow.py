"""`new sprint` がブランチ戦略の宣言（#3121・#3672）どおりにプランを組むこと。

ベースブランチを丸ごと出す宣言（merge_base_branch）は変更前の値（設計文書の「変更前の値」の表）のまま（受け入れ条件 2・13）。
選んで集める宣言（release_branch）では、起点が本番ブランチ、設計 PR の宛先がスプリントブランチ、検査の取り込みの元が本番ブランチ
になる（受け入れ条件 4・14・I4）。
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
import strategy_fixture  # noqa: E402
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import branch_strategy  # noqa: E402

SUPERVISE = SCRIPTS / "supervise.py"
# 選んで集める取り込み方（release_branch）と、ベースブランチを丸ごと出す取り込み方（merge_base_branch）の種類
SELECTED, WHOLE = "mobile", "web-develop"

pytestmark = pytest.mark.usefixtures("issue_bodies")


def make_repo(tmp_path: Path, flow=WHOLE, **edits) -> Path:
    repo = tmp_path / "repo"
    (repo / ".ndf").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "develop", str(repo)], check=True)
    write_flow(repo, flow, **edits)
    (repo / ".ndf" / "supervise.json").write_text(json.dumps({"test": {"command": "true"}}))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], check=True)
    return repo


def write_flow(repo: Path, flow, **edits) -> None:
    """種類 `flow` の宣言（ベースブランチ develop・本番ブランチ main）を書く。種類の名前でなければ、その値をセクションとして書く。
    `edits` はセクションの最上位のキーを置き換える（`reviews=...` など）。"""
    if flow in strategy_fixture.KINDS:
        strategy_fixture.write_strategy(repo, flow, base="develop", production="main", **edits)
    else:
        (repo / ".ndf" / "project.json").write_text(json.dumps({"version": 1, branch_strategy.SECTION: flow}))


def add_reviews(repo: Path, reviews: dict) -> None:
    """今の宣言にレビューの単位（`reviews`）を足す。"""
    path = repo / ".ndf" / "project.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc[branch_strategy.SECTION]["reviews"] = reviews
    path.write_text(json.dumps(doc))


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
        "branch.from": "--from " + step(branch, "sprint-branch")["cmd"].split("--from ")[1].split()[0],
        "check.pr.base": step(check, "pr")["base"],
        "check.sync": " ".join(step(check, "base-sync")["cmd"].split(" base-sync ")[1].split()[:2]),
        "check.merge.guard": "gh pr list --base" in step(check, "merge")["cmd"],
    }


BASELINE = {
    "design.起点": "origin/develop",
    "design.pr.base": "develop",
    "design.glossary": "--diff origin/develop",
    "branch.from": "--from develop",
    "check.pr.base": "develop",
    "check.sync": "--pr {pr}",
    "check.merge.guard": False,
}


def test_the_whole_flow_keeps_the_values_before_the_change(tmp_path):
    """受け入れ条件 2・13・I2: ベースブランチを丸ごと出す宣言は、変更前の値と一致する。"""
    repo = make_repo(tmp_path)
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
        "branch.from": "--from main",
        "check.pr.base": "develop",
        "check.sync": "--base main",
        "check.merge.guard": True,
    }
    manifest = json.loads((out / "sprint.json").read_text())
    assert manifest["ステージ"][0]["name"] == "スプリントブランチ"
    snap = manifest[branch_strategy.SNAPSHOT_KEY]["section"]
    assert snap["release_strategy"] == "release_branch" and snap["branches"] == {"production": "main", "base": "develop"}


def test_a_running_sprint_keeps_the_snapshot_after_the_declaration_changes(tmp_path):
    """受け入れ条件 10・I3: 開始時のスナップショットを目録へ書き、宣言を変えて打ち直しても同じスプリントは開始時の値で組む。"""
    repo = make_repo(tmp_path)
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
    "edits, pointer",
    [
        ({"sprint": {"start": "production", "integrate": "check", "doc_prs": "base"}}, "R9 `/sprint/start`"),
        ({"sprint": {"start": "trunk", "integrate": "check", "doc_prs": "base"}}, "`/sprint/start`"),
        ({"unknown": 1}, "shape `/`"),
    ],
    ids=["combination", "value", "unknown-key"],
)
def test_a_broken_declaration_stops_new_sprint_with_the_key_to_fix(tmp_path, edits, pointer):
    """受け入れ条件 1・I1: 表に合わない組・知らないキー・型の違う値は、既定値へ倒さずに終了コード 2 と直すキーで止まる。"""
    repo = make_repo(tmp_path, **edits)
    p, out = new_sprint(tmp_path, repo)
    assert p.returncode == 2
    assert pointer in p.stderr
    assert not (out / "sprint.json").exists()


# --- レビューの単位（#3163） ---------------------------------------------------------------

REVIEW_IDS = ("review-push", "assess", "refactor", "review", "behavior-targets", "behavior", "behavior-verify", "review-pull")


def review_ids(plan: dict) -> list[str]:
    return [s["id"] for s in plan["steps"] if s["id"] in REVIEW_IDS]


def drives(plan: dict) -> list[tuple[str, str | None]]:
    return [(s["id"], s.get("drive")) for s in plan["steps"]]


def test_without_reviews_the_review_steps_stay_as_before(tmp_path):
    """受け入れ条件 3・I5: レビューの単位を書かない宣言（まとまりの記録なし）は、実装 PR にレビューを置かず、スプリントの PR に
    cross-refactoring・cross-review・振る舞いの確認を 1 回ずつ通し、設計 PR に cross-review を掛ける。"""
    repo = make_repo(tmp_path)
    p, out = new_sprint(tmp_path, repo)
    assert p.returncode == 0, p.stdout + p.stderr
    got = plans(out)
    assert review_ids(got["impl-11"]) == []
    assert review_ids(got["check"]) == ["assess", "refactor", "review", "behavior-targets", "behavior", "behavior-verify"]
    assert {("refactor", "cross-refactoring"), ("review", "cross-review")} <= set(drives(got["check"]))
    assert ("review", "cross-review") in drives(got["design-11"])
    snap = json.loads((out / "sprint.json").read_text())[branch_strategy.SNAPSHOT_KEY]["section"]
    assert "reviews" not in snap  # 書いていない種類は既定値の表から解く


REVIEWS = {
    "impl": {"checks": ["cross-review", "behavior"], "diff": "pr"},
    "sprint": {"checks": ["cross-refactoring"], "diff": "unchecked"},
    "release": {"checks": ["cross-review"], "diff": "resolution"},
    "design": {"checks": [], "diff": "none"},
}


def test_declared_reviews_place_only_the_chosen_steps(tmp_path):
    """受け入れ条件 2: PR の種類ごとに、宣言で選んだレビューのステップだけがプランに現れる。"""
    repo = make_repo(tmp_path, SELECTED, reviews=REVIEWS)
    p, out = new_sprint(tmp_path, repo)
    assert p.returncode == 0, p.stdout + p.stderr
    got = plans(out)
    impl = got["impl-11"]
    assert review_ids(impl) == ["review-push", "review", "behavior-targets", "behavior", "behavior-verify", "review-pull"]
    assert review_ids(impl)[-1] == "review-pull" and step(impl, "review-pull")["next"] == "ready"
    assert review_ids(got["check"]) == ["assess", "refactor"]
    design = got["design-11"]
    assert all(s.get("drive") != "cross-review" for s in design["steps"])
    assert "review-settled" not in [s["id"] for s in design["steps"]] and step(design, "requirements-recheck")
    snap = json.loads((out / "sprint.json").read_text())[branch_strategy.SNAPSHOT_KEY]["section"]
    assert snap["reviews"] == REVIEWS


def test_a_running_sprint_keeps_its_review_units(tmp_path):
    """受け入れ条件 9・I6: 開始の後に宣言へ reviews を足しても、同じスプリントの打ち直しは開始時のレビューの単位で組む。"""
    repo = make_repo(tmp_path)
    p, out = new_sprint(tmp_path, repo)
    assert p.returncode == 0, p.stdout + p.stderr
    before = {k: drives(v) for k, v in plans(out).items()}
    both = {"impl": {"checks": ["cross-review"], "diff": "pr"}, "sprint": {"checks": ["behavior"], "diff": "unchecked"}}
    add_reviews(repo, both)
    p, out = new_sprint(tmp_path, repo)
    assert p.returncode == 0, p.stdout + p.stderr
    assert {k: drives(v) for k, v in plans(out).items()} == before
    p, other = new_sprint(tmp_path, repo, name="s2")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "review" in review_ids(plans(other)["impl-11"])


@pytest.mark.parametrize(
    "flow, reviews, pointer",
    [
        # 実装の差分がどの PR でも cross-review されない（衝突の解消の差分は覆いに数えない）
        (SELECTED, {**REVIEWS, "impl": {"checks": [], "diff": "none"}}, "`/reviews`"),
        # 既定値の sprint が書いた impl と同じ実装の差分を cross-review する
        (WHOLE, {"impl": {"checks": ["cross-review"], "diff": "pr"}}, "`/reviews`"),
        # 種類に合わない対象の差分・取り込み方に合わない種類
        (WHOLE, {"sprint": {"checks": ["cross-review"], "diff": "resolution"}}, "`/reviews/sprint/diff`"),
        (WHOLE, {"release": {"checks": ["cross-review"], "diff": "pr"}}, "`/reviews`"),
        (WHOLE, {"other": {}}, "`/reviews`"),
    ],
)
def test_a_review_declaration_that_cannot_hold_stops_new_sprint(tmp_path, flow, reviews, pointer):
    """受け入れ条件 5・6・7: 直すキーを出して終了コード 2 で止まり、プランを作らない。"""
    repo = make_repo(tmp_path, flow, reviews=reviews)
    p, out = new_sprint(tmp_path, repo)
    assert p.returncode == 2
    assert pointer in p.stderr
    assert not (out / "sprint.json").exists()


def test_fast_pace_follows_the_sprint_and_impl_rows(tmp_path):
    """受け入れ条件 2: pace: fast でも、実装 PR とスプリントの PR（未検査の差分の検査）に選んだレビューだけを置き、
    スプリントの PR に cross-review を選ばなければ実装レビューのステージを置かない。"""
    from pace_state import sprint_state
    from test_supervise_pace_manual import commit_all
    from test_supervise_pace_verify import PRD, STG
    from test_supervise_pace_verify import make_repo as pace_repo

    repo = pace_repo(tmp_path, [STG, PRD])
    reviews = {"impl": {"checks": ["cross-review"], "diff": "pr"}, "sprint": {"checks": ["cross-refactoring"], "diff": "unchecked"}}
    add_reviews(repo, reviews)
    commit_all(repo)
    out = tmp_path / "f"
    args = ["new", "sprint", "--name", "f1", "--worktree", str(repo), "--issue", "11", "--out", str(out)]
    p = inproc.run(SUPERVISE, *args, "--pace", "fast", "--state", str(sprint_state(tmp_path, "fast")), cwd=repo)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "実装レビュー" not in [e["name"] for e in json.loads((out / "sprint.json").read_text())["ステージ"]]
    got = plans(out)
    assert review_ids(got["impl-11"]) == ["review-push", "review", "review-pull"]
    assert review_ids(got["check"]) == ["assess", "refactor"]
