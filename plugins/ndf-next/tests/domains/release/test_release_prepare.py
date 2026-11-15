"""次の版のリリースブランチの用意（#3572 の受け入れ条件 10・I6）。"""

from __future__ import annotations

import json

import pytest

from ndf.adapters.github import GitHubError
from ndf.domains.release import cli as release_cli
from ndf.domains.release import prepare, version_decide

from ._fakes import FakeHub, pull

MERGE_SHA = "c" * 40


def _merged(ref: str = "release/v10.27.68", base: str = "main") -> FakeHub:
    return FakeHub(pulls={7: pull(ref=ref, base=base, merged=True)._replace(merge_sha=MERGE_SHA)})


def _created(hub: FakeHub) -> list[dict]:
    return [p for m, path, p in hub.requests if (m, path) == ("POST", "/repos/o/r/git/refs")]


def test_next_branch_is_the_next_patch_without_the_dev_suffix() -> None:
    assert prepare.next_branch("release/v10.27.68") == ("10.27.69", "release/v10.27.69")
    assert prepare.next_branch("release/v1.3.0-dev.2") == ("1.3.1", "release/v1.3.1")
    with pytest.raises(version_decide.VersionError):
        prepare.next_branch("develop")


def test_prepare_next_creates_the_branch_from_the_merge_commit_of_the_production_branch() -> None:
    """受け入れ条件 10: 起点は本番ブランチのマージ後の先端（本番の PR のマージコミット）で、ベースブランチではない。"""
    hub = _merged()
    made = prepare.prepare_next(hub, 7, "main")
    assert made == prepare.Prepared("release/v10.27.69", "10.27.69", True)
    assert _created(hub) == [{"ref": "refs/heads/release/v10.27.69", "sha": MERGE_SHA}]


def test_prepare_next_twice_keeps_one_branch() -> None:
    """I6: 既にあれば（GitHub が 422 で断る）作らずに名前を返す。"""
    hub = _merged()
    hub.responses[("POST", "/repos/o/r/git/refs")] = GitHubError("Reference already exists", 422)
    assert prepare.prepare_next(hub, 7, "main") == prepare.Prepared("release/v10.27.69", "10.27.69", False)


def test_prepare_next_raises_other_errors() -> None:
    hub = _merged()
    hub.responses[("POST", "/repos/o/r/git/refs")] = GitHubError("Forbidden", 403)
    with pytest.raises(GitHubError):
        prepare.prepare_next(hub, 7, "main")


@pytest.mark.parametrize("hub", [FakeHub(pulls={7: pull()}), _merged(base="develop")])
def test_prepare_next_refuses_an_unmerged_or_non_production_pr(hub: FakeHub) -> None:
    with pytest.raises(prepare.PrepareError):
        prepare.prepare_next(hub, 7, "main")
    assert _created(hub) == []


def test_cli_prepare_next_reports_the_branch(monkeypatch, capsys) -> None:
    hub = _merged()
    monkeypatch.setattr(release_cli, "_hub", lambda args: hub)
    assert release_cli.main(["prepare-next", "--pr", "7", "--production", "main"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    values = {i["name"]: i["value"] for i in out["items"] if i["kind"] == release_cli.VALUE_KIND}
    assert values == {"branch": "release/v10.27.69", "version": "10.27.69", "created": True}
    assert "outputs" not in out  # 次の版はスプリントの文脈の版（出した版）を書き換えない
    monkeypatch.setattr(release_cli, "_hub", lambda args: FakeHub(pulls={7: pull()}))
    assert release_cli.main(["prepare-next", "--pr", "7", "--production", "main"]) != 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["cause"]["kind"] == "precondition"
