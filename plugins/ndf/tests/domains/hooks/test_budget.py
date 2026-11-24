"""hook の予算と上限の順序（M3-H4・IH3）と、止め方の変数（M3-H12）。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from ndf import identity
from ndf.contracts import facts, limits
from ndf.domains.hooks import budget, tables

from ._hooks import bash, call_entry, records, sample, stop


def compared_policies() -> dict[str, dict]:
    """予算の検査が比べる方針（ランタイムのイベントの予算と guard の部品の上限）の、名前 → 中身の複製。"""
    specs = [(rt, ev) for rt, spec in tables.runtimes().items() for ev in spec["events"]]
    names = {found.policy for rt, ev in specs if (found := budget.budget_for(rt, ev)) is not None}
    names |= {name for guard in tables.guards() for name in guard["limits"]}
    return {name: copy.deepcopy(tables.policy(name)) for name in names}


def test_declared_budgets_keep_the_order():
    # CI の検査: 部品の上限 ≤ 締め切り − 余白、締め切り ≤ 登録の timeout ≤ ランタイムの上限
    assert budget.check_budget(budget.budget_registrations()) == []


def test_every_runtime_event_has_a_budget_with_all_values():
    for runtime, rt in tables.runtimes().items():
        for event in rt["events"]:
            found = budget.budget_for(runtime, event)
            assert found is not None, (runtime, event)
            assert found.deadline > found.margin >= 0 and found.timeout >= found.deadline


def test_runtime_specific_budget_is_read_first():
    assert budget.budget_for("codex", "SessionEnd").policy == "hook.budget.session-end.codex"
    assert budget.budget_for("claude", "SessionEnd").policy == "hook.budget.session-end"
    assert budget.budget_for("codex", "SessionEnd").timeout <= tables.runtimes()["codex"]["events"]["SessionEnd"]["timeout_max"]


def test_stop_budget_adds_the_judge_seconds_only_for_the_runtimes_that_judge():
    """Codex の Stop はモデルを呼ばないので、判定の秒を足した Claude Code の予算でなく元の予算を読む（#4000）。"""
    judge = tables.policy("hook.stop-wait.judge")
    assert judge["runtimes"] == ["claude"]
    assert budget.budget_for("codex", "Stop").policy == "hook.budget.stop.codex"
    assert budget.budget_for("claude", "Stop").policy == "hook.budget.stop"
    assert budget.budget_for("codex", "Stop").usable < judge["seconds"] <= budget.budget_for("claude", "Stop").usable


def test_component_limit_is_compared_only_with_the_budgets_of_its_runtimes(monkeypatch):
    widened = compared_policies()
    widened["hook.stop-wait.judge"]["runtimes"] = ["claude", "codex"]
    monkeypatch.setattr(tables, "policy", widened.get)
    errors = budget.check_budget(budget.budget_registrations())
    seconds, usable = widened["hook.stop-wait.judge"]["seconds"], budget.budget_for("codex", "Stop").usable
    assert [e for e in errors if "hook.stop-wait.judge" in e] == [
        f"stop-wait の部品の上限 hook.stop-wait.judge（{seconds} 秒）が codex の Stop の締め切り − 余白（{usable} 秒）を超える"
    ]


def test_component_limit_longer_than_the_deadline_is_a_violation(monkeypatch):
    broken = compared_policies()
    broken["hook.ask-forward"]["seconds"] = 8
    monkeypatch.setattr(tables, "policy", broken.get)
    errors = budget.check_budget(budget.budget_registrations())
    assert any("hook.ask-forward" in e and "PreToolUse" in e for e in errors)


def test_guard_limit_errors_report_missing_budgets_and_components_and_overruns(monkeypatch):
    # 現状固定: 予算の行の欠落・部品の上限の欠落・締め切り − 余白の超過の文と、重複を除いて並べた順
    broken = compared_policies()
    del broken["hook.budget.stop"]
    del broken["hook.ask-forward"]
    broken["hook.counter-lock"]["seconds"] = 99
    monkeypatch.setattr(tables, "policy", broken.get)
    assert budget.check_budget([]) == [
        # ask-answered・ask-closed は ask-forward と同じ上限 hook.ask-forward を使う（#3814）
        "ask-answered の部品の上限 contracts/limits/hook.ask-forward.json が無い",
        "ask-closed の部品の上限 contracts/limits/hook.ask-forward.json が無い",
        "ask-forward の部品の上限 contracts/limits/hook.ask-forward.json が無い",
        # codex の Stop は hook.budget.stop.codex を読む（stop-wait のモデルの判定の秒を足さない。#4000）
        "claude の Stop の予算が contracts/limits/hook.budget.stop.json に無い",
        "read-repeat の部品の上限 hook.counter-lock（99 秒）が claude の PreToolUse の締め切り − 余白（0.5 秒）を超える",
        "read-repeat の部品の上限 hook.counter-lock（99 秒）が codex の PreToolUse の締め切り − 余白（0.5 秒）を超える",
        # relay-marker の Stop は、claude では予算の行を消したので上の 1 行に、codex では hook.budget.stop.codex に当たる
        "relay-marker の部品の上限 hook.counter-lock（99 秒）が codex の Stop の締め切り − 余白（2.0 秒）を超える",
        "turn の部品の上限 hook.counter-lock（99 秒）が claude の UserPromptSubmit の締め切り − 余白（0.5 秒）を超える",
        "turn の部品の上限 hook.counter-lock（99 秒）が codex の UserPromptSubmit の締め切り − 余白（0.5 秒）を超える",
    ]


