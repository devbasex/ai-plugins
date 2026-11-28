"""この変更より前のスプリントの変換（`ndf.progress.units_convert`。#3702 の決定 10、受け入れ条件 3・4）のテスト。

送り先の URL は `git@github.com:o/r.git` で、`GIT_SSH_COMMAND` の偽の ssh が手元の bare リポジトリで受ける。変換の担当は
`Ports.call_plan` を偽にし、渡された作業場所で単位のファイルと表を書いてコミットする。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import git_sandbox
import pytest
import strategy_fixture
from ndf.adapters.git import GitError
from ndf.contracts import result, worker_kinds
from ndf.core import route
from ndf.core.plan import units_decide as ud
from ndf.core.plan import units_files
from ndf.core.plan.review_units_decide import PlanDoc, Unit, unit_key
from ndf.progress import (
    definition,
    design_record,
    gate,
    invoke,
    issue_values,
    model,
    plan_runs,
    run,
    run_view,
    stages,
    units_convert,
    units_flow,
    units_invoke,
)
from ndf.progress.gate import design as gate_design
from ndf.progress.plan_runs import StageResult, StageRun, UnitsStopped

URL = "git@github.com:o/r.git"
SPRINT = "sprint/s1"
PLAN = "issues/issue-3-plan.md"
UNITS = "issues/issue-3-plan.units.json"
PROJECT = {"branch_strategy": strategy_fixture.section("plugin")}
OLD_PLAN = """# 3: 実装計画

## タスク分解

### Task 1: 読む

読む本文。

### Task 2: 書く

