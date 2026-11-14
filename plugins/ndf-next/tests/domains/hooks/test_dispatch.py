"""guard の表の振り分け・失敗の扱い・注入の上限（M3-H5・M3-H6・IH4・IH8）。"""

from __future__ import annotations

import sys
import time
import types

import pytest

from ndf.domains.hooks import budget, dispatch, main
from ndf.domains.hooks.context import HookContext
from ndf.domains.hooks.counter import Counter
from ndf.domains.hooks.verdict import Verdict, advise, deny, pushback

from ._hooks import bash, call_entry, records, state_dir


def _spec(name: str, module: str, **kw) -> dispatch.GuardSpec:
    row = {"name": name, "module": module, "events": ["PreToolUse"], "tools": [], "order": 1, "strict": False, "suggests": [], "deny_key": "reason", "limits": [], "params": {}}
    return dispatch.GuardSpec.from_row({**row, **kw})


@pytest.fixture
def fake_guards(monkeypatch):
    """`check` を差し替えた guard のモジュールを作る。"""

    def make(name: str, check) -> str:
        module = types.ModuleType(f"fake_guard_{name}")
        module.check = check
        monkeypatch.setitem(sys.modules, module.__name__, module)
        return module.__name__

    return make


def _ctx(tmp_path) -> HookContext:
    return HookContext("claude", "PreToolUse", bash(tmp_path, "ls"))


def _run(tmp_path, specs, clock=time.monotonic):
    found = budget.budget_for("claude", "PreToolUse")
    with Counter.open("s-1", tmp_path) as counter:
        return dispatch.run(_ctx(tmp_path), specs, found, counter, tmp_path, clock)


def test_guards_are_ordered_by_the_table():
    specs = dispatch.load_specs()
    assert [s.order for s in specs] == sorted(s.order for s in specs)
    assert {s.name for s in specs if s.strict} == {"merge-production"}


def test_select_drops_strict_off_and_runtime_skipped_guards(tmp_path):
    ctx = HookContext("codex", "PreToolUse", {"tool_name": "Read"})
    names = [s.name for s in dispatch.select(dispatch.load_specs(), ctx, frozenset({"sleep"}))]
    assert "merge-production" not in names and "read-repeat" not in names and "context-limit" not in names
    claude = HookContext("claude", "PreToolUse", {"tool_name": "Read"})
    assert "read-repeat" in [s.name for s in dispatch.select(dispatch.load_specs(), claude, frozenset())]


def test_guard_exception_passes_and_records_the_kind(tmp_path, fake_guards):
    def boom(ctx, svc):
        raise RuntimeError("壊れた")

    out = _run(tmp_path, [_spec("boom", fake_guards("boom", boom)), _spec("ok", fake_guards("ok", lambda c, s: advise("案内")))])
    assert out.verdict.kind == "advise"
    assert [(r.name, r.kind, r.error) for r in out.runs] == [("boom", "pass", "RuntimeError"), ("ok", "advise", "")]


def test_deadline_skips_the_remaining_guards(tmp_path, fake_guards):
    ticks = iter([0.0, 0.0, 0.1, 0.9, 0.9, 0.9])
    specs = [_spec("a", fake_guards("a", lambda c, s: None)), _spec("b", fake_guards("b", lambda c, s: deny("x")))]
    out = _run(tmp_path, specs, clock=lambda: next(ticks))
    assert out.stopped == "deadline" and [r.name for r in out.runs] == ["a"] and out.verdict.kind == "pass"


def test_combine_keeps_the_strongest_and_joins_its_texts():
    got = dispatch.combine([advise("a"), deny("x"), pushback("p"), deny("y"), Verdict("pass")])
    assert got.kind == "deny" and got.reason == "x\n\ny"
    assert dispatch.combine([advise("a"), advise("b")]).context == "a\n\nb"
    assert dispatch.combine([]).kind == "pass"


def test_long_injection_is_written_to_a_file_and_replaced_by_its_path(tmp_path, fake_guards, monkeypatch):
    # M3-H5・IH8: 注入の上限（PreToolUse は 600 字）を超える文はファイルへ書き、パスと先頭を入れる
    long_text = "長い案内。" * 400
    spec = _spec("long", fake_guards("long", lambda c, s: advise(long_text)))
    monkeypatch.setattr(dispatch, "load_specs", lambda: [spec])
    out = call_entry("claude", "PreToolUse", bash(tmp_path, "ls"))
    text = out["hookSpecificOutput"]["additionalContext"]
    limit = budget.budget_for("claude", "PreToolUse").inject_chars
    assert len(text) <= limit and "全文:" in text
    notes = list((state_dir() / "hooks" / "notes").glob("*.md"))
    assert len(notes) == 1 and notes[0].read_text(encoding="utf-8") == long_text and str(notes[0]) in text
    assert records()[-1]["inject_chars"] == len(text)


def test_clip_keeps_short_text_and_drops_text_when_no_injection_is_allowed(tmp_path):
    assert dispatch.clip("短い", 10, tmp_path) == "短い"
    assert dispatch.clip("x" * 20, 0, tmp_path) == ""
    assert not list(tmp_path.glob("*.md"))


def test_entry_exception_and_deadline_print_nothing(tmp_path, monkeypatch):
    # M3-H6: 例外・締め切り超えは出力なし・0
    def boom(*a, **k):
        raise RuntimeError("壊れた")

    monkeypatch.setattr(dispatch, "run", boom)
    assert call_entry("claude", "PreToolUse", bash(tmp_path, "sleep 999")) is None
    assert records()[-1]["error"] == "RuntimeError"

    def slow(*a, **k):
        time.sleep(5)

    monkeypatch.setattr(dispatch, "run", slow)
    started = time.monotonic()
    assert call_entry("claude", "PreToolUse", bash(tmp_path, "sleep 999")) is None
    assert time.monotonic() - started < 3 and records()[-1]["error"] == "deadline"


def test_entry_without_state_dir_still_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_state_dir", lambda: None)
    assert call_entry("claude", "PreToolUse", bash(tmp_path, "sleep 999")) is None
