"""サブコマンド `ndf launch-config`（#1959 の受け入れ条件 9・10・14）。git と端末は差し替え、子プロセスを起動しない。"""

from __future__ import annotations

import json
import os
import shlex
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


def _typed(monkeypatch, sha: str, name: str = "山田") -> None:
    """端末で、承認者の名前とハッシュの先頭 8 文字を打つ。"""
    monkeypatch.setattr(lc, "_terminal", lambda: True)
    typed = iter([name, sha[:8]])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(typed))


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


def test_an_empty_candidate_is_reflected_without_an_approval_row_only_after_a_human_confirmation(repo, capsys, monkeypatch):
    """承認の対象が空でも、設定の書き換えには毎回人の確認が要る（C7）。端末では反映先のパスを示す。"""
    target = repo / ".ndf" / "launch.json"
    candidate = _draft(capsys, EMPTY)
    sha = _run(capsys, "show", "--candidate", str(candidate))["metrics"]["sha256"]
    assert _run(capsys, "approve", "--sha256", sha, "--candidate", str(candidate))["cause"]["kind"] == "refused"  # 端末でない
    assert not target.exists()
    _typed(monkeypatch, sha)
    code = cli.main([lc.TOOL, "approve", "--sha256", sha, "--candidate", str(candidate)])
    out = capsys.readouterr()
    assert code == 0 and str(target) in out.err
    assert json.loads(target.read_text()) == EMPTY
    assert _approve_rows() == []
    assert _run(capsys, "approve", "--sha256", sha, "--candidate", str(candidate))["status"] == "ok"  # 反映済みの打ち直し


def test_an_approved_candidate_is_not_reflected_without_a_human_confirmation(repo, capsys):
    candidate = _draft(capsys)
    sha = _run(capsys, "show", "--candidate", str(candidate))["metrics"]["sha256"]
    la.append("/r", sha, la.Action.APPROVE, approver="人")
    assert _run(capsys, "approve", "--sha256", sha, "--candidate", str(candidate))["cause"]["kind"] == "refused"
    assert not (repo / ".ndf" / "launch.json").exists()


def test_an_empty_candidate_is_reflected_by_the_answer(repo, capsys):
    candidate = _draft(capsys, EMPTY)
    _, question, approve = _ask(capsys, candidate)
    assert _run(capsys, *approve)["cause"]["kind"] == "refused"  # 未回答
    _answer(question, la.APPROVE_LABEL.format("人"))
    assert _run(capsys, *approve)["status"] == "ok"
    assert json.loads((repo / ".ndf" / "launch.json").read_text()) == EMPTY and _approve_rows() == []


def _elsewhere(capsys, monkeypatch, repo: Path) -> Path:
    """別の作業場所（同じリポジトリのキー）で作った候補。"""
    other = repo.parent / "other"
    (other / ".git").mkdir(parents=True)
    monkeypatch.chdir(other)
    candidate = _draft(capsys)
    monkeypatch.chdir(repo)
    return candidate


def test_a_candidate_of_another_worktree_is_neither_asked_nor_reflected(repo, capsys, monkeypatch):
    candidate = _elsewhere(capsys, monkeypatch, repo)
    asked = _run(capsys, "show", "--candidate", str(candidate), "--question", "approve")
    assert asked["cause"]["kind"] == "refused" and not la.state_file(la.QUESTIONS).exists()
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")  # 端末のコマンドを返す会話でも拒む
    assert _run(capsys, "show", "--candidate", str(candidate), "--question", "approve")["cause"]["kind"] == "refused"
    sha = _run(capsys, "show", "--candidate", str(candidate))["metrics"]["sha256"]
    _typed(monkeypatch, sha)
    assert _run(capsys, "approve", "--sha256", sha, "--candidate", str(candidate))["cause"]["kind"] == "refused"
    assert not (repo / ".ndf" / "launch.json").exists() and not (repo.parent / "other" / ".ndf" / "launch.json").exists()
    assert _approve_rows() == []


def test_the_answer_to_another_candidate_does_not_reflect_this_candidate(repo, capsys):
    asked = _draft(capsys)
    other = _draft(capsys)  # 同じ中身の別の候補
    _, question, approve = _ask(capsys, asked)
    _answer(question, la.APPROVE_LABEL.format("人"))
    i = approve.index("--candidate")
    assert _run(capsys, *approve[: i + 1], str(other))["cause"]["kind"] == "refused"
    assert not (repo / ".ndf" / "launch.json").exists() and _approve_rows() == []


