"""工程「実装」を実装計画のレビューの単位ごとに流す処理（#4369）の、単位を順に流す部分（`run_stage`）のテスト。

git は手元の作業場所と裸のリモートで本物を打つ。プランの実行（`Ports.call_plan`）とコマンド（`ndf worktree create`・
`ndf ci merge`）は偽にする。偽の `impl` は単位の作業場所にコミットを足し、回の保存先へ実行の記録・PR の番号・レビューした
HEAD を書く。偽のマージは単位のブランチをリモートのスプリントブランチへ fast-forward で送り、マージ済みの PR として残す。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import sprint_fixture

from ndf.adapters import git, github, github_pulls
from ndf.contracts import result
from ndf.core import durable, route
from ndf.core.plan import files as plan_files
from ndf.core.plan import runner as plan_runner
from ndf.progress import definition, invoke, issue_values, plan_runs, units_invoke
from ndf.progress.plan_runs import StageResult, StageRun

BRANCH = "sprint/s1"
PROJECT = {"ci": {"required_checks": ["pytest"]}}
PLAN = "issues/issue-3-plan.md"
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
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    return done.stdout.strip()


class _Hub:
    def __init__(self, repo, api):
        self.repo = repo

    def nodes(self, numbers, node, fields, batch="github.graphql-batch"):
        return {n: {"body": BODIES[n]} for n in numbers}


class World:
    """スプリントの作業場所・リモートと、偽のプランとコマンドの記録。`fail` は単位の名前 → `impl` が返す止まった結果、
    `merge` はマージが返す結果の並び（先頭から使い、尽きれば ok）、`merged` はマージ済みと読む head → PR（偽のマージが足す）。"""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.sprint = sprint_fixture.sprint_repo(tmp_path, PROJECT)
        for key, value in (("user.name", "t"), ("user.email", "t@example.com")):
            _git(self.sprint, "config", key, value)
        _git(self.sprint, "commit", "-q", "--allow-empty", "-m", "start")
        self.remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        _git(self.sprint, "remote", "add", "origin", str(self.remote))
        (self.sprint / "issues").mkdir()
        (self.sprint / PLAN).write_text(PLAN_WITH_UNITS, encoding="utf-8")
        _git(self.sprint, "add", "issues")
        _git(self.sprint, "commit", "-q", "-m", "plan")
        self.work = tmp_path / "w"
        self.runs: list[tuple[StageRun, Path]] = []
        self.commands: list[list[str]] = []
        self.fail: dict[str, dict] = {}
        self.merge: list[dict] = []
        self.merged: dict[str, github_pulls.MergedPull] = {}
        self.lookups: list[list[str]] = []
        self.answer_sha = True

    def stage_run(self) -> StageRun:
        stage = definition.load_definition("standard").stage("実装")
        context = {"issues": [3, 4], "worktree": str(self.sprint), "mode": "standard", "branch": BRANCH}
        return invoke.stage_run(stage, stage.steps[0], invoke.resolve_inputs(stage, context))

    def ports(self) -> units_invoke.Ports:
        return units_invoke.Ports(self.call_plan, self.command)

    def call_plan(self, run: StageRun, round_dir: Path) -> StageResult:
        self.runs.append((run, round_dir))
        if run.name == "pr":
            return StageResult(result.result("plan", result.OK, "PR を開いた"), {})
        name = run.overrides["branch"]
        worktree = Path(run.inputs["worktree"])
        (worktree / f"{len(self.runs)}.txt").write_text(name, encoding="utf-8")
        _git(worktree, "add", ".")
        _git(worktree, "commit", "-q", "-m", f"実装 {name}")
        unit = next((u for u, b in self.unit_names().items() if b == name), "")
        done = self.fail[unit] if unit in self.fail else result.result("plan", result.OK, "通った")
        plan_files.write_json(round_dir / plan_runner.RUN_FILE, {"key": f"k{len(self.runs)}", "name": f"impl-{name}"})
        plan_files.write_json(plan_runner.result_path(round_dir, durable.run_name(f"k{len(self.runs)}")), done)
        (round_dir / "pr-number").write_text(f"{100 + len(self.runs)}\n", encoding="utf-8")
        if done["status"] == result.OK:
            (round_dir / "reviewed-head").write_text(_git(worktree, "rev-parse", "HEAD") + "\n", encoding="utf-8")
        return StageResult(done, {})

    def command(self, argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        words = argv[argv.index("worktree") :] if "worktree" in argv else argv[argv.index("ci") :]
        self.commands.append(words)
        if words[:2] == ["worktree", "create"]:
            return self._create(words[2], cwd)
        flag = _flags(words)
        out = self.merge.pop(0) if self.merge else None
        if out is None:
            landed = self.land(cwd, flag["--branch"], int(flag["--pr"]))
            row = {"kind": "pr", "number": landed.number, **({"merge_sha": landed.merge_sha} if self.answer_sha else {})}
            out = result.result("ci", result.OK, "マージした", items=[row], metrics={"merge": "merged"})
        return subprocess.CompletedProcess(argv, 0, json.dumps(out) + "\n", "")

    def land(self, worktree: Path, branch: str, number: int) -> github_pulls.MergedPull:
        """単位の作業場所の HEAD をリモートのスプリントブランチへ送り、マージ済みの PR として残す（GitHub のマージの代わり）。"""
        _git(worktree, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
        self.merged[branch] = github_pulls.MergedPull(number, _git(worktree, "rev-parse", "HEAD"))
        return self.merged[branch]

    def lookup(self, hub, heads, base) -> dict[str, github_pulls.MergedPull]:
        """GitHub のマージ済みの PR の照会の代わり。照会した head の並びを `lookups` に残す。"""
        self.lookups.append(list(heads))
        return {h: m for h, m in self.merged.items() if h in set(heads)}

    def _create(self, branch: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        path = self.tmp / "wt" / branch
        if _git(cwd, "branch", "--list", branch):
            _git(cwd, "worktree", "add", "-q", str(path), branch)
        elif _git(cwd, "branch", "-r", "--list", f"origin/{branch}"):
            _git(cwd, "worktree", "add", "-q", "-b", branch, str(path), f"origin/{branch}")
        else:
            _git(cwd, "worktree", "add", "-q", "-b", branch, str(path), f"origin/{BRANCH}")
        out = result.result("worktree", result.OK, "作った", outputs={"worktree": str(path), "branch": branch})
        return subprocess.CompletedProcess([], 0, json.dumps(out) + "\n", "")

    def unit_names(self) -> dict[str, str]:
        units = [("読み手", 3), ("書き手", 3), ("issue-4", 4)]
        return {n: units_invoke.unit_branch(BRANCH, _unit(n, i)) for n, i in units}

    def impl_runs(self) -> list[tuple[StageRun, Path]]:
        return [(r, d) for r, d in self.runs if r.name == "impl"]

    def run(self) -> StageResult | None:
        return units_invoke.run_stage(self.stage_run(), self.work, self.sprint, self.ports())


def _flags(words: list[str]) -> dict[str, str]:
    """`ndf ci merge` の引数の旗 → 値（`--旗=値` の形も分ける）。"""
    pairs = [w.split("=", 1) for w in words[2:] if "=" in w and w.startswith("--")]
    rest = [w for w in words[2:] if not ("=" in w and w.startswith("--"))]
    return {**dict(zip(rest[::2], rest[1::2])), **dict(pairs)}


def _unit(name: str, issue: int):
    from ndf.core.plan.review_units_decide import Unit

    return Unit(name, issue, PLAN, (), (), False)


@pytest.fixture
def world(tmp_path, monkeypatch, request) -> World:
    """`World`。間接の引数（`indirect=True`）で `tmp_path` の下の場所の名前を渡せば、そこに作る（空白を含む場所を試す）。"""
    w = World(tmp_path / request.param if hasattr(request, "param") else tmp_path)
    monkeypatch.setattr(git.Git, "remote_repo", lambda self, remote=None: "o/r")
    monkeypatch.setattr(github, "GitHub", _Hub)
    monkeypatch.setattr(github, "client", lambda *a, **k: None)
    monkeypatch.setattr(github_pulls, "merged_pulls", w.lookup)
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n, repo=None: (f"#{n} の題", f"#{n} の本文"))
    return w


def _stopped_at(step: str, kind: str = "check_failed") -> dict:
    return result.stopped("plan", kind, f"{step} で止まった", items=[{"kind": "plan", "name": "impl-x", "step": step}])


# --- 順に流してマージする（受け入れ条件 1・3・4） -------------------------------------------------------


def test_units_run_in_order_on_their_own_branches_and_each_is_cut_after_the_previous_merge(world):
    got = world.run()

    assert got is not None and got.status == "ok", got.report
    names = world.unit_names()
    impl = world.impl_runs()
    assert [r.overrides["branch"] for r, _ in impl] == [names["読み手"], names["書き手"], names["issue-4"]]
    first, round_dir = impl[0]
    assert first.overrides["base"] == BRANCH and first.overrides["remote"] == "origin"
    assert first.overrides["acceptance"] == ["--issue", "3", "--criteria", "3:1"]
    assert first.overrides["required"] == ["pytest"]
    assert first.overrides["title"] == "#3 の題" and first.per_unit is False and "branch" not in first.inputs
    steps = [s["id"] for s in first.template.plan["steps"]]
    assert "done-check" not in steps
    assert next(s for s in first.template.plan["steps"] if s["id"] == "ci")["run"][-2:] == ["--repo", "o/r"]
    assert first.overrides["review"][:5] == ["sh", "-c", route.UNIT_REVIEW, "o/r", "{state_dir}"]
    assert first.env_extra == {"GH_REPO": "o/r", "NDF_UNIT_FILE": first.overrides["unit_file"]}
    assert "Task 1" in Path(first.overrides["unit_file"]).read_text(encoding="utf-8") and round_dir.name == "1"
    # 補った単位は単位の指示を持たず、課題の全条件を確かめる
    last = impl[2][0]
    assert last.overrides["unit_file"] == "" and "NDF_UNIT_FILE" not in last.env_extra
    assert last.overrides["acceptance"] == ["--issue", "4"]
    # 次の単位の作業場所は、前の単位をマージした後のスプリントブランチから切られる
    second = Path(impl[1][0].inputs["worktree"])
    assert _git(second, "merge-base", "--is-ancestor", names["読み手"], "HEAD") == ""
    merges = [c for c in world.commands if c[:2] == ["ci", "merge"]]
    assert [m[m.index("--branch") + 1] for m in merges] == list(names.values())
    flags = _flags(merges[0])
    assert flags == {"--repo": "o/r", "--pr": "101", "--branch": names["読み手"], "--base": BRANCH,
                     "--reviewed": (round_dir / "reviewed-head").read_text().strip()}  # fmt: skip
    assert [i["status"] for i in got.report["items"]] == ["merged"] * 3 and got.report["metrics"]["unit_ci"]["base"] == BRANCH
    # スプリントの PR は最後に 1 回、push のステップなしで開く
    pr, pr_dir = world.runs[-1]
    assert pr.name == "pr" and pr_dir == world.work / units_invoke.SPRINT_PR_DIR
    assert {"push", "fix-push"}.isdisjoint(s["id"] for s in pr.template.plan["steps"]) and pr.env_extra == {"GH_REPO": "o/r"}
    assert _git(world.sprint, "rev-parse", "HEAD") == _git(world.sprint, "rev-parse", f"origin/{BRANCH}")


def test_the_ci_steps_are_dropped_when_the_unit_waits_for_no_checks(world):
    (world.sprint / ".ndf" / "project.json").write_text(
        json.dumps({**json.loads((world.sprint / ".ndf" / "project.json").read_text()), "ci": {"required_checks": []}}), encoding="utf-8"
    )

    assert world.run().status == "ok"

    first = world.impl_runs()[0][0]
    assert "required" not in first.overrides
    assert set(units_invoke.CI_STEPS).isdisjoint(s["id"] for s in first.template.plan["steps"])


def test_the_implement_stage_flows_by_unit_and_records_gathered_after_it():
    stage = definition.load_definition("standard").stage("実装")

    assert stage.per_unit and "branch" in stage.optional_inputs
    assert [s.id for s in stage.steps] == ["implement", "gathered"]


# --- 止まる（受け入れ条件 4・5） ---------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["incomplete", "test_failed"])
def test_a_failing_unit_stops_the_stage_before_the_later_units_and_names_the_unit(world, kind):
    world.fail["書き手"] = _stopped_at("resolved", kind)

    got = world.run()

    assert got.status == "stopped" and got.cause["kind"] == kind
    assert "単位 書き手（課題 #3、PR #102）" in got.cause["detail"] and "resolved で止まった" in got.cause["detail"]
    assert [r.overrides["branch"] for r, _ in world.impl_runs()] == list(world.unit_names().values())[:2]
    assert len([c for c in world.commands if c[:2] == ["ci", "merge"]]) == 1
    assert all(r.name != "pr" for r, _ in world.runs)
    assert [(i["name"], i["status"]) for i in got.report["items"]] == [("読み手", "merged"), ("書き手", "stopped")]
    stopped_head = world.impl_runs()[1][1] / units_invoke.STOPPED_HEAD_FILE
    assert stopped_head.read_text().strip() == _git(Path(world.impl_runs()[1][0].inputs["worktree"]), "rev-parse", "HEAD")


def test_a_unit_that_cannot_be_merged_stops_and_keeps_the_kind_of_the_merge(world):
    world.merge = [result.stopped("ci", "ci_pending", "マージキューで待つ", retry_after=30)]

    got = world.run()

    assert got.status == "stopped" and got.cause["kind"] == "ci_pending" and got.cause["retry_after"] == 30
    assert "単位 読み手" in got.cause["detail"] and "PR #101 をマージできない" in got.cause["detail"]
    assert json.loads((world.impl_runs()[0][1] / units_invoke.MERGE_FILE).read_text()) == {"kind": "ci_pending"}
    assert len(world.impl_runs()) == 1


def test_a_unit_without_the_reviewed_head_is_not_merged(world):
    original = world.call_plan

    def without_reviewed(run: StageRun, round_dir: Path) -> StageResult:
        got = original(run, round_dir)
        (round_dir / "reviewed-head").unlink(missing_ok=True)
        return got

    world.call_plan = without_reviewed

    got = world.run()

    assert got.status == "stopped" and got.cause["kind"] == "precondition" and "レビューした HEAD" in got.cause["detail"]
    assert [c for c in world.commands if c[:2] == ["ci", "merge"]] == []


def _push_from_elsewhere(world: World, other: Path, message: str) -> None:
    """別の作業場所（`other` に clone）からスプリントブランチへ空のコミットを送る。"""
    if not other.exists():
        subprocess.run(["git", "clone", "-q", "-b", BRANCH, str(world.remote), str(other)], check=True)
    _git(other, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", message)
    _git(other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")


def _after_the_last_merge(world: World, monkeypatch, then) -> None:
    """最後の単位のマージの直後に `then()` を流す。"""
    original = units_invoke.merge_unit

    def merge_then(ctx, branch, worktree, round_dir, ports):
        done = original(ctx, branch, worktree, round_dir, ports)
        if branch == world.unit_names()["issue-4"]:
            then()
        return done

    monkeypatch.setattr(units_invoke, "merge_unit", merge_then)


@pytest.mark.parametrize("remote_moves", [False, True])
def test_local_only_commits_stop_without_a_command_to_send_them(world, monkeypatch, tmp_path, remote_moves):
    def local_commit() -> None:
        if remote_moves:
            _push_from_elsewhere(world, tmp_path / "other", "リモートで")
        _git(world.sprint, "merge", "-q", "--ff-only", world.merged[world.unit_names()["issue-4"]].merge_sha)
        _git(world.sprint, "commit", "-q", "--allow-empty", "-m", "レビューを経ていない")

    _after_the_last_merge(world, monkeypatch, local_commit)

    got = world.run()

    tip, head = _git(world.remote, "rev-parse", BRANCH), _git(world.sprint, "rev-parse", "HEAD")
    assert got.status == "stopped" and got.cause["kind"] == "precondition"
    assert f"{tip}..{head}" in got.cause["detail"] and "ndf progress next" in got.cause["detail"]
    # 手元にだけあるコミットは送らせない。未コミットの変更の保全も案内する
    assert "コマンド: " not in got.cause["detail"] and "git stash" in got.cause["detail"]
    assert all(r.name != "pr" for r, _ in world.runs) and [i["status"] for i in got.report["items"]] == ["merged"] * 3


@pytest.mark.parametrize("world", ["空白 を含む"], indirect=True)
def test_after_a_push_from_another_worktree_the_shown_command_lets_the_rerun_open_the_sprint_pr(world, tmp_path, monkeypatch):
    # 空白を含む場所の作業場所でも、停止の理由に出たコマンドをシェルへそのまま貼れば進められる
    _after_the_last_merge(world, monkeypatch, lambda: _push_from_elsewhere(world, tmp_path / "other", "別の作業場所から"))
    got = world.run()

    tip = _git(world.remote, "rev-parse", BRANCH)
    assert got.status == "stopped" and got.cause["kind"] == "precondition"
    command = got.cause["detail"].split("コマンド: ", 1)[1]
    assert command.endswith(f"merge --ff-only {tip}") and all(r.name != "pr" for r, _ in world.runs)
    # 利用者が変更を確かめ、停止の理由が示したコマンドで作業場所を進めてから打ち直す
    subprocess.run(["sh", "-c", command], check=True, capture_output=True)
    world.runs.clear()

    again = world.run()

    assert again.status == "ok", again.report
    assert world.impl_runs() == [] and world.runs[-1][0].name == "pr"


def test_a_sprint_worktree_on_another_branch_is_only_told_to_switch(world, monkeypatch, tmp_path):
    def move_away() -> None:
        _push_from_elsewhere(world, tmp_path / "other", "別の作業場所から")
        _git(world.sprint, "switch", "-q", "-c", "other")

    _after_the_last_merge(world, monkeypatch, move_away)

    got = world.run()

    assert got.status == "stopped" and got.cause["kind"] == "precondition" and "other" in got.cause["detail"]
    # 差分の範囲は示さず、切り替えだけを示す（打ち直しでスプリントブランチの HEAD から取り直す）
    assert got.cause["detail"].split("コマンド: ", 1)[1].endswith(f"switch {BRANCH}") and ".." not in got.cause["detail"]


def test_a_github_failure_on_the_lookup_of_the_last_unit_keeps_the_kind_the_wait_and_every_unit(world, monkeypatch):
    world.answer_sha = False
    first = world.lookup

    def fails_the_second(hub, heads, base):
        if world.lookups:
            raise github.GitHubError("上限に当たった", status=403, retry_after=60, quota="graphql")
        return first(hub, heads, base)

    monkeypatch.setattr(github_pulls, "merged_pulls", fails_the_second)

    got = world.run()

    assert got.status == "stopped" and got.cause["kind"] == "transient" and got.cause["retry_after"] == 60
    assert [i["status"] for i in got.report["items"]] == ["merged"] * 3 and got.report["metrics"]["unit_ci"]["base"] == BRANCH


def test_a_failed_lookup_of_the_last_unit_keeps_every_unit_and_the_ci_settings_in_the_result(world, monkeypatch):
    world.answer_sha = False
    first = world.lookup
    monkeypatch.setattr(github_pulls, "merged_pulls", lambda hub, heads, base: first(hub, heads, base) if not world.lookups else {})

    got = world.run()

    assert got.status == "stopped" and got.cause["kind"] == "precondition" and "GitHub から読めない" in got.cause["detail"]
    assert [i["status"] for i in got.report["items"]] == ["merged"] * 3 and got.report["metrics"]["unit_ci"]["base"] == BRANCH
    assert all(r.name != "pr" for r, _ in world.runs)


# --- 打ち直し（受け入れ条件 6） ---------------------------------------------------------------------


def test_a_rerun_skips_merged_units_and_reviews_the_stopped_unit_again_from_push(world):
    world.fail["書き手"] = _stopped_at("review")
    assert world.run().status == "stopped"
    names = world.unit_names()
    world.fail.clear()
    world.runs.clear()

    got = world.run()

    assert got.status == "ok", got.report
    assert [(i["name"], i["status"], i["pr"]) for i in got.report["items"]][0] == ("読み手", "already_merged", 101)
    again, round_dir = world.impl_runs()[0]
    assert again.overrides["branch"] == names["書き手"] and round_dir.name == "2" and again.start == "push"


def test_a_rerun_after_a_merge_wait_only_merges_again(world):
    world.merge = [result.stopped("ci", "ci_pending", "マージキューで待つ")]
    assert world.run().status == "stopped"
    world.runs.clear()

    got = world.run()

    assert got.status == "ok", got.report
    assert [r.overrides["branch"] for r, _ in world.impl_runs()] == list(world.unit_names().values())[1:]
    assert json.loads((world.work / "units" / world.unit_names()["読み手"] / "1" / units_invoke.MERGE_FILE).read_text())["kind"] == "merged"


def test_a_rerun_after_the_queue_merged_the_last_unit_moves_the_sprint_worktree_and_opens_the_sprint_pr(world):
    names = world.unit_names()
    original = world.command
    pending = {"count": 0}

    def queue_waits_on_the_last(argv, cwd, env):
        words = argv[argv.index("ci") :] if "ci" in argv and "worktree" not in argv else []
        if words[:2] == ["ci", "merge"] and _flags(words)["--branch"] == names["issue-4"]:
            pending["count"] += 1
            pending["cwd"] = cwd
            out = result.stopped("ci", "ci_pending", "マージキューで待つ")
            return subprocess.CompletedProcess(argv, 0, json.dumps(out) + "\n", "")
        return original(argv, cwd, env)

    world.command = queue_waits_on_the_last
    assert world.run().status == "stopped"
    # 止まった後に、マージキューが最後の単位の PR をマージした
    world.land(pending["cwd"], names["issue-4"], 103)
    world.runs.clear()

    got = world.run()

    assert got.status == "ok", got.report
    assert [i["status"] for i in got.report["items"]] == ["already_merged"] * 3 and world.impl_runs() == []
    assert world.runs[-1][0].name == "pr" and pending["count"] == 1
    assert _git(world.sprint, "rev-parse", "HEAD") == _git(world.sprint, "rev-parse", f"origin/{BRANCH}")


def test_a_rerun_from_another_branch_stops_before_any_unit(world):
    world.fail["書き手"] = _stopped_at("review")
    assert world.run().status == "stopped"
    _git(world.sprint, "switch", "-q", "-c", "other")
    world.runs.clear()

    # 文脈を組む前の停止は例外で上がり、呼び手（`invoke.plan_failure`）が `stopped` の結果に変える
    with pytest.raises(plan_runs.UnitsStopped) as raised:
        world.run()

    assert raised.value.kind == "precondition" and "other" in raised.value.detail
    assert world.runs == []


# --- 単位の作業場所 ---------------------------------------------------------------------------------


def _ctx(world: World) -> units_invoke.Context:
    found = units_invoke.prepare(world.stage_run(), world.work, world.sprint)
    assert found is not None
    return units_invoke._unit_context(world.stage_run(), world.work, world.sprint, found)


def test_a_worktree_with_uncommitted_changes_is_not_touched(world):
    ctx, branch = _ctx(world), world.unit_names()["読み手"]
    path = units_invoke.unit_worktree(ctx, branch, world.ports())
    (path / "利用者.txt").write_text("書きかけ", encoding="utf-8")

    with pytest.raises(plan_runs.UnitsStopped) as raised:
        units_invoke.unit_worktree(ctx, branch, world.ports())

    assert raised.value.kind == "precondition" and "利用者.txt" in raised.value.detail


def test_a_unit_branch_behind_the_remote_is_moved_forward_and_a_diverged_one_stops(world, tmp_path):
    ctx, branch = _ctx(world), world.unit_names()["読み手"]
    path = units_invoke.unit_worktree(ctx, branch, world.ports())
    _git(path, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", "-b", branch, str(world.remote), str(other)], check=True)
    _git(other, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "別のマシン")
    _git(other, "push", "-q", "origin", "HEAD")

    assert units_invoke.unit_worktree(ctx, branch, world.ports()) == path
    assert _git(path, "rev-parse", "HEAD") == _git(other, "rev-parse", "HEAD")

    _git(path, "commit", "-q", "--allow-empty", "-m", "手元")
    _git(other, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "向こう")
    _git(other, "push", "-q", "origin", "HEAD")
    with pytest.raises(plan_runs.UnitsStopped) as raised:
        units_invoke.unit_worktree(ctx, branch, world.ports())
    assert raised.value.kind == "precondition" and "分かれている" in raised.value.detail


def test_including_the_sprint_branch_aborts_its_own_merge_on_a_conflict(world):
    ctx, branch = _ctx(world), world.unit_names()["読み手"]
    path = units_invoke.unit_worktree(ctx, branch, world.ports())
    (path / "a.txt").write_text("単位\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "単位")
    (world.sprint / "a.txt").write_text("スプリント\n", encoding="utf-8")
    _git(world.sprint, "add", "a.txt")
    _git(world.sprint, "commit", "-q", "-m", "スプリント")
    _git(world.sprint, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    _git(world.sprint, "fetch", "-q", "origin")

    with pytest.raises(plan_runs.UnitsStopped) as raised:
        units_invoke.include_sprint(ctx, path)

    assert raised.value.kind == "base_conflict" and BRANCH in raised.value.detail
    assert _git(path, "status", "--porcelain") == ""


# --- 工程から作業を呼ぶ層（受け入れ条件 1・7） ------------------------------------------------------------


def test_invoke_hands_a_stage_with_units_to_the_unit_run_and_prepare_writes_no_values(world):
    run = world.stage_run()

    assert invoke.prepare_plan(run, world.work, world.sprint) is None
    assert not (world.work / invoke.VALUES_FILE).exists()
    calls: list[list[str]] = []

    def runner(argv, cwd, env):
        calls.append(argv)
        return world.command(argv, cwd, env)

    world_plans: list[str] = []
    original = units_invoke.run_stage

    def run_stage(r, wd, cwd, ports):
        fake = units_invoke.Ports(lambda rr, d: world_plans.append(rr.name) or world.call_plan(rr, d), ports.command)
        return original(r, wd, cwd, fake)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(units_invoke, "run_stage", run_stage)
        got = invoke.invoke(run, world.sprint, world.work, runner=runner)

    assert got.status == "ok", got.report
    assert world_plans == ["impl", "impl", "impl", "pr"]
    assert [c[c.index("worktree") + 1] for c in calls if "worktree" in c] == ["create"] * 3


def test_invoke_stops_before_any_plan_when_the_units_cannot_be_read(world):
    (world.sprint / PLAN).write_text(PLAN_WITH_UNITS.replace("| 書き手 | Task 2 | 2 |\n", ""), encoding="utf-8")
    _git(world.sprint, "commit", "-q", "-am", "壊す")
    run = replace(world.stage_run())

    got = invoke.invoke(run, world.sprint, world.work, runner=lambda *a: pytest.fail("プランもコマンドも打たない"))

    assert got.status == "stopped" and got.cause["kind"] == "invalid" and "Task 2" in got.cause["detail"]
