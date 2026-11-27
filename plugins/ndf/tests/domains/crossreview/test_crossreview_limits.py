"""cross-review の上限（#2468）と、上限を足す前から守る振る舞い。相手役と修正担当は偽物、git は一時の origin で本物を打つ
（`test_crossreview_run` の形）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import contracts
from ndf.adapters import agents
from ndf.domains.crossreview import criteria_decide, settings
from ndf.domains.crossreview import finish_decide as fin

from ._repos import Repos, commit
from .test_crossreview_run import PR, Fake, _push_to_pr, _run, _state


def _declare(main: Path, body: dict) -> None:
    """実行者のメインディレクトリ `main` の `.ndf/review.json` を書く。"""
    (main / ".ndf").mkdir(parents=True, exist_ok=True)
    (main / ".ndf" / "review.json").write_text(json.dumps(body), encoding="utf-8")


# --- 今の振る舞いの退行テスト（受け入れ条件 2・6） -----------------------------------------------


class RequestsChanges(Fake):
    """指摘が 0 件なのに、判定の文字は REQUEST_CHANGES を返す相手役。"""

    def __call__(self, spec: agents.Launch) -> agents.AgentStatus:
        got = super().__call__(spec)
        spec.paths.out.write_text(json.dumps({"verdict": "REQUEST_CHANGES", "findings": []}) + "\n", encoding="utf-8")
        return got


def test_issue_2468_2_a_round_without_findings_converges_whatever_the_reviewers_verdict_says(
    wt: Path, tmp_path: Path, capsys, monkeypatch
) -> None:
    fake = RequestsChanges(tmp_path / "st", {})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"], got["metrics"]) == (0, "ok", {"rounds": 1, "findings": 0})
    assert _state(fake.state_dir)["rounds"][-1]["verdict"] == fin.CONVERGED
    assert sorted(fake.calls) == ["codex", "kiro"]


def test_issue_2468_6_a_version_1_declaration_without_limits_is_read_and_runs(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch
) -> None:
    _declare(repos.main, {"version": 1, "focus": ["重点"]})
    assert settings.read_settings(repos.main, wt).focus == ("重点",)
    code, got = _run(capsys, monkeypatch, Fake(tmp_path / "st", {}), wt)
    assert (code, got["status"]) == (0, "ok")


def test_a_rerun_after_convergence_reviews_the_added_diff(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """収束した後に差分を足して打ち直すと、上限の数が同じでもラウンドを回す。"""
    state_dir = tmp_path / "st"
    _run(capsys, monkeypatch, Fake(state_dir, {}), wt, "--max-rounds", "1")
    commit(repos.other, "doc.md", "# 設計\n\n## セクション C\n\n足した\n", "セクション C")
    _push_to_pr(repos)
    again = Fake(state_dir, {})
    code, got = _run(capsys, monkeypatch, again, wt, "--max-rounds", "1")
    assert (code, got["status"]) == (0, "ok")
    assert sorted(again.calls) == ["codex", "kiro"]
    state = _state(state_dir)
    assert state["pr"] == PR
    assert [r["verdict"] for r in state["rounds"]] == [fin.CONVERGED, fin.CONVERGED]
    assert state["rounds"][0]["head"] != state["rounds"][1]["head"]


# --- 上限の設定（.ndf/review.json の limits） ------------------------------------------------------


def _limits(limits: object) -> criteria_decide.ProjectLimits:
    return criteria_decide.project_limits_of({"version": 1, "focus": [], "limits": limits}, "r")


def test_issue_2468_project_limits_of_reads_the_declared_keys_and_leaves_the_rest_empty() -> None:
    assert _limits({"rounds": 4, "seconds": 1800.5, "usd": 5}) == criteria_decide.ProjectLimits(4, 1800.5, 5.0)
    rounds = _limits({"rounds": 4.0}).rounds
    assert (rounds, type(rounds)) == (4, int)
    assert _limits({"usd": 0.5}) == criteria_decide.ProjectLimits(usd=0.5)
    assert _limits({}) == criteria_decide.ProjectLimits()
    assert criteria_decide.project_limits_of({"version": 1, "focus": []}, "r") == criteria_decide.ProjectLimits()


@pytest.mark.parametrize(
    "limits",
    [
        None,
        [],
        {"round": 3},
        {"rounds": 0},
        {"rounds": 1.5},
        {"rounds": True},
        {"rounds": "3"},
        {"rounds": None},
        {"seconds": 0},
        {"seconds": -1},
        {"seconds": False},
        {"seconds": "3600"},
        {"seconds": float("inf")},
        {"usd": 0},
        {"usd": float("nan")},
        {"usd": 10**400},
    ],
)
def test_issue_2468_project_limits_of_a_wrong_shape_is_invalid(limits: object) -> None:
    with pytest.raises(criteria_decide.ReviewDeclInvalid, match="limits"):
        _limits(limits)


@pytest.mark.parametrize(
    "body",
    [None, {"version": 1, "focus": []}, {"version": 1, "focus": [], "limits": {}}],
)
def test_issue_2468_6_without_declared_limits_seconds_and_usd_take_the_defaults(tmp_path: Path, body: object) -> None:
    """上限を書かないプロジェクトは秒と費用だけに既定が入り、ラウンドには上限を置かない。"""
    if body is not None:
        _declare(tmp_path / "main", body)
    assert settings.read_settings(tmp_path / "main", tmp_path / "wt").limits == criteria_decide.ProjectLimits(None, 3600, 20)


def test_issue_2468_declared_limits_replace_the_defaults(tmp_path: Path) -> None:
    _declare(tmp_path / "main", {"version": 1, "focus": [], "limits": {"rounds": 2, "usd": 50}})
    assert settings.read_settings(tmp_path / "main", tmp_path / "wt").limits == criteria_decide.ProjectLimits(2, 3600, 50)


def test_issue_2468_wrong_limits_stop_the_run_before_any_launch(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    _declare(repos.main, {"version": 1, "focus": [], "limits": {"seconds": -1}})
    fake = Fake(tmp_path / "st", {})
    _, got = _run(capsys, monkeypatch, fake, wt)
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "invalid")
    assert fake.calls == []


@pytest.mark.parametrize(
    ("body", "valid"),
    [
        ({"version": 1, "focus": [], "limits": {"rounds": 4, "seconds": 3600, "usd": 20}}, True),
        ({"version": 1, "focus": [], "limits": {}}, True),
        ({"version": 1, "focus": [], "limits": {"rounds": 0}}, False),
        ({"version": 1, "focus": [], "limits": {"seconds": 0}}, False),
        ({"version": 1, "focus": [], "limits": {"minutes": 1}}, False),
        ({"version": 3, "focus": [], "limits": {"rounds": 4}}, False),
    ],
)
def test_issue_2468_the_settings_schema_takes_limits_only_in_version_1(body: dict, valid: bool) -> None:
    assert (contracts.schema_errors(body, "schemas/ndf-settings/review.schema.json") == []) is valid