def test_a_candidate_changed_after_the_hash_check_is_not_reflected(repo, capsys, monkeypatch):
    target = repo / ".ndf" / "launch.json"
    candidate = _draft(capsys, EMPTY)
    sha = _run(capsys, "show", "--candidate", str(candidate))["metrics"]["sha256"]
    _typed(monkeypatch, sha)
    reflect = la.reflect

    def changed_first(cand, *args, **kwargs):
        cand.write_text(json.dumps(LSP), encoding="utf-8")  # ハッシュを照らした後、反映の前に候補を書き換える
        return reflect(cand, *args, **kwargs)

    monkeypatch.setattr(la, "reflect", changed_first)
    assert _run(capsys, "approve", "--sha256", sha, "--candidate", str(candidate))["cause"]["kind"] == "refused"
    assert not target.exists()


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


def test_without_a_github_remote_show_gives_the_terminal_command(repo, capsys, monkeypatch):
    candidate = _draft(capsys)
    monkeypatch.setattr(lc, "_reading_recordable", lambda _repo: False)  # 読み手役の確認を記録できない
    got = _run(capsys, "show", "--candidate", str(candidate), "--question", "approve")
    assert "terminal" in got["metrics"] and not la.state_file(la.QUESTIONS).exists()


def test_the_terminal_route_with_a_candidate_can_be_confirmed_after_the_reflection(repo, capsys, monkeypatch):
    sub = repo / "sub"
    sub.mkdir()
    candidate = _draft(capsys)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    monkeypatch.chdir(sub)
    got = _run(capsys, "show", "--candidate", os.path.relpath(candidate, sub), "--question", "approve")
    assert shlex.quote(str(candidate)) in got["metrics"]["terminal"]  # 根へ cd しても同じ候補を指す絶対パス
    monkeypatch.setattr(lc, "_terminal", lambda: True)
    typed = iter(["山田", got["metrics"]["sha256"][:8]])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(typed))
    assert _run(capsys, "approve", "--sha256", got["metrics"]["sha256"], "--candidate", str(candidate))["status"] == "ok"
    confirmed = _run(capsys, *got["routes"]["approved"]["argv"][2:])
    assert confirmed["status"] == "ok" and confirmed["metrics"]["reflected"]
    assert got["metrics"]["required"] and confirmed["metrics"]["approved"]


def test_an_unreflected_candidate_with_the_same_hash_as_no_setting_is_not_reported_as_reflected(repo, capsys, monkeypatch):
    """許可の指定が無い候補は、設定が無い状態と承認の対象のハッシュが同じになる。反映したかは完了の記録で決める。"""
    candidate = _draft(capsys, {"version": 1})
    shown = _run(capsys, "show", "--candidate", str(candidate))
    assert shown["metrics"]["reflected"] is False and not (repo / ".ndf" / "launch.json").exists()
    _typed(monkeypatch, shown["metrics"]["sha256"])
    assert _run(capsys, "approve", "--sha256", shown["metrics"]["sha256"], "--candidate", str(candidate))["status"] == "ok"
    assert _run(capsys, "show", "--candidate", str(candidate))["metrics"]["reflected"]


def test_a_candidate_edited_after_the_reflection_is_not_reported_as_reflected(repo, capsys, monkeypatch):
    monkeypatch.setattr(la.launch_candidates, "_settleable", lambda: False)  # 候補を退避できず、反映の後も残る環境
    candidate = _draft(capsys, EMPTY)
    sha = _run(capsys, "show", "--candidate", str(candidate))["metrics"]["sha256"]
    _typed(monkeypatch, sha)
    assert _run(capsys, "approve", "--sha256", sha, "--candidate", str(candidate))["status"] == "ok"
    assert candidate.exists() and _run(capsys, "show", "--candidate", str(candidate))["metrics"]["reflected"]
    candidate.write_text(json.dumps(LSP), encoding="utf-8")  # 反映の後に直し直した
    shown = _run(capsys, "show", "--candidate", str(candidate))
    assert shown["metrics"]["reflected"] is False and shown["metrics"]["required"]


def test_approving_a_candidate_equal_to_the_hand_written_setting_records_the_approval(repo, capsys, monkeypatch):
    _settings(repo, LSP)  # 利用者が手で書いた、未承認の許可
    candidate = _draft(capsys, LSP)
    sha = _run(capsys, "show", "--candidate", str(candidate))["metrics"]["sha256"]
    monkeypatch.setattr(lc, "_terminal", lambda: True)
    typed = iter(["山田", sha[:8]])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(typed))
    assert _run(capsys, "approve", "--sha256", sha, "--candidate", str(candidate))["status"] == "ok"
    assert _run(capsys, "show")["metrics"]["approved"]


