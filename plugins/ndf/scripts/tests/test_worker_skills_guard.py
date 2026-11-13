"""読む Skill の guard（hook_lib/worker_skills.py。#2399 の設計の I1・I2・I5・I6 と受け入れ条件 4）。

偽の会話の記録（<セッション>.jsonl と <セッション>/subagents/agent-<ID>.jsonl）を一時ディレクトリに作り、
hook.py を PreToolUse の入力で起動して、拒否の有無と理由を見る。
"""

from __future__ import annotations

import json
import pathlib

import pytest
from hook_run import deny_reason as run

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
AGENT_ID = "w1x2y3"
WITH_SKILL = "作業: 設計文書を書く\n入力: 無し\n読む Skill:\n- some-rule — /abs/some-rule/SKILL.md\n返す形: 作業の報告"
WITH_NONE = "作業: 作業場所を用意する\n| 読む Skill | 無し |\n返す形: 作業の報告"
WITHOUT = "作業: 設計文書を書く\n置き場所: /tmp/x\n報告: 作業の報告"


def bash(command: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}


def write(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def transcript(tmp_path, own_rows) -> pathlib.Path:
    parent = tmp_path / "proj" / "sess.jsonl"
    write(parent, [])
    if own_rows is not None:
        write(parent.with_suffix("") / "subagents" / f"agent-{AGENT_ID}.jsonl", own_rows)
    return parent


def payload(tp, prompt, agent_type="ndf:supervisor", subagent="ndf:worker", tool="Agent"):
    p = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"subagent_type": subagent, "description": "設計: 文書", "prompt": prompt},
        "session_id": "sess",
        "transcript_path": str(tp),
        "agent_id": AGENT_ID,
    }
    if agent_type is not None:
        p["agent_type"] = agent_type
    return p


LOOKUP = bash(f'python3 "{SCRIPTS}/stage-skills.py" lookup 設計')


@pytest.mark.parametrize("agent_type", ["ndf:supervisor", "ndf:supervisor-waits"])
def test_missing_field_is_denied_with_section(tmp_path, agent_type):
    reason = run(payload(transcript(tmp_path, [LOOKUP]), WITHOUT, agent_type=agent_type), tmp_path)
    assert reason is not None
    assert "supervisor → worker" in reason
    assert str(SCRIPTS) in reason


@pytest.mark.parametrize(
    "rows,stop",
    [
        ([LOOKUP], False),
        ([bash("ls")], True),
        # 指示の文や Tool の結果に lookup の字があっても、Bash の実行でなければ数えない
        ([{"type": "user", "message": {"content": "stage-skills.py lookup 設計 を打つ"}}], True),
        # 別の checkout の stage-skills.py で引いた記録だけでは通さない
        ([bash('python3 "/tmp/other-checkout/plugins/ndf/scripts/stage-skills.py" lookup 設計')], True),
        ([bash('python3 "$SCRIPTS/stage-skills.py" lookup 設計')], True),
        ([bash(f"cd {SCRIPTS} && python3 stage-skills.py lookup 設計")], True),
        ([bash('python3 "/tmp/other/stage-skills.py" lookup 設計'), LOOKUP], False),
        ([bash(f"python3 {SCRIPTS}/stage-skills.py lookup 設計 | jq .")], False),
    ],
)
def test_listed_skills_need_lookup(tmp_path, rows, stop):
    reason = run(payload(transcript(tmp_path, rows), WITH_SKILL), tmp_path)
    assert (reason is not None) is stop
    if stop:
        assert "stage-skills.py" in reason and str(SCRIPTS) in reason


def test_lookup_through_symlink_passes(tmp_path):
    link = tmp_path / "linked-scripts"
    link.symlink_to(SCRIPTS, target_is_directory=True)
    rows = [bash(f'python3 "{link}/stage-skills.py" lookup 設計')]
    assert run(payload(transcript(tmp_path, rows), WITH_SKILL), tmp_path) is None


def test_none_passes_without_lookup(tmp_path):
    assert run(payload(transcript(tmp_path, [bash("ls")]), WITH_NONE), tmp_path) is None


@pytest.mark.parametrize(
    "agent_type,subagent,tool",
    [
        (None, "ndf:worker", "Agent"),  # conductor から
        ("general-purpose", "ndf:worker", "Agent"),
        ("ndf:supervisor", "general-purpose", "Agent"),
        ("ndf:supervisor", "ndf:supervisor", "Task"),
    ],
)
def test_other_launches_pass(tmp_path, agent_type, subagent, tool):
    assert run(payload(transcript(tmp_path, []), WITHOUT, agent_type=agent_type, subagent=subagent, tool=tool), tmp_path) is None


def test_task_tool_is_checked(tmp_path):
    assert run(payload(transcript(tmp_path, []), WITHOUT, tool="Task"), tmp_path) is not None


def test_unreadable_transcript_passes(tmp_path):
    assert run(payload(transcript(tmp_path, None), WITH_SKILL), tmp_path) is None


def test_env_off_passes(tmp_path):
    assert run(payload(transcript(tmp_path, []), WITHOUT), tmp_path, {"NDF_WORKER_SKILLS_GUARD": "0"}) is None


@pytest.fixture
def guard(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS / "lib"))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    from hook_lib import worker_skills

    return worker_skills


