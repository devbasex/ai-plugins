"""工程のプランの組み立て（`route._by_refactor_gate`）と、手元の経路の `follow` のステップ（#4566 の決定 6）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import git_sandbox
import pytest

from ndf.core import route
from ndf.progress import invoke

from ._flow import plan_of, stopped, walk

CI = ["bash", "-c", "x", "b", "base", "ndf", "refactor", "run", "--required", "test"]
LOCAL = ["bash", "-c", "x", "b", "base", "ndf", "refactor", "run", "--local-test"]


def _ids(plan: dict) -> list[str]:
    return [s["id"] for s in plan["steps"]]


def test_the_ci_route_keeps_push_and_has_no_follow():
    plan = plan_of("refactor", refactor=CI)
    assert _ids(plan) == ["refactor", "push", "fix-push"]
    visited, _ = walk(plan, {"refactor": [stopped("incomplete")]})
    assert visited == ["refactor", "push"]


@pytest.mark.parametrize("launch", [LOCAL, [*LOCAL, "--candidates", "/tmp/c.md"]])
def test_the_local_route_follows_the_pushed_sha_instead_of_pushing(launch):
    plan = plan_of("refactor", refactor=launch)
    assert _ids(plan) == ["refactor", "follow"]
    refactor = plan["steps"][0]
    assert refactor["run"][-2:] == ["--pushed-out", "{state_dir}/pushed-sha"]
    assert "incomplete" not in refactor.get("on_cause", {})
    visited, _ = walk(plan, {})
    assert visited == ["refactor", "follow"]
    # refactor が止まればプランもそこで止まる（follow を通らない）
    visited, _ = walk(plan, {"refactor": [stopped("incomplete")]})
    assert visited == ["refactor"]


def test_the_check_plan_is_not_built_for_the_local_route():
    with pytest.raises(route.RouteUnsupported):
        plan_of("check", refactor=LOCAL)
    got = invoke.plan_failure("check", route.RouteUnsupported("x"))
    assert got is not None and got["cause"]["kind"] == "precondition"


def _follow(plan: dict) -> list[str]:
    return next(s for s in plan["steps"] if s["id"] == "follow")["run"]


@pytest.fixture
def clones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, list[str]]:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    origin = tmp_path / "origin.git"
    git_sandbox.sh(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    caller = tmp_path / "caller"
    git_sandbox.sh(tmp_path, "clone", "-q", str(origin), str(caller))
    git_sandbox.sh(caller, "commit", "-q", "--allow-empty", "-m", "a")
    git_sandbox.sh(caller, "push", "-q", "origin", "HEAD:refs/heads/b")
    state = tmp_path / "state"
    state.mkdir()
    argv = [a.replace("{state_dir}", str(state)) for a in _follow(plan_of("refactor", refactor=LOCAL, branch="b", remote="origin"))]
    return caller, state, argv


def _run(cwd: Path, argv: list[str]) -> int:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True).returncode


def _head(cwd: Path) -> str:
    return git_sandbox.sh(cwd, "rev-parse", "HEAD", strip=True)


def test_follow_fast_forwards_to_the_recorded_sha(clones, tmp_path):
    caller, state, argv = clones
    other = tmp_path / "other"
    git_sandbox.sh(tmp_path, "clone", "-q", "-b", "b", str(caller.parent / "origin.git"), str(other))
    git_sandbox.sh(other, "commit", "-q", "--allow-empty", "-m", "pushed")
    sha = _head(other)
    git_sandbox.sh(other, "push", "-q", "origin", "HEAD:refs/heads/b")
    (state / "pushed-sha").write_text(sha + "\n", encoding="utf-8")
    assert _run(caller, argv) == 0
    assert _head(caller) == sha


def test_follow_at_the_sha_does_not_fetch(clones):
    caller, state, argv = clones
    (state / "pushed-sha").write_text(_head(caller) + "\n", encoding="utf-8")
    git_sandbox.sh(caller, "remote", "set-url", "origin", "/nowhere")
    assert _run(caller, argv) == 0


def test_follow_stops_ahead_of_the_sha_or_without_the_file(clones):
    caller, state, argv = clones
    assert _run(caller, argv) == 1
    (state / "pushed-sha").write_text(_head(caller) + "\n", encoding="utf-8")
    git_sandbox.sh(caller, "commit", "-q", "--allow-empty", "-m", "ahead")
    assert _run(caller, argv) == 1


def test_follow_does_not_overwrite_an_ignored_file(clones, tmp_path):
    caller, state, argv = clones
    other = tmp_path / "other"
    git_sandbox.sh(tmp_path, "clone", "-q", "-b", "b", str(caller.parent / "origin.git"), str(other))
    (other / "gen.txt").write_text("tracked\n", encoding="utf-8")
    git_sandbox.sh(other, "add", "gen.txt")
    git_sandbox.sh(other, "commit", "-q", "-m", "track")
    git_sandbox.sh(other, "push", "-q", "origin", "HEAD:refs/heads/b")
    (caller / ".git" / "info" / "exclude").write_text("gen.txt\n", encoding="utf-8")
    (caller / "gen.txt").write_text("mine\n", encoding="utf-8")
    (state / "pushed-sha").write_text(_head(other) + "\n", encoding="utf-8")
    assert _run(caller, argv) == 1
    assert (caller / "gen.txt").read_text(encoding="utf-8") == "mine\n"
