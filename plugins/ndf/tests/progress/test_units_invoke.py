"""工程「実装」を実装計画のレビューの単位ごとに流す処理（#4369）の、開始前の確かめと打ち直しの判定のテスト。

開始前の確かめ（`prepare`）は、手元の git の作業場所と裸のリモートで確かめる。GitHub は課題の本文の一括読み取りだけを偽にし、
リモートの `<所有者>/<リポジトリ>` は作業場所のリモートの URL（ローカルのパス）から決まらないので差し替える。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import sprint_fixture
import strategy_fixture

from ndf.adapters import git, github
from ndf.contracts import result
from ndf.core import durable
from ndf.core.plan import files as plan_files
from ndf.core.plan import runner as plan_runner
from ndf.core.plan.review_units_decide import Unit
from ndf.progress import issue_values, plan_runs, units_invoke
from ndf.progress.plan_runs import StageRun
from ndf.progress.units_invoke import RoundState

BRANCH = "sprint/s1"
PROJECT = {"ci": {"required_checks": ["pytest"]}}
PLAN = "issues/issue-3-plan.md"
PLAN_WITHOUT_UNITS = "# 3: 実装計画\n\n## タスク分解\n\n### Task 1: 読む\n"
PLAN_WITH_UNITS = """# 3: 実装計画

## タスク分解

### Task 1: 読む

