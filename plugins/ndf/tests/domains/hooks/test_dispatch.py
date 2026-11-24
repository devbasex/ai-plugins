"""guard の表の振り分け・失敗の扱い・注入の上限（M3-H5・M3-H6・IH4・IH8）。"""

from __future__ import annotations

import json
import sys
import time
import types

import pytest

from ndf import identity
from ndf.domains.hooks import budget, dispatch, main
from ndf.domains.hooks.context import HookContext
from ndf.domains.hooks.counter import Counter
from ndf.domains.hooks.guards import ask, context_limit
from ndf.domains.hooks.verdict import Verdict, advise, deny, pushback
from ndf.domains.relay import signals

from ._hooks import bash, call_entry, deny_reason, records, sample, state_dir


def _spec(name: str, module: str, **kw) -> dispatch.GuardSpec:
    row = {
        "name": name,
        "module": module,
        "events": ["PreToolUse"],
        "tools": [],
        "order": 1,
        "strict": False,
        "suggests": [],
        "deny_key": "reason",
        "limits": [],
        "params": {},
        "fail_closed": False,
    }
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


def _run(tmp_path, specs, clock=time.monotonic, deadline=0.0):
    found = budget.budget_for("claude", "PreToolUse")
    with Counter.open("s-1", tmp_path) as counter:
        return dispatch.run(_ctx(tmp_path), specs, found, dispatch.GuardServices(tmp_path, counter, deadline), clock)


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


def test_combine_shows_only_the_sole_texts_of_the_strongest_kind():
    # #3995（r4-1）: 運用上限の引継ぎのような `sole` の拒否があれば、ほかの拒否の文をつながない
    got = dispatch.combine([deny("x"), deny("h", sole=True), deny("y")])
    assert got.kind == "deny" and got.reason == "h"
    assert dispatch.combine([deny("x"), advise("h"), Verdict("advise", context="s", sole=True)]).reason == "x"


def test_combine_joins_every_sole_text_in_order():
    got = dispatch.combine([deny("x"), deny("h1", sole=True), deny("y"), deny("h2", sole=True)])
    assert got.kind == "deny" and got.reason == "h1\n\nh2"


def test_combine_skips_empty_texts_when_joining():
    assert dispatch.combine([deny(""), deny("x"), deny(""), deny("y")]).reason == "x\n\ny"
    assert dispatch.combine([advise(""), advise("a")]).context == "a"


def test_combine_with_an_empty_sole_text_drops_the_other_texts():
    # 今の振る舞い: `sole` の判定を選んでから空の文を外すので、`sole` の文が空なら結果の文も空になる
    got = dispatch.combine([deny("x"), deny("", sole=True)])
    assert got.kind == "deny" and got.reason == ""


def test_combine_keeps_pushback_as_a_reason_kind():
    got = dispatch.combine([advise("a"), pushback("p"), pushback("q"), Verdict("pass")])
    assert got.kind == "pushback" and got.reason == "p\n\nq" and got.context == ""


def test_combine_of_advises_puts_the_text_in_context():
    got = dispatch.combine([Verdict("pass"), advise("a"), Verdict("advise", context="b", sole=True)])
    assert got.kind == "advise" and got.context == "b" and got.reason == ""


def test_combine_of_only_passes_is_the_plain_pass():
    got = dispatch.combine([Verdict("pass", notes={"n": 1}), Verdict("pass")])
    assert got is dispatch.PASS


def test_combine_does_not_carry_key_notes_or_sole():
    got = dispatch.combine([deny("x", key="k", notes={"n": 1}, sole=True)])
    assert got == Verdict("deny", reason="x")


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


def test_clip_saves_the_text_without_the_reply_excerpt(tmp_path):
    # 会話へ返す文は抜粋を含んでよいが、平文のバックアップには本文を含まない文を書く
    shown = dispatch.clip("抜粋 s3cr3t " + "x" * 400, 200, tmp_path, saved="固定の文")
    notes = list(tmp_path.glob("*.md"))
    assert len(notes) == 1 and notes[0].read_text(encoding="utf-8") == "固定の文"
    assert "s3cr3t" in shown and len(shown) <= 200


