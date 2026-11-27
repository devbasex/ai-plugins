"""サブコマンド `ndf launch-config`（#1959 の受け入れ条件 9・10・14）。git と端末は差し替え、子プロセスを起動しない。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import cli
from ndf.adapters import launch_approval as la
from ndf.contracts import result
from ndf.domains.hooks.guards import launch_answer
from ndf.domains.settings import launch_config_cli as lc

LSP = {"version": 1, "permissions": {"steps": {"*": {"load": ["LSP"]}}, "unsandboxed": ["codex"]}}
EMPTY = {"version": 1, "permissions": {"steps": {}}}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "wt"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    monkeypatch.setattr(la, "repo_key", lambda _root: "/r")
    monkeypatch.setattr(lc, "_approver_name", lambda _root: "人")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    for name in ("NDF_AI_CHILD", "NDF_FACTORY_RUN", "NDF_RELAY_PLAN", "NDF_ORGANIZER_DIR"):
        monkeypatch.delenv(name, raising=False)
    return root


def _run(capsys, *argv: str) -> dict:
    code = cli.main([lc.TOOL, *argv])
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result.errors(got, code) == []
    return got


def _settings(repo: Path, content: object) -> Path:
    target = repo / ".ndf" / "launch.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return target


def _draft(capsys, content=LSP) -> Path:
    got = _run(capsys, "draft")
    assert got["metrics"]["cleanup_targets"] == []
    candidate = Path(got["metrics"]["candidate"])
    candidate.write_text(json.dumps(content), encoding="utf-8")
    return candidate


def _answer(question: dict, label: str) -> None:
    """hook が受ける回答の PostToolUse を、guard へそのまま渡す。"""

    class Ctx:
        env: dict = {}
        tool_input = question
        session_id = "s1"
        payload = {"tool_response": {"answers": {question["questions"][0]["question"]: label}}, "tool_use_id": "toolu_1"}

    class Svc:
        state_dir = la.identity.load().state_dir

    launch_answer.check(Ctx, Svc)


def _ask(capsys, candidate: Path) -> tuple[dict, dict, list[str]]:
    got = _run(capsys, "show", "--candidate", str(candidate), "--question", "approve")
    question = next(i["input"] for i in got["items"] if i["kind"] == "question")
    return got, question, got["routes"]["approved"]["argv"][2:]


def _approve_rows() -> list[dict]:
    return [r for r in la.records.whole_rows(la.state_file(la.APPROVALS)) if r["action"] == "approve"]


def test_the_candidate_is_reflected_only_after_the_answer_and_the_retry_succeeds(repo, capsys):
    target = repo / ".ndf" / "launch.json"
    candidate = _draft(capsys)
    got, question, approve = _ask(capsys, candidate)
    assert got["status"] == "gate" and not target.exists()
    assert question["metadata"] == {"source": "ndf-ask:redline:C7"}
    assert lc.WARNING in question["questions"][0]["question"]  # unsandboxed の警告の文は質問の中にある
    assert _run(capsys, *approve)["cause"]["kind"] == "refused"  # 未回答
    _answer(question, la.APPROVE_LABEL.format("人"))
    assert _run(capsys, *approve)["status"] == "ok"
    assert json.loads(target.read_text()) == LSP
    shown = _run(capsys, "show")
    assert shown["metrics"]["approved"] and "next" not in shown
    assert _run(capsys, *approve)["status"] == "ok"  # 候補を消した後の打ち直しは行を足さずに成功する
    assert len(_approve_rows()) == 1


def test_a_denied_answer_leaves_the_settings_unchanged(repo, capsys):
    candidate = _draft(capsys)
    _, question, approve = _ask(capsys, candidate)
    _answer(question, la.DENY_LABEL)
    assert _run(capsys, *approve)["cause"]["kind"] == "refused"
    assert not (repo / ".ndf" / "launch.json").exists()


def test_a_denied_answer_also_stops_an_empty_candidate(repo, capsys):
    candidate = _draft(capsys, EMPTY)
    _, question, approve = _ask(capsys, candidate)
    _answer(question, la.DENY_LABEL)
    assert _run(capsys, *approve)["cause"]["kind"] == "refused"
    assert not (repo / ".ndf" / "launch.json").exists()


def test_an_empty_candidate_is_reflected_without_an_approval_row(repo, capsys):
    candidate = _draft(capsys, EMPTY)
    sha = _run(capsys, "show", "--candidate", str(candidate))["metrics"]["sha256"]
    assert _run(capsys, "approve", "--sha256", sha, "--candidate", str(candidate))["status"] == "ok"
    assert json.loads((repo / ".ndf" / "launch.json").read_text()) == EMPTY
    assert _approve_rows() == []
    assert _run(capsys, "approve", "--sha256", sha, "--candidate", str(candidate))["status"] == "ok"


def test_show_returns_the_continuation_of_an_answered_request(repo, capsys):
    candidate = _draft(capsys)
    got, question, _ = _ask(capsys, candidate)
    _answer(question, la.APPROVE_LABEL.format("人"))
    assert _run(capsys, "show")["next"] == got["routes"]["approved"]


def test_a_target_changed_after_the_draft_is_not_overwritten(repo, capsys):
    target = _settings(repo, EMPTY)
    candidate = _draft(capsys)
    _, question, approve = _ask(capsys, candidate)
    _answer(question, la.APPROVE_LABEL.format("人"))
    target.write_text(json.dumps({"version": 1}), encoding="utf-8")
    assert _run(capsys, *approve)["cause"]["kind"] == "refused"
    assert json.loads(target.read_text()) == {"version": 1}


@pytest.mark.parametrize("name", ["NDF_AI_CHILD", "NDF_FACTORY_RUN", "NDF_RELAY_PLAN", "NDF_ORGANIZER_DIR", "CLAUDE_CODE_SESSION_ID"])
def test_where_answers_are_not_recorded_show_gives_the_terminal_command(repo, capsys, monkeypatch, name):
    candidate = _draft(capsys)
    if name == "CLAUDE_CODE_SESSION_ID":
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, "1")
    got = _run(capsys, "show", "--candidate", str(candidate), "--question", "approve")
    assert got["status"] == "gate" and got["routes"]["approved"]["argv"][2] == "show"
    assert "launch-config approve --sha256 " + got["metrics"]["sha256"] in got["metrics"]["terminal"]
    assert not any(i["kind"] == "question" for i in got["items"])
    assert not la.state_file(la.QUESTIONS).exists()


def test_a_broken_setting_stops_show_but_a_candidate_can_still_be_shown(repo, capsys):
    _settings(repo, "{broken")
    assert _run(capsys, "show")["cause"]["kind"] == "precondition"
    candidate = _draft(capsys, EMPTY)
    assert _run(capsys, "show", "--candidate", str(candidate))["status"] == "ok"


def test_show_of_a_missing_candidate_stops(repo, capsys):
    candidate = _draft(capsys)
    candidate.unlink()
    assert _run(capsys, "show", "--candidate", str(candidate))["cause"]["kind"] == "precondition"


def test_approve_without_a_human_confirmation_or_with_another_hash_is_refused(repo, capsys, monkeypatch):
    target = _settings(repo, LSP)
    sha = _run(capsys, "show")["metrics"]["sha256"]
    assert _run(capsys, "approve", "--sha256", sha)["cause"]["kind"] == "refused"  # 端末でない
    assert _run(capsys, "approve", "--sha256", "b" * 64)["cause"]["kind"] == "refused"
    monkeypatch.setattr(lc, "_terminal", lambda: True)
    typed = iter(["山田", "00000000"])  # ハッシュの打ち間違い
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(typed))
    assert _run(capsys, "approve", "--sha256", sha)["cause"]["kind"] == "refused"
    assert not la.state_file(la.APPROVALS).exists()
    assert json.loads(target.read_text()) == LSP


def test_approve_on_a_terminal_records_the_typed_name(repo, capsys, monkeypatch):
    _settings(repo, LSP)
    sha = _run(capsys, "show")["metrics"]["sha256"]
    monkeypatch.setattr(lc, "_terminal", lambda: True)
    typed = iter(["山田", sha[:8]])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(typed))
    assert _run(capsys, "approve", "--sha256", sha)["status"] == "ok"
    assert _approve_rows()[-1]["approver"] == "山田"
    assert _run(capsys, "show")["metrics"]["approved"]


@pytest.mark.parametrize("command", ["draft", "approve", "revoke"])
def test_inside_an_ai_started_by_ndf_draft_approve_and_revoke_are_refused(repo, capsys, monkeypatch, command):
    monkeypatch.setenv("NDF_AI_CHILD", "1")
    args = [] if command == "draft" else ["--sha256", "a" * 64]
    assert _run(capsys, command, *args)["cause"]["kind"] == "refused"
    assert not la.state_file(la.APPROVALS).exists()


def test_a_malformed_hash_is_invalid(repo, capsys):
    assert _run(capsys, "revoke", "--sha256", "ABC")["cause"]["kind"] == "invalid"


def test_revoke_adds_a_row(repo, capsys):
    la.append("/r", "a" * 64, la.Action.APPROVE, approver="人")
    assert _run(capsys, "revoke", "--sha256", "a" * 64)["status"] == "ok"
    assert not la.is_approved("/r", "a" * 64)
