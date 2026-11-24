"""承認と判断の質問の前に、読み手役が確認した記録を調べる guard（reading-check）。#4285 の受け入れ条件 17・18・19。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Optional

import git_sandbox
import pytest

from ndf.domains.hooks import context, dispatch, tables
from ndf.domains.hooks.counter import Counter
from ndf.domains.hooks.guards import reading_check as rc
from ndf.domains.records import reading
from ndf.domains.records import reading_decide as rd
from ndf.domains.relay import signals

from ._hooks import advise_text, call_entry, deny_reason, make_repo, records, sample

HEAD, MOVED = "a" * 40, "b" * 40
QUESTIONS = [
    {
        "question": '設計を承認しますか？\n「head」は "次" のとおりです',
        "header": "設計",
        "multiSelect": False,
        "options": [{"label": "承認する", "description": "マージします"}, {"label": "直す所がある", "description": ""}],
    }
]
PR = {"number": 7, "branch": "design/x", "head": HEAD}
GATE, ASK = "ndf-gate:1", "ndf-ask:redline:C7"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """リモート origin が owner/repo のリポジトリ。記録の保存先は一時のディレクトリ。締め切りまでの残りは減らない。"""
    monkeypatch.setattr(rc, "monotonic", lambda: 0.0)
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "state"))
    return make_repo(tmp_path / "r")


def _call(cwd: Path, source: Optional[str], questions: Any = QUESTIONS) -> Any:
    tool_input = {"questions": questions} | ({"metadata": {"source": source}} if source is not None else {})
    return call_entry("claude", "PreToolUse", sample("claude", "PreToolUse", cwd, tool_name="AskUserQuestion", tool_input=tool_input))


def _record(prs: tuple = (), **change: Any) -> None:
    """記録に 1 行足す（書き手 `reading.add` の行の形）。"""
    row = {"version": 1, "at": "2026-11-24T00:00:00+00:00", "repo": "owner/repo", "question_key": rd.question_key(QUESTIONS)}
    reads = [{"name": "question", "reader": "承認する人", "round": 2, "result": "remained"}]
    path = reading.checks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({**row, "prs": list(prs), "reads": reads, **change}, ensure_ascii=False) + "\n")


def _refs(monkeypatch: pytest.MonkeyPatch, sha: Optional[str]) -> list[list[str]]:
    """手元のリモート追跡ブランチ（None なら無い）。git へ渡した参照の名前を残す。"""
    asked: list[list[str]] = []

    def refs(root: Path, names: list[str], seconds: float) -> dict[str, str]:
        asked.append(names)
        return {"refs/remotes/origin/design/x": sha} if sha is not None else {}

    monkeypatch.setattr(rc, "_refs", refs)
    return asked


def _no_git(monkeypatch: pytest.MonkeyPatch) -> None:
    def refs(root: Path, names: list[str], seconds: float) -> dict[str, str]:
        raise AssertionError("git を起動しない")

    monkeypatch.setattr(rc, "_refs", refs)


@pytest.mark.parametrize("source", [None, "", "approval", "my-ndf-gate:1", "NDF-GATE:1"])
def test_a_question_without_the_category_passes(root: Path, source: Optional[str], monkeypatch) -> None:
    """順 1: 区分の無い質問は、記録が無くても通る（受け入れ条件 19。退行テスト）。"""
    _no_git(monkeypatch)
    assert _call(root, source) is None


def test_an_unattended_run_passes(root: Path, monkeypatch) -> None:
    """順 2: 人のいない実行では止めない。"""
    monkeypatch.setenv("NDF_FACTORY_RUN", "1")
    assert _call(root, GATE) is None


def test_a_record_that_cannot_be_opened_passes(root: Path) -> None:
    """順 3: 記録のファイルを開けない（同じ名前のディレクトリがある）。"""
    reading.checks_path().mkdir(parents=True)
    assert _call(root, GATE) is None
    assert records()[-1]["notes"]["reading"] == "unreadable"


def test_a_row_of_a_newer_version_passes(root: Path) -> None:
    """順 3: 今の NDF より新しいバージョンの行がある。"""
    _record(version=99)
    assert _call(root, GATE) is None
    assert records()[-1]["notes"]["reading"] == "unreadable"


def test_outside_a_repository_passes(tmp_path: Path, monkeypatch) -> None:
    """順 3: リポジトリの名前が決まらない。"""
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "plain").mkdir()
    assert _call(tmp_path / "plain", GATE) is None
    assert records()[-1]["notes"]["reading"] == "unreadable"


@pytest.mark.parametrize("source", [GATE, ASK])
def test_a_question_without_a_record_is_denied(root: Path, source: str, monkeypatch) -> None:
    """順 4: 記録が無い。理由は区分と語だけで決まる。"""
    _no_git(monkeypatch)
    kind = rc.GATE if source == GATE else rc.ASK
    assert deny_reason(_call(root, source)) == rc.denial(kind, ["no_record"])


@pytest.mark.parametrize(
    "change",
    [{"repo": "owner/other"}, {"question_key": "0" * 64}, {"prs": "x"}, {"version": "x", "repo": None}],
    ids=["another-repo", "another-question", "broken-prs", "broken-row"],
)
def test_a_record_of_something_else_is_not_counted(root: Path, change: dict) -> None:
    """順 4: 別のリポジトリ・別の質問の行と、壊れた行だけの記録は無いものとして止める。"""
    _record(**change)
    assert deny_reason(_call(root, ASK)) == rc.denial(rc.ASK, ["no_record"])


def test_a_question_of_another_text_is_denied(root: Path) -> None:
    _record()
    other = [{**QUESTIONS[0], "question": QUESTIONS[0]["question"] + "（直した）"}]
    assert deny_reason(_call(root, ASK, other)) == rc.denial(rc.ASK, ["no_record"])


def test_a_moved_head_is_denied(root: Path, monkeypatch) -> None:
    """順 4: 承認する PR の head が記録と違う。"""
    _record((PR,))
    asked = _refs(monkeypatch, MOVED)
    assert deny_reason(_call(root, GATE)) == rc.denial(rc.GATE, ["head_moved"])
    assert asked == [["refs/remotes/origin/design/x"]]


def test_a_matching_record_passes(root: Path, monkeypatch) -> None:
    """順 5: 2 回目にも残った記録でも、質問と head が合えば通る。"""
    _record((PR,))
    _refs(monkeypatch, HEAD)
    assert _call(root, GATE) is None
    assert "reading" not in records()[-1].get("notes", {})


def test_a_gate_without_a_pull_request_and_an_ask_do_not_start_git(root: Path, monkeypatch) -> None:
    _no_git(monkeypatch)
    _record((PR,))
    assert _call(root, ASK) is None
    _record()
    assert _call(root, GATE) is None


def test_a_missing_tracking_branch_passes(root: Path, monkeypatch) -> None:
    """順 5: PR のリモート追跡ブランチが手元に無い。"""
    _record((PR,))
    _refs(monkeypatch, None)
    assert _call(root, GATE) is None
    assert records()[-1]["notes"]["reading"] == "no_head"


@pytest.mark.parametrize("error", [OSError("git が無い"), subprocess.TimeoutExpired("git", 0.4), subprocess.CalledProcessError(128, "git")])
def test_a_failing_git_passes(root: Path, monkeypatch, error: Exception) -> None:
    """順 5: git が失敗した。"""

    def refs(root: Path, names: list[str], seconds: float) -> dict[str, str]:
        raise error

    _record((PR,))
    monkeypatch.setattr(rc, "_refs", refs)
    assert _call(root, GATE) is None
    assert records()[-1]["notes"]["reading"] == "no_head"


@pytest.mark.parametrize(("left", "seconds"), [(5.0, 0.4), (0.1, 0.1), (0.0, None), (-1.0, None)])
def test_git_leaves_the_margin_for_the_forwarding(root: Path, monkeypatch, left: float, seconds: Optional[float]) -> None:
    """git に使うのは締め切りから余白を引いた残りまで。残りが無ければ起動せず、head を読めない PR として通す。"""
    used: list[float] = []

    def refs(root: Path, names: list[str], seconds: float) -> dict[str, str]:
        used.append(seconds)
        return {"refs/remotes/origin/design/x": HEAD}

    _record((PR,))
    monkeypatch.setattr(rc, "_refs", refs)
    tool_input = {"questions": QUESTIONS, "metadata": {"source": GATE}}
    ctx = context.HookContext("claude", "PreToolUse", sample("claude", "PreToolUse", root, tool_input=tool_input))
    deadline = tables.policy_value("hook.budget.pre-tool-use", "margin") + left
    got = rc.check(ctx, dispatch.GuardServices(reading.checks_path().parent, Counter.unusable(), deadline))
    assert got.kind == "pass" and got.notes == ({"reading": "no_head"} if seconds is None else {})
    assert used == ([] if seconds is None else [pytest.approx(seconds)])


def test_the_third_denial_of_the_same_reason_becomes_advice(root: Path) -> None:
    """受け入れ条件 18: 同じ区分・同じ理由で止めるのは 2 回まで。質問の文が変わっても同じ理由として数える。"""
    questions = [[{**QUESTIONS[0], "question": f"質問 {n}"}] for n in range(3)]
    assert deny_reason(_call(root, GATE, questions[0])) and deny_reason(_call(root, GATE, questions[1]))
    third = _call(root, GATE, questions[2])
    assert deny_reason(third) is None and rc.denial(rc.GATE, ["no_record"]) in advise_text(third)
    # 別の区分は別に数える
    assert deny_reason(_call(root, ASK)) is not None


def test_the_guard_can_be_turned_off(root: Path, monkeypatch) -> None:
    monkeypatch.setenv("NDF_HOOK_OFF", "reading-check")
    assert _call(root, GATE) is None


def test_a_denied_question_is_not_handed_to_the_relay_or_the_organizer(root: Path, tmp_path: Path, monkeypatch) -> None:
    """止めた質問は人へ出ないので、中継とオーガナイザーの質問のファイルに書かない。"""
    relay, organizer = tmp_path / "relay", tmp_path / "org"
    relay.mkdir()
    signals.set_launch(relay, "L1")
    for name, value in {
        "NDF_RELAY_DIR": relay, "NDF_RELAY_LAUNCH": "L1", "NDF_ORGANIZER_DIR": organizer, "NDF_ORGANIZER_LANE": "l1"
    }.items():  # fmt: skip
        monkeypatch.setenv(name, str(value))
    assert deny_reason(_call(root, GATE)) == rc.denial(rc.GATE, ["no_record"])
    assert not signals.question_present(relay) and not list(organizer.rglob("*.json"))
    _record()
    assert _call(root, GATE) is None
    assert signals.question_present(relay) and list(organizer.rglob("*.json"))


def test_every_reason_fits_the_injection_limit() -> None:
    limit = tables.policy_value("hook.budget.pre-tool-use", "inject_chars")
    assert all(len(rc.denial(kind, ["no_record"])) <= limit for kind in (rc.GATE, rc.ASK))
    assert len(rc.denial(rc.GATE, ["head_moved"])) <= limit


def test_the_tracking_branches_are_read_by_exact_name(tmp_path: Path, monkeypatch) -> None:
    """本物の git: 指定した参照の SHA を読む。配下の参照は名前で見分けられる。"""
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    repo = tmp_path / "g"
    git_sandbox.sh(tmp_path, "init", "-q", str(repo))
    git_sandbox.sh(repo, "commit", "-q", "--allow-empty", "-m", "x")
    sha = git_sandbox.sh(repo, "rev-parse", "HEAD").strip()
    for name in ("design/x", "sprint/a"):
        git_sandbox.sh(repo, "update-ref", f"refs/remotes/origin/{name}", sha)
    assert rc._refs(repo, ["refs/remotes/origin/design/x", "refs/remotes/origin/none"], 10) == {"refs/remotes/origin/design/x": sha}
    assert rc._refs(repo, ["refs/remotes/origin/sprint"], 10) == {"refs/remotes/origin/sprint/a": sha}