def test_entry_without_state_dir_still_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_state_dir", lambda: None)
    assert call_entry("claude", "PreToolUse", bash(tmp_path, "sleep 999")) is None


def _fail_closed(name: str, module: str) -> dispatch.GuardSpec:
    return _spec(name, module, fail_closed=True)


def test_i7_fail_closed_guard_exception_denies(tmp_path, fake_guards):
    # I7: fail_closed の guard の例外は拒否にする。偽の guard は今と同じく通す
    def boom(ctx, svc):
        raise RuntimeError("壊れた")

    out = _run(tmp_path, [_fail_closed("ask", fake_guards("ask", boom))])
    assert out.verdict.kind == "deny"
    assert out.verdict.reason == dispatch.GUARD_FAILED.format(name="ask", error="RuntimeError")
    assert [(r.name, r.kind, r.error) for r in out.runs] == [("ask", "deny", "RuntimeError")]
    assert _run(tmp_path, [_spec("other", fake_guards("other", boom))]).verdict.kind == "pass"


def test_i7_fail_closed_deny_is_not_downgraded(tmp_path, fake_guards, monkeypatch):
    # I7: 同じ拒否を上限より多く繰り返しても、数え手が使えなくても、fail_closed の拒否は案内へ下げない
    closed = _fail_closed("ask", fake_guards("ask", lambda c, s: deny("記録できない")))
    plain = _spec("plain", fake_guards("plain", lambda c, s: deny("止める")))
    kinds = [(_run(tmp_path, [closed]).verdict.kind, _run(tmp_path, [plain]).verdict.kind) for _ in range(5)]
    assert {k for k, _ in kinds} == {"deny"} and kinds[-1][1] == "advise"
    found = budget.budget_for("claude", "PreToolUse")
    out = dispatch.run(_ctx(tmp_path), [closed, plain], found, dispatch.GuardServices(tmp_path, Counter.unusable(), 0.0))
    assert [r.kind for r in out.runs] == ["deny", "advise"] and out.verdict.reason == "記録できない"


def test_i7_fail_closed_guard_runs_after_the_deadline(tmp_path, fake_guards):
    # I7: 先の guard が締め切りを使い切っても fail_closed の guard は呼び、ほかの guard は呼ばない
    now = [0.0]

    def slow(ctx, svc):
        now[0] = 0.9

    specs = [
        _spec("slow", fake_guards("slow", slow)),
        _spec("later", fake_guards("later", lambda c, s: advise("案内"))),
        _fail_closed("ask", fake_guards("ask", lambda c, s: deny("記録できない"))),
    ]
    out = _run(tmp_path, specs, clock=lambda: now[0])
    assert out.stopped == "deadline" and [r.name for r in out.runs] == ["slow", "ask"] and out.verdict.kind == "deny"


def test_only_the_relay_records_are_fail_closed_in_the_table():
    assert {s.name for s in dispatch.load_specs() if s.fail_closed} == {"ask-forward", "relay-busy"}


def _run_on(tmp_path, ctx, specs, counter):
    svc = dispatch.GuardServices(tmp_path, counter, 0.0)
    return dispatch.run(ctx, specs, budget.budget_for("claude", "PreToolUse"), svc)


def test_deny_key_none_keeps_denying_without_counting(tmp_path, fake_guards):
    # #3995 の決定 6・3995:6: deny_key が none の拒否は数えず、違う呼び出しで 3 回続けても、数え手が使えなくても案内へ下げない
    kept = _spec("kept", fake_guards("kept", lambda c, s: deny("止め続ける")), deny_key="none")
    counted = _spec("counted", fake_guards("counted", lambda c, s: deny("止める")))
    kinds = []
    with Counter.open("s-1", tmp_path) as counter:
        for command in ("ls", "pwd", "git status"):
            ctx = HookContext("claude", "PreToolUse", bash(tmp_path, command))
            kinds.append((_run_on(tmp_path, ctx, [kept], counter).verdict.kind, _run_on(tmp_path, ctx, [counted], counter).verdict.kind))
    assert [k for k, _ in kinds] == ["deny", "deny", "deny"]
    assert [k for _, k in kinds] == ["deny", "deny", "advise"]
    out = _run_on(tmp_path, _ctx(tmp_path), [kept], Counter.unusable())
    assert out.verdict.kind == "deny" and out.verdict.reason == "止め続ける"


