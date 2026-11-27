"""手元の最終ゲート（`local_gate`。#4566 の決定 4・5・6・9・10・11）。

git は一時の bare リモートと clone を使い、テストの suite の起動と時計は差し替える（子のプロセスでテストを打たない）。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import git_sandbox
import pytest

from ndf.adapters import git
from ndf.domains.crossrefactor import final_gate, local_gate, workspace
from ndf.testing import means

BRANCH = "feat/x"
SUITES = [{"name": "unit", "command": "sh check.sh"}, {"name": "lint-ish", "command": "sh other.sh"}]
PROJECT = {"test": {"suites": SUITES}}


def sh(cwd: Path, *args: str) -> str:
    return git_sandbox.sh(cwd, *args, strip=True)


def commit(cwd: Path, files: dict[str, str], message: str = "c") -> str:
    for rel, text in files.items():
        (cwd / rel).parent.mkdir(parents=True, exist_ok=True)
        (cwd / rel).write_text(text, encoding="utf-8")
    sh(cwd, "add", "-A")
    sh(cwd, "commit", "-q", "-m", message)
    return sh(cwd, "rev-parse", "HEAD")


@dataclass
class World:
    origin: Path
    main: Path
    work: Path
    root: Path
    tip: str
    state: dict[str, Any] = field(default_factory=dict)

    def cfg(self, max_fix: int = 2, total: float = 600.0, suite: Optional[float] = 300.0) -> local_gate.LocalGateConfig:
        return local_gate.LocalGateConfig(7, BRANCH, ("src",), max_fix, self.main, self.root, total, suite)

    def save(self) -> None:
        (self.root / "state.json").write_text(json.dumps(self.state), encoding="utf-8")

    def remote_tip(self) -> str:
        return sh(self.origin, "rev-parse", f"refs/heads/{BRANCH}")


def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, project: dict[str, Any] = PROJECT) -> World:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    origin = tmp_path / "origin.git"
    sh(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    main = tmp_path / "main"
    sh(tmp_path, "clone", "-q", str(origin), str(main))
    tip = commit(main, {"src/a.py": "x = 1\n", ".ndf/project.json": json.dumps(project), "check.sh": "true\n"}, "初め")
    sh(main, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    root = tmp_path / "rf7"
    work = root / "work"
    sh(main, "worktree", "add", "-q", "--detach", str(work), tip)
    return World(origin, main, work, root, tip)


Rule = Callable[[str, Path], int]


@dataclass
class FakeExec:
    """suite の起動の代わり。`rule(command, tree)` が終了コードを返す。打った (コマンド, その時の src/a.py) を残す。"""

    rule: Rule = lambda command, tree: 0
    seen: list[tuple[str, str]] = field(default_factory=list)
    now: float = 0.0
    step: float = 1.0

    def run(self, command: str, cwd: Path, timeout: float, *, suites: Any = None) -> subprocess.CompletedProcess[str]:
        assert suites == SUITES or suites is not None
        self.seen.append((command, (cwd / "src/a.py").read_text(encoding="utf-8")))
        code = self.rule(command, cwd)
        if code == -1 or self.step > timeout:
            self.now += timeout
            raise subprocess.TimeoutExpired(command, timeout)
        self.now += self.step
        return subprocess.CompletedProcess(command, code, "TOKEN=ghp_" + "a" * 36 + "\n", "")

    def clock(self) -> float:
        return self.now

    def ex(self) -> local_gate.Exec:
        return local_gate.Exec(self.run, self.clock, lambda began: 0.0)


def _run(w: World, fake: FakeExec, fix: Optional[Callable[[Any, int], bool]] = None, **cfg: Any) -> dict[str, Any]:
    w.root.mkdir(parents=True, exist_ok=True)
    launcher = fix if fix is not None else (lambda failed, n: pytest.fail("直しを起動した"))
    return local_gate.run_local_gate(git.Git(w.work), w.cfg(**cfg), w.state, w.save, launcher, fake.ex())


def bad_src(command: str, tree: Path) -> int:
    """src/a.py が `x = 2` なら unit が落ちる。"""
    return 1 if command == "sh check.sh" and "x = 2" in (tree / "src/a.py").read_text(encoding="utf-8") else 0


def test_a_passing_change_is_tested_once_and_its_sha_is_pushed(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    sha = commit(w.work, {"src/a.py": "x = 3\n"})
    fake = FakeExec()
    got = _run(w, fake)
    assert (got["status"], got["tests"], got["head"]) == ("passed", "passed", sha)
    assert w.remote_tip() == sha
    assert [c for c, _ in fake.seen] == ["sh check.sh", "sh other.sh"]
    assert w.state["pushing"]["sha"] == sha
    assert not w.state.get("verify_trees")
    # ログは伏せ字にして、実行ユーザーだけが読める権限で書く
    logs = sorted((w.root / "logs").glob("local-gate-*.log"))
    assert [p.name for p in logs] == ["local-gate-0-head-1.log", "local-gate-0-head-2.log"]
    assert "ghp_" not in logs[0].read_text(encoding="utf-8")
    assert stat.S_IMODE(logs[0].stat().st_mode) == 0o600
    assert stat.S_IMODE((w.root / "logs").stat().st_mode) == 0o700


def test_no_change_finishes_without_tests(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    fake = FakeExec()
    got = _run(w, fake)
    assert (got["tests"], got["head"]) == ("skipped", w.tip)
    assert fake.seen == []
    assert not (w.root / "logs").exists()


def test_a_change_to_a_test_definition_is_refused_and_not_pushed(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    commit(w.work, {"check.sh": "exit 0\n"})
    with pytest.raises(final_gate.GateStopped) as got:
        _run(w, FakeExec())
    assert got.value.kind == "refused"
    assert "check.sh" in got.value.detail
    assert w.remote_tip() == w.tip


def test_a_failure_caused_by_the_change_is_fixed_and_the_fixed_sha_is_pushed(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    commit(w.work, {"src/a.py": "x = 2\n"})
    asked: list[Any] = []

    def fix(failed: Any, n: int) -> bool:
        asked.append(list(failed))
        commit(w.work, {"src/a.py": "x = 4\n"})
        return True

    got = _run(w, FakeExec(bad_src), fix)
    assert got["tests"] == "passed"
    assert len(asked) == 1 and len(asked[0]) == 1
    assert asked[0][0].startswith("- #1 unit（終了コード 1）")
    assert "ghp_" not in asked[0][0]
    assert sh(w.origin, "show", f"{w.remote_tip()}:src/a.py") == "x = 4"


def test_a_suite_failing_also_before_the_change_stops_without_a_fix(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    commit(w.work, {"src/a.py": "x = 3\n"})
    with pytest.raises(final_gate.GateStopped) as got:
        _run(w, FakeExec(lambda c, t: 1 if c == "sh other.sh" else 0))
    assert got.value.kind == "test_failed"
    assert "見分けていない" in got.value.detail
    assert got.value.record["base_failed"] == [{"suite": "#2 lint-ish", "code": 1}]
    assert got.value.record["head_failed"] == [{"suite": "#2 lint-ish", "code": 1}]
    assert w.remote_tip() == w.tip


def test_the_same_127_on_the_base_is_a_precondition(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    commit(w.work, {"src/a.py": "x = 3\n"})
    with pytest.raises(final_gate.GateStopped) as got:
        _run(w, FakeExec(lambda c, t: 127 if c == "sh check.sh" else 0), max_fix=0)
    assert got.value.kind == "precondition"


def test_the_last_round_failure_without_126_127_does_not_test_the_base(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    commit(w.work, {"src/a.py": "x = 2\n"})
    fake = FakeExec(bad_src)
    with pytest.raises(final_gate.GateStopped) as got:
        _run(w, fake, max_fix=0)
    assert got.value.kind == "test_failed"
    assert len(fake.seen) == 2  # 変更後の 1 周だけ


def test_a_push_rejected_because_the_branch_moved_rebases_and_tests_again(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    mine = commit(w.work, {"src/a.py": "x = 3\n"})
    other = tmp_path / "other"
    sh(tmp_path, "clone", "-q", "-b", BRANCH, str(w.origin), str(other))
    pushed = {"done": False}

    def rule(command: str, tree: Path) -> int:
        # 最初のテストの間に他者が push する
        if not pushed["done"]:
            commit(other, {"docs.md": "d\n"})
            sh(other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
            pushed["done"] = True
        return 0

    fake = FakeExec(rule)
    got = _run(w, fake)
    assert got["head"] != mine
    assert w.remote_tip() == got["head"]
    assert sh(w.origin, "show", f"{got['head']}:docs.md") == "d"
    assert len(fake.seen) == 4


def test_a_push_rejected_without_a_move_is_push_rejected(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    hook = w.origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    commit(w.work, {"src/a.py": "x = 3\n"})
    with pytest.raises(final_gate.GateStopped) as got:
        _run(w, FakeExec())
    assert got.value.kind == "push_rejected"
    assert "pushing" not in w.state


def test_the_total_limit_stops_with_fix_limit(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    commit(w.work, {"src/a.py": "x = 3\n"})
    fake = FakeExec(step=400.0)
    with pytest.raises(final_gate.GateStopped) as got:
        _run(w, fake, total=500.0, suite=None)
    assert got.value.kind == "fix_limit"
    assert "test.local_total_minutes" in got.value.detail
    assert len(fake.seen) == 2


def test_a_timeout_under_the_suite_limit_is_a_failure_and_at_the_total_is_fix_limit(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    commit(w.work, {"src/a.py": "x = 3\n"})
    with pytest.raises(final_gate.GateStopped) as got:
        _run(w, FakeExec(lambda c, t: -1 if c == "sh check.sh" else 0), max_fix=0, suite=10.0)
    assert got.value.kind == "test_failed"
    with pytest.raises(final_gate.GateStopped) as limit:
        _run(w, FakeExec(lambda c, t: -1), max_fix=0, suite=None)
    assert limit.value.kind == "fix_limit"


def test_a_test_that_changes_tracked_files_is_a_precondition(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    commit(w.work, {"src/a.py": "x = 3\n"})

    def dirty(command: str, tree: Path) -> int:
        (tree / "src/a.py").write_text("changed\n", encoding="utf-8")
        return 0

    with pytest.raises(final_gate.GateStopped) as got:
        _run(w, FakeExec(dirty))
    assert got.value.kind == "precondition"
    assert w.remote_tip() == w.tip


def test_a_restart_after_the_push_restores_the_result_without_tests(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    sha = commit(w.work, {"src/a.py": "x = 3\n"})
    _run(w, FakeExec())
    fake = FakeExec()
    got = _run(w, fake)
    assert (got["tests"], got["head"]) == ("passed", sha)
    assert fake.seen == []
    # PATH が変われば、その SHA でテストを打ち直す
    monkeypatch.setenv("PATH", os.environ["PATH"] + ":/nowhere")
    again = FakeExec()
    assert _run(w, again)["tests"] == "passed"
    assert len(again.seen) == 2


def test_fetch_runs_once_even_when_a_rebase_is_needed(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    commit(w.work, {"src/a.py": "x = 3\n"})
    commit(w.main, {"docs.md": "d\n"})
    sh(w.main, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    fetches: list[tuple[str, ...]] = []
    held = git.Git.run

    def counted(self: git.Git, *args: str) -> str:
        if args and args[0] == "fetch":
            fetches.append(args)
        return held(self, *args)

    monkeypatch.setattr(git.Git, "run", counted)
    got = _run(w, FakeExec())
    assert len(fetches) == 1
    assert w.state["onto"] == sh(w.main, "rev-parse", "HEAD")
    assert got["tests"] == "passed"


def test_a_fix_outside_the_scope_or_on_a_protected_file_is_undone(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    bad = commit(w.work, {"src/a.py": "x = 2\n"})

    def fix(failed: Any, n: int) -> bool:
        commit(w.work, {"src/conftest.py": "# hook\n"})
        return True

    with pytest.raises(final_gate.GateStopped) as got:
        _run(w, FakeExec(bad_src), fix)
    assert got.value.kind == "refused"
    assert sh(w.work, "rev-parse", "HEAD") == bad


def test_a_command_pointing_into_the_work_tree_is_a_precondition(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    project = {"test": {"suites": [{"name": "u", "command": f"pytest {w.main}/tests"}]}}
    commit(w.main, {".ndf/project.json": json.dumps(project)})
    sh(w.main, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    commit(w.work, {"src/a.py": "x = 3\n"})
    with pytest.raises(final_gate.GateStopped) as got:
        _run(w, FakeExec())
    assert got.value.kind == "precondition"
    assert "#1 u" in got.value.detail and str(w.main) not in got.value.detail


def test_settings_are_read_from_the_fetched_tip_not_from_the_work_tree(tmp_path, monkeypatch):
    w = make_world(tmp_path, monkeypatch)
    project = {"test": {"suites": [{"name": "u", "command": "sh new.sh"}]}}
    commit(w.main, {".ndf/project.json": json.dumps(project)})
    sh(w.main, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    commit(w.work, {"src/a.py": "x = 3\n"})
    fake = FakeExec()
    _run(w, fake)
    assert [c for c, _ in fake.seen] == ["sh new.sh"]


def test_redact_output_hides_tokens_and_private_key_bodies():
    text = "a\n-----BEGIN RSA PRIVATE KEY-----\nMIIBody\n-----END RSA PRIVATE KEY-----\nghp_" + "b" * 36 + "\nok"
    got = local_gate.redact_output(text)
    assert "MIIBody" not in got and "ghp_" not in got
    assert got.split("\n")[0] == "a" and got.split("\n")[-1] == "ok"
    assert "rest" not in local_gate.redact_output("-----BEGIN PRIVATE KEY-----\nrest")


def test_suite_label_drops_a_name_that_looks_like_a_secret():
    assert local_gate.suite_label(2, "unit") == "#2 unit"
    assert local_gate.suite_label(1, "x" * 150) == "#1 " + "x" * 100
    assert local_gate.suite_label(3, "ghp_" + "c" * 36) == "#3"


def test_fix_excerpt_redacts_before_cutting():
    out = "\n".join(f"line {i}" for i in range(300)) + "\nghp_" + "d" * 36
    failed = local_gate.Failed(1, "#1 unit", 1, "pytest " + "z" * 900, out)
    [got] = local_gate.fix_excerpt([failed])
    assert "ghp_" not in got
    assert "line 199" not in got and "line 299" in got
    assert len(got) < 500 + 8_000 + 200


def test_write_private_log_replaces_without_touching_a_linked_file(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir(mode=0o755)
    os.chmod(logs, 0o755)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep\n", encoding="utf-8")
    os.link(outside, logs / "local-gate-0-head-1.log")
    state: dict[str, Any] = {}
    path = workspace.write_private_log(logs, "local-gate-0-head-1.log", "new\n", state, lambda: None)
    assert path.read_text(encoding="utf-8") == "new\n"
    assert outside.read_text(encoding="utf-8") == "keep\n"
    assert stat.S_IMODE(logs.stat().st_mode) == 0o700
    assert set(state["gate_logs"][0]) == {"name", "tmp", "st_dev", "st_ino"}


def test_write_private_log_refuses_a_linked_directory(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "logs").symlink_to(tmp_path / "real")
    with pytest.raises(workspace.LogRefused):
        workspace.write_private_log(tmp_path / "logs", "local-gate-x.log", "x", {}, lambda: None)


def test_means_kind_is_local_for_the_world():
    assert means.decide(PROJECT).kind == means.Kind.LOCAL_TEST