### Task 2: 書く

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 |
| --- | --- | --- |
| 読み手 | Task 1 | 1 |
| 書き手 | Task 2 | 2 |
"""
BODIES = {3: "## 受け入れ条件\n\n- [ ] 1. 読める\n- [ ] 2. 書ける\n", 4: "## 受け入れ条件\n\n- [ ] 1. 動く\n"}


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def _commit_plan(repo: Path, text: str) -> None:
    (repo / "issues").mkdir(exist_ok=True)
    (repo / PLAN).write_text(text, encoding="utf-8")
    _git(repo, "add", "issues")
    _git(repo, "commit", "-q", "-m", "plan")


class _Hub:
    """課題の本文を一括で読む GitHub の偽。読んだ番号を `read` に残す。"""

    read: list[list[int]] = []

    def __init__(self, repo, api):
        self.repo = repo

    def nodes(self, numbers, node, fields, batch="github.graphql-batch"):
        _Hub.read.append(sorted(numbers))
        return {n: {"body": BODIES[n]} for n in numbers}


@pytest.fixture
def sprint(tmp_path, monkeypatch):
    """ブランチ sprint/s1 に居る作業場所と、そのリモート（origin。裸のリポジトリ）。実装計画は無く、リモートへ push 済み。"""
    repo = sprint_fixture.sprint_repo(tmp_path, PROJECT)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "start")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    monkeypatch.setattr(git.Git, "remote_repo", lambda self, remote=None: "o/r")
    monkeypatch.setattr(github, "GitHub", _Hub)
    monkeypatch.setattr(github, "client", lambda *a, **k: None)
    _Hub.read = []
    return repo


@pytest.fixture
def git_calls(monkeypatch):
    """打った git の引数の並び。"""
    calls: list[list[str]] = []
    start = git._start

    def recorded(args, cwd, **kwargs):
        calls.append(list(args))
        return start(args, cwd, **kwargs)

    monkeypatch.setattr(git, "_start", recorded)
    return calls


def _run(repo: Path, **inputs) -> StageRun:
    held = {"issues": [3, 4], "worktree": str(repo), "branch": BRANCH, **inputs}
    return StageRun("plan", "impl", held, (), "s", "implement", per_unit=True)


def _units_file(work: Path) -> dict:
    return json.loads((work / issue_values.UNITS_FILE).read_text(encoding="utf-8"))


def _clone(sprint: Path, tmp_path: Path) -> Path:
    """リモートを別の場所で clone した作業場所（ほかのマシンの代わり）。"""
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", "-b", BRANCH, str(sprint.parent / "remote.git"), str(other)], check=True)
    return other


def _remote_head(sprint: Path) -> str:
    return _git(sprint, "ls-remote", "origin", f"refs/heads/{BRANCH}").split()[0]


# --- 開始前の確かめ: これまでの経路 ---------------------------------------------------------------


@pytest.mark.parametrize("text", [PLAN_WITHOUT_UNITS, None], ids=["見出しの無い実装計画", "実装計画が無い"])
def test_a_sprint_without_review_units_keeps_the_worktree_and_asks_the_remote_only_once(sprint, tmp_path, git_calls, text):
    if text is not None:
        _commit_plan(sprint, text)
    head = _git(sprint, "rev-parse", "HEAD")
    work = tmp_path / "w"

    got = units_invoke.prepare(_run(sprint), work, sprint)

    assert got is None
    assert _units_file(work) == {"route": "plain", "units": []}
    assert _git(sprint, "rev-parse", "HEAD") == head
    assert [c for c in git_calls if c[0] in ("fetch", "ls-remote", "push")] == [c for c in git_calls if c[0] == "fetch"]
    assert len([c for c in git_calls if c[0] == "fetch"]) == 1
    assert _Hub.read == []


def test_a_branch_not_on_the_remote_is_confirmed_absent_and_read_from_the_local_head(sprint, tmp_path, git_calls):
    _git(sprint, "push", "-q", "origin", f":refs/heads/{BRANCH}")
    _commit_plan(sprint, PLAN_WITHOUT_UNITS)

    got = units_invoke.prepare(_run(sprint), tmp_path / "w", sprint)

    assert got is None
    assert [c[0] for c in git_calls if c[0] in ("fetch", "ls-remote", "push")] == ["fetch", "ls-remote"]


def test_a_fetch_that_fails_for_another_reason_stops_before_reading_the_plans(sprint, tmp_path):
    _git(sprint, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    with pytest.raises(plan_runs.UnitsStopped) as raised:
        units_invoke.prepare(_run(sprint), tmp_path / "w", sprint)

    assert raised.value.kind == "precondition" and BRANCH in raised.value.detail
    assert not (tmp_path / "w" / issue_values.UNITS_FILE).exists()


def test_an_uncommitted_plan_without_units_does_not_stop_a_sprint_without_units(sprint, tmp_path):
    (sprint / "issues").mkdir()
    (sprint / PLAN).write_text(PLAN_WITHOUT_UNITS, encoding="utf-8")

    assert units_invoke.prepare(_run(sprint), tmp_path / "w", sprint) is None


# --- 開始前の確かめ: 単位の経路 -------------------------------------------------------------------


def test_units_are_lined_up_and_written_with_the_ci_to_wait_for(sprint, tmp_path, capsys):
    _commit_plan(sprint, PLAN_WITH_UNITS)
    work = tmp_path / "w"

    got = units_invoke.prepare(_run(sprint), work, sprint)

    assert got is not None
    assert [(u.name, u.issue, u.supplemented) for u in got.units] == [("読み手", 3, False), ("書き手", 3, False), ("issue-4", 4, True)]
    assert got.units[0].criteria == ((3, 1),)
    held = _units_file(work)
    assert held["route"] == "units"
    assert [u["name"] for u in held["units"]] == ["読み手", "書き手", "issue-4"]
    assert held["units"][1]["criteria"] == [[3, 2]]
    expected_ci = {"base": BRANCH, "required": ["pytest"], "key": "ci.required_checks"}
    assert got.unit_ci == held["unit_ci"] == expected_ci
    assert _Hub.read == [[3, 4]]
    # 実装計画のコミットを単位の作業場所に含めるため、リモートへ送る
    assert _remote_head(sprint) == _git(sprint, "rev-parse", "HEAD")
    err = capsys.readouterr().err
    assert BRANCH in err and "pytest" in err and "ci.required_checks" in err


@pytest.mark.parametrize(
    ("workflow", "required", "key"),
    [
        ({"unit_required_checks": ["lint"]}, ["lint"], "workflow.unit_required_checks"),
        ({"unit_required_checks": []}, [], "workflow.unit_required_checks"),
    ],
    ids=["名前の並び", "空の並び"],
)
def test_the_unit_checks_are_read_from_the_declaration_when_written(sprint, tmp_path, workflow, required, key):
    strategy_fixture.write(sprint, "web-develop", {**PROJECT, "workflow": workflow})
    _commit_plan(sprint, PLAN_WITH_UNITS)

    got = units_invoke.prepare(_run(sprint), tmp_path / "w", sprint)

    assert got is not None and got.unit_ci == {"base": BRANCH, "required": required, "key": key}


def test_a_branch_not_on_the_remote_is_created_by_the_push(sprint, tmp_path):
    _git(sprint, "push", "-q", "origin", f":refs/heads/{BRANCH}")
    _commit_plan(sprint, PLAN_WITH_UNITS)

    got = units_invoke.prepare(_run(sprint), tmp_path / "w", sprint)

    assert got is not None
    assert _remote_head(sprint) == _git(sprint, "rev-parse", "HEAD")


def test_a_lagging_worktree_is_moved_forward_and_reads_the_units_added_on_the_remote(sprint, tmp_path):
    other = _clone(sprint, tmp_path)
    _commit_plan(other, PLAN_WITH_UNITS)
    _git(other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")

    got = units_invoke.prepare(_run(sprint), tmp_path / "w", sprint)

    assert got is not None and [u.name for u in got.units] == ["読み手", "書き手", "issue-4"]
    assert _git(sprint, "rev-parse", "HEAD") == _git(other, "rev-parse", "HEAD")


def test_a_worktree_diverged_from_the_remote_stops(sprint, tmp_path):
    other = _clone(sprint, tmp_path)
    _git(other, "commit", "-q", "--allow-empty", "-m", "elsewhere")
    _git(other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    _commit_plan(sprint, PLAN_WITH_UNITS)
    head = _git(sprint, "rev-parse", "HEAD")

    with pytest.raises(plan_runs.UnitsStopped) as raised:
        units_invoke.prepare(_run(sprint), tmp_path / "w", sprint)

    assert raised.value.kind == "precondition" and BRANCH in raised.value.detail
    assert _git(sprint, "rev-parse", "HEAD") == head
    assert _remote_head(sprint) == _git(other, "rev-parse", "HEAD")


@pytest.mark.parametrize("change", ["modified", "untracked"])
def test_an_uncommitted_plan_stops_a_sprint_with_units(sprint, tmp_path, change):
    if change == "modified":
        _commit_plan(sprint, PLAN_WITH_UNITS)
        (sprint / PLAN).write_text(PLAN_WITH_UNITS + "\n追記\n", encoding="utf-8")
    else:
        (sprint / "issues").mkdir()
        (sprint / PLAN).write_text(PLAN_WITH_UNITS, encoding="utf-8")

    with pytest.raises(plan_runs.UnitsStopped) as raised:
        units_invoke.prepare(_run(sprint), tmp_path / "w", sprint)

    assert raised.value.kind == "precondition" and PLAN in raised.value.detail
    assert "コミット" in raised.value.detail


@pytest.mark.parametrize(
    ("inputs", "fragment"),
    [({"branch": None}, "登録"), ({"branch": "sprint/other"}, "sprint/other")],
    ids=["文脈に無い", "今のブランチと違う"],
)
def test_a_branch_other_than_the_registered_sprint_branch_stops(sprint, tmp_path, inputs, fragment):
    _commit_plan(sprint, PLAN_WITH_UNITS)
    run = _run(sprint, **inputs)
    if inputs["branch"] is None:
        run = StageRun("plan", "impl", {k: v for k, v in run.inputs.items() if k != "branch"}, (), "s", "implement", per_unit=True)

    with pytest.raises(plan_runs.UnitsStopped) as raised:
        units_invoke.prepare(run, tmp_path / "w", sprint)

    assert raised.value.kind == "precondition" and fragment in raised.value.detail
    assert _Hub.read == []


def test_a_branch_declared_as_a_delivery_target_stops(sprint, tmp_path):
    section = strategy_fixture.section("web-develop")
    # 開発版のチャネルの取得元をスプリントブランチにする（ベースブランチと本番ブランチは別）
    section["channels"] = [c if c["name"] != "dev" else {**c, "ref": BRANCH} for c in section["channels"]]
    (sprint / ".ndf" / "project.json").write_text(json.dumps({**PROJECT, "branch_strategy": section}), encoding="utf-8")
    _commit_plan(sprint, PLAN_WITH_UNITS)

    with pytest.raises(plan_runs.UnitsStopped) as raised:
        units_invoke.prepare(_run(sprint), tmp_path / "w", sprint)

    assert raised.value.kind == "precondition" and "リリース先" in raised.value.detail


def test_an_unreadable_table_stops_with_the_reasons(sprint, tmp_path):
    _commit_plan(sprint, PLAN_WITH_UNITS.replace("| 書き手 | Task 2 | 2 |\n", ""))
    work = tmp_path / "w"

    with pytest.raises(plan_runs.UnitsStopped) as raised:
        units_invoke.prepare(_run(sprint), work, sprint)

    assert raised.value.kind == "invalid" and "Task 2 がどの単位にも無い" in raised.value.detail
    assert not (work / issue_values.UNITS_FILE).exists()


# --- リリース先のブランチと単位のブランチ ---------------------------------------------------------------


def test_released_branches_are_collected_from_the_four_declarations():
    section = {
        "branches": {"base": "develop", "production": "main"},
        "channels": [{"name": "stable", "ref": "main"}, {"name": "qa", "ref": "qa"}],
        "release": {"deliveries": [{"target": "web", "branch": "staging"}, {"target": "app", "branch": None}]},
    }

    assert units_invoke.released_branches(section) == frozenset({"develop", "main", "qa", "staging"})


def test_released_branches_need_only_the_branches():
    assert units_invoke.released_branches({"branches": {"base": "main", "production": "main"}, "release": {}}) == frozenset({"main"})


def test_a_unit_branch_is_the_sprint_branch_the_issue_and_a_short_hash_of_the_name():
    unit = Unit("単位の表の読み手", 4369, PLAN, (3,), (), False)

    got = units_invoke.unit_branch(BRANCH, unit)

    assert got == units_invoke.unit_branch(BRANCH, unit._replace(tasks=(4,)))
    assert got.startswith(f"{BRANCH}-u4369-") and len(got.rsplit("-", 1)[1]) == 8
    assert got != units_invoke.unit_branch(BRANCH, unit._replace(name="PR の読み取り"))
    assert _git_ok_ref(got)


def _git_ok_ref(name: str) -> bool:
    return subprocess.run(["git", "check-ref-format", "--branch", name], capture_output=True).returncode == 0


# --- 回の状態 ---------------------------------------------------------------------------------------


def _round(unit_dir: Path, number: int, report: dict | None, **files: str) -> Path:
    """回の保存先に、プランの実行の記録（`run.json`）と、終わっていればその結果と、ほかのファイルを置く。"""
    path = unit_dir / str(number)
    key = f"impl-{number}"
    plan_files.write_json(path / plan_runner.RUN_FILE, {"key": key, "name": "impl-x"})
    if report is not None:
        plan_files.write_json(plan_runner.result_path(path, durable.run_name(key)), report)
    for name, text in files.items():
        (path / name.replace("_", "-")).write_text(text, encoding="utf-8")
    return path


def _stopped_at(step: str) -> dict:
    return result.stopped("plan", "check_failed", "止まった", items=[{"kind": "plan", "name": "impl-x", "step": step}])


def test_the_round_state_reads_the_latest_round_with_a_run_record(tmp_path):
    unit_dir = tmp_path / "u"
    _round(unit_dir, 1, _stopped_at("review"))
    second = _round(unit_dir, 2, result.result("plan", result.OK, "通った"), reviewed_head="a" * 40 + "\n", stopped_head="b" * 40)
    (second / units_invoke.MERGE_FILE).write_text(json.dumps({"kind": "ci_pending"}), encoding="utf-8")
    (unit_dir / "3").mkdir()  # 記録の無い空の回は数えない

    got = units_invoke.round_state(unit_dir)

    assert got.round_dir == second and got.ran
    assert got.done is not None and got.done["status"] == result.OK
    assert (got.merge, got.reviewed, got.stopped_head) == ("ci_pending", "a" * 40, "b" * 40)


def test_the_round_state_without_rounds_points_at_the_first_round(tmp_path):
    got = units_invoke.round_state(tmp_path / "u")

    assert got == RoundState(tmp_path / "u" / "1", False, None, None, None, None)


def test_the_round_state_of_an_interrupted_round_has_no_result(tmp_path):
    first = _round(tmp_path / "u", 1, None)

    got = units_invoke.round_state(tmp_path / "u")

    assert got.round_dir == first and got.ran and got.done is None and got.stopped_head is None


# --- 打ち直しの判定 ---------------------------------------------------------------------------------

HEAD, OTHER = "1" * 40, "2" * 40


def _state(done: dict | None, *, merge=None, reviewed=None, stopped_head=None, ran=True) -> RoundState:
    return RoundState(Path("u/1"), ran, done, merge, reviewed, stopped_head)


OK_DONE = result.result("plan", result.OK, "通った")


@pytest.mark.parametrize(
    ("state", "head", "local_tests", "expected"),
    [
        (_state(None, ran=False), HEAD, True, ("run", False, None)),
        (_state(OK_DONE, merge="ci_pending", reviewed=HEAD, stopped_head=HEAD), HEAD, True, ("merge", False, None)),
        (_state(OK_DONE, merge="infra_wait", reviewed=HEAD, stopped_head=HEAD), HEAD, True, ("merge", False, None)),
        # マージを待つ回でも、HEAD がレビューした HEAD と違えばレビューからやり直す
        (_state(OK_DONE, merge="ci_pending", reviewed=OTHER, stopped_head=OTHER), HEAD, True, ("run", True, "test")),
        (_state(OK_DONE, merge="refused", reviewed=HEAD, stopped_head=HEAD), HEAD, True, ("run", True, "push")),
        (_state(OK_DONE, merge="refused", reviewed=HEAD, stopped_head=OTHER), HEAD, True, ("run", True, "test")),
        (_state(OK_DONE, merge="refused", reviewed=HEAD, stopped_head=OTHER), HEAD, False, ("run", True, "push")),
        (_state(_stopped_at("review"), stopped_head=HEAD), HEAD, True, ("run", True, "push")),
        (_state(_stopped_at("review-delta"), stopped_head=None), HEAD, True, ("run", True, "test")),
        (_state(_stopped_at("resolved"), stopped_head=OTHER), HEAD, False, ("run", True, "push")),
        (_state(_stopped_at("ci"), stopped_head=OTHER), HEAD, True, ("run", True, "test")),
        (_state(_stopped_at("ci"), stopped_head=None), HEAD, False, ("run", True, "push")),
        (_state(_stopped_at("ci"), stopped_head=HEAD), HEAD, True, ("run", False, None)),
        (_state(_stopped_at("test"), stopped_head=OTHER), HEAD, True, ("run", False, None)),
        (_state(_stopped_at("work"), stopped_head=None), HEAD, True, ("run", False, None)),
        (_state(None), HEAD, True, ("run", False, None)),
        (_state(result.result("plan", result.GATE, "待つ")), HEAD, True, ("run", False, None)),
    ],
    ids=[
        "回が無い",
        "CI の待ちでマージだけ",
        "基盤の待ちでマージだけ",
        "マージの待ちで HEAD が違う",
        "マージを拒まれ HEAD が同じ",
        "マージを拒まれ HEAD が違う",
        "マージを拒まれ HEAD が違いテストを外す",
        "レビューで止まり HEAD が同じ",
        "差分のレビューで止まり止まった HEAD が無い",
        "未解決で止まり HEAD が違いテストを外す",
        "テストより後で止まり HEAD が違う",
        "テストより後で止まり止まった HEAD が無くテストを外す",
        "テストより後で止まり HEAD が同じ",
        "テストで止まった",
        "作業で止まった",
        "結果の無い中断",
        "承認の待ち",
    ],
)
def test_resume_of_follows_the_table(state, head, local_tests, expected):
    got = units_invoke.resume_of(state, head, local_tests)

    assert (got.action, got.new_round, got.start) == expected