def _limit_repo(root, tokens):
    """メインディレクトリに .ndf を持つリポジトリと、最後の応答の文脈の量が `tokens` の会話の記録。"""
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".ndf" / "handoff").mkdir(parents=True, exist_ok=True)
    line = {"type": "assistant", "message": {"usage": {"input_tokens": tokens}}}
    (root / "transcript.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")


def _handoff_write(root):
    file = str(root / ".ndf" / "handoff" / "next.md")
    return sample("claude", "PreToolUse", root, tool_name="Write", tool_input={"file_path": file, "content": "x"})


@pytest.mark.parametrize("tool", ["Bash", "Read"])
def test_context_limit_is_the_first_guard_selected(tmp_path, tool):
    # #3995 の決定 6: dispatch.run の締め切りで飛ばされないよう、PreToolUse の Bash・Read で context-limit が先頭に呼ばれる
    payload = (
        bash(tmp_path, "ls")
        if tool == "Bash"
        else sample("claude", "PreToolUse", tmp_path, tool_name="Read", tool_input={"file_path": "a"})
    )
    names = [s.name for s in dispatch.select(dispatch.load_specs(), HookContext("claude", "PreToolUse", payload), frozenset())]
    assert names[0] == "context-limit" and names[-1] == "relay-busy"


def test_context_limit_verdict_survives_the_deadline_inside_run(tmp_path, monkeypatch):
    # #3995 の決定 6: context-limit の判定の中で締め切りを越えても、その判定は残る（後の guard だけが飛ばされる）
    _limit_repo(tmp_path, 600_000)
    found = budget.budget_for("claude", "PreToolUse")
    now = [0.0]
    real = context_limit.check

    def slow(ctx, svc):
        got = real(ctx, svc)
        now[0] = found.usable + 1.0
        return got

    monkeypatch.setattr(context_limit, "check", slow)
    outs = []
    for payload in (bash(tmp_path, "ls"), _handoff_write(tmp_path)):
        now[0] = 0.0
        ctx = HookContext("claude", "PreToolUse", payload)
        specs = dispatch.select(dispatch.load_specs(), ctx, frozenset())
        svc = dispatch.GuardServices(tmp_path, Counter.unusable(), 0.0)
        outs.append(dispatch.run(ctx, specs, found, svc, clock=lambda: now[0]))
    # relay-busy は fail_closed なので、締め切りを越えても最後に呼ばれる（#4049）
    assert [[r.name for r in out.runs] for out in outs] == [["context-limit", "relay-busy"]] * 2
    assert outs[0].stopped == "deadline" and [out.verdict.kind for out in outs] == ["deny", "pass"]


def _deadline(payload):
    return main._deadline_out(main._Run({}, None, 0.0, "claude", "PreToolUse", payload))


def test_deadline_out_drops_the_context_limit_verdict_only_for_that_call(tmp_path):
    # #3995 の決定 6: プロセスの締め切りを過ぎた 1 回は通るが、続く締め切りを過ぎない呼び出しは止まる
    _limit_repo(tmp_path, 600_000)
    assert _deadline(bash(tmp_path, "ls")) == "" and _deadline(_handoff_write(tmp_path)) == ""
    assert deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "ls"))) is not None


