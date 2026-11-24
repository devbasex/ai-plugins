"""現状固定テスト: `run` と `review-scope` が設定の解決で止まったときの結果（cli._ready・cli.cmd_scope。I-002）。

どちらのサブコマンドも、相手役と設定の解決（`cli._resolve`）が投げた例外を同じ終わり方へ写す。
- `ValueError`（名指しの誤り・設定の形の誤り）は invalid
- `participants.FixerNotAllowed`・`cli.PolicyUnresolved` は precondition
- `worktree.WorktreeUnregistered` は precondition で、items に worktree と .git の指す先を載せる
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ndf.contracts import result
from ndf.core.converge import participants, worktree
from ndf.domains.crossreview import cli

from ._repos import BRANCH, run_cli

PR = 41


def _argv(command: str, wt: Path, state_dir: Path, *extra: str) -> list[str]:
    if command == "run":
        return [
            "run",
            "--pr",
            str(PR),
            "--state-dir",
            str(state_dir),
            "--base",
            "origin/main",
            "--worktree",
            str(wt),
            "--branch",
            BRANCH,
            *extra,
        ]
    return ["review-scope", str(wt), "--pr", str(PR), "--state-dir", str(state_dir), "--base", "origin/main", "--purpose", "check", *extra]


def _no_launch(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """担当の起動を記録するだけの偽物を差し込む（準備で止まれば空のまま）。"""
    calls: list[Any] = []
    monkeypatch.setattr(cli, "LAUNCH", lambda spec: calls.append(spec))
    return calls


def _shape(got: dict) -> dict:
    """結果のうち、準備で止まったときに比べる部分。"""
    return {"status": got["status"], "kind": got["cause"]["kind"], "detail": got["cause"]["detail"], "items": got.get("items", [])}


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (ValueError("設定の形が違う"), "invalid"),
        (participants.UnknownParticipant("相手役 nobody は無い"), "invalid"),
        (participants.FixerNotAllowed("修正担当 claude が allowed に無い"), "precondition"),
        (cli.PolicyUnresolved("メインディレクトリを決められない"), "precondition"),
    ],
)
@pytest.mark.parametrize("command", ["run", "review-scope"])
def test_resolve_errors_map_to_the_same_stop_in_run_and_scope(
    wt: Path, tmp_path: Path, capsys, monkeypatch, command: str, error: Exception, kind: str
) -> None:
    def broken(path: Path, requested: Any) -> cli.Resolved:
        raise error

    monkeypatch.setattr(cli, "_resolve", broken)
    calls = _no_launch(monkeypatch)
    code, got = run_cli(capsys, *_argv(command, wt, tmp_path / "st"))
    assert result.errors(got, code) == []
    assert code != 0
    assert _shape(got) == {"status": "stopped", "kind": kind, "detail": str(error), "items": []}
    assert calls == []


@pytest.mark.parametrize("command", ["run", "review-scope"])
def test_unregistered_worktree_from_resolve_carries_the_path_in_run_and_scope(
    wt: Path, tmp_path: Path, capsys, monkeypatch, command: str
) -> None:
    """items の path は例外の worktree（引数の worktree ではない）、points_to は .git の指す先。"""
    gone = tmp_path / "gone"
    points = tmp_path / "gitdir"

    def broken(path: Path, requested: Any) -> cli.Resolved:
        raise worktree.WorktreeUnregistered(gone, points)

    monkeypatch.setattr(cli, "_resolve", broken)
    calls = _no_launch(monkeypatch)
    code, got = run_cli(capsys, *_argv(command, wt, tmp_path / "st"))
    assert result.errors(got, code) == []
    assert code != 0
    detail = str(worktree.WorktreeUnregistered(gone, points))
    assert _shape(got) == {
        "status": "stopped",
        "kind": "precondition",
        "detail": detail,
        "items": [{"kind": "worktree", "path": str(gone), "points_to": str(points)}],
    }
    assert calls == []


@pytest.mark.parametrize("command", ["run", "review-scope"])
def test_unknown_reviewer_is_invalid_in_run_and_scope(wt: Path, tmp_path: Path, capsys, monkeypatch, command: str) -> None:
    """本物の解決で、ランタイムの表に無い相手役の名指しはどちらも invalid で同じ説明になる。"""
    calls = _no_launch(monkeypatch)
    code, got = run_cli(capsys, *_argv(command, wt, tmp_path / "st", "--reviewer", "nobody"))
    assert result.errors(got, code) == []
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "invalid")
    assert "nobody" in got["cause"]["detail"] and got.get("items", []) == []
    assert calls == []


def test_unknown_reviewer_detail_is_the_same_in_run_and_scope(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    _no_launch(monkeypatch)
    details = []
    for command in ("run", "review-scope"):
        _, got = run_cli(capsys, *_argv(command, wt, tmp_path / "st", "--reviewer", "nobody"))
        details.append(_shape(got))
    assert details[0] == details[1]


@pytest.mark.parametrize("command", ["run", "review-scope"])
def test_unregistered_worktree_stops_with_its_path_in_run_and_scope(wt: Path, tmp_path: Path, capsys, monkeypatch, command: str) -> None:
    """本物の解決で、管理情報の消えた worktree はどちらも precondition で、items に渡した worktree のパスを載せる。"""
    gitdir = Path((wt / ".git").read_text().split(":", 1)[1].strip())
    (gitdir / "HEAD").unlink()
    calls = _no_launch(monkeypatch)
    code, got = run_cli(capsys, *_argv(command, wt, tmp_path / "st"))
    assert result.errors(got, code) == []
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "precondition")
    assert [i["kind"] for i in got["items"]] == ["worktree"] and got["items"][0]["path"] == str(wt)
    assert str(wt) in got["cause"]["detail"]
    assert calls == []


def test_fixer_outside_allowed_is_precondition_in_run_and_scope(repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """本物の解決で、メインディレクトリの allowed に修正担当が無ければ、どちらも precondition で同じ説明になる。"""
    (repos.main / ".ndf").mkdir(exist_ok=True)
    (repos.main / ".ndf" / "runtimes.json").write_text('{"allowed": ["codex", "kiro"]}', encoding="utf-8")
    calls = _no_launch(monkeypatch)
    shapes = []
    for command in ("run", "review-scope"):
        code, got = run_cli(capsys, *_argv(command, wt, tmp_path / "st"))
        assert result.errors(got, code) == []
        shapes.append(_shape(got))
    assert shapes[0] == shapes[1]
    assert (shapes[0]["status"], shapes[0]["kind"]) == ("stopped", "precondition") and "allowed" in shapes[0]["detail"]
    assert calls == []
