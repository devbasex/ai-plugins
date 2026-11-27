"""`ndf refactor run --local-test`（手元の経路。#4566 の受け入れ条件 4・6）を、担当とテストの起動の代わりで提案から最後まで通す。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ndf.contracts import causes
from ndf.domains.crossrefactor import cli, drive, local_gate, store, workspace

from ._drive_world import BRANCH, FakeAgents, FakeHub, World, base_acts, commit_in, make_world, ports, sh

PROJECT = {"test": {"suites": [{"name": "unit", "command": "sh check.sh"}]}}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    w = make_world(tmp_path, monkeypatch)
    commit_in(w.main, {".ndf/project.json": json.dumps(PROJECT), "check.sh": "true\n"}, "テストの設定")
    sh(w.main, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    return w


class SuiteRuns:
    """テストの suite の起動の代わり。打ったコマンドを数え、`code` を返す。"""

    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.seen: list[str] = []

    def run(self, command: str, cwd: Path, timeout: float, *, suites: Any = None) -> subprocess.CompletedProcess[str]:
        assert suites, "判定に使った設定を渡す"
        self.seen.append(command)
        return subprocess.CompletedProcess(command, self.code, "", "")

    def ex(self) -> local_gate.Exec:
        return local_gate.Exec(self.run, lambda: 0.0, lambda began: 0.0)


def _local(world: World, tmp_path: Path, **over: Any) -> drive.RunConfig:
    kw: dict[str, Any] = {"required": (), "tests": (), "local_test": True, "pushed_out": tmp_path / "pushed-sha"}
    kw.update(over)
    return world.config(**kw)


def _ports(fake: FakeAgents, tests: SuiteRuns) -> drive.Ports:
    p = ports(fake, FakeHub("clean"))
    p.tests = tests.ex()
    return p


def _remote(world: World) -> str:
    return sh(world.origin, "rev-parse", f"refs/heads/{BRANCH}")


def test_a_passing_local_run_pushes_the_tested_sha_and_writes_it_out(world, tmp_path) -> None:
    tests = SuiteRuns()
    got = drive.drive(_local(world, tmp_path), _ports(FakeAgents(base_acts()), tests))
    assert got.kind == "ok", got.detail
    final = next(i for i in got.items if i["kind"] == "final")
    assert (final["means"], final["tests"]) == ("local_test", "passed")
    assert "手元のテスト全体が通った" in got.detail
    assert final["head"] == _remote(world)
    assert (tmp_path / "pushed-sha").read_text(encoding="utf-8") == final["head"] + "\n"
    assert tests.seen == ["sh check.sh"]
    # 再利用しても同じ SHA を書く
    (tmp_path / "pushed-sha").unlink()
    again = drive.drive(_local(world, tmp_path), _ports(FakeAgents({}), SuiteRuns()))
    assert again.kind == "ok"
    assert (tmp_path / "pushed-sha").read_text(encoding="utf-8") == final["head"] + "\n"


def test_a_failing_local_run_is_not_ok_and_does_not_push(world, tmp_path) -> None:
    before = _remote(world)
    got = drive.drive(_local(world, tmp_path, max_fix=0), _ports(FakeAgents(base_acts()), SuiteRuns(1)))
    assert got.kind == "test_failed"
    assert _remote(world) == before
    assert not (tmp_path / "pushed-sha").exists()


def test_a_scope_of_only_protected_files_finishes_without_agents_or_tests(world, tmp_path) -> None:
    tests = SuiteRuns()
    got = drive.drive(_local(world, tmp_path, scope=("check.sh", ".ndf")), _ports(FakeAgents({}), tests))
    assert got.kind == "ok", got.detail
    assert "変更が無いのでテストは打っていない" in got.detail
    assert tests.seen == []
    assert (tmp_path / "pushed-sha").read_text(encoding="utf-8") == _remote(world) + "\n"


def test_a_changed_path_restarts_reverify_without_agents(world, tmp_path, monkeypatch) -> None:
    drive.drive(_local(world, tmp_path), _ports(FakeAgents(base_acts()), SuiteRuns()))
    monkeypatch.setenv("PATH", os.environ["PATH"] + ":/nowhere")
    tests = SuiteRuns()
    got = drive.drive(_local(world, tmp_path), _ports(FakeAgents({}), tests))
    assert got.kind == "ok", got.detail
    assert tests.seen == ["sh check.sh"]


def test_a_held_lock_stops_without_changes(world, tmp_path) -> None:
    root = world.root()
    held = workspace.hold_pr(root.parent, 7)
    try:
        got = drive.drive(_local(world, tmp_path), _ports(FakeAgents({}), SuiteRuns()))
    finally:
        held.release()
    assert got.kind == "precondition"
    assert not store.path_of(root).exists()


def test_pushed_out_refuses_a_symlink_and_another_file(world, tmp_path) -> None:
    target = tmp_path / "mine.txt"
    target.write_text("keep\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)
    got = drive.drive(_local(world, tmp_path, scope=(".ndf",), pushed_out=link), _ports(FakeAgents({}), SuiteRuns()))
    assert got.kind == "precondition"
    assert target.read_text(encoding="utf-8") == "keep\n"
    got = drive.drive(_local(world, tmp_path, scope=(".ndf",), pushed_out=target), _ports(FakeAgents({}), SuiteRuns()))
    assert got.kind == "precondition"
    assert target.read_text(encoding="utf-8") == "keep\n"


def test_args_of_the_local_route_replace_the_required_checks(world, tmp_path) -> None:
    cfg = _local(world, tmp_path)
    assert cfg.args()[:4] == ["origin/main", "src", "--local-test", "--symbols"]
    assert world.config().args()[:6] == ["origin/main", "src", "--required", "pytest", "--ci-test", "pytest"]


def _cli(capsys, *argv: str) -> tuple[int, dict]:
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.mark.parametrize(
    ("project", "extra"),
    [
        ({"ci": {"required_checks": ["test"]}, "test": {"suites": PROJECT["test"]["suites"]}}, ["--local-test"]),
        ({"test": {"ci": {"only": True}, "suites": PROJECT["test"]["suites"]}}, ["--local-test"]),
        (PROJECT, ["--local-test", "--ci-test", "unit"]),
        (PROJECT, ["--local-test", "--required", "x"]),
        (PROJECT, []),
    ],
)
def test_run_refuses_gate_arguments_that_do_not_match_the_means(tmp_path, capsys, monkeypatch, project, extra) -> None:
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps(project), encoding="utf-8")
    monkeypatch.setattr(drive, "drive", lambda *a, **k: pytest.fail("担当を起動した"))
    argv = ["run", "--pr", "7", "--branch", "b", "--base", "main", "--scope", "src", "--work", str(tmp_path), *extra]
    code, got = _cli(capsys, *argv)
    assert got["cause"]["kind"] == "invalid" and code == causes.exit_code("invalid")


def test_release_leaves_only_the_log_records_and_removes_an_empty_state(world, tmp_path, capsys) -> None:
    drive.drive(_local(world, tmp_path), _ports(FakeAgents(base_acts()), SuiteRuns()))
    path = store.path_of(world.root())
    code, got = _cli(capsys, "release", "--pr", "7", "--work", str(world.main))
    assert code == 0, got
    assert set(store.load(path)) == {"gate_logs"}
    held = workspace.hold_pr(world.root().parent, 7)
    assert held is not None
    held.release()


def test_a_resume_after_widening_the_protection_refuses_with_the_recovery_steps(world, tmp_path) -> None:
    first = drive.drive(_local(world, tmp_path, max_fix=0), _ports(FakeAgents(base_acts()), SuiteRuns(1)))
    assert first.kind == "test_failed"
    widened = {"test": {**PROJECT["test"], "protected_files": ["src/geo.py"]}}
    commit_in(world.main, {".ndf/project.json": json.dumps(widened)}, "保護を広げる")
    sh(world.main, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    fake = FakeAgents({})
    got = drive.drive(_local(world, tmp_path, max_fix=0), _ports(fake, SuiteRuns()))
    assert got.kind == "refused"
    assert "src/geo.py" in got.detail and "ndf refactor release --pr 7" in got.detail
    assert fake.seen == []


def test_the_ci_route_records_the_names_of_the_passed_checks_and_not_a_test_pass(world) -> None:
    from ._drive_world import Watched, fake_ci

    got = drive.drive(world.config(), ports(FakeAgents(base_acts()), FakeHub("clean"), ci=fake_ci(Watched(None))))
    assert got.kind == "ok", got.detail
    final = next(i for i in got.items if i["kind"] == "final")
    assert (final["means"], final["checks"]) == ("required_checks", ["pytest"])
    assert "必須のチェック pytest が通った" in got.detail