def test_deadline_before_the_output_drops_a_plain_guard_deny(tmp_path, monkeypatch):
    # #3995 の決定 6: dispatch.run が full-test の拒否を返しても、出力の前に締め切りに達すれば出力は空（今と同じ）
    suites = [
        {
            "name": "unit",
            "runner": "pytest",
            "command": "uv run --frozen pytest . -q -n 4",
            "scope_command": "uv run --frozen pytest {paths} -q",
        }
    ]
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps({"version": 1, "test": {"suites": suites}}), encoding="utf-8")
    monkeypatch.setenv(identity.WORKER_KIND_ENV, "impl")
    got = []
    real = dispatch.run

    def capture(*args, **kw):
        got.append(real(*args, **kw))
        return got[-1]

    def late(*args, **kw):
        raise main._Deadline()

    monkeypatch.setattr(dispatch, "run", capture)
    monkeypatch.setattr(dispatch, "render", late)
    assert call_entry("claude", "PreToolUse", bash(tmp_path, "uv run --frozen pytest . -q -n 4")) is None
    assert got[-1].verdict.kind == "deny" and records()[-1]["error"] == "deadline"


def test_deadline_out_keeps_only_the_fail_closed_deny(tmp_path, monkeypatch):
    # #3995 の決定 6: 運用上限の中継の下でも、締め切りの後に残るのは ask-forward の on_deadline の拒否だけ
    _limit_repo(tmp_path, 600_000)
    relay = tmp_path / "relay"
    relay.mkdir()
    signals.set_launch(relay, "L1")
    monkeypatch.setenv("NDF_RELAY_DIR", str(relay))
    monkeypatch.setenv("NDF_RELAY_LAUNCH", "L1")
    questions = [{"question": "設計を承認するか", "header": "設計", "options": [], "multiSelect": False}]
    payload = sample(
        "claude", "PreToolUse", tmp_path, tool_name="AskUserQuestion", tool_input={"questions": questions}, tool_use_id="toolu_A"
    )
    assert deny_reason(json.loads(_deadline(payload))) == ask.QUESTION_NOT_RECORDED
    assert _deadline(bash(tmp_path, "ls")) == ""


def test_run_passes_the_deadline_to_the_guards(tmp_path, fake_guards):
    seen: list[float] = []
    spec = _spec("deadline", fake_guards("deadline", lambda c, s: seen.append(s.deadline)))
    _run(tmp_path, [spec], deadline=123.5)
    assert seen == [123.5]


def test_the_entry_point_passes_the_alarm_deadline_to_the_guards(tmp_path, monkeypatch):
    # SIGALRM と guard が同じ起点の時刻（起動の時刻 ＋ 締め切り）を使う。起動は 100.0、タイマーを置くのは 100.25
    ticks = iter([100.0])
    monkeypatch.setattr(main, "time", types.SimpleNamespace(monotonic=lambda: next(ticks, 100.25)))
    timers: list[float] = []
    monkeypatch.setattr(main.signal, "setitimer", lambda which, seconds: timers.append(seconds))
    seen: list[float] = []
    real = dispatch.run

    def capture(ctx, specs, found, svc, clock=time.monotonic):
        seen.append(svc.deadline)
        return real(ctx, specs, found, svc, clock)

    monkeypatch.setattr(dispatch, "run", capture)
    call_entry("claude", "PreToolUse", bash(tmp_path, "ls"))
    limit = budget.budget_for("claude", "PreToolUse").deadline
    assert seen == [100.0 + limit]
    assert timers[0] == pytest.approx(limit - 0.25) and timers[1:] == [0]


def test_later_guards_see_that_an_earlier_guard_already_asked_for_a_change(tmp_path, fake_guards):
    # #4000: 前の guard が変更要求か拒否を返した後の guard には `decided` が真で渡る（stop-wait はモデルを呼ばない）
    seen: list[tuple[str, bool]] = []

    def watch(name, verdict):
        def check(c, s):
            seen.append((name, s.decided))
            return verdict

        return fake_guards(name, check)

    specs = [
        _spec("first", watch("first", advise("案内"))),
        _spec("second", watch("second", pushback("戻す"))),
        _spec("third", watch("third", None)),
    ]
    _run(tmp_path, specs)
    assert seen == [("first", False), ("second", False), ("third", True)]