def test_a_revoke_before_the_lock_stops_the_reflection(repo, capsys, monkeypatch):
    candidate = _draft(capsys)
    _, question, approve = _ask(capsys, candidate)
    sha = approve[approve.index("--sha256") + 1]
    la.append("/r", sha, la.Action.APPROVE, approver="人")
    _answer(question, la.APPROVE_LABEL.format("人"))
    reflect = la.reflect

    def revoked_first(*args, **kwargs):
        la.append("/r", sha, la.Action.REVOKE)  # 承認済みと見た後、錠を取る前の取り消し
        return reflect(*args, **kwargs)

    monkeypatch.setattr(la, "reflect", revoked_first)
    assert _run(capsys, *approve)["cause"]["kind"] == "refused"
    assert not (repo / ".ndf" / "launch.json").exists()


def test_show_returns_the_continuation_even_when_the_setting_is_broken(repo, capsys):
    _settings(repo, "{broken")
    candidate = _draft(capsys)  # 壊れた設定を直す候補
    got, question, _ = _ask(capsys, candidate)
    _answer(question, la.APPROVE_LABEL.format("人"))
    shown = _run(capsys, "show")
    assert shown["cause"]["kind"] == "precondition" and shown["next"] == got["routes"]["approved"]


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


@pytest.mark.parametrize("name", ["NDF_FACTORY_RUN", "NDF_RELAY_PLAN", "NDF_ORGANIZER_DIR"])
def test_revoke_is_refused_where_answers_are_not_recorded(repo, capsys, monkeypatch, name):
    _run(capsys, "show")  # 最初のコマンドが状態の保存先を用意する（それより前に書いた記録は退避される）
    la.append("/r", "a" * 64, la.Action.APPROVE, approver="人")
    monkeypatch.setenv(name, "1")
    assert _run(capsys, "revoke", "--sha256", "a" * 64)["cause"]["kind"] == "refused"
    assert la.is_approved("/r", "a" * 64)


def test_a_malformed_hash_is_invalid(repo, capsys):
    assert _run(capsys, "revoke", "--sha256", "ABC")["cause"]["kind"] == "invalid"


def test_revoke_adds_a_row(repo, capsys):
    _run(capsys, "show")  # 最初のコマンドが状態の保存先を用意する（それより前に書いた記録は退避される）
    la.append("/r", "a" * 64, la.Action.APPROVE, approver="人")
    assert la.is_approved("/r", "a" * 64)
    assert _run(capsys, "revoke", "--sha256", "a" * 64)["status"] == "ok"
    assert not la.is_approved("/r", "a" * 64)


# --- #2944 の r1-1: 確認の画面に experiments の変更を載せる ---------------------------------------------

ARMS = {"version": 1, "experiments": {"stages": {"impl": {"arms": {"claude": [{}, {"model": "sonnet", "effort": "medium"}]}}}}}


def test_the_question_and_the_items_show_the_model_and_effort_changes(repo, capsys):
    _settings(repo, {"version": 1, "experiments": {"enabled": True}})
    got, question, _ = _ask(capsys, _draft(capsys, ARMS))
    (changes,) = [i["changes"] for i in got["items"] if i["kind"] == "launch-experiments"]
    assert changes == [
        {"path": "experiments/enabled", "before": True},
        {"path": "experiments/stages/impl/arms/claude", "after": ARMS["experiments"]["stages"]["impl"]["arms"]["claude"]},
    ]
    # 質問の文は変わる工程と要点だけ（選択肢の全体は items に任せる）
    text = question["questions"][0]["question"]
    assert "experiments/stages/impl/arms/claude: 書かない（同梱の値） → 2 組の選択肢" in text and "sonnet" not in text


def test_the_terminal_confirmation_shows_the_changes(repo, capsys, monkeypatch):
    candidate = _draft(capsys, ARMS)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    got = _run(capsys, "show", "--candidate", str(candidate), "--question", "approve")
    assert any(i["kind"] == "launch-experiments" for i in got["items"])
    _typed(monkeypatch, got["metrics"]["sha256"])
    shown: list[str] = []
    monkeypatch.setattr(lc, "print", lambda text, **kw: shown.append(text), raising=False)
    assert _run(capsys, "approve", "--sha256", got["metrics"]["sha256"], "--candidate", str(candidate))["status"] == "ok"
    assert any("モデルと effort の変更 experiments/stages/impl/arms/claude" in t and "sonnet" in t for t in shown)


def test_no_experiment_item_without_a_change(repo, capsys):
    got, _, _ = _ask(capsys, _draft(capsys, LSP))
    assert not any(i["kind"] == "launch-experiments" for i in got["items"])
