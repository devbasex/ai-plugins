"""suite の種別と起動の失敗（#1483）を**実際の git とシェル**で確かめる。

| 振る舞い | 確かめること |
| --- | --- |
| 静的解析の範囲テスト（AC14） | 項目が整形の違反を入れたら、その項目の範囲テストで落ちる。入れなければ通る |
| 起動の失敗（AC13・I8） | 項目の範囲テスト・テスト整備ラウンド・最終ゲートで起動できなければ、中断で止まり、項目も HEAD も変えない |
| テスト整備ラウンド（AC15・I9） | 足したテストの判定に静的解析の suite を使わない |
| 最終ゲートの静的解析（I12） | 着手前の成否と `scope_command` の有無で、既存失敗か変更起因かを分ける |
"""

from __future__ import annotations

import argparse
import importlib

import pytest

from crossref_helpers import CALC, build_git_flow, commit_with_trailers, git, item_trailers, read_state, strategy_state, write_state

FAR = "2099-01-01T00:00:00+00:00"
# 行末の空白を「整形の違反」とみなす静的解析（ruff format --check の代わり。ツールに依存しない）。
WS_SCOPE = 'sh -c \'for f; do if grep -q " $" "$f"; then exit 1; fi; done\' x {paths}'
WS_WHOLE = 'sh -c \'! git grep -q " $" -- "*.py"\''


def _lint(**over):
    return {"name": "ws", "command": WS_WHOLE, "scope_command": WS_SCOPE, "junit": None, "paths": ["*.py"], "kind": "lint", **over}


def _strategy(*lints):
    base = strategy_state()
    base["suites"] = [*base["suites"], *lints]
    return base


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    return build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir)


def _item(item_id, rank, **over):
    return {
        "id": item_id,
        "rank": rank,
        "path": "src/calc.py",
        "symbol": "add",
        "smell": "long_method",
        "technique": "extract_method",
        "severity": "major",
        "proposed_by": ["codex"],
        "tier": "high",
        "risk": False,
        "tests": [],
        "test_targets": ["tests/test_calc.py"],
        "scope_commands": [{"suite": "pytest", "kind": "test", "command": "pytest -q tests/test_calc.py"}],
        "command_source": "targets",
        "estimate": {"test": 0.0, "implement": 1.3, "verify": 0.2},
        "start_deadline": FAR,
        "test_start_deadline": None,
        "status": "planned",
        "commits": {"test": None, "implement": None, "fix": []},
        "seconds": {},
        "fix_count": 0,
        "danger": [],
        "estimated_diff_lines": 20,
        **over,
    }


def _call(module, name, **kwargs):
    return getattr(module, name)(argparse.Namespace(id=130, **kwargs))


def _emitted(capsys, key):
    out = capsys.readouterr().out
    values = [line.split("=", 1)[1] for line in out.splitlines() if line.startswith(key + "=")]
    return values[-1] if values else None


def _head(work):
    return git("rev-parse", "HEAD", cwd=work).stdout.strip()


def _implement(flow, cmd_setup, cmd_implement, text, strategy, **item_over):
    """項目 I-001 が `src/calc.py` を `text` にするコミットを積んで取り込む。"""
    work = flow["work"]
    state = read_state(flow["path"])
    state["items"] = [_item("I-001", 1, **item_over)]
    state["strategy"] = strategy
    state["plan"] = {
        "base_sha": _head(work),
        "reserve": {"danger_whole_test": 0.1, "final_whole_test": 0.1, "fix": 5.5},
        "end_at": FAR,
        "table_source": "defaults",
    }
    state["phase"] = "implement"
    write_state(flow["path"], state)
    _call(cmd_setup, "cmd_start_phase", phase="implement")
    (work / "src" / "calc.py").write_text(text, encoding="utf-8")
    commit_with_trailers(work, "Refactor I-001", item_trailers("I-001"))
    _call(cmd_implement, "cmd_merge_implement")