def test_reason_is_read_from_the_doc(tmp_path, guard, monkeypatch):
    doc = tmp_path / "agent-layers.md"
    doc.write_text("# 表題\n\n### supervisor → worker\n\n差し替えた節の本文 7f3a\n\n## 次の節\n\n含めない本文\n", encoding="utf-8")
    monkeypatch.setattr(guard, "DOC", doc)
    reason = guard._missing_field()["hookSpecificOutput"]["permissionDecisionReason"]
    assert "差し替えた節の本文 7f3a" in reason
    assert "含めない本文" not in reason


def test_unreadable_doc_still_denies_with_path(tmp_path, guard, monkeypatch):
    doc = tmp_path / "missing.md"
    monkeypatch.setattr(guard, "DOC", doc)
    reason = guard._missing_field()["hookSpecificOutput"]["permissionDecisionReason"]
    assert str(doc) in reason and "supervisor → worker" in reason


@pytest.mark.parametrize(
    "prompt,value",
    [
        ("読む Skill: 無し", "無し"),
        ("- **読む Skill**：無し", "無し"),
        ("## 読む Skill\n\n- a — /p/SKILL.md", "- a — /p/SKILL.md"),
        ("| 読む Skill | なし |", "なし"),
        ("読む Skill の guard が止める", None),
        ("作業: x", None),
    ],
)
def test_field_value(guard, prompt, value):
    assert guard.field_value(prompt) == value


# --- hook.py の guard の包み（_worker_skills_guard ほか）の現状固定（cross-refactoring I-011） ---
# 判定の関数を差し替え、包みが返す並びを見る。出力をそのまま 1 つの並びにする・None も 1 要素にする（ci-test だけは落とす）・
# Exception は飲んで空の並び・BaseException（締め切り）は飲まない、を縛る。

WRAPPERS = [
    ("_worker_skills_guard", "worker_skills", "skills_decision", True),
    ("_supervisor_docs_guard", "supervisor_docs", "docs_decision", True),
    ("_issue_guard", "issue_guard", "issue_decision", True),
    ("_ci_test_guard", "ci_test_guard", "ci_test_decision", False),
]


@pytest.fixture
def hook_mod(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS / "lib"))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import hook

    return hook


def _patch(monkeypatch, module: str, func: str, impl) -> None:
    import importlib

    monkeypatch.setattr(importlib.import_module(f"hook_lib.{module}"), func, impl)


@pytest.mark.parametrize(("wrapper", "module", "func", "keeps_none"), WRAPPERS)
def test_guard_wrapper_returns_the_decision_as_a_one_item_list(hook_mod, monkeypatch, wrapper, module, func, keeps_none):
    out = {"hookSpecificOutput": {"permissionDecision": "deny"}}
    seen = []
    _patch(monkeypatch, module, func, lambda ev: seen.append(ev) or out)
    ev = object()
    got = getattr(hook_mod, wrapper)(ev)
    assert got == [out] and got[0] is out and seen == [ev]


@pytest.mark.parametrize(("wrapper", "module", "func", "keeps_none"), WRAPPERS)
@pytest.mark.parametrize("value", [None, {}])
def test_guard_wrapper_keeps_or_drops_an_empty_decision(hook_mod, monkeypatch, wrapper, module, func, keeps_none, value):
    """worker-skills・supervisor-docs・issue は None も {} も 1 要素として返す。ci-test だけは偽の値を落とす。"""
    _patch(monkeypatch, module, func, lambda ev: value)
    assert getattr(hook_mod, wrapper)(object()) == ([value] if keeps_none else [])


@pytest.mark.parametrize(("wrapper", "module", "func", "keeps_none"), WRAPPERS)
@pytest.mark.parametrize("exc", [RuntimeError("x"), OSError("x"), KeyError("x"), ValueError("x")])
def test_guard_wrapper_swallows_an_exception_into_an_empty_list(hook_mod, monkeypatch, wrapper, module, func, keeps_none, exc):
    def boom(ev):
        raise exc

    _patch(monkeypatch, module, func, boom)
    assert getattr(hook_mod, wrapper)(object()) == []


@pytest.mark.parametrize(("wrapper", "module", "func", "keeps_none"), WRAPPERS)
def test_guard_wrapper_lets_the_deadline_through(hook_mod, monkeypatch, wrapper, module, func, keeps_none):
    from hook_lib import deadline

    def late(ev):
        raise deadline.HookDeadlineExceeded(wrapper)

    _patch(monkeypatch, module, func, late)
    with pytest.raises(deadline.HookDeadlineExceeded):
        getattr(hook_mod, wrapper)(object())


@pytest.mark.parametrize(
    ("wrapper", "module", "func"), [("_token_guard", "token_guard", None), ("_ask_guard", "ask_guard", "ask_decision")]
)
def test_unwrapped_guards_let_exceptions_through(hook_mod, monkeypatch, wrapper, module, func):
    """包みの無い guard（ask-guard）は例外を飲まない。token-guard は対象の道具でなければ判定を呼ばずに空の並び。"""
    if func is None:
        assert getattr(hook_mod, wrapper)(type("Ev", (), {"tool": "NotATool"})()) == []
        return

    def boom(ev):
        raise RuntimeError("x")

    _patch(monkeypatch, module, func, boom)
    with pytest.raises(RuntimeError):
        getattr(hook_mod, wrapper)(object())
