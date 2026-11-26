"""`ndf ci merge` のマージの対象の決め方（`--pr`・`--branch`・`--base`）。マージ先・head・リポジトリの違う PR はマージしない。"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from ndf import ci_cli
from ndf.adapters import github
from ndf.contracts import causes

import merge_hub

BASE = "sprint/x"
HEAD_REF = "unit/a"


def _pull(number: int = 7, base: str = BASE, ref: str = HEAD_REF, **changed: object) -> github.Pull:
    return merge_hub.pull(number=number, base=base, ref=ref)._replace(**changed)


@pytest.fixture
def use(monkeypatch: pytest.MonkeyPatch) -> Callable[..., merge_hub.MergeHub]:
    """作業場所の HEAD が `merge_hub.HEAD` で、`pulls` の PR を持つ偽の GitHub に差し替える。"""

    class FakeGit:
        def rev_parse(self, _rev: str) -> str:
            return merge_hub.HEAD

        def remote_repo(self) -> str:
            return merge_hub.REPO

    def make(*pulls: github.Pull, **hub_args: object) -> merge_hub.MergeHub:
        hub = merge_hub.MergeHub(
            pulls_of={p.number: p for p in pulls},
            files={BASE: (merge_hub.project_text(merge_hub.strategy()), "c1")},
            **hub_args,
        )
        monkeypatch.setattr(ci_cli.git, "Git", FakeGit)
        monkeypatch.setattr(ci_cli, "GitHub", lambda *_: hub)
        monkeypatch.setattr(github, "client", lambda **_: None)
        return hub

    return make


UNIT = ["merge", "--pr", "7", "--branch", HEAD_REF, "--base", BASE, "--reviewed", merge_hub.HEAD]


def test_the_numbered_pull_is_merged_when_it_matches(use, capsys, last_result) -> None:
    # 同じ head から別のマージ先へ出した PR があっても、番号の PR をマージする
    hub = use(_pull(number=8, base="main"), _pull())
    assert ci_cli.main(UNIT) == 0
    assert hub.merged == [7] and last_result(capsys.readouterr().out)["metrics"]["merge"] == "merged"


@pytest.mark.parametrize(
    ("changed", "word"),
    [
        ({"head_ref": "unit/b"}, "unit/b"),
        ({"base_ref": "main"}, "main"),
        ({"cross_repository": True}, "フォーク"),
        ({"state": "closed"}, "閉じ"),
        # マージ先の確かめはマージ済みの確かめより先
        ({"state": "closed", "merged": True, "base_ref": "main"}, "main"),
    ],
)
def test_a_numbered_pull_that_does_not_match_is_refused_without_requests(use, capsys, last_result, changed, word) -> None:
    hub = use(_pull(**changed))
    assert ci_cli.main(UNIT) == causes.exit_code("refused")
    found = last_result(capsys.readouterr().out)
    assert found["cause"]["kind"] == "refused" and word in found["cause"]["detail"]
    assert hub.calls == ["pulls"] and hub.merged == [] and hub.dequeued == []


@pytest.mark.parametrize("option", [["--base", BASE], ["--branch", HEAD_REF]])
def test_either_option_alone_checks_the_numbered_pull(use, option) -> None:
    hub = use(_pull(cross_repository=True))
    assert ci_cli.main(["merge", "--pr", "7", *option]) == causes.exit_code("refused")
    assert hub.merged == []


def test_a_merged_numbered_pull_that_matches_is_ok_and_hands_over_the_merge_commit(use, capsys, last_result) -> None:
    hub = use(_pull(state="closed", merged=True, merge_sha="e" * 40))
    assert ci_cli.main(UNIT) == 0
    found = last_result(capsys.readouterr().out)
    assert found["metrics"]["merge"] == "already" and hub.merged == []
    # 単位を流す処理は、この値で履歴を照合する（GitHub へ読み直さない。#4369）
    assert found["items"] == [{"kind": "pr", "number": 7, "merge_sha": "e" * 40}]


def test_the_number_alone_keeps_treating_a_closed_pull_as_a_precondition(use) -> None:
    use(_pull(state="closed"))
    assert ci_cli.main(["merge", "--pr", "7"]) == causes.exit_code("precondition")


def test_a_number_that_is_not_a_pull_is_a_precondition(use) -> None:
    use()
    assert ci_cli.main(UNIT) == causes.exit_code("precondition")


def test_a_base_changed_right_before_sending_is_refused(use, capsys, last_result) -> None:
    hub = use(_pull(), pull_at_merge=_pull(base="main"))
    assert ci_cli.main(UNIT) == causes.exit_code("refused")
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "refused" and hub.merged == []


def test_branch_with_base_picks_the_open_pull_to_that_base(use) -> None:
    hub = use(_pull(number=8, base="main"), _pull())
    assert ci_cli.main(["merge", "--branch", HEAD_REF, "--base", BASE]) == 0
    assert hub.merged == [7]


def test_branch_with_base_without_such_a_pull_is_a_precondition(use, capsys, last_result) -> None:
    hub = use(_pull(base="main"), _pull(number=8, cross_repository=True))
    assert ci_cli.main(["merge", "--branch", HEAD_REF, "--base", BASE]) == causes.exit_code("precondition")
    assert BASE in last_result(capsys.readouterr().out)["cause"]["detail"] and hub.merged == []


def test_neither_branch_nor_number_is_invalid(use, capsys, last_result) -> None:
    hub = use(_pull())
    assert ci_cli.main(["merge", "--base", BASE]) == causes.exit_code("invalid")
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "invalid" and hub.calls == []
