"""選んで集める取り込み方（ブランチ戦略の宣言の `release_strategy: release_branch`。#3121・#3672）のリリース。

- リリースブランチは本番チャネルの先頭と選んだ単位のマージだけからなる（受け入れ条件 5・16・17・I5）
- 承認資料の範囲は前のタグ..リリースブランチの先頭で、選んだ単位の中の PR だけが入る（受け入れ条件 6）
- 承認の後に別の単位をマージした先端は承認の外として止まり、版上げだけなら通る（分冊の決定 14）
- `new sprint` のリリースのプランが、開発版の後にリリースブランチを組み、本番は組んだブランチから本番チャネルへ直接出す
- ベースブランチを丸ごと出す宣言（merge_base_branch）のリリースのプランは変わらない（受け入れ条件 2・I2）
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
import strategy_fixture  # noqa: E402
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import approved_commit as ac  # noqa: E402
from release_lib import included  # noqa: E402
from release_lib import strategy_route as promotion  # noqa: E402

SUPERVISE = SCRIPTS / "supervise.py"
# 選んで集める取り込み方（release_branch）と、ベースブランチを丸ごと出す取り込み方（merge_base_branch）の種類
SELECTED, WHOLE = "mobile", "web-develop"
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


def make_repo(tmp_path: Path, flow: str = WHOLE, pace=None) -> Path:
    repo = tmp_path / "repo"
    (repo / ".ndf").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "develop", str(repo)], check=True)
    release = {**strategy_fixture.section("plugin")["release"], "plugin": "ndf", "runtimes": ["claude"]}
    strategy_fixture.write_strategy(repo, flow, base="develop", production="main", release=release)
    (repo / ".ndf" / "supervise.json").write_text(json.dumps({"test": {"command": "true"}}))
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
    assert "--release-strategy release_branch" in step(prod, "release")["cmd"]
    assert "origin develop" in step(prod, "verify")["cmd"]  # 作業ツリーはベースブランチを追う（本番チャネルは ff で入らない）


@pytest.mark.usefixtures("issue_bodies")
def test_the_whole_flow_keeps_the_release_plans(tmp_path):
    """受け入れ条件 2・I2: ベースブランチを丸ごと出す宣言ではリリースブランチを組まず、本番はベースブランチ経由のまま。"""
    repo = make_repo(tmp_path)
    p, out = new_sprint(tmp_path, repo)
    assert p.returncode == 0, p.stdout + p.stderr
    dev, prod = release_plans(out)
    assert step(dev, "release-branch") is None and step(dev, "verify")["next"] == "facts"
    assert "--head" not in step(dev, "facts")["cmd"] and "--head" not in step(dev, "others")["cmd"]
    assert "起点のコミット" not in prod
    assert "--base develop" in step(prod, "bump")["cmd"] and "--release-strategy" not in step(prod, "release")["cmd"]


@pytest.mark.usefixtures("issue_bodies")
def test_fast_pace_refuses_the_selected_flow(tmp_path):
    """pace: fast はスプリントブランチを作らないため、選んで集める宣言では計画を書かない。"""
    repo = make_repo(tmp_path, SELECTED, pace={"version": 1, "fast": {"enabled": True, "modes": ["standard"], "verify": "true"}})
    p, out = new_sprint(tmp_path, repo, "--pace", "fast")
    got = json.loads(p.stdout.strip().splitlines()[-1])
    assert got["status"] == "stopped" and "取り込み方 release_branch" in got["summary"]
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
    back = promotion.merge_back_after(
        Path("."), a, "ndf", promotion.ReleaseBranches(BRANCH, "develop", "main"), 30, _Ops(StepError("衝突"))
    )
    status, code, summary = back(items, metrics, "タグを作った")
    assert (status, code) == ("stopped", 1) and summary.startswith("タグを作った。") and "本番は出ている" in summary
    assert items[0]["result"] == "created" and items[-1]["result"] == "failed" and items[-1]["head"] == f"{BRANCH}-sync"
    items, metrics = [], {}
    back = promotion.merge_back_after(Path("."), a, "ndf", promotion.ReleaseBranches(BRANCH, "develop", "main"), 30, _Ops())
    assert back(items, metrics, "タグを作った") == ("ok", 0, "タグを作った") and metrics["sync_pr"] == 31
    assert items == [{"kind": "pr", "base": "develop", "head": f"{BRANCH}-sync", "name": "#31", "result": "merged"}]


# --- #2773: CD のある場合は先端と PR の上の承認の完全な一致だけで通す（#3168 を統合） ------------------------------------


class _MergeOps:
    """本番の PR のマージだけを差し替えた `release-steps.py` の代わり。"""

    def __init__(self, moved=False):
        self.merged, self.moved = [], moved

    def wait_and_merge(self, root, n, **kw):
        if self.moved:
            raise promotion.HeadMoved("動いた")
        self.merged.append(kw)
        return "m"


@pytest.mark.parametrize(
    "state, moved, merged",
    [("failure", False, False), ("success", True, False), ("success", False, True)],
)
def test_exact_match_gates_any_commit_added_after_the_approval(monkeypatch, state, moved, merged):
    """受け入れ条件 13・I6: 承認の後にコミットが足される・組み直すと先端が承認と違い 10 で止まる（許可リストの許容を使わない）。
    先端が承認と一致すれば迂回なしで 1 回だけマージする。マージを待つ間に動けば入れ直さずに 10。"""
    import release_case

    from release_lib import reviewed

    head = "c" * 40
    codes = []

    def emit(obj, code=None):
        codes.append(code)
        raise SystemExit(obj)

    monkeypatch.setattr(promotion.repo_lib, "owner_repo", lambda r: "o/r")
    monkeypatch.setattr(reviewed.approval_trail, "read_head", lambda repo, n: (head, "main", "open"))
    monkeypatch.setattr(reviewed.approval, "approved_on_pr", lambda repo, n, h, c: reviewed.approval_trail.Judgment(state, "理由"))
    monkeypatch.setattr(reviewed, "wait_ci_except", lambda *a: None)
    monkeypatch.setattr(reviewed, "wait_trail_status", lambda *a: None)
    monkeypatch.setattr(reviewed, "emit", emit)
    ops = _MergeOps(moved)
    a = type("A", (), {"version": "1.1.0", "ci_wait": 60.0})()
    case = release_case.decide_case(True, 0, "")
    if merged:
        assert promotion._merge_exact(Path("."), a, "ndf", (BRANCH, "main"), 30, case, [], {}, ops) == "m"
        assert ops.merged == [{"expect": head, "ci_wait": 60.0, "gate_approved": "user", "no_admin": True}]
        return
    with pytest.raises(SystemExit):
        promotion._merge_exact(Path("."), a, "ndf", (BRANCH, "main"), 30, case, [], {}, ops)
    assert codes == [10] and ops.merged == []


# --- 現状固定（I-003）: release_selected の経路ごとの戻り値・items・metrics・止まり方 -----------------------------------

from types import SimpleNamespace  # noqa: E402

_APPROVED, _MERGED, _PROD_TIP, _EXACT = "a" * 40, "m" * 40, "p" * 40, "e" * 40
_BASE_METRICS = {
    "channel": "prod",
    "version": "1.1.0",
    "release_pr": None,
    "sync_pr": None,
    "main_pr": None,
    "tag": None,
    "merge_commit": None,
    "plugins": "ndf,pk",
    "release_strategy": "release_branch",
}


class _SelOps:
    def __init__(self, found=None, merge_commit=_MERGED):
        self.found, self.merge_commit, self.created = found, merge_commit, []

    def find_pr(self, root, branch, prod, states=()):
        return self.found

    def merge_commit_of(self, root, n):
        return self.merge_commit

    def create_pr(self, root, base, head, title, body):
        self.created.append((base, head, title, body))
        return 41


@pytest.fixture
def selected(monkeypatch):
    """git・本番の PR のマージ・戻すマージを差し替えた release_selected。`run(approved, ops, case, ancestor=, tag=)`。"""
    from release_lib import reviewed

    monkeypatch.setattr(promotion, "merge_back_after", lambda root, a, plugin, br, pr, ops: ("back", br, pr))
    monkeypatch.setattr(reviewed, "admin_back", lambda back: ("admin_back", back))
    monkeypatch.setattr(promotion.repo_lib, "owner_repo", lambda r: "o/r")
    monkeypatch.setattr(
        reviewed,
        "admin_record_on_resume",
        lambda root, repo, case, pr: {"kind": "comment", "name": f"#{pr}", "record": "admin_merge"} if case.admin_merge else None,
    )
    monkeypatch.setattr(
        promotion, "_merge_exact", lambda root, a, plugin, refs, pr, case, items, metrics, ops: (items.append({"exact": refs}), _EXACT)[1]
    )
    verdict = SimpleNamespace(tip="t" * 40, tolerated=[SimpleNamespace(sha="s1", subject="版上げ")])
    monkeypatch.setattr(promotion, "_merge_approved", lambda root, a, plugin, branch, pr, approved, items, metrics, ops: ("", verdict))
    from release_lib.context import ReleaseCtx

    def run(approved, ops, case=None, ancestor=0, tag=1, channel="prod"):
        def git(root, *args, check=True):
            if args[0] == "merge-base":
                return SimpleNamespace(returncode=ancestor, stdout="")
            if args[:3] == ("rev-parse", "-q", "--verify"):
                return SimpleNamespace(returncode=tag, stdout="")
            if args[0] == "rev-parse":
                return SimpleNamespace(returncode=0, stdout=_PROD_TIP + "\n")
            return SimpleNamespace(returncode=0, stdout="")

        monkeypatch.setattr(promotion, "git", git)
        a = SimpleNamespace(channel=channel, version="1.1.0", release_strategy="release_branch")
        ctx = ReleaseCtx(Path("."), a, "ndf", ["ndf", "pk"], BRANCH, "develop", "main")
        return promotion.release_selected(ctx, approved, ops, case)

    return run


def _back(pr):
    return ("admin_back", ("back", promotion.ReleaseBranches(BRANCH, "develop", "main"), pr))


def test_release_selected_refuses_a_non_prod_channel(selected):
    from step_result import EXIT_UNREADABLE, StepError

    with pytest.raises(StepError) as e:
        selected(_APPROVED, _SelOps(), channel="dev")
    assert e.value.code == EXIT_UNREADABLE and "--channel prod だけ" in str(e.value)


def test_release_selected_resumes_a_merged_production_pr(selected):
    ops = _SelOps(found={"state": "MERGED", "number": 7})
    assert selected(_APPROVED, ops) == (
        7,
        _MERGED,
        [{"kind": "pr", "name": "#7", "result": "already_merged", "base": "main"}],
        {**_BASE_METRICS, "approved_sha": _APPROVED},
        _back(7),
    )
    assert ops.created == []


def test_release_selected_resume_falls_back_to_the_production_tip(selected):
    got = selected("", _SelOps(found={"state": "MERGED", "number": 7}, merge_commit=""))
    assert got[:2] == (7, _PROD_TIP) and got[3] == {**_BASE_METRICS, "approved_sha": ""}


def test_release_selected_resume_stops_when_the_merge_lacks_the_approval(selected):
    from step_result import StepError

    with pytest.raises(StepError) as e:
        selected(_APPROVED, _SelOps(found={"state": "MERGED", "number": 7}), ancestor=1)
    assert e.value.code == 1 and str(e.value) == "マージ済みの本番の PR #7 のコミット mmmmmmmm が承認したコミット aaaaaaaa を含まない"


def test_release_selected_resume_writes_the_admin_record(selected):
    import release_case

    case = release_case.decide_case(True, 1, "", admin=True)
    _, _, items, metrics, _ = selected(_APPROVED, _SelOps(found={"state": "MERGED", "number": 7}), case)
    assert items == [
        {"kind": "pr", "name": "#7", "result": "already_merged", "base": "main"},
        {"kind": "comment", "name": "#7", "record": "admin_merge"},
    ]
    assert "case" not in metrics


def test_release_selected_stops_when_the_tag_exists(selected):
    from step_result import StepError

    with pytest.raises(StepError) as e:
        selected(_APPROVED, _SelOps(), tag=0)
    assert e.value.code == 1 and str(e.value) == "タグ ndf--v1.1.0 は既にある"


def test_release_selected_creates_the_pr_and_merges_the_approved_commit(selected):
    ops = _SelOps()
    assert selected(_APPROVED, ops) == (
        41,
        _PROD_TIP,
        [
            {"kind": "commit", "name": "s1", "result": "outside_unshipped", "subject": "版上げ"},
            {"kind": "pr", "name": "#41", "result": "merged", "base": "main", "admin": False},
        ],
        {**_BASE_METRICS, "approved_sha": _APPROVED, "compared_head": "t" * 40, "tolerated": 1},
        _back(41),
    )
    assert ops.created == [
        (
            "main",
            BRANCH,
            "Release: ndf v1.1.0 を main へ",
            "ndf v1.1.0 を main へ出す（選んだスプリントブランチを main から組んだリリースブランチ）。\n\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
        )
    ]


@pytest.mark.parametrize("approvals, admin, case_name, merged_admin", [(1, True, "cd-admin", True), (0, False, "cd-no-review", False)])
def test_release_selected_reuses_the_open_pr_with_exact_match(selected, approvals, admin, case_name, merged_admin):
    import release_case

    ops = _SelOps(found={"state": "OPEN", "number": 9})
    case = release_case.decide_case(True, approvals, "", admin=admin)
    assert selected("", ops, case) == (
        9,
        _EXACT,
        [{"exact": (BRANCH, "main")}, {"kind": "pr", "name": "#9", "result": "merged", "base": "main", "admin": merged_admin}],
        {**_BASE_METRICS, "approved_sha": "", "case": case_name},
        _back(9),
    )
    assert ops.created == []
