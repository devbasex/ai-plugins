"""問い合わせの guard（#2206）: `hook.py` へ PreToolUse の `AskUserQuestion` を渡したときの判定・記録と `ask.py`。

hook は一時のプロジェクト（`.git` を持つ）を `cwd` にして起動し、記録は `NDF_MVV_STATE_DIR` の一時の置き場へ書く。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
HOOK = SCRIPTS / "hook.py"
ASK = SCRIPTS / "ask.py"
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import ask_ledger  # noqa: E402
import ask_policy  # noqa: E402
import hook as hook_entry  # noqa: E402

LEDGER_KEYS_VERDICT = {"v", "kind", "id", "at", "session", "cwd", "runtime", "source", "verdict", "reason", "mode", "decl_problem", "questions"}
LEDGER_KEYS_DECISION = {"v", "kind", "id", "at", "session", "cwd", "ref", "question", "answer", "reason"}


@pytest.fixture()
def proj(tmp_path, monkeypatch):
    p = tmp_path / "proj"
    (p / ".git").mkdir(parents=True)
    state = tmp_path / "state"
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(state))
    return p


def declare(proj, data) -> None:
    (proj / ".ndf").mkdir(exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data)
    (proj / ".ndf" / "ask.json").write_text(text)


def ask_input(proj, source=None, question="どれにしますか", **extra) -> dict:
    ti: dict = {"questions": [{"question": question, "options": [{"label": "A"}, {"label": "B"}]}]}
    if source is not None:
        ti["metadata"] = {"source": source}
    return {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "session_id": "s1", "cwd": str(proj), "tool_input": ti, **extra}


def run_hook(proj, data, env=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith("SLACK")}
    e.update(env or {})
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(data), capture_output=True, text=True, env=e, cwd=str(proj), timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def spec(out) -> dict:
    return (out or {}).get("hookSpecificOutput") or {}


def is_deny(out) -> bool:
    return spec(out).get("permissionDecision") == "deny"


def rows():
    return ask_ledger.read_rows()


# ---------------------------------------------------------------- AC1・AC6・AC9


def test_missing_source_is_denied_with_guidance(proj):
    out = run_hook(proj, ask_input(proj))
    assert is_deny(out)
    reason = spec(out)["permissionDecisionReason"]
    [row] = rows()
    assert f"decide --ref {row['id']}" in reason and str(ASK) in reason
    assert "ndf-gate:1" in reason and "ndf-ask:redline:<番号>" in reason and "ndf-ask:cost" in reason
    assert "0 USD" in reason
    assert row["verdict"] == "deny" and row["reason"] == "missing" and row["source"] == "" and row["session"] == "s1"
    assert row["questions"] == ["どれにしますか"] and row["cwd"] == os.path.realpath(proj)


def test_verdict_row_has_only_the_ledger_columns(proj):
    data = ask_input(proj, transcript_path="/secret/t.jsonl")
    data["tool_input"]["metadata"] = {"source": "ndf-gate:1", "other": "x"}
    run_hook(proj, data)
    [row] = rows()
    assert set(row) == LEDGER_KEYS_VERDICT
    assert "/secret" not in json.dumps(row) and "\"A\"" not in json.dumps(row)


# ---------------------------------------------------------------- AC2・AC3・AC4・AC5


@pytest.mark.parametrize("source", ["ndf-gate:1", "ndf-gate:2", "ndf-ask:redline:C4", "ndf-ask:redline:P1", "ndf-ask:redline:R2"])
def test_gate_and_redline_pass_even_with_empty_allow(proj, source):
    declare(proj, {"version": 1, "mode": "enforce", "allow": []})
    out = run_hook(proj, ask_input(proj, source))
    assert not is_deny(out)
    assert rows()[-1]["verdict"] == "allow"


@pytest.mark.parametrize("source", ["ndf-gate:3", "ndf-ask:redline", "ndf-ask:redline:C9", "gate1", "ndf-ask:Cost"])
def test_malformed_sources_are_denied(proj, source):
    out = run_hook(proj, ask_input(proj, source))
    assert is_deny(out) and rows()[-1]["reason"] == "malformed"


@pytest.mark.parametrize(
    "source, denied",
    [("ndf-ask:cost", False), ("ndf-ask:customer-contact", False), ("ndf-ask:legal", True), ("ndf-ask:unknown", True)],
)
def test_allow_and_extra_decide(proj, source, denied):
    declare(proj, {"version": 1, "allow": ["cost"], "extra": [{"name": "customer-contact", "description": "顧客へ連絡する"}]})
    out = run_hook(proj, ask_input(proj, source))
    assert is_deny(out) is denied


@pytest.mark.parametrize("name", ask_policy.BUILTIN)
def test_omitted_allow_allows_four_builtins(proj, name):
    declare(proj, {"version": 1})
    assert not is_deny(run_hook(proj, ask_input(proj, f"ndf-ask:{name}")))


def test_allowed_source_gets_threshold_context(proj):
    declare(proj, {"version": 1, "cost": {"threshold_usd": 5}})
    out = run_hook(proj, ask_input(proj, "ndf-ask:cost"))
    assert "permissionDecision" not in spec(out)
    assert "5 USD" in spec(out)["additionalContext"] and "ndf-ask:cost" in spec(out)["additionalContext"]


# ---------------------------------------------------------------- AC7・AC8


@pytest.mark.parametrize(
    "decl, where",
    [("{", "JSON"), ({"version": 2}, "version"), ({"version": True}, "version"), ({"version": 1, "allow": ["cost", "x"]}, "allow[1]"),
     ({"version": 1, "mode": "off", "extra": [{"name": "redline", "description": "d"}]}, "extra[0].name")],
)
def test_broken_declaration_falls_back_to_default(proj, decl, where):
    declare(proj, decl)
    out = run_hook(proj, ask_input(proj))
    assert is_deny(out)
    reason = spec(out)["permissionDecisionReason"]
    assert ".ndf/ask.json を読めなかった" in reason and where in reason
    assert not is_deny(run_hook(proj, ask_input(proj, "ndf-ask:legal")))


def test_off_outputs_nothing_and_records_nothing(proj):
    declare(proj, {"version": 1, "mode": "off"})
    assert run_hook(proj, ask_input(proj)) is None
    assert rows() == []


def test_advise_does_not_deny_but_guides(proj):
    declare(proj, {"version": 1, "mode": "advise"})
    out = run_hook(proj, ask_input(proj))
    assert not is_deny(out)
    ctx = spec(out)["additionalContext"]
    assert "advise" in ctx and "decide --ref" in ctx
    assert rows()[-1]["verdict"] == "advise"


# ---------------------------------------------------------------- I6・I9


def test_question_text_does_not_change_the_verdict():
    decl = ask_policy.Declaration()
    a = ask_policy.judge({"questions": [{"question": "費用がかかりますか"}]}, decl)
    b = ask_policy.judge({"questions": [{"question": "色は"}]}, decl)
    assert a == b


def test_one_row_per_verdict_and_rows_are_kept(proj):
    run_hook(proj, ask_input(proj))
    first = rows()
    run_hook(proj, ask_input(proj, "ndf-gate:1"))
    after = rows()
    assert len(after) == 2 and after[0] == first[0]


# ---------------------------------------------------------------- AC11・AC15


def test_failures_never_deny(proj, monkeypatch):
    for name in ("load_declaration", "judge"):
        with monkeypatch.context() as m:
            m.setattr(ask_policy, name, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
            from hook_lib import ask_guard, payload

            assert ask_guard.decision(payload.event_of(ask_input(proj))) is None


def test_unwritable_ledger_still_judges(proj, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    out = run_hook(proj, ask_input(proj), env={"NDF_MVV_STATE_DIR": str(blocker / "sub")})
    assert is_deny(out)


def test_deadline_passes_through(proj, monkeypatch):
    from hook_lib import ask_guard, deadline

    def slow(ev):
        raise deadline.HookDeadlineExceeded("ask-guard")

    monkeypatch.setattr(ask_guard, "decision", slow)
    out = hook_entry.dispatch("", "claude", ask_input(proj))
    assert not is_deny(out)


@pytest.mark.parametrize("source, notified", [(None, False), ("ndf-gate:1", True)])
def test_wait_notify_only_when_not_denied(proj, monkeypatch, source, notified):
    from hook_lib import wait_notify

    calls = []
    monkeypatch.setattr(wait_notify, "hook", lambda runtime, raw: calls.append(runtime))
    hook_entry.dispatch("", "claude", ask_input(proj, source))
    assert bool(calls) is notified


def test_other_runtimes_are_not_judged(proj):
    r = subprocess.run([sys.executable, str(HOOK), "--runtime", "codex"], input=json.dumps(ask_input(proj)), capture_output=True, text=True, cwd=str(proj), timeout=30)
    assert r.returncode == 0 and "deny" not in r.stdout


# ---------------------------------------------------------------- AC10・ask.py


def run_ask(*args):
    r = subprocess.run([sys.executable, str(ASK), *args], capture_output=True, text=True, timeout=30)
    return r.returncode, json.loads(r.stdout)


def test_decide_with_ref_links_the_denied_row(proj):
    run_hook(proj, ask_input(proj))
    [denied] = rows()
    code, rep = run_ask("decide", "--ref", denied["id"], "--answer", "A", "--reason", "既存の方針に合う")
    assert code == 0 and rep["status"] == "ok"
    row = rows()[-1]
    assert set(row) == LEDGER_KEYS_DECISION
    assert row["ref"] == denied["id"] and row["question"] == "どれにしますか" and row["session"] == "s1"
    assert row["answer"] == "A" and row["reason"] == "既存の方針に合う"


def test_decide_with_question_only(proj):
    code, rep = run_ask("decide", "--question", "plan を作るか", "--answer", "作る", "--reason", "複数ファイルにまたがる")
    assert code == 0 and rows()[-1]["ref"] == ""


@pytest.mark.parametrize(
    "args",
    [["--ref", "nope", "--answer", "A", "--reason", "r"], ["--question", "q", "--answer", " ", "--reason", "r"], ["--answer", "A", "--reason", "r"]],
)
def test_decide_rejects_bad_input(proj, args):
    code, rep = run_ask("decide", *args)
    assert code == 1 and rep["status"] == "stopped"
    assert rows() == []


def test_summary_counts(proj):
    run_hook(proj, ask_input(proj))
    run_hook(proj, ask_input(proj))
    run_hook(proj, ask_input(proj, "ndf-gate:1"))
    first = rows()[0]
    run_ask("decide", "--ref", first["id"], "--answer", "A", "--reason", "r")
    run_ask("decide", "--question", "q", "--answer", "A", "--reason", "r")
    code, rep = run_ask("summary")
    m = rep["metrics"]
    assert code == 0
    assert m["verdict"] == {"deny": 2, "allow": 1, "advise": 0}
    assert m["source"] == {"": 2, "ndf-gate:1": 1}
    assert m["decision"] == {"ref": 1, "no_ref": 1} and m["unanswered"] == 1
