"""本番のマージの後の CD の確かめ（偽の GitHub と git。#3572 の決定 7）。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from ndf.adapters.github import GitHubError
from ndf.domains.release import cli as release_cli
from ndf.domains.release import confirm, trail

from ._fakes import FakeHub, not_found, pull

PR = 7
MERGE = "e" * 40
BUMP_MERGE = "f" * 40
TAG = "kit--v1.3.0"


class FakeGit:
    """取り込みは常に通り、`ancestors` にある (祖先, 子孫) の組だけを祖先と読む偽物。"""

    def __init__(self, ancestors: set[tuple[str, str]]) -> None:
        self.ancestors = ancestors

    def fetch_branch(self, remote: str, name: str) -> Any:
        return SimpleNamespace(returncode=0, stderr="")

    def is_ancestor(self, a: str, d: str) -> bool:
        return (a, d) in self.ancestors


def _hub(bumped: bool = True, tagged: bool = True, released: bool = True) -> FakeHub:
    hub = FakeHub(pulls={PR: pull(number=PR, ref="sprint/x", merged=True)._replace(merge_sha=MERGE)})
    rows = [{"number": 30, "head": {"ref": "sprint/y"}, "merged_at": "t", "merge_commit_sha": "1" * 40}]
    if bumped:
        rows.append({"number": 20, "head": {"ref": "release/v1.3.0"}, "merged_at": "t", "merge_commit_sha": BUMP_MERGE})
    hub.responses[("GET", "/repos/o/r/pulls")] = rows
    hub.responses[("GET", f"/repos/o/r/git/ref/tags/{TAG}")] = {} if tagged else not_found()
    hub.responses[("GET", f"/repos/o/r/releases/tags/{TAG}")] = {} if released else not_found()
    return hub


def _watch(budget: float = 60.0) -> tuple[confirm.Watch, list[float]]:
    now = [0.0]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    return confirm.Watch(budget, lambda: now[0], sleep), slept


def _confirm(hub: FakeHub, budget: float = 60.0) -> tuple[confirm.Confirmed, list[float]]:
    watch, slept = _watch(budget)
    return confirm.confirm(hub, FakeGit({(MERGE, BUMP_MERGE)}), PR, "main", "kit", watch), slept


def _records(hub: FakeHub) -> list[str]:
    return [c.body for c in hub.comments(PR) if trail.VERSION_MARK in c.body]


def test_confirm_returns_the_version_and_writes_the_record_once() -> None:
    hub = _hub()
    got, slept = _confirm(hub)
    assert (got.kind, got.version, got.tag, got.bump_pr, got.bump_revision) == (None, "1.3.0", TAG, 20, BUMP_MERGE) and slept == []
    again, _ = _confirm(hub)
    assert again.kind is None and len(_records(hub)) == 1
    assert trail.read_version_record(hub, PR) == "1.3.0"


def test_confirm_without_the_bump_merge_stops_after_the_budget_with_bump_missing() -> None:
    hub = _hub(bumped=False)
    got, slept = _confirm(hub, budget=45.0)
    assert got.kind == "deploy_failed" and got.missing == "bump" and "本番はマージ済み" in got.detail
    assert sum(slept) == 45.0 and _records(hub) == [] and got.bump_revision is None


@pytest.mark.parametrize(("tagged", "released"), [(False, False), (True, False)])
def test_confirm_with_a_tag_but_no_release_keeps_waiting_and_names_publish(tagged: bool, released: bool) -> None:
    """タグだけがあり GitHub Release が無ければ ok にしない。足りないものは publish で、版上げの PR のマージコミットを出す。"""
    hub = _hub(tagged=tagged, released=released)
    got, _ = _confirm(hub, budget=31.0)
    assert got.kind == "deploy_failed" and got.missing == "publish" and got.bump_revision == BUMP_MERGE and _records(hub) == []


def test_confirm_before_the_merge_is_refused() -> None:
    hub = _hub()
    hub.pulls[PR] = pull(number=PR, ref="sprint/x")
    with pytest.raises(confirm.ConfirmError):
        _confirm(hub)


def test_confirm_picks_the_smallest_bump_containing_the_merge() -> None:
    hub = _hub()
    rows = hub.responses[("GET", "/repos/o/r/pulls")]
    rows.append({"number": 21, "head": {"ref": "release/v1.4.0"}, "merged_at": "t", "merge_commit_sha": "9" * 40})
    watch, _ = _watch()
    got = confirm.confirm(hub, FakeGit({(MERGE, BUMP_MERGE), (MERGE, "9" * 40)}), PR, "main", "kit", watch)
    assert (got.version, got.bump_pr) == ("1.3.0", 20)


def test_other_github_errors_are_not_read_as_missing() -> None:
    hub = _hub()
    hub.responses[("GET", f"/repos/o/r/git/ref/tags/{TAG}")] = GitHubError("壊れた", 500)
    with pytest.raises(GitHubError):
        _confirm(hub)


def test_cli_confirm_stops_with_one_rerun_move(monkeypatch, capsys, tmp_path) -> None:
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"plugins": [{"name": "kit", "source": "./kit"}]}), encoding="utf-8"
    )
    hub = _hub(bumped=False)
    monkeypatch.setattr(release_cli, "_hub", lambda args: hub)
    monkeypatch.setattr(release_cli.git_mod, "Git", lambda root: FakeGit(set()))
    code = release_cli.main(["confirm", "--root", str(tmp_path), "--pr", str(PR), "--production", "main", "--budget", "0"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code != 0 and out["cause"]["kind"] == "deploy_failed"
    assert {"kind": "cd", "name": "bump", "detail": out["cause"]["detail"]} in out["items"]
    values = {i["name"]: i["value"] for i in out["items"] if i["kind"] == "value"}
    assert list(values["moves"]) == ["rerun"] and values["moves"]["rerun"]["argv"][1:3] == ["release-steps", "confirm"]
