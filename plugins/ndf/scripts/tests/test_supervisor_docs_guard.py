"""規約の文書の guard（hook_lib/supervisor_docs.py。#2813 の受け入れ条件 3・5・6 と不変条件 I1・I3・I4・I5）。

一時ディレクトリに外のプラグインルート（`plugin.json` の `name` が `ndf`）を作り、hook.py を PreToolUse の入力で起動して、
拒否の有無と理由を見る。hook 自身のプラグインルートはこのテストのある checkout の `plugins/ndf` である。
"""

from __future__ import annotations

import json
import pathlib

import pytest
from hook_run import deny_reason as run

OWN = pathlib.Path(__file__).resolve().parents[2]
REL = pathlib.Path("skills/development-workflow/references/agent-layers.md")
ONLY_OUT = pathlib.Path("skills/development-workflow/references/only-outside-2813.md")


def plugin(root: pathlib.Path, name: str = "ndf", manifest: str | None = None) -> pathlib.Path:
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(manifest if manifest is not None else json.dumps({"name": name}), encoding="utf-8")
    for rel in (REL, ONLY_OUT, pathlib.Path("skills/design/SKILL.md"), pathlib.Path("scripts/resolve.sh")):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x\n", encoding="utf-8")
    return root


@pytest.fixture
def other(tmp_path) -> pathlib.Path:
    return plugin(tmp_path / "checkout" / "plugins" / "ndf")


def read(path, agent_type="ndf:supervisor", cwd=None) -> dict:
    return payload("Read", {"file_path": str(path)}, agent_type, cwd)


def bash(command, agent_type="ndf:supervisor", cwd=None) -> dict:
    return payload("Bash", {"command": command}, agent_type, cwd)


def payload(tool, tool_input, agent_type, cwd) -> dict:
    p = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "session_id": "s2813", "agent_id": "a1"}
    if agent_type is not None:
        p["agent_type"] = agent_type
    if cwd is not None:
        p["cwd"] = str(cwd)
    return p


@pytest.mark.parametrize("agent_type", ["ndf:supervisor", "ndf:supervisor-waits"])
def test_outside_read_is_denied_with_inner_path(tmp_path, other, agent_type):
    reason = run(read(other / REL, agent_type), tmp_path)
    assert reason is not None
    assert str(OWN / REL) in reason


def test_outside_read_without_inner_doc_points_to_skills(tmp_path, other):
    reason = run(read(other / ONLY_OUT), tmp_path)
    assert reason is not None
    assert str(OWN / "skills") in reason
    assert str(OWN / ONLY_OUT) not in reason


@pytest.mark.parametrize("agent_type", ["ndf:supervisor", "ndf:supervisor-waits"])
def test_bash_find_on_outside_root_is_denied(tmp_path, other, agent_type):
    reason = run(bash(f"find {other} -name agent-layers.md|head", agent_type), tmp_path)
    assert reason is not None
    assert str(OWN) in reason


def test_bash_cat_outside_doc_gives_inner_path(tmp_path, other):
    reason = run(bash(f'cat "{other / REL}" | head -40'), tmp_path)
    assert reason is not None
    assert str(OWN / REL) in reason


@pytest.mark.parametrize("cmd", ["cat < {p}", "cat <{p}", "wc -l 0<{p}"])
def test_bash_input_redirect_outside_doc_is_denied(tmp_path, other, cmd):
    reason = run(bash(cmd.format(p=other / REL)), tmp_path)
    assert reason is not None
    assert str(OWN / REL) in reason


def test_relative_path_resolves_from_cwd(tmp_path, other):
    assert run(bash("cat skills/development-workflow/references/agent-layers.md", cwd=other), tmp_path) is not None


@pytest.mark.parametrize("agent_type", [None, "ndf:worker", "ndf:code-reviewer", "general-purpose"])
def test_other_callers_are_not_judged(tmp_path, other, agent_type):
    data = read(other / REL, agent_type)
    data["transcript_path"] = str(tmp_path / "missing" / "sess.jsonl")
    assert run(data, tmp_path) is None
    assert run(bash(f"find {other} -name x", agent_type), tmp_path) is None


def test_inner_docs_and_non_docs_pass(tmp_path, other):
    link = tmp_path / "link-to-own"
    link.symlink_to(OWN)
    work = tmp_path / "work"
    (work / "issues").mkdir(parents=True)
    (work / "issues" / "issue-1-requirements.md").write_text("x\n", encoding="utf-8")
    renamed = plugin(tmp_path / "other-plugin", name="playwright-kit")
    for path in (
        OWN / REL,
        link / REL,
        work / "issues" / "issue-1-requirements.md",
        other / "scripts" / "resolve.sh",
        renamed / REL,
        tmp_path / "checkout" / "plugins",
    ):
        assert run(read(path), tmp_path) is None, path
    assert run(bash(f"cat {OWN / REL} && ls {work}"), tmp_path) is None
    assert run(bash('cat "$SCRIPTS/../skills/development-workflow/references/agent-layers.md"', cwd=other), tmp_path) is None


def test_env_turns_guard_off(tmp_path, other):
    assert run(read(other / REL), tmp_path, env={"NDF_SUPERVISOR_DOCS_GUARD": "0"}) is None


def test_broken_manifest_passes(tmp_path):
    broken = plugin(tmp_path / "broken", manifest="{not json")
    assert run(read(broken / REL), tmp_path) is None


# --- hook.py の _supervisor_docs_guard の包みの現状固定（cross-refactoring I-011） ---


@pytest.fixture
def docs_hook(monkeypatch):
    monkeypatch.syspath_prepend(str(OWN / "scripts" / "lib"))
    monkeypatch.syspath_prepend(str(OWN / "scripts"))
    import hook
    from hook_lib import supervisor_docs

    return hook, supervisor_docs


@pytest.mark.parametrize(
    ("impl", "want"),
    [
        (lambda ev: {"d": 1}, [{"d": 1}]),
        (lambda ev: None, [None]),
        (lambda ev: (_ for _ in ()).throw(RuntimeError("x")), []),
        (lambda ev: (_ for _ in ()).throw(OSError("x")), []),
    ],
    ids=["decision", "none", "runtime-error", "os-error"],
)
def test_supervisor_docs_wrapper_lists_the_decision_and_swallows_errors(docs_hook, monkeypatch, impl, want):
    hook, supervisor_docs = docs_hook
    monkeypatch.setattr(supervisor_docs, "docs_decision", impl)
    assert hook._supervisor_docs_guard(object()) == want


def test_supervisor_docs_wrapper_lets_the_deadline_through(docs_hook, monkeypatch):
    hook, supervisor_docs = docs_hook
    from hook_lib import deadline

    def late(ev):
        raise deadline.HookDeadlineExceeded("supervisor-docs")

    monkeypatch.setattr(supervisor_docs, "docs_decision", late)
    with pytest.raises(deadline.HookDeadlineExceeded):
        hook._supervisor_docs_guard(object())