def test_a_format_violation_fails_the_item_scope_test(flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    """AC14 — 宣言の静的解析の suite が項目の範囲テストで走り、整形の違反を入れた項目が落ちる。"""
    _implement(flow, cmd_setup, cmd_implement, CALC + "\nX = 1 \n", _strategy(_lint()))
    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "fix"
    assert read_state(flow["path"])["items"][0]["status"] == "failing"


def test_a_clean_change_passes_the_lint_scope_test(flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    _implement(flow, cmd_setup, cmd_implement, CALC + "\nX = 1\n", _strategy(_lint()))
    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "done"
    assert read_state(flow["path"])["items"][0]["status"] == "verified"


def test_an_old_item_command_is_still_read(flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    """移行性 — `scope_commands` を持たない旧形の項目（語の並びの `command`）も同じ語で走る。"""
    _implement(
        flow,
        cmd_setup,
        cmd_implement,
        CALC + "\nX = 1\n",
        strategy_state(),
        scope_commands=None,
        command=["pytest", "-q", "tests/test_calc.py"],
    )
    state = read_state(flow["path"])
    del state["items"][0]["scope_commands"]
    write_state(flow["path"], state)
    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "done"


def test_a_launch_failure_in_the_item_scope_test_stops_without_reverting(flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    """AC13・I8 — 起動の失敗なら中断で止まり、項目の状態も HEAD も変わらず、報告に起動できなかったコマンドが出る。"""
    runs = [{"suite": "pytest", "kind": "test", "command": "nosuchcmd_1483 tests/test_calc.py"}]
    _implement(flow, cmd_setup, cmd_implement, CALC + "\nX = 1\n", strategy_state(), scope_commands=runs)
    head = _head(flow["work"])
    with pytest.raises(SystemExit) as e:
        _call(cmd_converge, "cmd_verify")
    assert e.value.code == 4
    state = read_state(flow["path"])
    assert state["items"][0]["status"] == "implemented"
    assert _head(flow["work"]) == head
    assert state["launch_failure"]["phase"] == "verify" and state["launch_failure"]["code"] == 127
    assert "nosuchcmd_1483 tests/test_calc.py" in capsys.readouterr().err


def test_added_tests_are_judged_by_the_test_suites_only(flow, refactor):
    """AC15・I9 — 足したテストを走らせるコマンドは、テストの種別の suite だけから組む。"""
    implement = importlib.import_module("refactor_lib.commands.implement")
    state = read_state(flow["path"])
    state["strategy"] = _strategy(_lint(), _lint(name="ws2"))
    commands = implement._added_test_commands(state, _item("I-001", 1), ["src/calc.py"])
    assert commands == ["pytest -q tests/test_calc.py"]
    round_state = {**state, "strategy": {**_strategy(_lint()), "name": "round-only", "suites": [_lint()], "round_command": "true"}}
    assert implement._added_test_commands(round_state, _item("I-001", 1, scope_commands=[], command_source="round_test"), []) == []


def test_a_launch_failure_in_the_test_round_stops_without_dropping_the_item(flow, refactor, capsys):
    """AC13（テスト整備ラウンド）— 足したテストのコマンドが起動できなければ、項目を落とさずに止まる。"""
    implement = importlib.import_module("refactor_lib.commands.implement")
    state = read_state(flow["path"])
    runs = [{"suite": "pytest", "kind": "test", "command": "nosuchcmd_1483 tests/test_calc.py"}]
    state["items"] = [_item("I-001", 1, scope_commands=runs)]
    intake = implement.Intake(accepted={"I-001": {"sha": "x", "files": []}})
    with pytest.raises(SystemExit) as e:
        implement._run_added_tests(flow["path"], state, intake)
    assert e.value.code == 4 and intake.test_failed == {} and intake.extra == []
    assert read_state(flow["path"])["launch_failure"]["phase"] == "tests"


# ---------- 最終ゲートの静的解析（I12） ----------


def _gate_state(flow, *, before, lint, change):
    """着手前から `bad.py` に違反がある作業ディレクトリで、`change` を積んだ後の状態を作る。"""
    work = flow["work"]
    (work / "src" / "bad.py").write_text("Y = 2 \n", encoding="utf-8")
    commit_with_trailers(work, "既存の違反", {})
    state = read_state(flow["path"])
    state["baseline_test"]["head"] = _head(work)
    state["baseline_test"]["suites"] = {"pytest": "green", "ws": before}
    state["strategy"] = _strategy(lint)
    (work / "src" / "new.py").write_text(change, encoding="utf-8")
    commit_with_trailers(work, "変更", {})
    write_state(flow["path"], state)
    return state


@pytest.mark.parametrize(
    ("before", "lint", "change", "passed", "verdict"),
    [
        ("red", _lint(), "Z = 3\n", True, "preexisting"),
        ("red", _lint(), "Z = 3 \n", False, "caused"),
        ("red", _lint(scope_command=None), "Z = 3 \n", True, "preexisting"),
        ("green", _lint(), "Z = 3\n", False, "caused"),
    ],
)
def test_the_final_gate_judges_a_failing_lint_by_the_baseline(flow, refactor, before, lint, change, passed, verdict):
    """I12 — 着手前 red は変更したファイルに絞って（`scope_command` が無ければ絞らずに外して）判定し、green は変更起因。"""
    gate_lint = importlib.import_module("refactor_lib.gate_lint")
    state = _gate_state(flow, before=before, lint=lint, change=change)
    gate = {"fix_rounds": 0, "checks": [], "mode": "test"}
    ok, detail = gate_lint.lint_gate(flow["path"], state, gate)
    assert ok is passed
    assert [v["verdict"] for v in gate["lint"]] == [verdict]
    assert gate["checks"][-1]["mode"] == "lint"
    if lint["scope_command"] is None:
        assert "着手前から落ちていた" in detail


def test_the_final_gate_lint_shares_the_whole_timeout_with_the_test_run(flow, refactor):
    """最終ゲートの静的解析は、手元のテストの全体テストの開始時刻から同じ `whole_timeout` を数える。"""
    import time

    gate_lint = importlib.import_module("refactor_lib.gate_lint")
    state = _gate_state(flow, before="green", lint=_lint(), change="Z = 3\n")
    gate = {"fix_rounds": 0, "checks": [], "mode": "test"}
    limit = importlib.import_module("refactor_lib.timeline").state_whole_timeout(state)
    ok, _ = gate_lint.lint_gate(flow["path"], state, gate, started=time.monotonic() - limit)
    assert ok is False
    assert gate["lint"][0]["verdict"] == "caused"


def test_a_launch_failure_in_the_final_gate_stops(flow, refactor, capsys):
    """AC13（最終ゲート）— 静的解析の全体テストが起動できなければ中断で止まり、記録を残す。"""
    gate_lint = importlib.import_module("refactor_lib.gate_lint")
    state = read_state(flow["path"])
    state["strategy"] = _strategy(_lint(command="nosuchcmd_1483"))
    write_state(flow["path"], state)
    with pytest.raises(SystemExit) as e:
        gate_lint.lint_gate(flow["path"], state, {"checks": []})
    assert e.value.code == 4
    assert read_state(flow["path"])["launch_failure"]["command"] == "nosuchcmd_1483"


def test_a_scope_template_with_cd_runs_through_the_shell(tmp_path, refactor):
    """AC9・I5 — `(cd sub && ... {paths})` の雛形を、項目の範囲テストがシェル経由で走らせ、起動の失敗にならない。"""
    targets = importlib.import_module("refactor_lib.targets")
    ts = targets.ts
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "t.txt").write_text("x", encoding="utf-8")
    strategy = ts.Strategy("local-full", "test", [ts.Suite("s", "true", "(cd sub && test -f {paths})")])
    runs = ts.scope_runs(strategy, ["t.txt"], [])
    result, last = targets.run_commands([r.command for r in runs], str(tmp_path), 30, tmp_path / "v.log")
    assert result.status == "passed" and last == "(cd sub && test -f t.txt)"
