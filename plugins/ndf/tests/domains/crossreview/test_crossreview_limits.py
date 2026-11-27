"""cross-review の上限（#2468）と、上限を足す前から守る振る舞い。相手役と修正担当は偽物、git は一時の origin で本物を打つ
（`test_crossreview_run` の形）。
"""

from __future__ import annotations

import json
from pathlib import Path

from ndf.adapters import agents
from ndf.domains.crossreview import settings
from ndf.domains.crossreview import finish_decide as fin

from ._repos import BRANCH, Repos, commit, sh
from .test_crossreview_run import PR, Fake, _push_to_pr, _run, _state


def _declare(repos: Repos, body: dict) -> None:
    """実行者のメインディレクトリの `.ndf/review.json` を書く。"""
    (repos.main / ".ndf").mkdir(exist_ok=True)
    (repos.main / ".ndf" / "review.json").write_text(json.dumps(body), encoding="utf-8")


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
    _declare(repos, {"version": 1, "focus": ["重点"]})
    assert settings.read_settings(repos.main, wt).focus == ("重点",)
    code, got = _run(capsys, monkeypatch, Fake(tmp_path / "st", {}), wt)
    assert (code, got["status"]) == (0, "ok")


def test_a_rerun_after_convergence_reviews_the_added_diff(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """収束した後に差分を足して打ち直すと、上限の数が同じでもラウンドを回す。"""
    state_dir = tmp_path / "st"
    _run(capsys, monkeypatch, Fake(state_dir, {}), wt, "--max-rounds", "1")
    sh(repos.other, "pull", "-q", "--rebase", "origin", BRANCH)
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
