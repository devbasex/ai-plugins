"""`ndf launch-config show --question approve` が返す質問と結果の組み立て。候補の有無の両方を固定する。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import cli
from ndf.adapters import launch_approval as la
from ndf.adapters import launch_settings as ls
from ndf.contracts import result
from ndf.domains.hooks.guards import launch_answer
from ndf.domains.settings import launch_config_cli as lc

LSP = {"version": 1, "permissions": {"steps": {"*": {"load": ["LSP"]}}, "unsandboxed": ["codex"]}}
ARMS = {**LSP, "experiments": {"stages": {"impl": {"arms": {"claude": [{}, {"model": "sonnet", "effort": "medium"}]}}}}}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "wt"
    (root / ".git").mkdir(parents=True)
    (root / ".ndf").mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(la, "repo_key", lambda _root: "/r")
    monkeypatch.setattr(lc, "_approver_name", lambda _root: "人")
    monkeypatch.setattr(lc, "_reading_recordable", lambda _repo: True)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    for name in ("NDF_AI_CHILD", "NDF_FACTORY_RUN", "NDF_RELAY_PLAN", "NDF_ORGANIZER_DIR"):
        monkeypatch.delenv(name, raising=False)
    return root


def _run(capsys, *argv: str) -> dict:
    code = cli.main([lc.TOOL, *argv])
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result.errors(got, code) == []
    return got


def _candidate(capsys, content: object) -> Path:
    candidate = Path(_run(capsys, "draft")["metrics"]["candidate"])
    candidate.write_text(json.dumps(content), encoding="utf-8")
    return candidate


def _answer(question: dict, label: str) -> None:
    class Ctx:
        env: dict = {}
        tool_input = question
        session_id = "s1"
        payload = {"tool_response": {"answers": {question["questions"][0]["question"]: label}}, "tool_use_id": "toolu_1"}

    class Svc:
        state_dir = la.identity.load().state_dir

    launch_answer.check(Ctx, Svc)


def _expected_question(head: str, effect: str, target: ls.Target, changes: list, request: str) -> dict:
    text = "\n".join([head, *lc._target_lines(target), *lc._change_lines(changes), f"質問を見分けるリクエストの識別子: {request}"])
    options = [
        {"label": la.APPROVE_LABEL.format("人"), "description": effect},
        {"label": la.DENY_LABEL, "description": "記録も反映もしない。起動の設定は変わらない"},
    ]
    return {
        "questions": [{"question": text, "header": "起動の承認", "options": options, "multiSelect": False}],
        "metadata": {"source": "ndf-ask:redline:C7"},
    }


def _last_request() -> dict:
    return la.records.whole_rows(la.state_file(la.QUESTIONS))[-1]


def test_the_question_for_the_current_setting(repo, capsys):
    setting = repo / ".ndf" / "launch.json"
    setting.write_text(json.dumps(ARMS), encoding="utf-8")
    got = _run(capsys, "show", "--question", "approve")
    target = ls.approval_target(ls.load(repo))
    request = got["metrics"]["request"]
    question = _expected_question(
        f"この作業場所の {setting} にある、以下の起動時の許可を承認してよいですか（設定ファイルは変えません）",
        "この内容で起動してよい。承認を記録する。設定ファイルは変えない",
        target,
        [],
        request,
    )
    assert got["status"] == "gate" and "next" not in got
    assert got["items"] == [{"kind": "launch-target", **target.listing}, {"kind": "question", "input": question}]
    assert got["routes"]["rejected"] is None
    assert got["routes"]["approved"]["argv"][2:] == ["approve", "--sha256", target.sha256, "--request", request]
    assert got["metrics"] == {"sha256": target.sha256, "required": True, "approved": False, "cleanup_targets": [], "request": request}
    row = _last_request()
    assert (row["request_id"], row["sha256"], row["candidate"]) == (request, target.sha256, None)
    assert "experiments_sha" not in row


def test_the_question_for_a_candidate(repo, capsys):
    setting = repo / ".ndf" / "launch.json"
    candidate = _candidate(capsys, ARMS)
    got = _run(capsys, "show", "--candidate", str(candidate), "--question", "approve")
    target = ls.approval_target(ls.load(repo, candidate))
    changes = lc.experiment_changes(repo, candidate)
    request = got["metrics"]["request"]
    question = _expected_question(
        f"この作業場所の {setting} を候補の内容で置き換え、以下の起動時の許可を反映してよいですか",
        "この内容で起動してよい。承認を記録し、候補の内容で起動の設定を置き換える",
        target,
        changes,
        request,
    )
    assert changes and got["items"] == [
        {"kind": "launch-target", **target.listing},
        {"kind": "launch-experiments", "changes": changes},
        {"kind": "question", "input": question},
    ]
    assert got["routes"]["approved"]["argv"][2:] == [
        "approve", "--sha256", target.sha256, "--request", request, "--candidate", str(candidate),
    ]  # fmt: skip
    row = _last_request()
    assert (row["sha256"], row["candidate"]) == (target.sha256, str(candidate))
    assert row["experiments_sha"] == lc.experiments_sha(candidate.read_bytes())


def test_the_answer_to_the_current_setting_question_records_the_approval(repo, capsys):
    setting = repo / ".ndf" / "launch.json"
    setting.write_text(json.dumps(LSP), encoding="utf-8")
    got = _run(capsys, "show", "--question", "approve")
    _answer(got["items"][-1]["input"], la.APPROVE_LABEL.format("人"))
    assert _run(capsys, "show")["next"] == got["routes"]["approved"]
    assert _run(capsys, *got["routes"]["approved"]["argv"][2:])["status"] == "ok"
    assert _run(capsys, "show")["metrics"]["approved"]
    assert json.loads(setting.read_text()) == LSP


def test_a_new_question_also_carries_the_answered_continuation(repo, capsys):
    (repo / ".ndf" / "launch.json").write_text(json.dumps(LSP), encoding="utf-8")
    got = _run(capsys, "show", "--question", "approve")
    _answer(got["items"][-1]["input"], la.APPROVE_LABEL.format("人"))
    again = _run(capsys, "show", "--question", "approve")
    assert again["status"] == "gate" and again["next"] == got["routes"]["approved"]
    assert again["metrics"]["request"] != got["metrics"]["request"]


def test_the_terminal_command_for_the_current_setting_has_no_candidate(repo, capsys, monkeypatch):
    (repo / ".ndf" / "launch.json").write_text(json.dumps(LSP), encoding="utf-8")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    got = _run(capsys, "show", "--question", "approve")
    assert got["metrics"]["terminal"].endswith(f"launch-config approve --sha256 {got['metrics']['sha256']}")
    assert set(got["routes"]) == {"approved"} and got["routes"]["approved"]["argv"][2:] == ["show"]
    assert "reflected" not in got["metrics"]


def test_a_question_about_a_missing_candidate_stops_without_a_request(repo, capsys):
    candidate = _candidate(capsys, LSP)
    candidate.unlink()
    got = _run(capsys, "show", "--candidate", str(candidate), "--question", "approve")
    assert got["cause"]["kind"] == "precondition"
    assert not la.state_file(la.QUESTIONS).exists()