def test_registration_without_timeout_or_shorter_than_the_deadline_is_a_violation():
    regs = [
        {"runtime": "claude", "event": "Stop", "timeout": None},
        {"runtime": "claude", "event": "PreToolUse", "timeout": 0.5},
        {"runtime": "codex", "event": "SessionEnd", "timeout": 10},
        {"runtime": "claude", "event": "Nope", "timeout": 3},
    ]
    errors = budget.check_budget(regs)
    assert len(errors) == 4
    assert any("timeout が無い" in e for e in errors) and any("上限 3" in e for e in errors)


def test_registration_files_are_read_when_present(tmp_path):
    (tmp_path / "hooks").mkdir()
    reg = {"hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "x", "timeout": 6}]}]}}
    (tmp_path / "hooks" / "claude.json").write_text(json.dumps(reg), encoding="utf-8")
    regs = budget.registrations_of(tmp_path)
    assert {"runtime": "claude", "event": "Stop", "timeout": 6} in regs
    assert all(r["runtime"] != "claude" or r["event"] == "Stop" for r in regs)
    assert any(r["runtime"] == "codex" for r in regs)


def _write_registration(root, runtime: str, data) -> None:
    (root / "hooks").mkdir(exist_ok=True)
    (root / "hooks" / f"{runtime}.json").write_text(json.dumps(data), encoding="utf-8")


def test_registrations_without_files_are_built_from_the_budget(tmp_path):
    assert budget.registrations_of(tmp_path) == budget.budget_registrations()
    # ファイルでないもの（ディレクトリ）も無いものとして扱う
    (tmp_path / "hooks" / "claude.json").mkdir(parents=True)
    assert budget.registrations_of(tmp_path) == budget.budget_registrations()


def test_registration_files_keep_every_hook_in_order(tmp_path):
    claude = {
        "hooks": {
            "Stop": [{"hooks": [{"timeout": 6}, {"command": "x"}]}, {"hooks": [{"timeout": 2}]}],
            "PreToolUse": [{"hooks": [{"timeout": 3}]}],
            "SessionEnd": [],
        }
    }
    _write_registration(tmp_path, "claude", claude)
    _write_registration(tmp_path, "codex", {"hooks": {"Stop": [{"hooks": [{"timeout": 9}]}]}})
    assert budget.registrations_of(tmp_path) == [
        {"runtime": "claude", "event": "Stop", "timeout": 6},
        {"runtime": "claude", "event": "Stop", "timeout": None},
        {"runtime": "claude", "event": "Stop", "timeout": 2},
        {"runtime": "claude", "event": "PreToolUse", "timeout": 3},
        {"runtime": "codex", "event": "Stop", "timeout": 9},
    ]


def test_registration_file_without_a_hooks_object_has_no_rows(tmp_path):
    others = [r for r in budget.budget_registrations() if r["runtime"] != "claude"]
    for data in ({}, {"hooks": [1]}, {"hooks": None}):
        _write_registration(tmp_path, "claude", data)
        assert budget.registrations_of(tmp_path) == others


def test_session_start_advises_on_a_broken_budget(tmp_path, monkeypatch):
    broken = compared_policies()
    broken["hook.counter-lock"]["seconds"] = 30
    real = tables.policy
    monkeypatch.setattr(tables, "policy", lambda name: broken[name] if name in broken else real(name))
    out = call_entry("claude", "SessionStart", sample("claude", "SessionStart", tmp_path))
    assert "予算の検査で破れ" in out["hookSpecificOutput"]["additionalContext"]
    monkeypatch.undo()
    assert call_entry("claude", "SessionStart", sample("claude", "SessionStart", tmp_path)) is None


def test_hook_budget_policies_pass_the_limits_schema():
    # 予算のキーは上限の設定の形（D1）で読める
    for name in compared_policies():
        if name.startswith("hook.budget."):
            assert limits.value(name, "timeout") >= limits.value(name, "deadline")


def test_hook_off_is_the_only_switch_in_the_env_table():
    hooks_rows = facts.ids("env", area="hooks")
    # 止める口は NDF_HOOK_OFF だけ。NDF_CONTEXT_LIMIT は運用上限を下げる値で、止める口ではない（#3995 の詳細設計で area を hooks にした）
    assert sorted(hooks_rows) == ["NDF_CONTEXT_LIMIT", "NDF_HOOK_OFF"]
    # ほかの閾値は上限の設定から読み、環境変数では受けない（今の NDF の止める変数を読まない）
    assert not [n for n in facts.ids("env") if n.endswith("_GUARD")]


def test_hook_off_all_and_named_switch_guards_off(tmp_path, monkeypatch):
    payload = sample("claude", "PreToolUse", tmp_path, tool_name="Bash", tool_input={"command": "sleep 999"})
    assert call_entry("claude", "PreToolUse", payload) is not None
    monkeypatch.setenv("NDF_HOOK_OFF", "read-repeat, sleep")
    assert call_entry("claude", "PreToolUse", payload) is None
    monkeypatch.setenv("NDF_HOOK_OFF", "all")
    assert call_entry("claude", "PreToolUse", payload) is None


def _forget_tables():
    tables.load.cache_clear()
    tables.policy.cache_clear()


@pytest.fixture
def read_limits(monkeypatch):
    """`contracts/limits/` から読んだファイルの名前（読んだ順）。1 プロセス 1 回のキャッシュは空から始める。"""
    got: list[str] = []
    real = tables._read_json

    def spy(path):
        data = real(path)
        if path.parent == tables.LIMITS_DIR:
            got.append(path.name)
        return data

    monkeypatch.setattr(tables, "_read_json", spy)
    _forget_tables()
    yield got
    _forget_tables()


def _read_until_denied(tmp_path):
    """同じ範囲の Read。1 つ前の呼び出しまでで上限に達していて、次の呼び出しを read-repeat が断る。"""
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    payload = sample("claude", "PreToolUse", tmp_path, tool_name="Read", tool_input={"file_path": str(target)})
    for _ in range(tables.policy_value("hook.read-repeat", "attempts")):
        call_entry("claude", "PreToolUse", payload)
    return payload


def _stop_of_unattended_run(tmp_path):
    prompt = f"/{identity.load().prefix}development-workflow #12"
    call_entry("claude", "UserPromptSubmit", sample("claude", "UserPromptSubmit", tmp_path, prompt=prompt))
    return stop(tmp_path, "終わりました")


@pytest.mark.parametrize(
    ("event", "guard", "payload_of", "cap"),
    [
        ("PreToolUse", "read-repeat", _read_until_denied, 6),
        ("PreToolUse", "sleep", lambda p: bash(p, "sleep 999"), 6),
        ("Stop", "unattended", _stop_of_unattended_run, 6),
        ("SessionStart", "session", lambda p: sample("claude", "SessionStart", p), 20),
    ],
)
def test_one_hook_run_reads_only_the_policies_it_uses(tmp_path, monkeypatch, read_limits, event, guard, payload_of, cap):
    # #3687: 1 方針 1 ファイルなので、起動のたびに全件を読むと 200 を超えるファイルを読む。予算の解決から guard までの 1 起動分を数える
    monkeypatch.setenv("NDF_FACTORY_RUN", "1")
    payload = payload_of(tmp_path)
    _forget_tables()
    read_limits.clear()
    call_entry("claude", event, payload)
    record = records()[-1]
    assert record["event"] == event and not record["error"]
    assert guard in [g["name"] for g in record["guards"]]
    assert 1 < len(read_limits) <= cap, read_limits


@pytest.mark.parametrize(
    ("runtime", "checked"),
    [("codex", ["hook.budget.stop.codex.json"]), ("claude", ["hook.budget.stop.claude.json", "hook.budget.stop.json"])],
)
def test_budget_lookup_tries_each_policy_file_once(monkeypatch, runtime, checked):
    """予算の選択と読み込みが、方針のファイルの有無を照会せず、1 回の読みの結果を分け合う。"""
    tried: list[str] = []
    stats: list[str] = []
    real_read, real_is_file = tables._read_json, Path.is_file

    def spy_read(path):
        if path.parent == tables.LIMITS_DIR:
            tried.append(path.name)
        return real_read(path)

    def spy_is_file(path):
        if path.parent == tables.LIMITS_DIR:
            stats.append(path.name)
        return real_is_file(path)

    monkeypatch.setattr(tables, "_read_json", spy_read)
    monkeypatch.setattr(Path, "is_file", spy_is_file)
    _forget_tables()
    for _ in range(2):
        assert budget.budget_for(runtime, "Stop") is not None
    assert tried == checked
    assert stats == []


def test_budget_check_reads_only_the_policies_it_compares(read_limits):
    # 予算の検査で比べない hook の方針（hook.sleep など）を読まない（PR 4335 の r1-1）
    used = {p.policy for rt, spec in tables.runtimes().items() for ev in spec["events"] if (p := budget.budget_for(rt, ev)) is not None}
    used |= {name for guard in tables.guards() for name in guard["limits"]}
    _forget_tables()
    read_limits.clear()
    budget.check_budget(budget.budget_registrations())
    assert sorted(read_limits) == sorted(f"{name}.json" for name in used)


def test_policy_value_reads_its_own_file_once(read_limits):
    assert tables.policy_value("hook.sleep", "seconds") == tables.policy_value("hook.sleep", "seconds")
    assert read_limits == ["hook.sleep.json"]
    with pytest.raises(tables.TableError, match="no.such に seconds が無い"):
        tables.policy_value("no.such", "seconds")
