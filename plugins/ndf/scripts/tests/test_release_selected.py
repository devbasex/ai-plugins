"""選んで集める取り込み方（`branch_flow.promotion: selected`。#3121）のリリース。

- リリースブランチは本番チャネルの先頭と選んだ単位のマージだけからなる（受け入れ条件 5・16・17・I5）
- 承認資料の範囲は前のタグ..リリースブランチの先頭で、選んだ単位の中の PR だけが入る（受け入れ条件 6）
- 承認の後に別の単位をマージした先端は承認の外として止まり、版上げだけなら通る（分冊の決定 14）
- `new sprint` のリリースのプランが、開発版の後にリリースブランチを組み、本番は組んだブランチから本番チャネルへ直接出す
- 宣言の無いリポジトリのリリースのプランは変わらない（受け入れ条件 2・I2）
- pace: fast はスプリントブランチを作らないため、選んで集める宣言と組まない
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
import approved_commit as ac  # noqa: E402
from release_lib import included, promotion  # noqa: E402

SUPERVISE = SCRIPTS / "supervise.py"
SELECTED = {"start": "production", "integrate": "check", "doc_prs": "sprint", "promotion": "selected"}
BRANCH = "release/v1.1.0"


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True)
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout.strip()


def ancestor(repo: Path, a: str, b: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", a, b]).returncode == 0


def merge_pr(repo: Path, base: str, n: int, name: str) -> None:
    git(repo, "checkout", "-q", "-b", name, base)
    (repo / f"f{n}").write_text(f"{name}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"feat {name}")
    git(repo, "checkout", "-q", base)
    git(repo, "merge", "-q", "--no-ff", "-m", f"Merge pull request #{n} from o/{name}", name)


@pytest.fixture()
def history(tmp_path) -> Path:
    """main（タグ ndf--v1.0.0）から切った sprint/a（PR #11）と sprint/b（PR #12）。develop には sprint/b と開発版の版上げだけが
    入っている（リリース 9 の形）。"""
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(tmp_path / "o.git")], check=True)
    repo = tmp_path / "r"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "o.git"), str(repo)], check=True, capture_output=True)
    git(repo, "config", "user.name", "t")  # build_branch のマージは利用者の設定の無い CI でもコミットを作る
    git(repo, "config", "user.email", "t@t")
    git(repo, "checkout", "-q", "-b", "main")
    (repo / "a").write_text("0\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    git(repo, "tag", "ndf--v1.0.0")
    git(repo, "push", "-q", "origin", "main", "--tags")
    for sprint, n in (("sprint/a", 11), ("sprint/b", 12)):
        git(repo, "checkout", "-q", "-b", sprint, "main")
        merge_pr(repo, sprint, n, f"feat-{n}")
        git(repo, "push", "-q", "origin", sprint)
    git(repo, "checkout", "-q", "-b", "develop", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge pull request #13 from o/sprint/b", "sprint/b")
    (repo / "dev").write_text("1.1.0-dev.1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Release: ndf v1.1.0-dev.1")
    git(repo, "push", "-q", "origin", "develop")
    git(repo, "checkout", "-q", "main")
    return repo


def test_release_branch_holds_only_production_and_the_selected_units(history):
    """受け入れ条件 5・16・17・I5: 選ばない単位・develop にだけあるコミット（別のスプリントと開発版の版上げ）を含まない。"""
    got = promotion.build_branch(history, BRANCH, "main", ["sprint/a"])
    head = got["head"]
    assert git(history, "rev-parse", f"origin/{BRANCH}") == head
    assert ancestor(history, "origin/main", head) and ancestor(history, "origin/sprint/a", head)
    assert not ancestor(history, "origin/sprint/b", head)
    assert not ancestor(history, "origin/develop", head)
    assert got["units"] == {"sprint/a": git(history, "rev-parse", "origin/sprint/a")}


def test_the_approval_range_is_the_prs_inside_the_selected_units(history):
    """受け入れ条件 6: 範囲は前のタグ..リリースブランチの先頭で、選んだ単位へ入った PR だけが並ぶ。"""
    head = promotion.build_branch(history, BRANCH, "main", ["sprint/a"])["head"]
    assert [p.number for p in included.release_range(history, "ndf--v1.0.0", head)] == [11]


def test_the_approval_range_lists_the_prs_of_a_sprint_merged_into_the_unit(history):
    """分冊の決定 18: 依存先の sprint/a を sprint/b へ通常のマージで取り込み b だけを選ぶと、a の PR も範囲に並ぶ。"""
    git(history, "checkout", "-q", "sprint/b")
    git(history, "merge", "-q", "--no-ff", "--no-edit", "sprint/a")
    git(history, "push", "-q", "origin", "sprint/b")
    git(history, "checkout", "-q", "main")
    head = promotion.build_branch(history, BRANCH, "main", ["sprint/b"])["head"]
    assert [p.number for p in included.release_range(history, "ndf--v1.0.0", head)] == [11, 12]


def test_rebuilding_adds_onto_the_pushed_branch_without_force(history):
    """決定 6: 組み直しは push 済みのリリースブランチの子孫になる（強制 push をしない）。"""
    first = promotion.build_branch(history, BRANCH, "main", ["sprint/a"])["head"]
    git(history, "checkout", "-q", "sprint/a")
    (history / "more").write_text("y\n")
    git(history, "add", "-A")
    git(history, "commit", "-qm", "more")
    git(history, "push", "-q", "origin", "sprint/a")
    git(history, "checkout", "-q", "main")
    again = promotion.build_branch(history, BRANCH, "main", ["sprint/a"])
    assert again["rebuilt"] and ancestor(history, first, again["head"])
    assert ancestor(history, "origin/sprint/a", again["head"])


def test_the_production_worktree_follows_the_rebuilt_release_branch(history, monkeypatch):
    """本番のプランの作業ツリーを作った後に組み直すと、使い回す作業ツリーは組み直した先頭へ進む（古い HEAD で版を上げない）。"""
    from supervise_lib import paths

    monkeypatch.setattr(paths, "_prepare_deps", lambda *a, **k: None)
    promotion.build_branch(history, BRANCH, "main", ["sprint/a"])
    wt = history / ".worktrees" / BRANCH
    plan = {"作業場所": str(wt), "branch": BRANCH, "起点": "origin/main", "起点のコミット": f"origin/{BRANCH}", "リポジトリ": str(history)}
    assert paths.ensure_worktree(plan) is None
    again = promotion.build_branch(history, BRANCH, "main", ["sprint/a", "sprint/b"])["head"]
    assert paths.ensure_worktree(plan) is None
    assert git(wt, "rev-parse", "HEAD") == again


def test_a_unit_merged_after_approval_is_outside_the_approval(history, tmp_path):
    """分冊の決定 14: 承認の後の版上げは通し、承認の後に別のスプリントブランチをマージした先端は承認の外として止める。"""
    approved = promotion.build_branch(history, BRANCH, "main", ["sprint/a"])["head"]
    wt = tmp_path / "w"
    git(history, "worktree", "add", "-q", "-b", "rel", str(wt), f"origin/{BRANCH}")
    (wt / "ver").write_text("1.1.0\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "Release: ndf v1.1.0")
    git(wt, "push", "-q", "origin", f"HEAD:{BRANCH}")
    tip = git(wt, "rev-parse", "HEAD")
    assert ac.compare_approved(history, approved, tip, [promotion.allowed_after(history, approved, BRANCH, 1)]).ok
    git(wt, "merge", "-q", "--no-ff", "-m", f"Merge origin/sprint/b into {BRANCH}", "origin/sprint/b")
    git(wt, "push", "-q", "origin", f"HEAD:{BRANCH}")
    tip = git(wt, "rev-parse", "HEAD")
    verdict = ac.compare_approved(history, approved, tip, [promotion.allowed_after(history, approved, BRANCH, 1)])
    assert not verdict.ok and verdict.reason == ac.OUTSIDE_COMMITS


def make_repo(tmp_path: Path, flow=None, pace=None) -> Path:
    repo = tmp_path / "repo"
    (repo / ".ndf").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "develop", str(repo)], check=True)
    wt = {"version": 1, "base_branch": "develop", "production_branch": "main", **({"branch_flow": flow} if flow else {})}
    (repo / ".ndf" / "worktree.json").write_text(json.dumps(wt))
    release = {"form": "package-plugin", "plugin": "ndf", "runtimes": ["claude"]}
    (repo / ".ndf" / "supervise.json").write_text(json.dumps({"test": {"command": "true"}, "release": release}))
    if pace:
        (repo / ".ndf" / "pace.json").write_text(json.dumps(pace))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def new_sprint(tmp_path: Path, repo: Path, *extra: str):
    out = tmp_path / "out"
    args = ["new", "sprint", "--name", "s1", "--worktree", str(repo), "--issue", "11", "--version", "1.1.0-dev.1", "--out", str(out)]
    return inproc.run(SUPERVISE, *args, *extra, cwd=repo), out


def release_plans(out: Path) -> tuple[dict, dict]:
    def load(stem):
        return json.loads(next(out.glob(f"*-{stem}.json")).read_text(encoding="utf-8"))

    return load("release"), load("release-prod")


def step(plan: dict, sid: str) -> dict | None:
    return next((s for s in plan["steps"] if s["id"] == sid), None)


@pytest.mark.usefixtures("issue_bodies")
def test_selected_sprint_builds_the_release_branch_and_releases_it_directly(tmp_path):
    """開発版のデプロイの後・承認資料の前にスプリントブランチだけでリリースブランチを組み、承認資料はその先頭を出す。
    本番は組んだリリースブランチから作業ツリーを作り、版上げの比べ先を本番チャネルにし、本番チャネルへ直接出す。"""
    repo = make_repo(tmp_path, SELECTED)
    p, out = new_sprint(tmp_path, repo)
    assert p.returncode == 0, p.stdout + p.stderr
    dev, prod = release_plans(out)
    build = step(dev, "release-branch")
    assert build and "--units sprint/s1" in build["cmd"] and "--production main" in build["cmd"]
    assert step(dev, "verify")["next"] == "release-branch" and build["next"] == "facts"
    assert f"--head {BRANCH}" in step(dev, "facts")["cmd"] and f"--head {BRANCH}" in step(dev, "others")["cmd"]
    assert prod["起点のコミット"] == f"origin/{BRANCH}"
    assert "--base main" in step(prod, "bump")["cmd"]
    assert "--promotion selected" in step(prod, "release")["cmd"]
    assert "origin develop" in step(prod, "verify")["cmd"]  # 作業ツリーはベースブランチを追う（本番チャネルは ff で入らない）


@pytest.mark.usefixtures("issue_bodies")
def test_without_a_declaration_the_release_plans_do_not_change(tmp_path):
    """受け入れ条件 2・I2: 宣言が無ければリリースブランチを組まず、本番はベースブランチ経由のまま。"""
    repo = make_repo(tmp_path)
    p, out = new_sprint(tmp_path, repo)
    assert p.returncode == 0, p.stdout + p.stderr
    dev, prod = release_plans(out)
    assert step(dev, "release-branch") is None and step(dev, "verify")["next"] == "facts"
    assert "--head" not in step(dev, "facts")["cmd"] and "--head" not in step(dev, "others")["cmd"]
    assert "起点のコミット" not in prod
    assert "--base develop" in step(prod, "bump")["cmd"] and "--promotion" not in step(prod, "release")["cmd"]


@pytest.mark.usefixtures("issue_bodies")
def test_fast_pace_refuses_the_selected_flow(tmp_path):
    """pace: fast はスプリントブランチを作らないため、選んで集める宣言では計画を書かない。"""
    repo = make_repo(tmp_path, SELECTED, pace={"version": 1, "fast": {"enabled": True, "modes": ["standard"], "verify": "true"}})
    p, out = new_sprint(tmp_path, repo, "--pace", "fast")
    got = json.loads(p.stdout.strip().splitlines()[-1])
    assert got["status"] == "stopped" and "branch_flow.promotion" in got["summary"]
    assert not (out / "sprint.json").exists()


class _Ops:
    """戻すマージだけを差し替えた `release-steps.py` の代わり。`fail` があれば -sync のマージがその例外で落ちる。"""

    def __init__(self, fail=None):
        self.fail = fail

    def _merged_sync_pr(self, root, branch, base):
        return None

    def _merge_sync_pr(self, root, a, plugin, branch, base, main_pr, lead=None, sections=False):
        if self.fail:
            raise self.fail
        assert sections  # CHANGELOG.md の同じ版の節の衝突も解く
        return 31, "merge"


def test_merge_back_failure_is_reported_apart_from_the_published_release():
    """戻すマージは公開の後に打ち、落ちても本番の完了（タグ・GitHub Release）を消さずに stopped と失敗の項目だけを足す。"""
    from step_result import StepError

    a = type("A", (), {"version": "1.1.0"})()
    items, metrics = [{"kind": "tag", "name": "ndf--v1.1.0", "result": "created"}], {}
    back = promotion.merge_back_after(Path("."), a, "ndf", BRANCH, "develop", "main", 30, _Ops(StepError("衝突")))
    status, code, summary = back(items, metrics, "タグを作った")
    assert (status, code) == ("stopped", 1) and summary.startswith("タグを作った。") and "本番は出ている" in summary
    assert items[0]["result"] == "created" and items[-1]["result"] == "failed" and items[-1]["head"] == f"{BRANCH}-sync"
    items, metrics = [], {}
    back = promotion.merge_back_after(Path("."), a, "ndf", BRANCH, "develop", "main", 30, _Ops())
    assert back(items, metrics, "タグを作った") == ("ok", 0, "タグを作った") and metrics["sync_pr"] == 31
    assert items == [{"kind": "pr", "base": "develop", "head": f"{BRANCH}-sync", "name": "#31", "result": "merged"}]