書く本文。

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 |
| --- | --- | --- |
| 読み手 | Task 1 | 1 |
| 書き手 | Task 2 | 2 |
"""
CRITERIA = {3: 2}
LIMITS = ud.Limits(400, 1000)
REF_PREFIX = "refs/ndf/convert/"


def _doc(*, merged=False, edge=True, touch="src/b.py") -> dict:
    """旧い表と同じ分け方の単位のファイル。`merged` は 2 つのタスクを 1 単位にした違う分け方。"""
    tasks = [
        {"id": "T1", "title": "読む", "files": ["src/a.py"], "lines": 10, "criteria": ["3:1"], "after": []},
        {"id": "T2", "title": "書く", "files": [touch], "lines": 10, "criteria": ["3:2"], "after": []},
    ]
    units = [
        {"name": "読み手", "tasks": ["T1"], "after": []},
        {"name": "書き手", "tasks": ["T2"], "after": [{"unit": "読み手", "reason": "承認した表の順"}] if edge else []},
    ]
    if merged:
        units = [{"name": "読み手", "tasks": ["T1", "T2"], "after": []}]
    return {"version": 1, "issues": [3], "tasks": tasks, "units": units}


def _converted_files(root: Path, doc: dict) -> dict[str, str]:
    """単位のファイルと、`ndf plan units render` と同じ 2 つの表を書いた実装計画。"""
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    unit_file = units_files.parse_text(UNITS, text)
    views = ud.judge([unit_file], LIMITS, CRITERIA).views
    plan = units_files.render_tables((root / PLAN).read_text(encoding="utf-8"), views, UNITS)
    return {UNITS: text, PLAN: plan}


def _sh(cwd: Path, *args: str) -> str:
    return git_sandbox.sh(cwd, *args, strip=True, identity=True)


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def _commit(root: Path, files: dict[str, str], message: str = "c") -> str:
    _write(root, files)
    _sh(root, "add", "--", *files)
    _sh(root, "commit", "-q", "-m", message)
    return _sh(root, "rev-parse", "HEAD")


@pytest.fixture(scope="module")
def template(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("convert-template")
    work, bare = base / "work", base / "bare.git"
    _sh(base, "init", "-q", "--bare", "-b", "main", str(bare))
    _sh(base, "init", "-q", "-b", "main", str(work))
    for key, value in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        _sh(work, "config", key, value)
    _commit(work, {".ndf/project.json": json.dumps(PROJECT), ".gitignore": ".worktrees/\n"}, "init")
    _sh(work, "push", "-q", str(bare), "HEAD:refs/heads/main")
    _sh(work, "switch", "-q", "-c", SPRINT)
    _commit(work, {PLAN: OLD_PLAN}, "実装計画")
    _sh(work, "push", "-q", str(bare), f"HEAD:refs/heads/{SPRINT}")
    _sh(work, "remote", "add", "origin", URL)
    return base


class World:
    def __init__(self, work: Path, bare: Path, store: Path) -> None:
        self.work, self.bare, self.store = work, bare, store
        self.work_root = store / "machine"
        self.head = _sh(work, "rev-parse", "HEAD")
        self.events: list[model.Event] = []
        self.calls: list[StageRun] = []
        self.script: list = []  # 担当の起動ごとの振る舞い（尽きれば正しい変換）
        self.repo = URL

    # --- 回答 -------------------------------------------------------------------------------

    def answered(self, stage: str, payload: dict) -> None:
        draft = model.EventDraft(run_view.ANSWERED, run_view.CONDUCTOR, stage, reason="r", payload=payload, rev=1)
        self.events.append(model.Event(len(self.events) + 1, datetime(2026, 11, 28, tzinfo=timezone.utc), draft))

    def design_approved(self, head: str | None = None) -> None:
        payload = {"answer": "approved", "gate": "design", "gate_id": "g1", "seen": {"head": head or self.head}}
        self.answered("ドキュメントレビュー", payload)

    def reapproved(self, got: StageResult) -> None:
        self.answered("実装", {"answer": "approved", design_record.TARGET_KEY: got.report[design_record.TARGET_KEY]})

    # --- 呼び出し ---------------------------------------------------------------------------

    def job(self) -> units_convert.Job:
        snap = definition.load_definition("standard")
        view = run_view.View("sprint:abc:s1", 1, snap, {}, list(self.events))
        context = {"issues": [3], "branch": SPRINT, "worktree": str(self.work)}
        return units_convert.Job(view, context, lambda: self.repo, self.store, self.work_root)

    def request(self) -> plan_runs.ConvertRequest:
        stage_run = StageRun(
            "plan", "impl", {"issues": [3], "worktree": str(self.work), "mode": "standard", "branch": SPRINT}, (), "s", "i"
        )
        return plan_runs.ConvertRequest(stage_run, self.work, SPRINT, (3,), (), lambda numbers: CRITERIA)

    def call_plan(self, planned: StageRun, work_dir: Path) -> StageResult:
        self.calls.append(planned)
        step = self.script.pop(0) if self.script else self.convert_well
        got = step(Path(planned.inputs["worktree"]))
        return got or StageResult(result.result("plan", result.OK, "変換した"), {})

    def ports(self) -> units_invoke.Ports:
        return units_invoke.Ports(self.call_plan, lambda *a: pytest.fail("コマンドは打たない"))

    def convert(self, answer: str | None = None):
        return units_convert.convert(self.job(), self.request(), self.ports(), answer)

    def stopped(self, answer: str | None = None) -> UnitsStopped:
        with pytest.raises(UnitsStopped) as raised:
            self.convert(answer)
        return raised.value

    # --- 担当の振る舞い ---------------------------------------------------------------------

    def convert_well(self, tree: Path, **doc) -> None:
        _commit(tree, _converted_files(tree, _doc(**doc)), "変換")

    def writes(self, tree: Path, extra: dict[str, str] | None = None, **doc) -> None:
        _commit(tree, {**_converted_files(tree, _doc(**doc)), **(extra or {})}, "変換")

    # --- 状態 -------------------------------------------------------------------------------

    def remote(self) -> str:
        return _sh(self.bare, "rev-parse", f"refs/heads/{SPRINT}")

    def local(self) -> str:
        return _sh(self.work, "rev-parse", "HEAD")

    def refs(self) -> str:
        return _sh(self.work, "for-each-ref", "--format=%(refname) %(objectname)", REF_PREFIX)

    def saved(self) -> dict:
        return json.loads((self.work_root / units_convert.CONVERT_FILE).read_text(encoding="utf-8"))

    def push_remote(self, files: dict[str, str]) -> str:
        """ほかの場所から送り先のスプリントブランチを進める。"""
        other = self.store / "other"
        if not other.exists():
            _sh(self.store, "clone", "-q", "-b", SPRINT, str(self.bare), str(other))
        _sh(other, "pull", "-q", "--ff-only")
        head = _commit(other, files, "ほか")
        _sh(other, "push", "-q", "origin", f"HEAD:refs/heads/{SPRINT}")
        return head


@pytest.fixture
def world(template, tmp_path, monkeypatch) -> World:
    shutil.copytree(template / "work", tmp_path / "work")
    shutil.copytree(template / "bare.git", tmp_path / "bare.git")
    bare = tmp_path / "bare.git"
    ssh = tmp_path / "ssh"
    ssh.write_text(f'#!/bin/sh\nfor last; do :; done\nexec "${{last%% *}}" "{bare}"\n', encoding="utf-8")
    ssh.chmod(0o755)
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    for name, value in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.com"), ("GIT_COMMITTER_NAME", "t")):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_SSH_COMMAND", str(ssh))
    monkeypatch.setenv("GIT_SSH_VARIANT", "simple")
    store = tmp_path / "store"
    store.mkdir()
    return World(tmp_path / "work", bare, store)


def _pushed(w: World) -> None:
    assert w.remote() == w.local() != w.head
    assert json.loads(_sh(w.bare, "show", f"refs/heads/{SPRINT}:{UNITS}")) == _doc()
    assert w.refs() == ""


def _unpushed(w: World) -> None:
    assert (w.remote(), w.local()) == (w.head, w.head)


def test_a_conversion_matching_the_approved_table_is_pushed_without_asking(world):
    world.design_approved()

    assert world.convert() is None

    _pushed(world)
    [planned] = world.calls
    assert planned.name == "implementation-plan-convert" and planned.overrides["convert"]
    assert Path(planned.inputs["worktree"]) != world.work and not Path(planned.inputs["worktree"]).exists()
    assert world.saved() == {"commit": world.local(), "approval": {"kind": "design", "head": world.head}}
    # 打ち直しは担当を呼ばず、送らない
    assert world.convert() is None and len(world.calls) == 1


def test_the_template_of_the_conversion_never_pushes_and_hands_the_convert_input(tmp_path):
    t = route.templates()["implementation-plan-convert"]
    assert [step["id"] for step in route.expanded(t)["steps"]] == ["work"]
    assert "convert" in t.params and route.params()["convert"].default == ""


def test_the_plan_of_the_conversion_hands_the_convert_input_to_the_worker(world, tmp_path):
    world.script = [lambda tree: StageResult(result.stopped("plan", "transient", "止める"), {})]
    world.stopped()
    [planned] = world.calls
    tree = tmp_path / "tree"
    _sh(world.work, "worktree", "add", "-q", "--detach", str(tree), world.head)
    planned = replace(planned, inputs={**planned.inputs, "worktree": str(tree)}, overrides={**planned.overrides, "worktree": str(tree)})

    path = invoke.plan_path(planned, tmp_path / "state", world.work)

    plan = json.loads(path.read_text(encoding="utf-8"))
    assert [step["id"] for step in plan["steps"]] == ["work"] and plan["worktree"] == str(tree)
    values = json.loads((tmp_path / "state" / "values.json").read_text(encoding="utf-8"))["values"]
    assert worker_kinds.kind_values("implementation-plan", values, planned.inputs)["convert"] == "convert"


def test_without_a_design_approval_the_table_is_not_compared(world):
    world.script = [lambda tree: world.convert_well(tree, merged=True)]

    assert world.convert() is None

    assert world.remote() == world.local() != world.head
    assert world.saved()["approval"]["head"] is None


def _asked(world: World, got: StageResult) -> None:
    assert got is not None and got.status == "gate", got
    assert set(got.report["routes"]) == {"approved", "rejected"}
    assert got.report[design_record.TARGET_KEY]["sprint_branch"] == SPRINT
    material = Path(got.report["presentation_path"]).read_text(encoding="utf-8")
    assert "読み手" in material
    _unpushed(world)


def test_a_different_split_asks_again_and_pushes_only_after_the_approval(world):
    world.design_approved()
    world.script = [lambda tree: world.convert_well(tree, merged=True)]

    got = world.convert()
    _asked(world, got)
    # 回答の前の打ち直しも送らない
    _asked(world, world.convert())
    assert len(world.calls) == 1

    world.reapproved(got)
    assert world.convert("approved") is None
    assert world.remote() == world.local() != world.head and world.refs() == ""
    assert world.saved()["approval"]["kind"] == "reapproval"
    assert world.convert() is None


def test_a_rewritten_task_body_after_the_design_approval_asks_again(world):
    world.design_approved()
    world.head = _commit(world.work, {PLAN: OLD_PLAN.replace("書く本文。", "別の本文。")})
    _sh(world.work, "push", "-q", "origin", f"HEAD:refs/heads/{SPRINT}")

    got = world.convert()

    _asked(world, got)
    assert "書く本文" in Path(got.report["presentation_path"]).read_text(encoding="utf-8")


def test_a_change_of_the_tip_while_waiting_asks_again(world):
    world.design_approved()
    world.script = [lambda tree: world.convert_well(tree, merged=True)]
    got = world.convert()
    world.reapproved(got)
    world.head = world.push_remote({PLAN: OLD_PLAN.replace("読む本文。", "直した本文。")})
    _sh(world.work, "pull", "-q", "--ff-only", "origin", SPRINT)
    world.script = [lambda tree: world.convert_well(tree, merged=True)]

    again = world.convert("approved")

    assert again is not None and again.status == "gate"
    assert world.remote() == world.head


def test_a_rejection_drops_the_conversion_without_pushing(world):
    world.design_approved()
    world.script = [lambda tree: world.convert_well(tree, merged=True)]
    world.convert()

    assert world.stopped("rejected").kind == "refused"

    _unpushed(world)
    assert world.refs() == ""


def test_a_change_outside_the_two_sections_is_not_pushed_and_stops_after_two_launches(world):
    def outside(tree):
        _write(tree, {PLAN: OLD_PLAN.replace("読む本文。", "書き換え。")})
        world.convert_well(tree)

    world.script = [outside, outside, outside]

    stop = world.stopped()

    assert stop.kind == "invalid" and len(world.calls) == 2
    held = json.loads((world.work_root / units_convert.VIOLATIONS_FILE).read_text(encoding="utf-8"))
    assert held["violations"] and world.refs()
    _unpushed(world)


def test_a_violation_is_converted_again_once(world):
    def broken(tree):
        _commit(tree, {UNITS: "{}\n"})

    world.script = [broken]

    assert world.convert() is None

    assert len(world.calls) == 2
    _pushed(world)


def test_a_failed_worker_stops_with_its_cause(world):
    world.script = [lambda tree: StageResult(result.stopped("plan", "usage_limit", "上限"), {})]

    assert world.stopped().kind == "usage_limit"
    _unpushed(world)


def test_files_written_before_an_interruption_are_committed_on_the_next_run(world):
    def interrupted(tree):
        _write(tree, _converted_files(tree, _doc()))
        raise KeyboardInterrupt

    world.script = [interrupted]
    with pytest.raises(KeyboardInterrupt):
        world.convert()
    _unpushed(world)

    assert world.convert() is None

    assert len(world.calls) == 1
    _pushed(world)


def test_a_refused_push_url_never_launches_the_worker(world):
    _sh(world.work, "config", "remote.origin.pushurl", "git@github.com:o/other.git")

    assert world.stopped().kind == "refused"

    assert world.calls == []
    _unpushed(world)


def test_a_commit_touching_other_files_is_refused(world):
    world.script = [lambda tree: world.writes(tree, {"README.md": "x\n"})]

    assert world.stopped().kind == "refused"
    _unpushed(world)


def test_a_local_only_commit_is_not_sent(world):
    local = _commit(world.work, {"README.md": "手元\n"})

    assert world.stopped().kind == "refused"

    assert (world.remote(), world.local()) == (world.head, local)


def test_an_advanced_tip_gets_the_held_commit_on_top(world):
    world.design_approved()
    world.script = [lambda tree: world.convert_well(tree, merged=True)]
    got = world.convert()
    moved = world.push_remote({"README.md": "ほか\n"})
    world.reapproved(got)

    assert world.convert("approved") is None

    assert _sh(world.bare, "rev-parse", f"refs/heads/{SPRINT}^") == moved
    assert world.local() == world.remote() and len(world.calls) == 1


def test_a_conflicting_tip_is_converted_again_from_the_tip(world):
    world.design_approved()
    world.script = [lambda tree: world.convert_well(tree, merged=True)]
    got = world.convert()
    moved = world.push_remote({UNITS: "{}\n"})
    world.reapproved(got)
    world.script = [lambda tree: world.convert_well(tree, merged=True)]

    again = world.convert("approved")

    assert len(world.calls) == 2 and again is None, again
    assert _sh(world.bare, "rev-parse", f"refs/heads/{SPRINT}^") == moved


def test_a_rejected_push_keeps_the_local_branch_and_is_retried(world):
    hook = world.bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    assert world.stopped().kind == "precondition"
    _unpushed(world)

    hook.unlink()
    assert world.convert() is None
    assert len(world.calls) == 1
    _pushed(world)


def test_a_unit_file_rewritten_after_the_conversion_asks_again_once(world):
    world.convert()
    world.head = _commit(world.work, _converted_files(world.work, _doc(touch="src/c.py")))
    _sh(world.work, "push", "-q", "origin", f"HEAD:refs/heads/{SPRINT}")

    got = world.convert()

    assert got is not None and got.status == "gate" and len(world.calls) == 1
    world.reapproved(got)
    assert world.convert("approved") is None
    assert world.saved() == {"commit": world.head, "approval": {"kind": "reapproval", "head": world.head}}
    assert world.convert() is None


def test_a_published_file_without_a_conversion_record_asks_once(world):
    world.head = _commit(world.work, _converted_files(world.work, _doc()))
    _sh(world.work, "push", "-q", "origin", f"HEAD:refs/heads/{SPRINT}")

    got = world.convert()
    assert got is not None and got.status == "gate" and world.calls == []

    world.reapproved(got)
    assert world.convert("approved") is None
    assert world.saved()["commit"] == world.head and world.convert() is None


# --- 単位を流す処理とのつなぎ -----------------------------------------------------------------


def _stage_run(world: World, **sprint) -> StageRun:
    held = world.request().run
    plan = plan_runs.SprintPlan(work_root=world.work_root, **sprint)
    return StageRun(held.kind, held.name, held.inputs, (), "s", "i", per_unit=True, sprint=plan)


def _convert_of(world: World):
    return lambda request, ports, answer: units_convert.convert(world.job(), request, ports, answer)


def test_prepare_converts_an_older_sprint_and_reads_the_bodies_from_the_converted_commit(world, tmp_path, monkeypatch):
    monkeypatch.setattr(units_invoke, "_count_sprint_criteria", lambda g, remote, issues: CRITERIA)
    stage_run = _stage_run(world, convert=_convert_of(world))
    work = tmp_path / "w"

    # 起動の前の確かめ（ports が無い）は変換せず、並びも書かない
    assert units_invoke.prepare(stage_run, work, world.work) is not None
    assert world.calls == [] and not (work / issue_values.UNITS_FILE).exists()

    found = units_invoke.prepare(stage_run, work, world.work, world.ports())

    assert [u.name for u in found.units] == ["読み手", "書き手"]
    assert found.source == world.saved()["commit"]


def test_prepare_returns_the_reapproval_gate(world, tmp_path, monkeypatch):
    monkeypatch.setattr(units_invoke, "_count_sprint_criteria", lambda g, remote, issues: CRITERIA)
    world.design_approved()
    world.script = [lambda tree: world.convert_well(tree, merged=True)]

    got = units_invoke.run_stage(_stage_run(world, convert=_convert_of(world)), tmp_path / "w", world.work, world.ports())

    assert got is not None and got.status == "gate"


def test_a_new_definition_or_a_sprint_without_units_is_not_converted(world, tmp_path, monkeypatch):
    monkeypatch.setattr(units_invoke, "_count_sprint_criteria", lambda g, remote, issues: CRITERIA)

    def refused(*args):
        pytest.fail("変換しない")

    with pytest.raises(UnitsStopped, match="単位のファイル"):
        units_invoke.prepare(_stage_run(world, convert=refused, new_definition=True), tmp_path / "a", world.work, world.ports())

    _commit(world.work, {PLAN: "# 3: 実装計画\n"})
    assert units_invoke.prepare(_stage_run(world, convert=refused), tmp_path / "b", world.work, world.ports()) is None


# --- 旧い表との比較 ---------------------------------------------------------------------------


def _old(name: str, tasks: tuple[int, ...], *after: str) -> Unit:
    return Unit(name, 3, PLAN, tasks, (), False, tuple(unit_key(3, n) for n in after))


def _new(name: str, tasks: tuple[int, ...], after=(), touches=()) -> ud.View:
    return ud.View(UNITS, name, 1, 1, tuple((UNITS, n, "r") for n in after), (3,), tuple(f"T{t}" for t in tasks), (), 10, None, touches)


@pytest.mark.parametrize(
    ("old", "new", "words"),
    [
        pytest.param([_old("A", (1,)), _old("B", (2,))], [_new("A", (1,)), _new("B", (2,))], [], id="同じ"),
        pytest.param([_old("A", (1,))], [_new("C", (1,))], ["A", "C"], id="名前"),
        pytest.param([_old("A", (1, 2))], [_new("A", (1,))], ["A"], id="タスク"),
        pytest.param([_old("A", (1,)), _old("B", (2,), "A")], [_new("A", (1,)), _new("B", (2,))], ["B"], id="待ちが消える"),
        pytest.param(
            [_old("A", (1,)), _old("B", (2,))],
            [_new("A", (1,), touches=("x",)), _new("B", (2,), ["A"], ("x",))],
            [],
            id="重なるファイルの依存",
        ),
        pytest.param([_old("A", (1,)), _old("B", (2,))], [_new("A", (1,)), _new("B", (2,), ["A"])], ["B"], id="根拠の無い待ち"),
        pytest.param(
            [_old("A", (1,)), _old("B", (2,), "A"), _old("C", (3,))],
            [_new("A", (1,)), _new("B", (2,), ["A"], ("x",)), _new("C", (3,), ["B"], ("x",))],
            [],
            id="旧い順序と重なる辺でたどれる",
        ),
    ],
)
def test_compare_tables_reports_the_differences_from_the_old_table(old, new, words):
    got = units_convert.compare_tables(old, new)

    assert bool(got) is bool(words), got
    for word in words:
        assert any(word in line for line in got), got


def test_compare_tables_reports_only_the_unjustified_wait():
    old = [_old("A", (1,)), _old("B", (2,)), _old("C", (3,), "A")]
    new = [_new("A", (1,)), _new("B", (2,), ["A"], ("x",)), _new("C", (3,), ["B"], ("x",))]

    [line] = units_convert.compare_tables(old, new)

    assert "B" in line and "A" in line and "C" not in line


def test_the_old_waits_follow_the_order_column():
    from ndf.core.plan import review_units_decide as rd

    table = (
        "| 単位 | 含むタスク | 満たす受け入れ条件 | 実行順 |\n| --- | --- | --- | --- |\n"
        "| A | Task 1 | 1 | 1 |\n| B | Task 2 | 2 | 1 |\n| C | Task 3 | 無し | 2 |\n"
    )
    plan = f"## タスク分解\n\n### Task 1: a\n\n### Task 2: b\n\n### Task 3: c\n\n## レビューの単位\n\n{table}"

    units = rd.lineup_with_waits([rd.PlanDoc(PLAN, frozenset({3}), plan)], CRITERIA, [3])

    assert {u.name: u.after for u in units} == {"A": (), "B": (), "C": (unit_key(3, "A"), unit_key(3, "B"))}


# --- 再承認の回答 -----------------------------------------------------------------------------


def test_a_reapproval_is_answered_only_with_ndf_answer():
    got = result.result("plan", result.GATE, "再承認が要る", routes={"approved": None, "rejected": None}, presentation_path="/m/r.md")
    got[design_record.TARGET_KEY] = {"version": 1, "review": {"head": "a" * 40}}
    got[design_record.MATERIAL_KEY] = {"path": "/m/r.md", "sha256": "b" * 64}
    step = stages.StepDef(id="implement", stage="実装", call="ndf.plan", kind="work", range="none")
    impl = stages.StageDef(id="implement", name="実装", phase="implement", steps=(step,))
    waiting = run.StepProgress(waiting={"step": "implement", "report": got})
    move = run.Move(run.Action.GATE, impl, step, waiting)
    view = run_view.View("sprint:abc:s1", 1, stages.Snapshot("standard", "1", "0" * 64, 4, (impl,)), {}, [])

    asked = run.report(view, move)

    assert asked["presentation_path"] == "/m/r.md"
    assert asked["routes"]["approved"]["argv"][:3] == ["ndf", "answer", "sprint:abc:s1"]
    assert asked["routes"]["approved"]["ask"] == ["reason", "by", "key"]
    argv = asked["routes"]["approved"]["argv"]
    assert argv[argv.index("--head") + 1] == "a" * 40 and argv[argv.index("--material-sha256") + 1] == "b" * 64


@pytest.mark.parametrize(
    ("by", "head", "material", "accepted"),
    [
        pytest.param("human", "a" * 40, None, True, id="利用者"),
        pytest.param("conductor", "a" * 40, None, False, id="conductor"),
        pytest.param("service:bot", "a" * 40, None, False, id="サービス"),
        pytest.param("human", "c" * 40, None, False, id="別のコミット"),
        pytest.param("human", "a" * 40, "d" * 64, False, id="別の資料"),
    ],
)
def test_a_reapproval_binds_the_answer_to_a_person_and_the_presented_material(tmp_path, by, head, material, accepted):
    path = tmp_path / "reapproval.md"
    path.write_text("資料\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    got = result.result("plan", result.GATE, "再承認が要る", routes={"approved": None}, presentation_path=str(path))
    got[design_record.TARGET_KEY] = {"review": {"head": "a" * 40}}
    got[design_record.MATERIAL_KEY] = {"path": str(path), "sha256": digest}
    step = stages.StepDef(id="implement", stage="実装", call="ndf.plan", kind="work", range="none")
    impl = stages.StageDef(id="implement", name="実装", phase="implement", steps=(step,))
    waiting = model.EventDraft(
        run_view.STEP_EVENT,
        run_view.ACTOR,
        "実装",
        payload={"step": "implement", "status": "waiting", "report": got, "generation": 0},
        rev=1,
    )
    events = [model.Event(1, datetime(2026, 11, 28, tzinfo=timezone.utc), waiting)]
    view = run_view.View("sprint:abc:s1", 1, stages.Snapshot("standard", "1", "0" * 64, 4, (impl,)), {}, events)
    question = gate.Question("q", view.machine_id, "実装", "implement", None, ("approved",))
    answer = gate.Answer("q", "approved", "r", by, "k", seen=gate.Target(head, material or digest))

    if accepted:
        assert gate_design.reapproval(view, question, answer) == gate.Target("a" * 40, digest)
    else:
        with pytest.raises(model.Refused):
            gate_design.reapproval(view, question, answer)


def test_blank_lines_and_trailing_spaces_outside_the_sections_are_compared():
    from ndf.core.plan import review_units_decide as rd

    body = OLD_PLAN.replace("書く本文。", "```\na\n\nb\n```\n改行  ")
    titles = (units_files.PARALLEL_TITLE,)

    assert rd.without_sections(body, titles) != rd.without_sections(body.replace("a\n\nb", "a\nb"), titles)
    assert rd.without_sections(body, titles) != rd.without_sections(body.replace("改行  ", "改行"), titles)


# --- Git アダプターを差し替える確かめ -------------------------------------------------------------

OTHER = "git@github.com:o/other.git"
UNREADABLE = UnitsStopped("precondition", "送り先を読めない")


@pytest.mark.parametrize(
    ("stored", "fetch", "push", "accepted"),
    [
        pytest.param(URL, URL, URL, True, id="同じ"),
        pytest.param(URL, URL, OTHER, False, id="push の URL が別"),
        pytest.param(URL, OTHER, URL, False, id="取得の URL が別"),
        # 保存値は、スプリントの作成の時点の取得の URL からユーザー名を除いたもの（`command.repo_id`）
        pytest.param(
            "ssh://ssh.github.com:443/o/r.git",
            "ssh://git@ssh.github.com:443/o/r.git",
            URL,
            True,
            id="SSH の 443 番で取得して作ったスプリント",
        ),
    ],
)
def test_both_remote_urls_must_be_the_sprint_repository(stored, fetch, push, accepted):
    git = SimpleNamespace(
        remote=lambda: "origin",
        push_urls=lambda remote: [push],
        rewrites_again=lambda url: False,
        remote_url=lambda remote=None: fetch,
    )
    job = SimpleNamespace(machine_repo=lambda: stored)

    if accepted:
        assert units_convert.checked_url(job, git) == URL
    else:
        with pytest.raises(UnitsStopped) as raised:
            units_convert.checked_url(job, git)
        assert raised.value.kind == "refused"


@pytest.mark.parametrize("asked", [StageResult({"status": "gate"}, {}), None], ids=["再承認を待つ", "実装へ進む"])
def test_a_behind_branch_moves_only_when_no_reapproval_is_asked(tmp_path, monkeypatch, asked):
    moved: list[str] = []

    def merge_ff(commit):
        moved.append(commit)
        return SimpleNamespace(returncode=0, stderr="")

    git = SimpleNamespace(
        run=lambda *args: str(tmp_path),
        commit_or_none=lambda ref: None,
        rev_parse=lambda ref: "local",
        is_ancestor=lambda a, b: True,
        merge_ff=merge_ff,
    )
    monkeypatch.setattr(units_convert, "Git", lambda path: git)
    monkeypatch.setattr(units_convert, "_matches_conversion", lambda *args: False)
    monkeypatch.setattr(units_convert, "_check_units", lambda r, commit: units_convert._Checked(object(), []))
    monkeypatch.setattr(units_convert, "_published", lambda *args: asked)
    job = units_convert.Job(SimpleNamespace(machine_id="m"), {}, lambda: URL, tmp_path, tmp_path)
    request = plan_runs.ConvertRequest(None, tmp_path, SPRINT, (3,), (), lambda numbers: CRITERIA, (URL, "tip"))

    assert units_convert.convert(job, request, None, None) is asked
    assert moved == ([] if asked is not None else ["tip"])


@pytest.mark.parametrize(
    ("outcome", "by_name", "checked_to"),
    [
        pytest.param(UNREADABLE, ["origin"], None, id="送り先を読めなくてもリモート名の取得で続ける"),
        pytest.param((URL, "tip"), ["origin"], None, id="欄が無ければ送り先を読まない"),
    ],
)
def test_the_start_of_a_sprint_without_unit_headings(outcome, by_name, checked_to):
    fetched: list[str] = []

    def destination(g, branch):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def fetch_branch(remote, branch):
        fetched.append(remote)
        return SimpleNamespace(returncode=0, stderr="")

    git = SimpleNamespace(run=lambda *args: "", fetch_branch=fetch_branch, commits_of=lambda *refs: ("local", "local"))

    start = units_flow.read_start(git, "origin", SPRINT, [3], plan_runs.SprintPlan(destination=destination))

    assert (fetched, start.checked_to) == (by_name, checked_to)


def test_a_refused_fetch_url_stops_before_any_fetch():
    def refused(g, remote):
        raise UnitsStopped("refused", "取得の URL が違う")

    git = SimpleNamespace(run=lambda *args: "", fetch_branch=lambda *args: pytest.fail("取得しない"))
    sprint = plan_runs.SprintPlan(destination=lambda *args: pytest.fail("読まない"), verify_fetch=refused)

    with pytest.raises(UnitsStopped, match="取得の URL"):
        units_flow.read_start(git, "origin", SPRINT, [3], sprint)


def test_a_git_failure_of_the_route_stops_the_start_instead_of_meaning_no_units(tmp_path, monkeypatch):
    def run(*args):
        raise GitError("照会に失敗")

    monkeypatch.setattr(units_flow, "Git", lambda path: SimpleNamespace(run=run))
    monkeypatch.setattr(units_invoke, "Git", lambda path: pytest.fail("保存した失敗の後は git を打たない"))
    sprint = plan_runs.SprintPlan(convert=lambda *args: pytest.fail("変換しない"))

    routed = units_flow.by_units(StageRun("plan", "impl", {"issues": [3]}, (), "s", "i", sprint=sprint), tmp_path)

    with pytest.raises(GitError) as raised:
        units_invoke.prepare(routed, tmp_path / "w", tmp_path)
    assert raised.value is routed.peeked


@pytest.mark.parametrize("source", ["コミット", "作業場所"])
def test_a_plan_that_is_not_utf8_stops_with_its_path(tmp_path, source):
    broken = "計画".encode("cp932")
    (tmp_path / "issues").mkdir()
    (tmp_path / PLAN).write_bytes(broken)
    git = SimpleNamespace(run=lambda *args: PLAN + "\0", blobs=lambda *names: [broken])

    with pytest.raises(UnitsStopped, match=f"{PLAN} を UTF-8 で読めない") as raised:
        if source == "コミット":
            units_flow.plans_at(git, "HEAD", [3])
        else:
            units_flow.worktree_plans(tmp_path, [PLAN])

    assert raised.value.kind == "invalid"


def test_units_seen_at_the_fetch_url_but_not_at_the_destination_stop():
    empty = units_flow.Seen([], False)
    headed = units_flow.Seen([PlanDoc(PLAN, frozenset({3}), OLD_PLAN)], False)
    peeked = units_flow.Peeked("local", "tip", empty, headed)
    git = SimpleNamespace(rev_parse=lambda ref: "local")
    sprint = plan_runs.SprintPlan(destination=lambda g, branch: (URL, None))

    with pytest.raises(UnitsStopped, match="送り先"):
        units_flow.read_start(git, "origin", SPRINT, [3], sprint, peeked)


def test_a_release_branch_declared_at_the_tip_stops_before_the_conversion(tmp_path, monkeypatch):
    plan = units_flow.Seen([PlanDoc(PLAN, frozenset({3}), OLD_PLAN)], False)
    empty = units_flow.Seen([], False)
    git = SimpleNamespace(run=lambda *args: str(tmp_path), remote=lambda: "origin", is_ancestor=lambda a, b: a == "local")
    monkeypatch.setattr(units_invoke, "Git", lambda path: git)
    monkeypatch.setattr(units_flow, "branch_here", lambda g: SPRINT)
    monkeypatch.setattr(units_invoke, "_uncommitted", lambda g, issues: [])
    monkeypatch.setattr(units_flow, "read_start", lambda *args: units_flow.Start("local", "tip", empty, plan, None))
    monkeypatch.setattr(units_flow, "worktree_plans", lambda root, paths: [])
    monkeypatch.setattr(units_invoke, "ndf_settings_in", lambda root, name: {})
    monkeypatch.setattr(units_files, "project_of", lambda tree: {"released": tree.commit})
    monkeypatch.setattr(units_invoke.branch_strategy, "of_project", lambda project: SimpleNamespace(section=project))
    monkeypatch.setattr(units_invoke, "released_branches", lambda section: {SPRINT} if section == {"released": "tip"} else set())
    sprint = plan_runs.SprintPlan(convert=lambda *args: pytest.fail("変換しない"))
    stage_run = StageRun("plan", "impl", {"issues": [3], "branch": SPRINT}, (), "s", "i", sprint=sprint)

    with pytest.raises(UnitsStopped, match="リリース先"):
        units_invoke.prepare(stage_run, tmp_path / "w", tmp_path, units_invoke.Ports(None, None))
