"""工程のプランの組み立て（`route._by_refactor_gate`）と、手元の経路の `follow` のステップ（#4566 の決定 6）。

`follow` は `local_gate.follow_pushed` を同じプロセスで呼んで確かめる（シェルを起動しない。git は本物を使う）。
"""

from __future__ import annotations

from pathlib import Path

import git_sandbox
import pytest

from ndf.adapters import git
from ndf.core import route
from ndf.domains.crossrefactor import final_gate, local_gate
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


def test_the_follow_step_calls_the_follow_subcommand():
    argv = _follow(plan_of("refactor", refactor=LOCAL, branch="b", remote="origin"))
    assert argv == ["ndf", "refactor", "follow", "--pushed", "{state_dir}/pushed-sha", "--branch", "b", "--remote", "origin"]


@pytest.fixture
def clones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, list[tuple[str, ...]]]:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    origin = tmp_path / "origin.git"
    git_sandbox.sh(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    caller = tmp_path / "caller"
    git_sandbox.sh(tmp_path, "clone", "-q", str(origin), str(caller))
    git_sandbox.sh(caller, "commit", "-q", "--allow-empty", "-m", "a")
    git_sandbox.sh(caller, "push", "-q", "origin", "HEAD:refs/heads/b")
    fetches: list[tuple[str, ...]] = []
    held = git.Git.run

    def counted(self: git.Git, *args: str) -> str:
        if args and args[0] == "fetch":
            fetches.append(args)
        return held(self, *args)

    monkeypatch.setattr(git.Git, "run", counted)
    return caller, tmp_path / "pushed-sha", fetches


def _follow_to(caller: Path, pushed: Path) -> str:
    return local_gate.follow_pushed(git.Git(caller), pushed, "origin", "b")


def _head(cwd: Path) -> str:
    return git_sandbox.sh(cwd, "rev-parse", "HEAD", strip=True)


def _push_from_other(tmp_path: Path, caller: Path, files: dict[str, str] | None = None) -> str:
    other = tmp_path / "other"
    git_sandbox.sh(tmp_path, "clone", "-q", "-b", "b", str(caller.parent / "origin.git"), str(other))
    for rel, text in (files or {}).items():
        (other / rel).write_text(text, encoding="utf-8")
        git_sandbox.sh(other, "add", rel)
    git_sandbox.sh(other, "commit", "-q", "--allow-empty", "-m", "pushed")
    git_sandbox.sh(other, "push", "-q", "origin", "HEAD:refs/heads/b")
    return _head(other)


def test_follow_fetches_only_when_the_sha_is_not_here(clones, tmp_path):
    caller, pushed, fetches = clones
    sha = _push_from_other(tmp_path, caller)
    pushed.write_text(sha + "\n", encoding="utf-8")
    assert _follow_to(caller, pushed) == sha
    assert _head(caller) == sha and len(fetches) == 1


def test_follow_with_the_sha_here_fast_forwards_without_fetching(clones):
    caller, pushed, fetches = clones
    base = _head(caller)
    git_sandbox.sh(caller, "commit", "-q", "--allow-empty", "-m", "pushed")
    sha = _head(caller)
    git_sandbox.sh(caller, "reset", "-q", "--hard", base)
    pushed.write_text(sha + "\n", encoding="utf-8")
    assert _follow_to(caller, pushed) == sha
    assert _head(caller) == sha and fetches == []
    # すでにその SHA なら何もしない
    assert _follow_to(caller, pushed) == sha and fetches == []


def test_follow_stops_ahead_of_the_sha_or_without_the_file(clones):
    caller, pushed, _ = clones
    with pytest.raises(final_gate.GateStopped) as missing:
        _follow_to(caller, pushed)
    assert missing.value.kind == "precondition"
    # SHA でない中身（ブランチ名など）は、git を起動する前に止める
    pushed.write_text("b\n", encoding="utf-8")
    before = _head(caller)
    with pytest.raises(final_gate.GateStopped) as named:
        _follow_to(caller, pushed)
    assert named.value.kind == "precondition" and _head(caller) == before
    pushed.write_text(_head(caller) + "\n", encoding="utf-8")
    git_sandbox.sh(caller, "commit", "-q", "--allow-empty", "-m", "ahead")
    with pytest.raises(final_gate.GateStopped) as ahead:
        _follow_to(caller, pushed)
    assert ahead.value.kind == "precondition"


def test_follow_does_not_overwrite_an_ignored_file(clones, tmp_path):
    caller, pushed, _ = clones
    sha = _push_from_other(tmp_path, caller, {"gen.txt": "tracked\n"})
    (caller / ".git" / "info" / "exclude").write_text("gen.txt\n", encoding="utf-8")
    (caller / "gen.txt").write_text("mine\n", encoding="utf-8")
    pushed.write_text(sha + "\n", encoding="utf-8")
    with pytest.raises(final_gate.GateStopped):
        _follow_to(caller, pushed)
    assert (caller / "gen.txt").read_text(encoding="utf-8") == "mine\n"
