"""安全装置を外す指定（`launch_settings.safety_args`）の判定表の各行（#1959 の受け入れ条件 13）。

名前空間を作れるかは `agents.user_namespaces` を差し替え、リポジトリのキーは git を打たずに根のパスにする。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.adapters import agents
from ndf.adapters import launch_settings as ls
from ndf.contracts import internal

CODEX_OFF = ["--dangerously-bypass-approvals-and-sandbox"]


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setattr(ls.launch_approval, "repo_key", lambda r: str(r))
    return root


def _namespaces(monkeypatch, can: bool) -> list[int]:
    """名前空間を作れるかを `can` に決め、確かめた回数を返す並びを渡す。"""
    seen: list[int] = []

    def made() -> bool:
        seen.append(1)
        return can

    monkeypatch.setattr(agents, "user_namespaces", made)
    return seen


def _unsandboxed(root: Path, runtimes: list[str], approve: bool = True) -> None:
    path = root / ".ndf" / "launch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "permissions": {"unsandboxed": runtimes}}), encoding="utf-8")
    if approve:
        sha = ls.approval_target(ls.load(root)).sha256
        ls.launch_approval.append(str(root), sha, ls.launch_approval.Action.APPROVE, approver="人")


def _stops(runtime: str, use: str, cwd: Path) -> str:
    with pytest.raises(internal.InternalError) as got:
        ls.safety_args(runtime, use, cwd)
    assert got.value.kind == "precondition"
    return str(got.value)


@pytest.mark.parametrize("use", ["review", "delegate"])
@pytest.mark.parametrize("can", [True, False])
def test_claude_gets_no_arguments_either_way(repo: Path, monkeypatch, use: str, can: bool) -> None:
    _namespaces(monkeypatch, can)
    got = ls.safety_args("claude", use, repo)
    assert (got.args, got.verify) == ([], None)


@pytest.mark.parametrize("approve", [True, False])
def test_agy_review_stops_even_with_an_approval(repo: Path, monkeypatch, approve: bool) -> None:
    _namespaces(monkeypatch, False)
    _unsandboxed(repo, ["agy"], approve)
    assert "agy" in _stops("agy", "review", repo)


@pytest.mark.parametrize("approve", [True, False])
@pytest.mark.parametrize("can", [True, False])
def test_kiro_review_keeps_the_read_only_trust_whatever_the_approval(repo: Path, monkeypatch, approve: bool, can: bool) -> None:
    """kiro の review は承認の有無に関わらず読むだけの指定。委譲の停止の規則をレビューに当てない。"""
    _namespaces(monkeypatch, can)
    _unsandboxed(repo, ["kiro"], approve)
    got = ls.safety_args("kiro", "review", repo)
    assert (got.args, got.verify) == (["--trust-tools=fs_read"], None)


@pytest.mark.parametrize(("use", "args"), [("review", ["-s", "read-only"]), ("delegate", ["-s", "workspace-write"])])
def test_codex_uses_its_sandbox_when_namespaces_can_be_made_even_with_an_approval(repo: Path, monkeypatch, use: str, args) -> None:
    """承認は「サンドボックスが使えない環境で外してよい」なので、使える環境では承認があっても外さない。"""
    _namespaces(monkeypatch, True)
    _unsandboxed(repo, ["codex"])
    got = ls.safety_args("codex", use, repo)
    assert (got.args, got.verify) == (args, None)


@pytest.mark.parametrize("use", ["review", "delegate"])
def test_codex_without_namespaces_takes_the_unsandboxed_arguments_only_when_approved(repo: Path, monkeypatch, use: str) -> None:
    _namespaces(monkeypatch, False)
    _unsandboxed(repo, ["codex"])
    got = ls.safety_args("codex", use, repo)
    assert got.args == CODEX_OFF and got.verify is not None and got.verify() is None


@pytest.mark.parametrize(("runtime", "args"), [("kiro", ["--trust-all-tools"]), ("agy", ["--dangerously-skip-permissions"])])
def test_kiro_and_agy_delegate_take_the_unsandboxed_arguments_when_approved_without_looking_at_namespaces(
    repo: Path, monkeypatch, runtime: str, args
) -> None:
    seen = _namespaces(monkeypatch, True)
    _unsandboxed(repo, [runtime])
    got = ls.safety_args(runtime, "delegate", repo)
    assert got.args == args and got.verify is not None and seen == []


@pytest.mark.parametrize(
    ("runtime", "use", "listed", "approve"),
    [
        ("codex", "review", [], False),  # 設定が無い
        ("codex", "delegate", ["codex"], False),  # 書いたが承認していない
        ("codex", "delegate", ["kiro"], True),  # ほかのランタイムだけを承認した
        ("kiro", "delegate", ["codex"], True),
        ("agy", "delegate", ["agy"], False),
    ],
)
def test_without_an_approval_for_the_runtime_it_stops_with_how_to_approve(
    repo: Path, monkeypatch, runtime: str, use: str, listed: list[str], approve: bool
) -> None:
    _namespaces(monkeypatch, False)
    if listed:
        _unsandboxed(repo, listed, approve)
    text = _stops(runtime, use, repo)
    assert "launch-config" in text and "unsandboxed" in text


def test_a_revoke_after_the_arguments_makes_the_check_before_the_launch_return_a_reason(repo: Path, monkeypatch) -> None:
    _namespaces(monkeypatch, False)
    _unsandboxed(repo, ["codex"])
    got = ls.safety_args("codex", "delegate", repo)
    sha = ls.approval_target(ls.load(repo)).sha256
    ls.launch_approval.append(str(repo), sha, ls.launch_approval.Action.REVOKE)
    reason = got.verify()
    assert reason is not None and "launch-config" in reason


def test_an_unknown_use_stops(repo: Path) -> None:
    assert "use" in _stops("codex", "write", repo)


def test_given_settings_are_used_without_reading_the_file_again_but_the_check_before_the_launch_reads_it(repo: Path, monkeypatch) -> None:
    """渡された設定で判定し、照らし直しは使い回さずに読み直す（引数の後に外した codex で安全装置を外さない）。"""
    _namespaces(monkeypatch, False)
    _unsandboxed(repo, ["codex"])
    settings = ls.load(repo)
    real = ls.load
    seen: list[Path] = []
    monkeypatch.setattr(ls, "load", lambda *a, **kw: seen.append(a[0]) or real(*a, **kw))
    got = ls.safety_args("codex", "delegate", repo, settings=settings)
    assert got.args == CODEX_OFF and seen == []
    _unsandboxed(repo, [], approve=False)
    reason = got.verify()
    assert reason is not None and len(seen) == 1
